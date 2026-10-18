#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自动合并前的有界等待与严格检查判定（PR-first）。

职责：
  * 启动后立即**只读**读取 PR、`refs/heads/<base>` 与候选分支状态，
    核实 `PR.base.ref == base`、`PR.base.sha == refs/heads/<base>` 当前 SHA、`PR.head.sha == 预期 head`；
  * 在限定的总超时内用 GET 轮询（递增间隔，不做任何写请求）；
  * 检查尚未出现/仍在运行时继续等待；**明确失败立即终止**；
  * 全部完成后再次读取 PR 与 base ref，确认仍 open、目标分支未变、head 未漂移、base SHA 仍一致；
  * 任何超时、漂移、权限不足（403）、限流（429）、5xx 或状态不明一律 fail-closed。

严格口径（与 `scripts/ci/check_provenance.py` 共享同一实现，避免口径漂移）：
  必需检查不仅要 `name + head_sha + app_id` 匹配，还必须通过 **workflow/job 身份认证**：
  check_suite → workflow run（path 必须为 `.github/workflows/pr-verify.yml`、event 必须为
  `pull_request`、head_sha 必须等于 PR head）→ job 记录（check run id == job id、
  job name == manifest 的 job_id）。**其它 workflow 的同名成功检查不得采信**。
  结论必须是 `success`；`skipped`/`neutral`/`cancelled`/`timed_out`/`failure`/queued/in-progress
  一律视为不满足。

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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gate_status import choose_mode, evaluate_statuses, load_status_contexts  # noqa: E402
from check_provenance import (  # noqa: E402
    ApiError, DEFAULT_MANIFEST, ManifestError, STATE_MISSING, STATE_NOT_SUCCESS, STATE_SUCCESS,
    STATE_UNVERIFIED, collect_check_runs, collect_jobs, collect_workflow_runs, http_get_json,
    index_jobs, load_manifest, needed_run_ids, overall_state, select_checks,
)

STRICT_CONCLUSION = "success"
STATUS_PENDING, STATUS_SUCCESS, STATUS_FAILED, STATUS_TIMEOUT, STATUS_ERROR = (
    "pending", "success", "failed", "timeout", "error")


def evaluate(check_runs: list[dict], workflow_runs: list[dict], jobs_by_id: dict, required: list[dict],
             expected_head: str, pr_head: str = "") -> tuple[str, dict, list[str]]:
    """纯函数判定：返回 (overall_status, per_check, problems)。

    overall_status ∈ {pending, success, failed}；failed 为终局（立即终止）。
    身份认证失败的**已完成**同名检查 ⇒ failed（不得由其它 workflow 的同名成功检查覆盖）。
    """
    per_check, problems = select_checks(check_runs=check_runs, manifest=required,
                                        workflow_runs=workflow_runs, jobs_by_id=jobs_by_id,
                                        expected_head=expected_head, pr_head=pr_head or expected_head)
    state = overall_state(per_check)
    for name, info in per_check.items():
        if info["state"] == STATE_MISSING:
            problems.append(f"{name}: 该 head SHA 上未找到任何同名检查")
        elif info["state"] == STATE_UNVERIFIED:
            problems.append(f"{name}: 同名检查存在但 workflow/job 身份认证失败（拒绝采信）")
        elif info["state"] == STATE_NOT_SUCCESS:
            problems.append(f"{name}: 结论 {info.get('conclusion')!r}，严格口径仅接受 {STRICT_CONCLUSION!r}")
    return state, per_check, problems


