#!/usr/bin/env python3
"""robot_pr.py — 用内置 GITHUB_TOKEN 创建/更新**机器人作者**的 PR（只读校验 + 幂等写入）。

为什么需要它：GitHub 不允许 PR 作者批准自己的 PR。本仓库唯一的可信审批人是
``Hawaiine``（user.id 152380613），因此由他创建的 PR 在结构上永远拿不到有效审批。
唯一合规解法是让 PR 的 **author** 变成机器人（``github-actions[bot]``, app 15368），
再由 ``Hawaiine`` 正式 ``APPROVED``。

身份三个字段必须分开看，不能混用：

* commit author —— git 提交里的名字/邮箱，与权限无关，**不能**用来解决作者冲突；
* PR author    —— 真正调用 ``POST /pulls`` 的身份，这才决定「谁能审」；
* review actor —— 提交正式 review 的用户，必须是可信审批人。

安全边界（全部 fail-closed）：

* 分支名、标题、body 都当**数据**处理，不拼进 shell；
* 只对 ``source_branch`` 走 API 读取 ref/compare，**不检出、不执行**该分支的代码；
* 回读校验 PR 的 author / head SHA / head ref / base repo / base ref / state；
* 幂等：同一 head+base 已有 open PR 时只更新标题/body，不重复创建；
* 多个候选 PR 视为歧义，拒绝；
* 机器人身份不符、回读失败、diff 为空 → 失败，绝不返回成功。

用法：
  python3 scripts/ci/robot_pr.py create-or-update --repo O/R --source BR --base main \
      --title "..." --body-file /path/body.md [--json out.json] [--dry-run]
  python3 scripts/ci/robot_pr.py validate --source BR --base main
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

BOT_APP_SLUG = "github-actions"
BOT_USER_ID = 41898282
BOT_LOGIN = "github-actions[bot]"

# GitHub 分支名允许的字符；显式拒绝能构造路径穿越/选项注入的形态。
SAFE_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
MAX_TITLE = 240
MAX_BODY = 60000


class RobotPrError(RuntimeError):
    pass


# ─────────────────────────── 纯校验（可单测） ───────────────────────────

def validate_branch_name(name: object, *, reserved=("main", "master")) -> list[str]:
    """分支名必须是安全形态；任何可疑输入返回 reason code，不抛异常。"""
    problems: list[str] = []
    if not isinstance(name, str) or not name:
        return ["SOURCE_BRANCH_INVALID:empty"]
    if name in reserved:
        return [f"SOURCE_BRANCH_INVALID:reserved:{name}"]
    if not SAFE_BRANCH_RE.match(name):
        return [f"SOURCE_BRANCH_INVALID:charset:{name!r}"]
    if ".." in name or "//" in name:
        return [f"SOURCE_BRANCH_INVALID:traversal:{name!r}"]
    if name.startswith("/") or name.endswith("/") or name.endswith(".lock"):
        return [f"SOURCE_BRANCH_INVALID:shape:{name!r}"]
    if name.startswith("-") or name.startswith("@"):
        return [f"SOURCE_BRANCH_INVALID:option:{name!r}"]
    return problems


def validate_base_ref(name: object, *, allowed=("main",)) -> list[str]:
    if not isinstance(name, str) or name not in allowed:
        return [f"BASE_REF_NOT_ALLOWED:{name!r}"]
    return []


def validate_text(value: object, *, limit: int, field: str) -> list[str]:
    if not isinstance(value, str) or not value.strip():
        return [f"{field}_EMPTY"]
    if len(value) > limit:
        return [f"{field}_TOO_LONG:{len(value)}>{limit}"]
    return []


def classify_author(pr: object) -> tuple[str, list[str]]:
    """返回 (BOT|HUMAN|UNKNOWN, problems)。机器人身份必须同时满足 id 与 type。"""
    if not isinstance(pr, dict):
        return "UNKNOWN", ["PR_OBJECT_MALFORMED"]
    user = pr.get("user")
    if not isinstance(user, dict):
        return "UNKNOWN", ["PR_AUTHOR_UNREADABLE"]
    uid = user.get("id")
    utype = user.get("type")
    login = user.get("login")
    if isinstance(uid, bool) or not isinstance(uid, int) or uid < 1:
        return "UNKNOWN", ["PR_AUTHOR_UNREADABLE"]
    if uid == BOT_USER_ID and utype == "Bot" and login == BOT_LOGIN:
        return "BOT", []
    if utype == "Bot":
        return "HUMAN", [f"PR_AUTHOR_UNEXPECTED_BOT:{login}"]
    return "HUMAN", [f"PR_AUTHOR_NOT_BOT:{login}/{uid}"]


def verify_pr_readback(pr: object, *, repo: str, base_ref: str, head_sha: str,
                       source_branch: str) -> tuple[bool, list[str], dict]:
    """回读 PR 对象，确认作者/head/base 与本次请求完全一致。"""
    problems: list[str] = []
    evidence: dict = {"number": None, "author": None, "author_id": None, "head_sha": None,
                      "head_ref": None, "base_ref": None, "base_repo": None, "state": None}
    if not isinstance(pr, dict):
        return False, ["PR_OBJECT_MALFORMED"], evidence

    evidence["number"] = pr.get("number")
    author_class, author_problems = classify_author(pr)
    problems.extend(author_problems)
    user = pr.get("user") or {}
    evidence["author"] = user.get("login")
    evidence["author_id"] = user.get("id")

    if pr.get("state") != "open":
        problems.append(f"PR_NOT_OPEN:{pr.get('state')!r}")

    head = pr.get("head") if isinstance(pr.get("head"), dict) else {}
    base = pr.get("base") if isinstance(pr.get("base"), dict) else {}
    evidence["head_sha"] = head.get("sha")
    evidence["head_ref"] = head.get("ref")
    evidence["base_ref"] = base.get("ref")
    evidence["base_repo"] = (base.get("repo") or {}).get("full_name") \
        if isinstance(base.get("repo"), dict) else None

    if not isinstance(head.get("sha"), str) or not SHA_RE.match(str(head.get("sha"))):
        problems.append("PR_HEAD_SHA_MALFORMED")
    elif str(head["sha"]) != head_sha:
        problems.append("PR_HEAD_SHA_MISMATCH")
    if head.get("ref") != source_branch:
        problems.append("PR_HEAD_REF_MISMATCH")
    if base.get("ref") != base_ref:
        problems.append("PR_BASE_REF_MISMATCH")
    repo_name = evidence["base_repo"]
    if not isinstance(repo_name, str):
        problems.append("PR_BASE_REPO_UNREADABLE")
    elif repo_name.casefold() != repo.casefold():
        problems.append("PR_BASE_REPO_MISMATCH")

    ok = not problems and author_class == "BOT"
    return ok, problems, evidence


def choose_action(existing: object, *, repo: str, source_branch: str,
                  base_ref: str) -> tuple[str, dict | None, list[str]]:
    """在同 head/base 的候选 PR 中做幂等决策：恰好 1 个→update；0 个→create；>1→歧义。"""
    if not isinstance(existing, list):
        return "fail", None, ["PR_LIST_MALFORMED"]
    ours = [p for p in existing
            if isinstance(p, dict)
            and (p.get("head") or {}).get("ref") == source_branch
            and (p.get("base") or {}).get("ref") == base_ref
            and p.get("state") == "open"]
    if len(ours) > 1:
        return "fail", None, [f"AMBIGUOUS_PR_CANDIDATES:{len(ours)}"]
    if len(ours) == 1:
        return "update", ours[0], []
    return "create", None, []


def classify_run_state(run: object) -> tuple[str, list[str]]:
    """把 PR 相关 run 归入 A–F，避免把「有 run」当成「已运行并成功」。

    A 无 run；B run 存在但等待批准；C 已运行但失败/被跳过；D 运行成功但绑定错误；
    E 只有同运行内 verify；F 独立事件成功。
    """
    if run is None:
        return "A_NO_RUN", []
    if not isinstance(run, dict):
        return "UNKNOWN", ["RUN_OBJECT_MALFORMED"]
    status = run.get("status")
    conclusion = run.get("conclusion")
    if status in ("queued", "waiting", "pending") or conclusion == "action_required":
        return "B_ACTION_REQUIRED", []
    if status != "completed":
        return "B_ACTION_REQUIRED", [f"RUN_NOT_COMPLETED:{status!r}"]
    if conclusion != "success":
        return "C_FAILED_OR_SKIPPED", [f"RUN_CONCLUSION:{conclusion!r}"]
    return "F_SUCCESS", []


# ─────────────────────────── HTTP ───────────────────────────

def _call(token: str, method: str, path: str, api: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{api}{path}", data=data, method=method, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
        "User-Agent": "mihomo-robot-pr"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")[:300]


def create_or_update(*, repo: str, source_branch: str, base_ref: str, title: str,
                     body: str, token: str, api: str, dry_run: bool = False) -> dict:
    problems = (validate_branch_name(source_branch) + validate_base_ref(base_ref)
                + validate_text(title, limit=MAX_TITLE, field="TITLE")
                + validate_text(body, limit=MAX_BODY, field="BODY"))
    if problems:
        return {"ok": False, "problems": problems}

    if dry_run:
        return {"ok": True, "dry_run": True, "action": "would-create-or-update"}

    owner = repo.split("/")[0]
    quoted = urllib.parse.quote(source_branch, safe="")
    status, ref = _call(token, "GET", f"/repos/{repo}/git/ref/heads/{quoted}", api)
    if status != 200 or not isinstance(ref, dict):
        return {"ok": False, "write_attempted": False,
                "problems": [f"SOURCE_BRANCH_UNREADABLE:HTTP{status}"]}
    head_sha = ((ref.get("object") or {}).get("sha"))
    if not isinstance(head_sha, str) or not SHA_RE.match(head_sha):
        return {"ok": False, "write_attempted": False, "problems": ["SOURCE_BRANCH_SHA_MALFORMED"]}
    if head_sha == base_ref:
        return {"ok": False, "write_attempted": False, "problems": ["SOURCE_EQUALS_BASE"]}

    status, compare = _call(token, "GET",
                            f"/repos/{repo}/compare/{base_ref}...{head_sha}", api)
    if status != 200 or not isinstance(compare, dict):
        return {"ok": False, "write_attempted": False,
                "problems": [f"COMPARE_UNREADABLE:HTTP{status}"]}
    files = compare.get("files") or []
    if not files:
        return {"ok": False, "write_attempted": False,
                "problems": ["DIFF_EMPTY:no file changes vs base"]}

    status, existing = _call(
        token, "GET",
        f"/repos/{repo}/pulls?state=open&head={owner}:{quoted}&base={base_ref}", api)
    if status != 200:
        return {"ok": False, "write_attempted": False,
                "problems": [f"PR_LIST_FAILED:HTTP{status}"]}

    action, pr, decide_problems = choose_action(
        existing, repo=repo, source_branch=source_branch, base_ref=base_ref)
    if decide_problems:
        return {"ok": False, "write_attempted": False, "problems": decide_problems}

    payload = {"title": title, "body": body}
    if action == "update":
        # R-1：身份保护必须**先于任何写请求**。真实事故中这里先 PATCH 了他人 PR 的
        # 标题/正文，之后才发现作者不是机器人。因此把作者分类提前到写入之前，
        # 且拒绝路径不发出任何写请求（zero-write）。
        number = pr.get("number")
        author_class, author_problems = classify_author(pr)
        if author_class != "BOT":
            return {"ok": False, "action": "none", "write_attempted": False,
                    "existing_pr": number,
                    "problems": [f"PR_EXISTS_OTHER_AUTHOR:{number}"] + list(author_problems)}
        status, obj = _call(token, "PATCH", f"/repos/{repo}/pulls/{number}", api, payload)
    else:
        status, obj = _call(token, "POST", f"/repos/{repo}/pulls", api,
                            {"title": title, "body": body, "head": source_branch,
                             "base": base_ref, "draft": False})
    if status not in (200, 201) or not isinstance(obj, dict):
        return {"ok": False, "problems": [f"PR_WRITE_FAILED:HTTP{status}:{str(obj)[:200]}"]}

    number = obj.get("number")
    if isinstance(number, bool) or not isinstance(number, int) or number < 1:
        return {"ok": False, "problems": ["PR_NUMBER_UNREADABLE"]}

    # 回读：写入响应不等于最终状态，必须重新 GET 并逐项校验
    status, reread = _call(token, "GET", f"/repos/{repo}/pulls/{number}", api)
    if status != 200 or not isinstance(reread, dict):
        return {"ok": False, "problems": [f"PR_REREAD_FAILED:HTTP{status}"]}
    ok, read_problems, evidence = verify_pr_readback(
        reread, repo=repo, base_ref=base_ref, head_sha=head_sha,
        source_branch=source_branch)
    return {"ok": ok, "action": action, "problems": read_problems,
            "head_sha": head_sha, "changed_files": len(files), "evidence": evidence}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="机器人作者 PR 创建/更新（GITHUB_TOKEN）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("validate")
    v.add_argument("--source", required=True)
    v.add_argument("--base", default="main")

    c = sub.add_parser("create-or-update")
    c.add_argument("--repo", required=True)
    c.add_argument("--source", required=True)
    c.add_argument("--base", default="main")
    c.add_argument("--title", required=True)
    c.add_argument("--body-file", required=True)
    c.add_argument("--json", type=lambda p: open(p, "w", encoding="utf-8"))
    c.add_argument("--dry-run", action="store_true")

    args = ap.parse_args(argv)
    if args.cmd == "validate":
        problems = validate_branch_name(args.source) + validate_base_ref(args.base)
        print(json.dumps({"ok": not problems, "problems": problems}, ensure_ascii=False))
        return 0 if not problems else 1

    try:
        with open(args.body_file, encoding="utf-8") as fh:
            body = fh.read()
    except OSError as exc:
        print(json.dumps({"ok": False, "problems": [f"BODY_UNREADABLE:{exc}"]}, ensure_ascii=False))
        return 1

    report = create_or_update(
        repo=args.repo, source_branch=args.source, base_ref=args.base, title=args.title,
        body=body, token=os.environ.get("GITHUB_TOKEN", ""),
        api=os.environ.get("GITHUB_API_URL", "https://api.github.com"),
        dry_run=args.dry_run)
    print(json.dumps(report, ensure_ascii=False, indent=1))
    if args.json:
        json.dump(report, args.json, ensure_ascii=False, indent=1)
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
