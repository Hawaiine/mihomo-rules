#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自动合并前的有界等待与严格检查判定（PR-first）。

职责：
  * 启动后立即**只读**重新读取 PR 与候选分支 ref SHA；
  * 在限定的总超时内用 GET 轮询检查状态（递增间隔，不做任何写请求）；
  * 检查尚未出现/仍在运行时继续等待；**明确失败立即终止**；
  * 全部完成后再次读取 PR，确认仍 open、base 仍为目标分支、head SHA 未漂移；
  * 任何超时、head 漂移、权限不足（403）、限流（429）、5xx 或状态不明一律 fail-closed。

严格口径：必需检查的结论必须是 `success`。`skipped` / `neutral` / `cancelled` /
`timed_out` / `failure` / queued / in-progress / 未找到 一律视为**不满足**。
（GitHub 官方文档：成功状态包含 `success`、`skipped`、`neutral`，因此跳过的 job 可能被
状态检查体系视为成功——本脚本不把「存在检查」等同于「检查真正成功」。）

只读：本脚本只发 GET；不写仓库、不请求 auto-merge。
用法：
  python3 scripts/ci/await_checks.py --repo owner/name --pr 123 --expected-head-sha <sha> \
      [--base-branch main] [--required-checks .github/required-checks.json] \
      [--timeout 900] [--interval 15] [--max-interval 60] [--json out.json]
退出码：0 = 所有必需检查在该 head SHA 上真实成功；1 = 失败/超时/API 异常（禁止合并）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

DEFAULT_MANIFEST = ".github/required-checks.json"
STRICT_CONCLUSION = "success"
STATUS_PENDING, STATUS_SUCCESS, STATUS_FAILED, STATUS_TIMEOUT, STATUS_ERROR = (
    "pending", "success", "failed", "timeout", "error")


class ApiError(Exception):
    def __init__(self, status: int, detail: str = ""):
        super().__init__(f"HTTP {status} {detail}".strip())
        self.status = status
        self.detail = detail


class ManifestError(Exception):
    pass


def load_required(path: str) -> list[dict]:
    """读取必需检查清单（单一来源）。兼容旧版 {"required": [...]} 形态。"""
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"无法读取必需检查清单 {path}: {exc}") from exc
    if isinstance(doc, dict) and isinstance(doc.get("checks"), list):
        out = []
        for c in doc["checks"]:
            if isinstance(c, str):
                out.append({"name": c, "app_slug": None, "app_id": None})
            elif isinstance(c, dict) and c.get("name"):
                src = c.get("source") or {}
                out.append({"name": c["name"], "app_slug": src.get("app_slug"),
                            "app_id": src.get("app_id"), "job_id": c.get("job_id")})
            else:
                raise ManifestError(f"清单项非法：{c!r}")
        if not out:
            raise ManifestError(f"{path}: checks 为空")
        return out
    if isinstance(doc, dict) and isinstance(doc.get("required"), list) and doc["required"]:
        return [{"name": n, "app_slug": None, "app_id": None} for n in doc["required"]]
    raise ManifestError(f"{path}: 结构无法识别（需 checks[] 或 required[]）")


def _app_matches(run: dict, req: dict) -> bool:
    app = run.get("app") or {}
    if req.get("app_slug") and app.get("slug") != req["app_slug"]:
        return False
    if req.get("app_id") is not None and app.get("id") != req["app_id"]:
        return False
    return True


