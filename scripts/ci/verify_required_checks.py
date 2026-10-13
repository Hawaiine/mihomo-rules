#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""必需检查的严格验证（PR-first 自动合并前置条件，fail-closed）。

严格口径：
  * 每个必需检查必须在**当前 PR head SHA** 上找到 check run；
  * 必须通过**来源与身份认证**（见 `scripts/ci/check_provenance.py`）：
    check_suite → workflow run（path 精确等于 manifest 的 `.github/workflows/pr-verify.yml`、
    event == `pull_request`、head_sha == PR head）→ job 记录（check run id == job id，
    job name == manifest 的 job_id），且 App 来源为 `github-actions`/15368。
    **其它 workflow 的同名成功检查不得覆盖真实必需检查的失败**；
  * 只有通过认证之后，才在同一预期 workflow/job 内按时间选取最新 run；
    未通过认证的旧 run 不得被记为 superseded；
  * `status == completed` 且 `conclusion == "success"`（严格白名单）；
  * PR 必须仍为 open、目标分支仍是 base 分支、head SHA 未漂移，
    且 `PR.base.sha` 与 `refs/heads/<base_branch>` 当前 SHA 一致（base 漂移即失败）；
  * 所有 API 请求必须检查 HTTP 状态码；响应异常即 fail-closed。

只读：本脚本只发 GET。
用法：
  python3 scripts/ci/verify_required_checks.py --repo owner/name --pr 123 --head-sha <sha> \
      [--required-checks .github/required-checks.json] [--base-branch main] [--json out.json]
退出码：0 = 全部满足；1 = 任一不满足（禁止合并）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check_provenance import (  # noqa: E402
    ApiError, DEFAULT_MANIFEST, ManifestError, STRICT_CONCLUSION, collect_check_runs, collect_jobs,
    collect_workflow_runs, http_get_json, index_jobs, load_manifest, needed_run_ids, overall_state,
    select_checks,
)


def evaluate(pr_obj: dict, check_runs: list[dict], workflow_runs: list[dict], jobs_by_id: dict,
             required: list[dict], expected_head: str, base_branch: str = "main",
             main_sha: str | None = None) -> tuple[bool, list[str], dict]:
    """纯函数判定（便于负向测试）。返回 (ok, problems, report)。"""
    problems: list[str] = []
    report: dict = {"expected_head_sha": expected_head, "checks": {}, "stale_runs": []}

    if pr_obj.get("state") != "open":
        problems.append(f"PR 状态为 {pr_obj.get('state')!r}，非 open")
    if pr_obj.get("merged"):
        problems.append("PR 已被合并")
    base_ref = (pr_obj.get("base") or {}).get("ref")
    base_sha = (pr_obj.get("base") or {}).get("sha")
    report["base_ref"] = base_ref
    report["base_sha"] = base_sha
    report["main_sha"] = main_sha
    if base_ref != base_branch:
        problems.append(f"PR 目标分支为 {base_ref!r}，非 {base_branch!r}")
    if main_sha is not None and base_sha != main_sha:
        problems.append(f"PR base SHA {base_sha} 与 refs/heads/{base_branch} 当前 SHA {main_sha} "
                        f"不一致（base 漂移）")
    head_sha = (pr_obj.get("head") or {}).get("sha")
    report["pr_head_sha"] = head_sha
    if head_sha != expected_head:
        problems.append(f"PR head SHA {head_sha} != 预期 {expected_head}（head 漂移）")

    per_check, prov_problems = select_checks(
        check_runs=check_runs, manifest=required, workflow_runs=workflow_runs,
        jobs_by_id=jobs_by_id, expected_head=expected_head, pr_head=head_sha or "")
    report["checks"] = per_check
    state = overall_state(per_check)
    report["provenance_state"] = state
    problems.extend(prov_problems)
    if state != "success":
        problems.append(f"必需检查未全部通过来源/身份认证并 success（provenance_state={state}）")

    for run in check_runs:
        if run.get("head_sha") != expected_head:
            report["stale_runs"].append({"name": run.get("name"), "head_sha": run.get("head_sha"),
                                         "conclusion": run.get("conclusion")})
    return (not problems), problems, report


