#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""请求 GitHub auto-merge（PR-first），并严格区分三个不同状态。

三个状态**不得混用**：
  1. `graphql_request_ok`：GraphQL mutation 请求本身成功（HTTP 200 且响应含
     `data.enablePullRequestAutoMerge` 对象——只检查没有 `errors` 是不够的）。
  2. `auto_merge_enabled`：回读 PR 确认 `auto_merge` 非空（GitHub 已接受该请求）。
  3. `merged`：PR 真实完成合并（`merged == true`）。只有此时才报告 merge commit SHA。

只有 1 与（2 或 3）同时成立才返回 0；否则 fail-closed。
实际合并与否仍由 GitHub 按 Ruleset 执行——本脚本不代替 Ruleset 判定。

用法：
  python3 scripts/ci/request_automerge.py --repo owner/name --pr 123 --head-sha <sha> \
      [--required-checks .github/required-checks.json] [--method SQUASH] [--json out.json] [--dry-run]
退出码：0 = 已确认 auto-merge 请求被接受（或已合并）；1 = 任一环节失败/状态不明。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import verify_required_checks as vrc  # noqa: E402  （同目录模块；严格检查为前置条件）

MUTATION = ("mutation($id:ID!,$method:PullRequestMergeMethod!){"
            "enablePullRequestAutoMerge(input:{pullRequestId:$id,mergeMethod:$method})"
            "{clientMutationId pullRequest{number autoMergeRequest{enabledAt mergeMethod}}}}")


def http_get_json(url: str, token: str, timeout: int = 30) -> tuple[int, object]:
    req = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "prfirst-automerge"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as exc:
        return exc.code, {"_error": exc.read()[:400].decode("utf-8", "replace")}


def http_post_json(url: str, token: str, payload: dict, timeout: int = 30) -> tuple[int, object]:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "prfirst-automerge",
        "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as exc:
        return exc.code, {"_error": exc.read()[:400].decode("utf-8", "replace")}


def _require_dict(status: int, body: object, what: str, problems: list[str]) -> dict | None:
    if status != 200:
        problems.append(f"{what}: HTTP {status} {str(body)[:200]}")
        return None
    if not isinstance(body, dict) or "_error" in body:
        problems.append(f"{what}: 响应无法解析：{str(body)[:200]}")
        return None
    return body


def _pre_mutation_reconcile(*, repo: str, pr: int, head_sha: str, base_branch: str, required_manifest: str,
                            token: str, api_base: str, getter, log=print) -> tuple[bool, list[str], dict]:
    """发送 GraphQL mutation **紧邻之前**的只读状态对账（Fix C）。

    必须确认：PR 仍 open、目标分支仍为 base_branch、`PR.base.sha` 与 `refs/heads/<base>` 当前 SHA
    一致（base 未漂移）、`PR.head.sha` 仍等于完成检查时的 SHA、node_id 存在，
    并**重新执行一次严格必需检查**（含 workflow/job 身份认证）。
    任一 GET 失败、字段缺失或 SHA 不一致 ⇒ 返回 False（禁止 mutation）。
    """
    problems: list[str] = []
    info: dict = {"phase": "pre-mutation"}
    status, body = getter(f"{api_base}/repos/{repo}/pulls/{pr}", token)
    pr_obj = _require_dict(status, body, "GET PR（mutation 前对账）", problems)
    if pr_obj is None:
        return False, problems, info
    base_ref = (pr_obj.get("base") or {}).get("ref")
    base_sha = (pr_obj.get("base") or {}).get("sha")
    pr_head = (pr_obj.get("head") or {}).get("sha")
    info.update({"pr_state": pr_obj.get("state"), "base_ref": base_ref, "base_sha": base_sha,
                 "pr_head_sha": pr_head})
    if pr_obj.get("state") != "open":
        problems.append(f"mutation 前：PR 状态为 {pr_obj.get('state')!r}，非 open")
    if pr_obj.get("merged"):
        problems.append("mutation 前：PR 已被合并")
    if base_ref != base_branch:
        problems.append(f"mutation 前：PR 目标分支为 {base_ref!r}，非 {base_branch!r}")
    if pr_head != head_sha:
        problems.append(f"mutation 前：head SHA 漂移（{pr_head} != {head_sha}）")
    if not pr_obj.get("node_id"):
        problems.append("mutation 前：PR 缺少 node_id")
    status, ref = getter(f"{api_base}/repos/{repo}/git/ref/heads/{base_branch}", token)
    ref_obj = _require_dict(status, ref, f"GET refs/heads/{base_branch}（mutation 前对账）", problems)
    if ref_obj is None:
        return False, problems, info
    main_sha = (ref_obj.get("object") or {}).get("sha")
    info[f"refs_heads_{base_branch}"] = main_sha
    if not main_sha:
        problems.append(f"mutation 前：refs/heads/{base_branch} 响应缺少 sha")
    elif base_sha != main_sha:
        problems.append(f"mutation 前：PR base SHA {base_sha} != refs/heads/{base_branch} 当前 SHA "
                        f"{main_sha}（base 漂移）")
    if problems:
        return False, problems, info
    # 紧邻 mutation 前再次执行严格必需检查（来源/身份 + base 一致性）
    ok, sub_report, sub_problems = vrc.verify(repo=repo, pr=int(pr), expected_head=head_sha,
                                              required_manifest=required_manifest, token=token,
                                              base_branch=base_branch, api_base=api_base, getter=getter,
                                              log=log)
    info["required_checks"] = sub_report
    if not ok:
        return False, [f"mutation 前必需检查复核未通过：{p}" for p in sub_problems], info
    return True, [], info


