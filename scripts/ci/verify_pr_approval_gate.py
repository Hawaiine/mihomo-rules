#!/usr/bin/env python3
"""verify_pr_approval_gate.py — PR 作者 × 审批人的合并前闸门（只读，fail-closed）。

为什么需要它：GitHub 原生禁止 PR 作者批准自己的 PR，因此「谁能审谁」不是平台保证，
而是**作者身份的函数**。本模块把这条语义显式化，避免把「有 APPROVED review」误当成
「审批有效」：

* ``TRUSTED_AUTHOR``  —— PR 作者本身就是策略里的受信审批人：
  该 PR 不可能拿到有效人工审批（自审被平台禁止，闸门也不接受），必须由机器人身份
  重新创建 PR，或由第二位受信审批人批准；
* ``BOT_AUTHOR``      —— 作者是机器人（不使用策略审批权）：
  必须由受信人类审批，且审批精确绑定当前 head；
* ``OTHER_HUMAN_AUTHOR`` —— 第三方人类作者：由受信审批人批准即可。

同时校验 PR 快照仍在预期状态（open、未合并、head/base 精确等于预期）。任何读取失败、
字段缺失、类型不合法都返回 ``ok=False`` 与稳定 reason code，不抛异常、不猜测。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

import verify_approval as va

SHA_RE = re.compile(r"^[0-9a-f]{40}$")

TRUSTED_AUTHOR = "TRUSTED_AUTHOR"
BOT_AUTHOR = "BOT_AUTHOR"
OTHER_HUMAN_AUTHOR = "OTHER_HUMAN_AUTHOR"
UNKNOWN_AUTHOR = "UNKNOWN_AUTHOR"


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def _sha(value: object) -> str | None:
    return value if isinstance(value, str) and SHA_RE.match(value) else None


def classify_author(pr: object, approver_ids) -> tuple[str, list[str], dict]:
    """按不可变 user.id 与账号类型判定作者类别；读不到身份即 UNKNOWN（下游 fail-closed）。"""
    problems: list[str] = []
    evidence: dict = {"author_id": None, "author_login": None, "author_type": None}
    if not isinstance(pr, dict):
        return UNKNOWN_AUTHOR, ["PR_OBJECT_MALFORMED"], evidence
    user = pr.get("user")
    if not isinstance(user, dict):
        return UNKNOWN_AUTHOR, ["PR_AUTHOR_UNREADABLE"], evidence
    author_id = _positive_int(user.get("id"))
    author_type = user.get("type")
    login = user.get("login")
    evidence.update({"author_id": author_id,
                     "author_login": login if isinstance(login, str) else None,
                     "author_type": author_type if isinstance(author_type, str) else None})
    if author_id is None:
        return UNKNOWN_AUTHOR, ["PR_AUTHOR_UNREADABLE"], evidence
    ids = {x for x in (approver_ids or []) if isinstance(x, int) and not isinstance(x, bool)}
    if author_id in ids:
        # 作者即受信审批人：结构上无法获得有效审批
        problems.append("SELF_APPROVAL_BY_TRUSTED_AUTHOR")
        return TRUSTED_AUTHOR, problems, evidence
    if author_type == "Bot":
        return BOT_AUTHOR, problems, evidence
    if author_type == "User":
        return OTHER_HUMAN_AUTHOR, problems, evidence
    return UNKNOWN_AUTHOR, ["AUTHOR_TYPE_UNKNOWN"], evidence


def _full_name(obj: object) -> str | None:
    if not isinstance(obj, dict):
        return None
    name = obj.get("full_name")
    return name if isinstance(name, str) and "/" in name else None


def verify_pr_snapshot(pr: object, *, expected_head_sha: object,
                       expected_base_sha: object, expected_pr: object = None,
                       expected_repo: object = None,
                       expected_base_ref: str = "main") -> tuple[bool, list[str], dict]:
    """PR 必须仍 open、未合并，且**编号/仓库/base 分支/head/base SHA** 与预期精确一致。

    只比 SHA 不够：同一 head SHA 可以出现在另一个 PR 编号、另一个仓库（fork）或另一条
    base 分支上，把别处的审批证据搬过来就会变成假阳性。因此身份五个维度都要绑定。
    """
    problems: list[str] = []
    evidence: dict = {"state": None, "merged": None, "number": None, "repo": None,
                      "base_ref": None, "head_repo": None,
                      "head_sha": None, "base_sha": None}
    if not isinstance(pr, dict):
        return False, ["PR_OBJECT_MALFORMED"], evidence
    state = pr.get("state")
    evidence["state"] = state
    if state != "open":
        problems.append(f"PR_NOT_OPEN:{state!r}")
    merged = pr.get("merged")
    evidence["merged"] = merged
    if merged is True or pr.get("merged_at") is not None:
        problems.append("PR_ALREADY_MERGED")

    # —— 编号绑定 ——
    number = pr.get("number")
    evidence["number"] = number
    expected_number = expected_pr
    if expected_number is not None:
        want = expected_number if isinstance(expected_number, int) and \
            not isinstance(expected_number, bool) and expected_number > 0 else None
        if want is None:
            problems.append("EXPECTED_PR_INVALID")
        elif isinstance(number, bool) or not isinstance(number, int):
            problems.append("PR_NUMBER_UNREADABLE")
        elif number != want:
            problems.append("PR_NUMBER_MISMATCH")

    # —— 仓库 + base 分支绑定 ——
    base = pr.get("base") if isinstance(pr.get("base"), dict) else {}
    repo_name = _full_name(base.get("repo"))
    evidence["repo"] = repo_name
    evidence["head_repo"] = _full_name((pr.get("head") or {}).get("repo")
                                       if isinstance(pr.get("head"), dict) else None)
    if expected_repo is not None:
        want_repo = expected_repo if isinstance(expected_repo, str) and "/" in expected_repo else None
        if want_repo is None:
            problems.append("EXPECTED_REPO_INVALID")
        elif repo_name is None:
            problems.append("PR_REPOSITORY_UNREADABLE")
        elif repo_name.casefold() != want_repo.casefold():
            problems.append("PR_REPOSITORY_MISMATCH")

    base_ref = base.get("ref")
    evidence["base_ref"] = base_ref
    if expected_base_ref:
        if not isinstance(base_ref, str) or not base_ref:
            problems.append("PR_BASE_REF_UNREADABLE")
        elif base_ref != expected_base_ref:
            problems.append("PR_BASE_REF_MISMATCH")

    want_head = _sha(expected_head_sha)
    if want_head is None:
        problems.append("EXPECTED_HEAD_SHA_MALFORMED")
    real_head = _sha((pr.get("head") or {}).get("sha") if isinstance(pr.get("head"), dict) else None)
    evidence["head_sha"] = real_head
    if real_head is None:
        problems.append("PR_HEAD_MALFORMED")
    elif want_head is not None and real_head != want_head:
        problems.append("PR_HEAD_MOVED")

    want_base = _sha(expected_base_sha)
    if want_base is None:
        problems.append("EXPECTED_BASE_SHA_MALFORMED")
    real_base = _sha((pr.get("base") or {}).get("sha") if isinstance(pr.get("base"), dict) else None)
    evidence["base_sha"] = real_base
    if real_base is None:
        problems.append("PR_BASE_MALFORMED")
    elif want_base is not None and real_base != want_base:
        problems.append("PR_BASE_MOVED")

    return not problems, problems, evidence


def evaluate(pr: object, reviews: object, *, expected_head_sha: object,
             expected_base_sha: object, policy, review_complete: bool = True,
             expected_pr: object = None, expected_repo: object = None,
             expected_base_ref: str = "main") -> dict:
    """组合判定：PR 身份绑定 + 快照 + 作者类别 + 审批有效性。任一不满足即 ok=False。"""
    problems: list[str] = []
    evidence: dict = {"expected_head_sha": expected_head_sha,
                      "expected_base_sha": expected_base_sha,
                      "expected_pr": expected_pr,
                      "expected_repo": expected_repo,
                      "expected_base_ref": expected_base_ref}

    snapshot_ok, snapshot_problems, snapshot_ev = verify_pr_snapshot(
        pr, expected_head_sha=expected_head_sha, expected_base_sha=expected_base_sha,
        expected_pr=expected_pr, expected_repo=expected_repo,
        expected_base_ref=expected_base_ref)
    problems.extend(snapshot_problems)
    evidence["snapshot"] = snapshot_ev

    author_class, author_problems, author_ev = classify_author(
        pr, getattr(policy, "approver_ids", ()) if policy is not None else ())
    problems.extend(author_problems)
    evidence["author_class"] = author_class
    evidence["author"] = author_ev
    evidence["pr_number"] = pr.get("number") if isinstance(pr, dict) else None

    approval_ok = False
    if policy is None:
        problems.append("APPROVAL_POLICY_UNAVAILABLE")
    else:
        approval_ok, approval_problems, approval_ev = va.verify_approvals(
            reviews if isinstance(reviews, list) else [],
            head_sha=expected_head_sha if isinstance(expected_head_sha, str) else "",
            approver_ids=policy.approver_ids,
            required=policy.required_approvals,
            complete=review_complete,
            allow_bots=policy.allow_bot_approvers,
            pr_author_id=author_ev.get("author_id"))
        problems.extend(approval_problems)
        evidence["approval"] = approval_ev

    ok = snapshot_ok and approval_ok and not problems
    return {"ok": ok,
            "verdict": "APPROVAL_GATE_PASS" if ok else "APPROVAL_GATE_FAIL",
            "problems": problems,
            "evidence": evidence}


# ───────────────────────────── CLI ─────────────────────────────

def _get(url: str, token: str) -> tuple[int, object]:
    return va._default_getter(url, token)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="PR 作者 × 审批人闸门（只读，fail-closed）")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--pr", type=int, required=True)
    ap.add_argument("--head-sha", required=True)
    ap.add_argument("--base-sha", required=True)
    ap.add_argument("--base-branch", default="main",
                    help="PR 必须针对该 base 分支（默认 main）")
    ap.add_argument("--policy", default=".github/approval-policy.json")
    ap.add_argument("--json", type=lambda p: open(p, "w", encoding="utf-8"))
    args = ap.parse_args(argv)

    token = os.environ.get("GITHUB_TOKEN", "")
    api = os.environ.get("GITHUB_API_URL", "https://api.github.com")
    try:
        import approval_policy as apol
        policy_result = apol.load_policy(args.policy)
        if not policy_result.ok or policy_result.policy is None:
            report = {"ok": False, "verdict": "APPROVAL_GATE_FAIL",
                      "problems": list(policy_result.reason_codes),
                      "evidence": {"policy": policy_result.to_dict()}}
        else:
            status, pr = _get(f"{api}/repos/{args.repo}/pulls/{args.pr}", token)
            reviews, complete = va.collect_reviews(va._default_getter, repo=args.repo,
                                                   pr=args.pr, token=token, api_base=api)
            if status != 200:
                report = {"ok": False, "verdict": "APPROVAL_GATE_FAIL",
                          "problems": [f"PR_READ_FAILED:HTTP{status}"],
                          "evidence": {"policy": policy_result.to_dict()}}
            else:
                report = evaluate(pr, reviews, expected_head_sha=args.head_sha,
                                  expected_base_sha=args.base_sha,
                                  policy=policy_result.policy, review_complete=complete,
                                  expected_pr=args.pr, expected_repo=args.repo,
                                  expected_base_ref=args.base_branch)
                report["evidence"]["policy"] = policy_result.to_dict()
    except (OSError, json.JSONDecodeError, va.ApprovalError) as exc:
        report = {"ok": False, "verdict": "APPROVAL_GATE_FAIL",
                  "problems": [f"ERROR:{exc}"], "evidence": {}}
    print(json.dumps(report, ensure_ascii=False, indent=1))
    if args.json:
        json.dump(report, args.json, ensure_ascii=False, indent=1)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
