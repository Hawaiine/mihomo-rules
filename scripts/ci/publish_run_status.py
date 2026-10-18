#!/usr/bin/env python3
"""publish_run_status.py — P0-1 受信状态发布器（人工 PR 路径）。

运行在**默认分支**上下文（`workflow_run` 指向的 workflow 由 main 版本定义），因此执行
逻辑可信；候选内容只作为 API 数据被检查，绝不检出、绝不解包、绝不执行。

发布前逐项核实（任一不符即拒绝，不发布成功状态）：

1. 该 run 属于本仓库、事件为 `pull_request`、名称匹配、状态 completed+success；
2. 该 run 的全部 job 名称覆盖必需检查集合且全部 success（skipped 视为不通过）；
3. PR 仍 open、base 为 `main`、base SHA 等于当前 `refs/heads/main`、head SHA 等于目标 SHA；
4. 发布后回读状态与 PR head；head 已变化则整轮判失败。

exit: 0 成功；1 拒绝/失败（原因见 stderr 与 --json）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

CONTEXT = "route-a/verify"
EXPECTED_RUN_NAME = "General PR verification"
EXPECTED_RUN_PATH = ".github/workflows/pr-verify.yml"
TRUSTED_BOT = "github-actions[bot]"


class Refused(RuntimeError):
    pass


class Client:
    def __init__(self, repo: str, token: str, api: str = "https://api.github.com"):
        if not token:
            raise Refused("缺少 GITHUB_TOKEN")
        self.repo, self.token, self.api = repo, token, api.rstrip("/")

    def call(self, path: str) -> tuple[int, object]:
        req = urllib.request.Request(
            f"{self.api}{path}",
            headers={"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "route-a-run-status"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace")[:200]
        except (urllib.error.URLError, TimeoutError) as exc:
            raise Refused(f"GitHub API 不可用: {exc}") from exc

    def post(self, path: str, body: dict) -> tuple[int, object]:
        req = urllib.request.Request(
            f"{self.api}{path}", data=json.dumps(body).encode(), method="POST",
            headers={"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json",
                     "User-Agent": "route-a-run-status"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace")[:200]
        except (urllib.error.URLError, TimeoutError) as exc:
            raise Refused(f"状态发布请求失败: {exc}") from exc


def verify_run(client: Client, run_id: str, head_sha: str, required: set[str],
               pr: int | None = None) -> dict:
    status, run = client.call(f"/repos/{client.repo}/actions/runs/{run_id}")
    if status != 200 or not isinstance(run, dict):
        raise Refused(f"读取 workflow run 失败: HTTP {status}")
    if (run.get("repository") or {}).get("full_name") != client.repo:
        raise Refused("run 不属于本仓库")
    if (run.get("head_repository") or {}).get("full_name") != client.repo:
        raise Refused("run 的 head_repository 不属于本仓库（可能来自 fork）")
    if run.get("path") != EXPECTED_RUN_PATH:
        raise Refused(f"run path 为 {run.get('path')!r}，非 {EXPECTED_RUN_PATH!r}"
                      f"（同名 workflow 不能冒充）")
    prs = run.get("pull_requests")
    if isinstance(prs, list) and prs:
        numbers = {p.get("number") for p in prs if isinstance(p, dict)}
        if pr is not None and pr not in numbers:
            raise Refused(f"run 关联的 PR {sorted(numbers)} 不包含目标 PR #{pr}")
    if run.get("event") != "pull_request":
        raise Refused(f"run 事件为 {run.get('event')!r}，非 pull_request（拒绝采信）")
    if run.get("name") != EXPECTED_RUN_NAME:
        raise Refused(f"run 名称为 {run.get('name')!r}，非 {EXPECTED_RUN_NAME!r}")
    if run.get("head_sha") != head_sha:
        raise Refused("run head_sha 与目标 SHA 不一致")
    if run.get("status") != "completed" or run.get("conclusion") != "success":
        raise Refused(f"run 状态为 {run.get('status')}/{run.get('conclusion')}，非 completed/success")
    status, jobs = client.call(f"/repos/{client.repo}/actions/runs/{run_id}/jobs?per_page=100")
    if status != 200 or not isinstance(jobs, dict):
        raise Refused(f"读取 run jobs 失败: HTTP {status}")
    names, bad = set(), []
    for job in jobs.get("jobs") or []:
        name, concl = job.get("name"), job.get("conclusion")
        names.add(name)
        if concl != "success":
            bad.append(f"{name}={concl}")
    missing = required - names
    if missing:
        raise Refused(f"该 run 未包含必需检查 job：{sorted(missing)}")
    if bad:
        raise Refused(f"存在未成功/被跳过的 job：{bad}")
    return {"run_id": run_id, "head_sha": head_sha, "jobs": sorted(names)}


def resolve_pr(client: Client, head_sha: str, base_branch: str = "main") -> int:
    """由 head SHA 反查 PR（workflow_run 事件不携带 PR 编号）。必须唯一命中。"""
    status, items = client.call(f"/repos/{client.repo}/commits/{head_sha}/pulls")
    if status != 200 or not isinstance(items, list):
        raise Refused(f"反查 PR 失败: HTTP {status}")
    cands = [p for p in items if p.get("state") == "open"
             and (p.get("base") or {}).get("ref") == base_branch]
    if len(cands) != 1:
        raise Refused(f"head {head_sha[:12]} 对应 {len(cands)} 个 open PR（要求恰好 1 个）")
    return int(cands[0]["number"])


def verify_pr(client: Client, pr: int, head_sha: str, base_branch: str = "main") -> dict:
    status, pr_obj = client.call(f"/repos/{client.repo}/pulls/{pr}")
    if status != 200 or not isinstance(pr_obj, dict):
        raise Refused(f"读取 PR 失败: HTTP {status}")
    if pr_obj.get("state") != "open":
        raise Refused(f"PR 状态为 {pr_obj.get('state')!r}，非 open")
    if (pr_obj.get("base") or {}).get("ref") != base_branch:
        raise Refused("PR base 分支不是 main")
    if (pr_obj.get("head") or {}).get("sha") != head_sha:
        raise Refused("PR head SHA 与目标 SHA 不一致（head 漂移）")
    status, ref = client.call(f"/repos/{client.repo}/git/ref/heads/{base_branch}")
    if status != 200 or not isinstance(ref, dict):
        raise Refused(f"读取 {base_branch} 失败: HTTP {status}")
    base_sha = (ref.get("object") or {}).get("sha")
    if (pr_obj.get("base") or {}).get("sha") != base_sha:
        raise Refused("PR base SHA 与当前基线不一致（需要 rebase）")
    return {"pr": pr, "base_sha": base_sha, "author": (pr_obj.get("user") or {}).get("login")}


def publish(client: Client, sha: str, run_id: str, description: str, target_url: str) -> dict:
    status, body = client.post(f"/repos/{client.repo}/statuses/{sha}", {
        "state": "success", "context": CONTEXT, "description": description[:140],
        "target_url": target_url})
    if status not in (200, 201):
        raise Refused(f"发布状态失败: HTTP {status} {body}")
    return {"posted": True, "sha": sha, "context": CONTEXT}


def verify_published(client: Client, sha: str, *, attempts: int = 5, delay: float = 1.0) -> dict:
    last = None
    for i in range(attempts):
        status, items = client.call(f"/repos/{client.repo}/commits/{sha}/statuses?per_page=100")
        if status == 200 and isinstance(items, list):
            ours = [s for s in items if s.get("context") == CONTEXT]
            if ours:
                last = ours[0]
                creator = ((last.get("creator") or {}).get("login") or "")
                if last.get("state") == "success" and creator == TRUSTED_BOT:
                    return {"verified": True, "creator": creator, "state": last["state"]}
        time.sleep(delay * (i + 1))
    raise Refused(f"发布后回读未看到可信 {CONTEXT}=success（当前 {last!r}）")


def publish_for_run(*, repo: str, run_id: str, pr: int, head_sha: str, required: set[str],
                    token: str, api: str = "https://api.github.com", target_url: str = "",
                    attempts: int = 5) -> dict:
    client = Client(repo, token, api)
    if not pr:
        pr = resolve_pr(client, head_sha)
    run_info = verify_run(client, run_id, head_sha, required, pr=pr)
    pr_info = verify_pr(client, pr, head_sha)
    posted = publish(client, head_sha, run_id, "受信校验通过（run 级核验）", target_url)
    verified = verify_published(client, head_sha, attempts=attempts)
    after = verify_pr(client, pr, head_sha)          # 发布后再次确认未漂移
    return {"ok": True, "run": run_info, "pr": pr_info, **posted, "verify": verified, "after": after}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="P0-1 受信状态发布（人工 PR）")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--pr", type=int, required=True)
    ap.add_argument("--head-sha", required=True)
    ap.add_argument("--required-checks", default=".github/required-checks.json")
    ap.add_argument("--target-url", default="")
    ap.add_argument("--json", type=lambda p: open(p, "w", encoding="utf-8"))
    args = ap.parse_args(argv)
    try:
        doc = json.loads(Path(args.required_checks).read_text(encoding="utf-8"))
        required = {c["name"] for c in doc.get("checks") or [] if isinstance(c, dict) and c.get("name")}
    except (OSError, json.JSONDecodeError) as exc:
        print(f"❌ 读取必需检查清单失败：{exc}", file=sys.stderr)
        return 1
    try:
        report = publish_for_run(repo=args.repo, run_id=args.run_id, pr=args.pr, head_sha=args.head_sha,
                                 required=required, token=os.environ.get("GITHUB_TOKEN", ""),
                                 api=os.environ.get("GITHUB_API_URL", "https://api.github.com"),
                                 target_url=args.target_url)
    except Refused as exc:
        print(f"❌ 拒绝发布：{exc}", file=sys.stderr)
        if args.json:
            json.dump({"ok": False, "reason": str(exc)}, args.json, ensure_ascii=False, indent=1)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=1))
    if args.json:
        json.dump(report, args.json, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
