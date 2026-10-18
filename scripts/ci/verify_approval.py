#!/usr/bin/env python3
"""verify_approval.py — 人工审批校验（可信代码；只读 GitHub review API）。

规则（全部满足才算"当前 head 已获有效人工批准"）：

* 审批者是**已核实的真实人类**：`user.login` 精确等于 `--approver` 且 `user.type == "User"`；
* 取该用户**最新**一条 review（按 `submitted_at`，同时间取列表中后出现者）；
* 该条状态必须是 `APPROVED`；
* 该条的 `commit_id` 必须**精确等于当前 PR head SHA**（缺失即失败）；
* 后续若有 `DISMISSED` / `CHANGES_REQUESTED`，因"取最新"自然失效；
* review 列表必须是**完整分页**结果，否则视为无法验证。

不信任 PR body、label、commit message、分支名或 workflow 输出。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

ALLOWED_STATE = "APPROVED"
INVALIDATING_STATES = {"DISMISSED", "CHANGES_REQUESTED"}


class ApprovalError(RuntimeError):
    pass


def latest_by_user(reviews: list[dict]) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for review in reviews:
        login = ((review.get("user") or {}).get("login") or "")
        if not login:
            continue
        prev = latest.get(login)
        if prev is None or str(review.get("submitted_at") or "") >= str(prev.get("submitted_at") or ""):
            latest[login] = review
    return latest


def verify_approval(reviews: list[dict], *, head_sha: str, approver: str,
                    complete: bool = True) -> tuple[bool, list[str], dict]:
    problems: list[str] = []
    evidence: dict = {"head_sha": head_sha, "approver": approver, "reviews": len(reviews or [])}
    if not complete:
        return False, ["REVIEWS_PAGINATION_INCOMPLETE"], evidence
    if not isinstance(reviews, list):
        return False, ["REVIEWS_MALFORMED"], evidence
    if not approver:
        return False, ["APPROVER_NOT_CONFIGURED"], evidence

    latest = latest_by_user(reviews)
    review = latest.get(approver)
    if review is None:
        return False, ["APPROVAL_MISSING"], evidence

    state = review.get("state")
    commit_id = review.get("commit_id")
    utype = ((review.get("user") or {}).get("type"))
    evidence.update({"state": state, "commit_id": commit_id, "user_type": utype,
                     "submitted_at": review.get("submitted_at")})
    if utype != "User":
        problems.append("APPROVER_NOT_HUMAN")
    if state in INVALIDATING_STATES:
        problems.append("APPROVAL_INVALIDATED")
    elif state != ALLOWED_STATE:
        problems.append("APPROVAL_NOT_APPROVED")
    if not isinstance(commit_id, str) or len(commit_id) != 40:
        problems.append("APPROVAL_MISSING_COMMIT")
    elif commit_id != head_sha:
        problems.append("APPROVAL_STALE")
    return not problems, problems, evidence


def collect_reviews(getter, *, repo: str, pr: int, token: str,
                    api_base: str = "https://api.github.com", per_page: int = 100,
                    max_pages: int = 20) -> tuple[list[dict], bool]:
    """读取全部 review；返回 (reviews, complete)。分页未读完 ⇒ complete=False。"""
    out: list[dict] = []
    for page in range(1, max_pages + 1):
        status, body = getter(f"{api_base}/repos/{repo}/pulls/{pr}/reviews"
                              f"?per_page={per_page}&page={page}", token)
        if status != 200 or not isinstance(body, list):
            return out, False
        out.extend(body)
        if len(body) < per_page:
            return out, True
    return out, False


def _default_getter(url: str, token: str) -> tuple[int, object]:
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "mihomo-approval-check"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")[:200]
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ApprovalError(f"GitHub API 不可用: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="校验当前 PR head 的人工批准")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--pr", type=int, required=True)
    ap.add_argument("--head-sha", required=True)
    ap.add_argument("--approver", required=True)
    ap.add_argument("--reviews-json", help="离线模式：直接给 reviews 数组文件")
    ap.add_argument("--json", type=lambda p: open(p, "w", encoding="utf-8"))
    args = ap.parse_args(argv)

    try:
        if args.reviews_json:
            reviews = json.loads(open(args.reviews_json, encoding="utf-8").read())
            complete = True
        else:
            reviews, complete = collect_reviews(
                _default_getter, repo=args.repo, pr=args.pr,
                token=os.environ.get("GITHUB_TOKEN", ""),
                api_base=os.environ.get("GITHUB_API_URL", "https://api.github.com"))
        ok, problems, evidence = verify_approval(reviews, head_sha=args.head_sha,
                                                 approver=args.approver, complete=complete)
    except (OSError, json.JSONDecodeError, ApprovalError) as exc:
        ok, problems, evidence = False, [f"ERROR:{exc}"], {}
    report = {"ok": ok, "problems": problems, "evidence": evidence}
    print(json.dumps(report, ensure_ascii=False, indent=1))
    if args.json:
        json.dump(report, args.json, ensure_ascii=False, indent=1)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
