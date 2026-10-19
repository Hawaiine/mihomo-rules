#!/usr/bin/env python3
"""verify_approval.py — 人工审批校验（可信代码；只读 GitHub review API）。

授权只由 ``verify_approvals()`` 按策略中的不可变 GitHub user ID 判定；login 只用于展示。
最新 review 必须来自 ``type == "User"``、状态 ``APPROVED``，且 ``commit_id`` 精确等于
当前 PR head SHA；不完整分页、缺失身份字段或不合法记录均 fail-closed。
不信任 PR body、label、commit message、分支名、login 输入或 workflow 输出。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

ALLOWED_STATE = "APPROVED"


class ApprovalError(RuntimeError):
    pass


def latest_by_id(reviews: list[dict]) -> dict[int, dict]:
    latest: dict[int, dict] = {}
    for review in reviews:
        raw_id = ((review.get("user") or {}).get("id"))
        if isinstance(raw_id, bool) or not isinstance(raw_id, int) or raw_id < 1:
            continue
        prev = latest.get(raw_id)
        if prev is None or str(review.get("submitted_at") or "") >= str(prev.get("submitted_at") or ""):
            latest[raw_id] = review
    return latest


def verify_approvals(reviews: list[dict], *, head_sha: str, approvers=None,
                     approver_ids=None, required: int = 1,
                     complete: bool = True, allow_bots: bool = False) -> tuple[bool, list[str], dict]:
    """多审批人版本：按显式策略名单核验，并阻断任何"请求变更"。

    与 `verify_approval` 的差异：
    * 需要 `required` 个**不同的受信 user ID** 各自最新 review 均 APPROVED 且 `commit_id == head`；
    * **任何人**在当前 head 上的最新 review 为 CHANGES_REQUESTED 都阻断（不能靠换人绕过）；
    * 审批人账号类型默认必须是 `User`（`allow_bots` 仅用于策略显式允许的离线测试）。
    """
    problems: list[str] = []
    evidence: dict = {"head_sha": head_sha, "approver_ids": list(approver_ids or []),
                      "required": required, "reviews": len(reviews or []),
                      "valid_approver_ids": []}
    if not complete:
        return False, ["REVIEWS_PAGINATION_INCOMPLETE"], evidence
    if not isinstance(reviews, list):
        return False, ["REVIEWS_MALFORMED"], evidence
    if approver_ids is None:
        return False, ["APPROVER_ID_POLICY_REQUIRED"], evidence
    id_list = [x for x in approver_ids if isinstance(x, int) and not isinstance(x, bool) and x > 0]
    if len(id_list) != len(list(approver_ids)) or not id_list:
        return False, ["APPROVER_ID_POLICY_INVALID"], evidence
    if not isinstance(required, int) or isinstance(required, bool) or required < 1 \
            or required > len(id_list):
        return False, ["APPROVAL_REQUIREMENT_INVALID"], evidence
    evidence["approver_ids"] = id_list
    latest = latest_by_id(reviews)
    for user_id, review in latest.items():
        if review.get("state") == "CHANGES_REQUESTED" and review.get("commit_id") == head_sha:
            problems.append(f"CHANGES_REQUESTED_PRESENT:{user_id}")
    valid: list[int] = []
    for user_id in id_list:
        review = latest.get(user_id)
        if review is None:
            problems.append(f"APPROVAL_MISSING_ID:{user_id}")
            continue
        utype = ((review.get("user") or {}).get("type"))
        if utype != "User" and not allow_bots:
            problems.append(f"APPROVER_NOT_HUMAN_ID:{user_id}")
            continue
        if review.get("state") != ALLOWED_STATE:
            problems.append(f"APPROVAL_NOT_CURRENT_ID:{user_id}:{review.get('state')}")
            continue
        commit_id = review.get("commit_id")
        if not isinstance(commit_id, str) or len(commit_id) != 40:
            problems.append(f"APPROVAL_MISSING_COMMIT_ID:{user_id}")
            continue
        if commit_id != head_sha:
            problems.append(f"APPROVAL_STALE_ID:{user_id}")
            continue
        valid.append(user_id)
    evidence["valid_approver_ids"] = valid
    if len(valid) < required:
        problems.append(f"APPROVALS_INSUFFICIENT:{len(valid)}/{required}")
    evidence["ok"] = not problems
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
    ap.add_argument("--policy", default=".github/approval-policy.json",
                    help="可信审批策略；缺失或不一致时 fail-closed")
    ap.add_argument("--json", type=lambda p: open(p, "w", encoding="utf-8"))
    args = ap.parse_args(argv)

    try:
        import approval_policy as apol
        policy_result = apol.load_policy(args.policy)
        if not policy_result.ok or policy_result.policy is None:
            ok, problems, evidence = False, list(policy_result.reason_codes), {
                "policy": policy_result.to_dict(), "head_sha": args.head_sha}
        else:
            reviews, complete = collect_reviews(
                _default_getter, repo=args.repo, pr=args.pr,
                token=os.environ.get("GITHUB_TOKEN", ""),
                api_base=os.environ.get("GITHUB_API_URL", "https://api.github.com"))
            ok, problems, evidence = verify_approvals(
                reviews, head_sha=args.head_sha,
                approver_ids=policy_result.policy.approver_ids,
                required=policy_result.policy.required_approvals,
                complete=complete)
            evidence["policy"] = policy_result.to_dict()
    except (OSError, json.JSONDecodeError, ApprovalError) as exc:
        ok, problems, evidence = False, [f"ERROR:{exc}"], {}
    report = {"ok": ok, "problems": problems, "evidence": evidence}
    print(json.dumps(report, ensure_ascii=False, indent=1))
    if args.json:
        json.dump(report, args.json, ensure_ascii=False, indent=1)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