def run(*, repo: str, pr: int, head_sha: str, required_manifest: str, token: str, method: str = "SQUASH",
        api_base: str = "https://api.github.com", graphql_url: str | None = None, dry_run: bool = False,
        base_branch: str = "main", getter=http_get_json, poster=http_post_json, log=print) -> dict:
    api_base = api_base.rstrip("/")
    graphql_url = graphql_url or f"{api_base}/graphql"
    report: dict = {"repo": repo, "pr": int(pr), "head_sha": head_sha, "method": method,
                    "base_branch": base_branch,
                    "graphql_request_ok": False, "auto_merge_enabled": False, "merged": False,
                    "problems": [], "writes_performed": []}

    # 1) 严格必需检查（前置条件，fail-closed）
    ok, sub_report, problems = vrc.verify(repo=repo, pr=int(pr), expected_head=head_sha,
                                          required_manifest=required_manifest, token=token,
                                          base_branch=base_branch, api_base=api_base, getter=getter,
                                          log=log)
    report["required_checks"] = sub_report
    if not ok:
        report["problems"] = [f"必需检查未满足：{p}" for p in problems]
        report["reason"] = "严格必需检查未通过：不请求 auto-merge"
        return report

    # 2) 读取 PR node_id（同时复核 open/base/head）
    status, body = getter(f"{api_base}/repos/{repo}/pulls/{pr}", token)
    pr_obj = _require_dict(status, body, "GET PR", report["problems"])
    if pr_obj is None:
        report["reason"] = "读取 PR 失败（fail-closed）"
        return report
    report["pr_state"] = pr_obj.get("state")
    report["pr_head_sha"] = (pr_obj.get("head") or {}).get("sha")
    if pr_obj.get("state") != "open" or report["pr_head_sha"] != head_sha:
        report["problems"].append(
            f"PR 状态/head 不符：state={pr_obj.get('state')} head={report['pr_head_sha']} != {head_sha}")
        report["reason"] = "PR 状态或 head SHA 不符（fail-closed）"
        return report
    node_id = pr_obj.get("node_id")
    if not node_id:
        report["problems"].append("PR 缺少 node_id")
        report["reason"] = "无法取得 node_id（fail-closed）"
        return report

    # 3) **mutation 紧邻之前**的只读对账（Fix C）：base 未漂移 + 必需检查来源/身份复核
    ok, problems, recon = _pre_mutation_reconcile(
        repo=repo, pr=int(pr), head_sha=head_sha, base_branch=base_branch,
        required_manifest=required_manifest, token=token, api_base=api_base, getter=getter, log=log)
    report["pre_mutation_reconciliation"] = recon
    if not ok:
        report["problems"] = list(report["problems"]) + problems
        report["reason"] = "mutation 前状态对账失败（base/head 漂移或必需检查复核未通过）：不发送 mutation"
        return report

    if dry_run:
        report["reason"] = "dry-run：已完成全部只读校验（含 mutation 前对账），未发送 GraphQL 请求"
        report["dry_run"] = True
        return report

    # 4) GraphQL mutation：必须同时检查 HTTP 状态与 data 内容
    status, body = poster(graphql_url, token,
                          {"query": MUTATION, "variables": {"id": node_id, "method": method}})
    report["writes_performed"].append("POST /graphql enablePullRequestAutoMerge")
    gql = _require_dict(status, body, "POST /graphql", report["problems"])
    if gql is None:
        report["reason"] = "GraphQL 请求失败（fail-closed）"
        return report
    if gql.get("errors"):
        report["problems"].append(f"GraphQL errors: {str(gql['errors'])[:300]}")
        report["reason"] = "GraphQL 返回 errors"
        return report
    data = gql.get("data") or {}
    if not isinstance(data.get("enablePullRequestAutoMerge"), dict):
        report["problems"].append("GraphQL 响应缺少 data.enablePullRequestAutoMerge 对象")
        report["reason"] = "GraphQL 响应结构不符合预期"
        return report
    report["graphql_request_ok"] = True

    # 5) 回读确认 GitHub 是否真的接受（auto_merge 非空）
    status, body = getter(f"{api_base}/repos/{repo}/pulls/{pr}", token)
    after = _require_dict(status, body, "GET PR（回读）", report["problems"])
    if after is None:
        report["reason"] = "回读 PR 失败：不能确认 auto-merge 已启用"
        return report
    report["auto_merge_request"] = after.get("auto_merge")
    report["auto_merge_enabled"] = after.get("auto_merge") is not None
    report["merged"] = bool(after.get("merged"))
    report["pr_state_after"] = after.get("state")

    # 6) 只有真实合并后才报告 merge commit 与 main HEAD
    if report["merged"]:
        report["merge_commit_sha"] = after.get("merge_commit_sha")
        status, ref = getter(f"{api_base}/repos/{repo}/git/ref/heads/{base_branch}", token)
        ref_obj = _require_dict(status, ref, f"GET ref heads/{base_branch}", report["problems"])
        if ref_obj is not None:
            report["main_head_sha"] = (ref_obj.get("object") or {}).get("sha")
            report["main_advanced"] = report["main_head_sha"] != recon.get("base_sha")
        report["reason"] = "PR 已真实合并（GitHub 已按 Ruleset 完成合并）"
        return report
    if not report["auto_merge_enabled"]:
        report["problems"].append("回读显示 auto_merge 为空：GitHub 未接受 auto-merge 请求")
        report["reason"] = "auto-merge 未启用（fail-closed）"
        return report
    report["reason"] = "auto-merge 请求已被接受，等待 GitHub 按 Ruleset 合并（尚未合并）"
    return report