def evaluate(runs: list[dict], required: list[dict], expected_head: str) -> tuple[str, dict, list[str]]:
    """返回 (overall_status, per_check, problems)。

    overall_status ∈ {pending, success, failed}；failed 为终局（立即终止）。
    """
    per_check: dict[str, dict] = {}
    problems: list[str] = []
    any_pending = False
    any_failed = False
    for req in required:
        name = req["name"]
        same_name = [r for r in runs if r.get("name") == name]
        on_head = [r for r in same_name if r.get("head_sha") == expected_head]
        matching = [r for r in on_head if _app_matches(r, req)]
        stale = [r for r in same_name if r.get("head_sha") != expected_head]
        if not matching:
            state, conclusion, detail = "missing", None, "该 head SHA 上未找到匹配来源的检查"
            if on_head and not any(_app_matches(r, req) for r in on_head):
                detail = "存在同名检查但来源（app）不符：拒绝采信"
                problems.append(f"{name}: {detail}")
            any_pending = True
        else:
            run = sorted(matching, key=lambda r: (r.get("started_at") or "", r.get("id") or 0))[-1]
            app = run.get("app") or {}
            conclusion = run.get("conclusion")
            if run.get("status") != "completed":
                state, detail = "in-progress", f"status={run.get('status')}"
                any_pending = True
            elif conclusion == STRICT_CONCLUSION:
                state, detail = "success", "completed/success"
            else:
                state = "failed"
                detail = f"completed/{conclusion}（严格口径仅接受 success）"
                any_failed = True
                problems.append(f"{name}: {detail}")
            per_check[name] = {"state": state, "conclusion": conclusion, "status": run.get("status"),
                               "app_slug": app.get("slug"), "app_id": app.get("id"),
                               "head_sha": run.get("head_sha"), "details_url": run.get("details_url"),
                               "detail": detail}
            if stale:
                per_check[name]["stale_runs_on_other_sha"] = [
                    {"head_sha": r.get("head_sha"), "conclusion": r.get("conclusion")} for r in stale]
            continue
        per_check[name] = {"state": state, "conclusion": conclusion, "detail": detail,
                           "head_sha": expected_head}
        if stale:
            per_check[name]["stale_runs_on_other_sha"] = [
                {"head_sha": r.get("head_sha"), "conclusion": r.get("conclusion")} for r in stale]
    if any_failed:
        return STATUS_FAILED, per_check, problems
    if any_pending:
        return STATUS_PENDING, per_check, problems
    return STATUS_SUCCESS, per_check, problems


# ---------------------------------------------------------------- HTTP（可注入）

def http_get_json(url: str, token: str, timeout: int = 30) -> tuple[int, object]:
    req = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "prfirst-await-checks"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as exc:
        body = exc.read()[:400].decode("utf-8", "replace")
        return exc.code, {"_error": body}