class Poller:
    """可注入依赖的轮询器（便于 mock 测试；不产生任何写请求）。"""

    def __init__(self, *, repo: str, pr: int, expected_head: str, base_branch: str, required: list[dict],
                 token: str, api_base: str = "https://api.github.com", timeout: float = 900.0,
                 interval: float = 15.0, max_interval: float = 60.0, max_api_retries: int = 3,
                 fetcher=http_get_json, sleeper=time.sleep, clock=time.monotonic, log=print,
                 contexts: list[str] | None = None):
        self.repo, self.pr, self.expected_head = repo, int(pr), expected_head
        self.base_branch, self.required, self.token = base_branch, required, token
        self.api_base, self.timeout = api_base.rstrip("/"), timeout
        self.interval, self.max_interval, self.max_api_retries = interval, max_interval, max_api_retries
        self.fetcher, self.sleeper, self.clock, self.log = fetcher, sleeper, clock, log
        self.contexts = list(contexts or [])
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

    def read_base_ref(self) -> dict:
        return self._get_with_retry(f"/repos/{self.repo}/git/ref/heads/{self.base_branch}")

    def _retrying_getter(self, url: str, _token: str | None = None) -> tuple[int, object]:
        """把 `_get_with_retry` 适配成 `(url, token) -> (status, body)` 形态。

        有界重试（最多 `max_api_retries` 次，指数退避）用于抵御瞬时 429/5xx；
        重试耗尽仍失败 ⇒ 抛 ApiError ⇒ 立即停止，绝不触发任何 mutation。
        """
        try:
            return 200, self._get_with_retry(url[len(self.api_base):] if url.startswith(self.api_base) else url)
        except ApiError as exc:
            return exc.status, {"_error": exc.detail}

    def read_provenance(self) -> tuple[list[dict], list[dict], dict]:
        getter = self._retrying_getter
        check_runs = collect_check_runs(getter=getter, token=self.token, api_base=self.api_base,
                                        repo=self.repo, head_sha=self.expected_head)
        workflow_runs = collect_workflow_runs(getter=getter, token=self.token, api_base=self.api_base,
                                              repo=self.repo, head_sha=self.expected_head)
        jobs_by_id: dict[int, dict] = {}
        for rid in needed_run_ids(check_runs, self.required, workflow_runs):
            jobs_by_id.update(index_jobs(collect_jobs(getter=getter, token=self.token,
                                                      api_base=self.api_base, repo=self.repo, run_id=rid)))
        return check_runs, workflow_runs, jobs_by_id

    # --- 前置与终局校验（含 base SHA 漂移闭环）---
    def _pr_and_base_state(self, phase: str) -> tuple[bool, list[str], dict]:
        """读取 PR 与 base ref，校验 open/base 名/base SHA/head SHA 一致性。"""
        problems: list[str] = []
        pr = self.read_pr()
        ref = self.read_base_ref()
        base_ref = (pr.get("base") or {}).get("ref")
        base_sha = (pr.get("base") or {}).get("sha")
        head_sha = (pr.get("head") or {}).get("sha")
        ref_sha = (ref.get("object") or {}).get("sha")
        info = {"phase": phase, "pr_state": pr.get("state"), "base_ref": base_ref, "base_sha": base_sha,
                "pr_head_sha": head_sha, f"refs_heads_{self.base_branch}": ref_sha}
        if pr.get("state") != "open":
            problems.append(f"{phase}：PR 状态为 {pr.get('state')!r}，非 open")
        if pr.get("merged"):
            problems.append(f"{phase}：PR 已被合并")
        if base_ref != self.base_branch:
            problems.append(f"{phase}：PR 目标分支为 {base_ref!r}，非 {self.base_branch!r}")
        if not ref_sha:
            problems.append(f"{phase}：refs/heads/{self.base_branch} 响应缺少 sha（fail-closed）")
        elif base_sha != ref_sha:
            problems.append(f"{phase}：PR base SHA {base_sha} != refs/heads/{self.base_branch} 当前 SHA "
                            f"{ref_sha}（base 漂移）")
        if head_sha != self.expected_head:
            problems.append(f"{phase}：PR head SHA {head_sha} != 预期 {self.expected_head}（head 漂移）")
        return (not problems), problems, info

    def preflight(self) -> tuple[bool, list[str], dict]:
        return self._pr_and_base_state("前置校验")

    def _read_statuses(self) -> list[dict]:
        """commit status 列表（GET 返回数组；失败即 fail-closed）。"""
        self.http_requests += 1
        status, body = self.fetcher(
            f"{self.api_base}/repos/{self.repo}/commits/{self.expected_head}/statuses?per_page=100",
            self.token)
        if status != 200 or not isinstance(body, list):
            raise ApiError(status, "commit statuses 读取失败")
        return body

    def recheck_pr(self) -> tuple[bool, list[str]]:
        ok, problems, _ = self._pr_and_base_state("检查完成后复核")
        return ok, problems

    # --- 主循环 ---
    def run(self) -> dict:
        started = self.clock()
        report: dict = {"head_sha": self.expected_head, "repo": self.repo, "pr": self.pr,
                        "base_branch": self.base_branch,
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
                check_runs, workflow_runs, jobs_by_id = self.read_provenance()
                report["polls"] += 1
                pr_head = (report.get("pr_head_sha") or self.expected_head)
                mode = choose_mode(check_runs, self.contexts)
                report["gate_mode"] = mode
                if mode == "status":
                    statuses = self._read_statuses()
                    status, per_check, problems = evaluate_statuses(
                        statuses, self.contexts, self.expected_head)
                    report["status_contexts"] = per_check
                else:
                    status, per_check, problems = evaluate(check_runs, workflow_runs, jobs_by_id,
                                                           self.required, self.expected_head, pr_head)
                report["checks"] = per_check
                report["problems"] = problems
                report["provenance_state"] = overall_state(per_check)
                self.log(f"轮询 #{report['polls']}：status={status} "
                         f"{ {k: v['state'] for k, v in per_check.items()} }")
                if status == STATUS_FAILED:
                    report.update({"status": STATUS_FAILED,
                                   "reason": "存在未通过/被跳过/身份认证失败的必需检查"})
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
            report.update({"status": STATUS_SUCCESS,
                           "reason": "全部必需检查在该 head SHA 上真实 success，且来源/身份/base 均通过认证"})
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
        required = load_manifest(a.required_checks)
        contexts = load_status_contexts(a.required_checks)
    except ManifestError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1
    poller = Poller(repo=a.repo, pr=a.pr, expected_head=a.expected_head_sha, base_branch=a.base_branch,
                    required=required, token=token, api_base=a.api_base, timeout=a.timeout,
                    interval=a.interval, max_interval=a.max_interval, contexts=contexts)
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