def succeeded(report: dict) -> bool:
    return bool(report.get("graphql_request_ok")) and (bool(report.get("auto_merge_enabled")) or bool(report.get("merged")))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="请求 auto-merge（严格三态区分）")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--pr", required=True)
    ap.add_argument("--head-sha", required=True)
    ap.add_argument("--required-checks", default=vrc.DEFAULT_MANIFEST)
    ap.add_argument("--method", default="SQUASH", choices=["SQUASH", "MERGE", "REBASE"])
    ap.add_argument("--base-branch", default="main")
    ap.add_argument("--api-base", default=os.environ.get("GITHUB_API_URL", "https://api.github.com"))
    ap.add_argument("--json")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    if not token:
        print("❌ 缺少 GITHUB_TOKEN", file=sys.stderr)
        return 1
    report = run(repo=a.repo, pr=a.pr, head_sha=a.head_sha, required_manifest=a.required_checks,
                 token=token, method=a.method, api_base=a.api_base, dry_run=a.dry_run,
                 base_branch=a.base_branch)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    if succeeded(report):
        print(f"✅ graphql_request_ok={report['graphql_request_ok']} "
              f"auto_merge_enabled={report['auto_merge_enabled']} merged={report['merged']}")
        return 0
    print(f"❌ {report.get('reason')}：{'；'.join(report.get('problems') or [])}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
