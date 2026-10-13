#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""候选 head SHA 判定与一致性校验（PR-first）。

解决两个具体问题：
  1. 候选树与远端候选分支树一致而跳过推送时，**必须输出实际远端 ref 的 commit SHA**，
     而不是本地新建（未推送）的 commit SHA。
  2. 创建/更新 PR 之后，必须读取 PR 真实 head SHA 与候选分支 ref SHA，并对
     「实际 head / 候选分支 ref / 预期 SHA」做一致性校验；任一不一致、读取失败或
     响应未知都必须 fail-closed（绝不请求 auto-merge）。

本模块只做纯判定（无网络、无写入），网络读取由调用方（workflow 中的 curl）完成。
用法：
  python3 scripts/ci/candidate_sha.py decide  --remote-ref-exists true|false \
      --remote-ref-sha <sha|-> --local-sha <sha> --trees-equal true|false \
      --pr-state <open|closed|none> --pr-head-sha <sha|-> [--json out.json]
  python3 scripts/ci/candidate_sha.py verify --expected <sha> --pr-head <sha> \
      --branch-ref <sha> [--json out.json]
退出码：0 成功/一致；1 失败关闭（不一致、非法 SHA、状态不允许）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys

SHA_RE = re.compile(r"^[0-9a-f]{40}$")

# 决策值
DECIDE_PUSH = "push"
DECIDE_SKIP_PUSH = "skip-push"
DECIDE_ABORT = "abort"


class CandidateError(Exception):
    """无法判定 → fail-closed。"""


def is_sha(value: str | None) -> bool:
    return bool(value) and bool(SHA_RE.match(str(value).strip()))


def _req_sha(value: str | None, field: str, problems: list[str]) -> str:
    v = (value or "").strip()
    if not is_sha(v):
        problems.append(f"{field} 不是合法的 40 位十六进制 commit SHA：{value!r}")
        return ""
    return v


def effective_head_sha(local_sha: str, remote_ref_sha: str | None, trees_equal: bool) -> str:
    """树摘要一致（跳过推送）时，实际 head 是**远端 ref 的 SHA**；否则是本地新提交 SHA。"""
    if trees_equal:
        if not is_sha(remote_ref_sha):
            raise CandidateError("树摘要一致但远端 ref SHA 非法/缺失：无法确定实际 head（不回退本地 SHA）")
        return str(remote_ref_sha).strip()
    return local_sha


def decide_publish(*, remote_ref_exists: bool, remote_ref_sha: str | None, local_sha: str,
                   trees_equal: bool, pr_state: str | None, pr_head_sha: str | None) -> dict:
    """幂等决策：返回 {decision, expected_head_sha, problems, reason}。"""
    problems: list[str] = []
    _req_sha(local_sha, "local_sha", problems)
    if remote_ref_exists:
        _req_sha(remote_ref_sha, "remote_ref_sha", problems)
    if pr_state in ("closed", "merged"):
        # 候选 PR 被关闭/合并：不自动复活、不覆盖，交回人工决策
        problems.append(f"候选分支已存在 {pr_state} 的 PR：不自动复用（需人工决策）")
    if problems:
        return {"decision": DECIDE_ABORT, "expected_head_sha": None, "problems": problems,
                "reason": "前置状态非法或不一致"}

    if remote_ref_exists and trees_equal:
        expected = str(remote_ref_sha).strip()
        if pr_head_sha and is_sha(pr_head_sha) and str(pr_head_sha).strip() != expected:
            return {"decision": DECIDE_ABORT, "expected_head_sha": None,
                    "problems": [f"PR head SHA {pr_head_sha} 与候选分支 ref {expected} 不一致（陈旧 head）"],
                    "reason": "陈旧 SHA"}
        return {"decision": DECIDE_SKIP_PUSH, "expected_head_sha": expected, "problems": [],
                "reason": "候选树未变化：跳过推送，head 取远端 ref SHA"}
    if remote_ref_exists and not trees_equal:
        return {"decision": DECIDE_PUSH, "expected_head_sha": local_sha.strip(), "problems": [],
                "reason": "候选树变化：推送新提交"}
    return {"decision": DECIDE_PUSH, "expected_head_sha": local_sha.strip(), "problems": [],
            "reason": "候选分支尚不存在：推送新分支"}


def check_consistency(expected_head: str, pr_head: str | None, branch_ref: str | None) -> list[str]:
    """对「预期 head / PR 真实 head / 候选分支 ref」三者做一致性校验，返回问题列表。"""
    problems: list[str] = []
    exp = _req_sha(expected_head, "expected_head", problems)
    pr = _req_sha(pr_head, "pr_head", problems)
    ref = _req_sha(branch_ref, "branch_ref", problems)
    if problems:
        return problems
    if exp != pr:
        problems.append(f"PR head SHA {pr} != 预期 {exp}（PR 未被候选分支推送更新或发生漂移）")
    if exp != ref:
        problems.append(f"候选分支 ref {ref} != 预期 {exp}（分支发生未经验证的漂移）")
    return problems


def _bool(v: str) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "y")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="候选 head SHA 判定 / 一致性校验（只读）")
    sub = ap.add_subparsers(dest="mode", required=True)

    d = sub.add_parser("decide")
    d.add_argument("--remote-ref-exists", required=True)
    d.add_argument("--remote-ref-sha", default="-")
    d.add_argument("--local-sha", required=True)
    d.add_argument("--trees-equal", required=True)
    d.add_argument("--pr-state", default="none")
    d.add_argument("--pr-head-sha", default="-")
    d.add_argument("--json")

    v = sub.add_parser("verify")
    v.add_argument("--expected", required=True)
    v.add_argument("--pr-head", required=True)
    v.add_argument("--branch-ref", required=True)
    v.add_argument("--json")

    a = ap.parse_args(argv)
    if a.mode == "decide":
        dash = lambda x: None if x in ("-", "", None) else x
        out = decide_publish(remote_ref_exists=_bool(a.remote_ref_exists),
                             remote_ref_sha=dash(a.remote_ref_sha), local_sha=a.local_sha,
                             trees_equal=_bool(a.trees_equal),
                             pr_state=None if a.pr_state in ("none", "", "-") else a.pr_state,
                             pr_head_sha=dash(a.pr_head_sha))
        print(json.dumps(out, ensure_ascii=False, indent=2))
        if a.json:
            open(a.json, "w", encoding="utf-8").write(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
        if out["decision"] == DECIDE_ABORT:
            print("❌ 决策=abort（fail-closed）：" + "；".join(out["problems"]), file=sys.stderr)
            return 1
        print(f"✅ 决策={out['decision']} expected_head_sha={out['expected_head_sha']}（{out['reason']}）")
        return 0

    problems = check_consistency(a.expected, a.pr_head, a.branch_ref)
    out = {"consistent": not problems, "expected_head_sha": a.expected, "pr_head_sha": a.pr_head,
           "branch_ref_sha": a.branch_ref, "problems": problems}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    if a.json:
        open(a.json, "w", encoding="utf-8").write(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    if problems:
        print("❌ 一致性校验失败（fail-closed，不请求 auto-merge）：" + "；".join(problems), file=sys.stderr)
        return 1
    print("✅ PR head / 候选分支 ref / 预期 SHA 三者一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