class Poller:
    """可注入依赖的轮询器（便于 mock 测试；不产生任何写请求）。"""

    def __init__(self, *, repo: str, pr: int, expected_head: str, base_branch: str, required: list[dict],
                 token: str, api_base: str = "https://api.github.com", timeout: float = 900.0,
                 interval: float = 15.0, max_interval: float = 60.0, max_api_retries: int = 3,
                 fetcher=http_get_json, sleeper=time.sleep, clock=time.monotonic, log=print):
        self.repo, self.pr, self.expected_head = repo, int(pr), expected_head
        self.base_branch, self.required, self.token = base_branch, required, token
        self.api_base, self.timeout = api_base.rstrip("/"), timeout
        self.interval, self.max_interval, self.max_api_retries = interval, max_interval, max_api_retries
        self.fetcher, self.sleeper, self.clock, self.log = fetcher, sleeper, clock, log
        self.http_requests = 0

    # --- 只读原语 ---
    def _get(self, path: str) -> dict:
        self.http_requests += 1
        status, body = self.fetcher(f"{self.api_base}{path}", self.token)
        if status != 200 or not isinstance(body, dict) or "_error" in body:
            raise ApiError(status, str((body or {}).get("_error", ""))[:200] if isinstance(body, dict) else "")
        return body

    def _get_with_retry(self, path: str, backoff_base: float = 2.0) -> dict:
        last: ApiError | None = None
        for attempt in range(self.max_api_retries + 1):
            try:
                return self._get(path)
            except ApiError as exc:
                last = exc
                if attempt >= self.max_api_retries:
                    break
                wait = backoff_base * (2 ** attempt)
                self.log(f"⚠️ API {exc} → {wait:.0f}s 后重试（{attempt + 1}/{self.max_api_retries}）")
                self.sleeper(wait)
        raise ApiError(last.status if last else 0, f"重试 {self.max_api_retries} 次后仍失败")

    def read_pr(self) -> dict:
        return self._get_with_retry(f"/repos/{self.repo}/pulls/{self.pr}")

    def read_ref(self) -> dict:
        return self._get_with_retry(f"/repos/{self.repo}/git/ref/heads/{self.base_branch}")

    def read_runs(self) -> list[dict]:
        body = self._get_with_retry(f"/repos/{self.repo}/commits/{self.expected_head}/check-runs?per_page=100")
        return list(body.get("check_runs") or [])

    # --- 前置与终局校验 ---
    def preflight(self) -> tuple[bool, list[str], dict]:
        problems: list[str] = []
        pr = self.read_pr()
        info = {"pr_state": pr.get("state"), "base_ref": (pr.get("base") or {}).get("ref"),
                "pr_head_sha": (pr.get("head") or {}).get("sha")}
        if pr.get("state") != "open":
            problems.append(f"PR 状态为 {pr.get('state')!r}，非 open")
        if (pr.get("base") or {}).get("ref") != self.base_branch:
            problems.append(f"PR 目标分支为 {(pr.get('base') or {}).get('ref')!r}，非 {self.base_branch!r}")
        if (pr.get("head") or {}).get("sha") != self.expected_head:
            problems.append(f"PR head SHA {(pr.get('head') or {}).get('sha')} != 预期 {self.expected_head}")
        ref = self.read_ref()
        info["base_ref_sha"] = (ref.get("object") or {}).get("sha")
        return (not problems), problems, info

    def recheck_pr(self) -> tuple[bool, list[str]]:
        problems: list[str] = []
        pr = self.read_pr()
        if pr.get("state") != "open":
            problems.append(f"检查完成后 PR 状态变为 {pr.get('state')!r}")
        if (pr.get("base") or {}).get("ref") != self.base_branch:
            problems.append("检查完成后 PR 目标分支发生变化")
        if (pr.get("head") or {}).get("sha") != self.expected_head:
            problems.append(f"检查完成后 head SHA 漂移：{(pr.get('head') or {}).get('sha')}")
        return (not problems), problems

    # --- 主循环 ---
    def run(self) -> dict:
        started = self.clock()
        report: dict = {"head_sha": self.expected_head, "repo": self.repo, "pr": self.pr,
                        "required": [r["name"] for r in self.required], "polls": 0, "problems": []}
        try:
            ok, problems, info = self.preflight()
            report.update(info)
            if not ok:
                report.update({"status": STATUS_FAILED, "reason": "前置校验失败（fail-closed）",
                               "problems": problems})
                return self._finish(report, started)
            interval = self.interval
            while True:
                runs = self.read_runs()
                report["polls"] += 1
                status, per_check, problems = evaluate(runs, self.required, self.expected_head)
                report["checks"] = per_check
                report["problems"] = problems
                self.log(f"轮询 #{report['polls']}：status={status} "
                         f"{ {k: v['state'] for k, v in per_check.items()} }")
                if status == STATUS_FAILED:
                    report.update({"status": STATUS_FAILED, "reason": "存在未通过/被跳过的必需检查"})
                    return self._finish(report, started)
                if status == STATUS_SUCCESS:
                    break
                if self.clock() - started >= self.timeout:
                    report.update({"status": STATUS_TIMEOUT,
                                   "reason": f"在 {self.timeout:.0f}s 内未等到全部必需检查完成"})
                    return self._finish(report, started)
                wait = min(interval, self.max_interval)
                self.sleeper(wait)
                interval *= 2
            ok, problems = self.recheck_pr()
            if not ok:
                report.update({"status": STATUS_FAILED, "reason": "检查完成后状态复核失败（漂移）",
                               "problems": problems})
                return self._finish(report, started)
            report.update({"status": STATUS_SUCCESS, "reason": "全部必需检查在该 head SHA 上真实 success"})
            return self._finish(report, started)
        except ApiError as exc:
            report.update({"status": STATUS_ERROR, "reason": f"API 异常（fail-closed）：{exc}",
                           "problems": [str(exc)]})
            return self._finish(report, started)

    def _finish(self, report: dict, started: float) -> dict:
        report["elapsed_s"] = round(self.clock() - started, 3)
        report["http_requests"] = self.http_requests
        report["writes_performed"] = []
        return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="有界等待必需检查完成（只读）")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--pr", required=True)
    ap.add_argument("--expected-head-sha", required=True)
    ap.add_argument("--base-branch", default="main")
    ap.add_argument("--required-checks", default=DEFAULT_MANIFEST)
    ap.add_argument("--timeout", type=float, default=900.0)
    ap.add_argument("--interval", type=float, default=15.0)
    ap.add_argument("--max-interval", type=float, default=60.0)
    ap.add_argument("--api-base", default=os.environ.get("GITHUB_API_URL", "https://api.github.com"))
    ap.add_argument("--json")
    a = ap.parse_args(argv)

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    if not token:
        print("❌ 缺少 GITHUB_TOKEN（只读用途）", file=sys.stderr)
        return 1
    try:
        required = load_required(a.required_checks)
    except ManifestError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1
    poller = Poller(repo=a.repo, pr=a.pr, expected_head=a.expected_head_sha, base_branch=a.base_branch,
                    required=required, token=token, api_base=a.api_base, timeout=a.timeout,
                    interval=a.interval, max_interval=a.max_interval)
    report = poller.run()
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    if report["status"] == STATUS_SUCCESS:
        print("✅ 全部必需检查在该 head SHA 上真实成功（可以进入 auto-merge 请求）")
        return 0
    print(f"❌ 状态={report['status']}：{report.get('reason')}（fail-closed，禁止合并）", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