def verify(*, repo: str, pr: int, expected_head: str, required_manifest: str = DEFAULT_MANIFEST,
           token: str, base_branch: str = "main", api_base: str = "https://api.github.com",
           getter=http_get_json, log=print, require_base_consistency: bool = True,
           ) -> tuple[bool, dict, list[str]]:
    api_base = api_base.rstrip("/")
    try:
        required = load_manifest(required_manifest)
    except ManifestError as exc:
        return False, {"error": str(exc)}, [str(exc)]

    def get(path: str, what: str) -> tuple[dict | None, str]:
        status, body = getter(f"{api_base}{path}", token)
        if status != 200 or not isinstance(body, dict) or "_error" in body:
            msg = f"{what}: HTTP {status} {str(body)[:200]}"
            log(f"❌ {msg}")
            return None, msg
        return body, ""

    pr_obj, pr_msg = get(f"/repos/{repo}/pulls/{pr}", "GET PR")
    if pr_obj is None:
        return False, {"error": pr_msg}, [f"{pr_msg}（fail-closed）"]
    main_sha = None
    if require_base_consistency:
        main_obj, main_msg = get(f"/repos/{repo}/git/ref/heads/{base_branch}",
                                 f"GET refs/heads/{base_branch}")
        if main_obj is None:
            return False, {"error": main_msg}, [f"{main_msg}（fail-closed）"]
        main_sha = (main_obj.get("object") or {}).get("sha")
        if not main_sha:
            msg = f"GET refs/heads/{base_branch}: 响应缺少 sha"
            return False, {"error": msg}, [f"{msg}（fail-closed）"]
    try:
        check_runs = collect_check_runs(getter=getter, token=token, api_base=api_base, repo=repo,
                                        head_sha=expected_head)
        workflow_runs = collect_workflow_runs(getter=getter, token=token, api_base=api_base, repo=repo,
                                              head_sha=expected_head)
        jobs_by_id: dict[int, dict] = {}
        for rid in needed_run_ids(check_runs, required, workflow_runs):
            jobs_by_id.update(index_jobs(collect_jobs(getter=getter, token=token, api_base=api_base,
                                                      repo=repo, run_id=rid)))
    except ApiError as exc:
        log(f"❌ provenance 采集失败：{exc}")
        return False, {"error": str(exc)}, [f"provenance 采集失败：{exc}（fail-closed）"]

    ok, eval_problems, report = evaluate(pr_obj, check_runs, workflow_runs, jobs_by_id, required,
                                        expected_head, base_branch, main_sha)
    report.update({"repo": repo, "pr": int(pr), "required": [c["name"] for c in required],
                   "required_manifest": required_manifest, "writes_performed": []})
    for p in eval_problems:
        log(f"❌ {p}")
    return ok, report, eval_problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="严格验证必需检查（只读，fail-closed）")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--pr", required=True)
    ap.add_argument("--head-sha", required=True)
    ap.add_argument("--required-checks", default=DEFAULT_MANIFEST)
    ap.add_argument("--base-branch", default="main")
    ap.add_argument("--api-base", default=os.environ.get("GITHUB_API_URL", "https://api.github.com"))
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    if not token:
        print("❌ 缺少 GITHUB_TOKEN（只读用途）", file=sys.stderr)
        return 1
    ok, report, problems = verify(repo=a.repo, pr=a.pr, expected_head=a.head_sha,
                                  required_manifest=a.required_checks, token=token,
                                  base_branch=a.base_branch, api_base=a.api_base)
    text = json.dumps({"ok": ok, "problems": problems, "report": report}, ensure_ascii=False, indent=2)
    print(text)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    if ok:
        print("✅ 全部必需检查在该 head SHA 上真实 success，且来源/身份/目标分支均通过认证")
        return 0
    print(f"❌ 必需检查未满足（{len(problems)} 项）：{'；'.join(problems[:5])}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
