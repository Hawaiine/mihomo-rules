#!/usr/bin/env python3
"""publish_status.py — Route A：在**精确 head SHA** 上发布 commit status（唯一 GITHUB_TOKEN）。

为什么不是 check-run：`GITHUB_TOKEN` 创建的 PR 不会触发 `pull_request` workflow，
而 `workflow_dispatch` 产生的 check-run 不进入 PR checks。Route A 因此用
`POST /statuses/{sha}`（`statuses: write`）发布受信 context，由 Ruleset 引用该 context。

安全性质：
* 发布前必须确认：PR 仍 open、base 分支/基线未变、**PR head == --head-sha**；
  任一不符 → 拒绝发布（不把旧 SHA 的结论套到新提交上）。
* 只发布自己校验过的 SHA；不按分支名判定。
* 发布后回读，确认该 SHA 上存在本 context 且 state 一致。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

CONTEXT = "route-a/verify"
LEGAL_STATES = {"success", "failure", "pending", "error"}


class PublishRefused(RuntimeError):
    pass


class Client:
    def __init__(self, repo: str, token: str, api: str = "https://api.github.com"):
        if not token:
            raise PublishRefused("缺少 GITHUB_TOKEN：拒绝发布状态")
        self.repo, self.token, self.api = repo, token, api.rstrip("/")

    def call(self, method: str, path: str, body: dict | None = None) -> tuple[int, object]:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(
            f"{self.api}{path}", data=data, method=method,
            headers={"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json",
                     "User-Agent": "route-a-publish-status"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                return exc.code, (json.loads(raw) if raw else None)
            except json.JSONDecodeError:
                return exc.code, raw.decode("utf-8", "replace")
        except (urllib.error.URLError, TimeoutError) as exc:
            raise PublishRefused(f"GitHub API 不可用: {exc}") from exc


def preflight(client: Client, pr: int, head_sha: str, base_branch: str) -> dict:
    status, pr_doc = client.call("GET", f"/repos/{client.repo}/pulls/{pr}")
    if status != 200 or not isinstance(pr_doc, dict):
        raise PublishRefused(f"读取 PR #{pr} 失败: HTTP {status}")
    if pr_doc.get("state") != "open":
        raise PublishRefused(f"PR #{pr} 不是 open（state={pr_doc.get('state')}）")
    if (pr_doc.get("base") or {}).get("ref") != base_branch:
        raise PublishRefused(f"PR base 分支不是 {base_branch}")
    if (pr_doc.get("head") or {}).get("sha") != head_sha:
        raise PublishRefused("PR head 已漂移：拒绝把状态发布到另一个提交")
    status, ref = client.call("GET", f"/repos/{client.repo}/git/ref/heads/{base_branch}")
    if status != 200:
        raise PublishRefused(f"读取 {base_branch} 失败: HTTP {status}")
    base_sha = ref["object"]["sha"]
    if (pr_doc.get("base") or {}).get("sha") != base_sha:
        raise PublishRefused("PR base SHA 与当前基线不一致（需要 rebase）")
    return {"pr": pr, "head_sha": head_sha, "base_sha": base_sha}


def publish(client: Client, head_sha: str, state: str, description: str,
            target_url: str = "") -> dict:
    if state not in LEGAL_STATES:
        raise PublishRefused(f"非法 state: {state}")
    body = {"state": state, "context": CONTEXT, "description": description[:140]}
    if target_url:
        body["target_url"] = target_url
    status, payload = client.call("POST", f"/repos/{client.repo}/statuses/{head_sha}", body)
    if status not in (200, 201):
        raise PublishRefused(f"发布状态失败: HTTP {status} {payload}")
    return {"posted": True, "context": CONTEXT, "state": state, "sha": head_sha}


def verify_published(client: Client, head_sha: str, expected: str, *, attempts: int = 5,
                     delay: float = 1.0) -> dict:
    last = None
    for i in range(attempts):
        status, payload = client.call("GET", f"/repos/{client.repo}/commits/{head_sha}/statuses?per_page=100")
        if status == 200 and isinstance(payload, list):
            ours = [s for s in payload if s.get("context") == CONTEXT]
            if ours and ours[0].get("state") == expected:
                return {"verified": True, "state": ours[0]["state"], "context": CONTEXT,
                        "target_url": ours[0].get("target_url", "")}
            last = ours[0].get("state") if ours else None
        time.sleep(delay * (i + 1))
    raise PublishRefused(f"发布后回读未看到 {CONTEXT}={expected}（当前={last}）")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Route A commit status 发布（head SHA 绑定）")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--pr", type=int, required=True)
    ap.add_argument("--head-sha", required=True)
    ap.add_argument("--base-branch", default="main")
    ap.add_argument("--state", required=True, choices=sorted(LEGAL_STATES))
    ap.add_argument("--description", default="")
    ap.add_argument("--target-url", default="")
    ap.add_argument("--json", type=lambda p: open(p, "w", encoding="utf-8"))
    args = ap.parse_args(argv)

    token = os.environ.get("GITHUB_TOKEN", "")
    try:
        client = Client(args.repo, token)
        pre = preflight(client, args.pr, args.head_sha, args.base_branch)
        posted = publish(client, args.head_sha, args.state, args.description, args.target_url)
        verified = verify_published(client, args.head_sha, args.state)
        report = {"preflight": pre, **posted, "verify": verified}
    except PublishRefused as exc:
        report = {"ok": False, "reason": str(exc)}
        print(f"❌ {exc}")
        if args.json:
            json.dump(report, args.json, ensure_ascii=False, indent=1)
        return 1
    report["ok"] = True
    if args.json:
        json.dump(report, args.json, ensure_ascii=False, indent=1)
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
