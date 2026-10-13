#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""必需检查的严格验证（PR-first 自动合并前置条件，fail-closed）。

严格口径（不使用宽松口径）：
  * 每个必需检查必须在**当前 PR head SHA** 上找到 check run；
  * 该 check run 必须 `status == completed` 且 `conclusion == "success"`；
    `skipped` / `neutral` / `cancelled` / `timed_out` / `failure` / `action_required` /
    `stale` / queued / in-progress / 未找到 **一律禁止自动合并**；
  * 必须来自**预期来源**（`.github/required-checks.json` 中声明的 GitHub Actions
    integration：`app_slug` 与 `app_id`）。同名但来源不符的 check run 被拒绝采信；
  * PR 必须仍为 open、目标分支仍是 base 分支、head SHA 未漂移；
  * 所有 API 请求都必须检查 HTTP 状态码；响应异常即 fail-closed。

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
import urllib.error
import urllib.request

DEFAULT_MANIFEST = ".github/required-checks.json"
STRICT_CONCLUSION = "success"
# 明确禁止的结论（严格白名单之外的一切都不接受）
FORBIDDEN_CONCLUSIONS = ("skipped", "neutral", "cancelled", "timed_out", "failure",
                         "action_required", "stale", "startup_failure")


class ManifestError(Exception):
    """清单不可读/结构非法 → fail-closed。"""


class ApiError(Exception):
    """API 异常 → fail-closed。"""


def load_manifest(path: str) -> list[dict]:
    """读取必需检查清单。结构：
    {"schema":..., "checks":[{"name":..., "job_id":..., "workflow":...,
                              "source":{"app_slug":..,"app_id":..}}]}
    兼容旧形态 {"required": ["name", ...]}（此时来源退化为「必须来自 GitHub Actions」）。
    """
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"无法读取必需检查清单 {path}: {exc}") from exc
    checks: list[dict] = []
    if isinstance(doc, dict) and isinstance(doc.get("checks"), list):
        for item in doc["checks"]:
            if isinstance(item, str):
                checks.append({"name": item, "job_id": item, "workflow": None,
                               "app_slug": "github-actions", "app_id": None})
            elif isinstance(item, dict) and item.get("name"):
                src = item.get("source") or {}
                checks.append({"name": item["name"], "job_id": item.get("job_id") or item["name"],
                               "workflow": item.get("workflow"),
                               "app_slug": src.get("app_slug", "github-actions"),
                               "app_id": src.get("app_id")})
            else:
                raise ManifestError(f"清单项非法：{item!r}")
    elif isinstance(doc, dict) and isinstance(doc.get("required"), list) and doc["required"]:
        checks = [{"name": n, "job_id": n, "workflow": None, "app_slug": "github-actions", "app_id": None}
                  for n in doc["required"]]
    else:
        raise ManifestError(f"{path}: 结构无法识别（需 checks[] 或 required[]）")
    if not checks:
        raise ManifestError(f"{path}: 必需检查列表为空")
    names = [c["name"] for c in checks]
    if len(names) != len(set(names)):
        raise ManifestError(f"{path}: 必需检查名称重复：{names}")
    return checks


def http_get_json(url: str, token: str, timeout: int = 30) -> tuple[int, object]:
    req = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "prfirst-required-checks"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as exc:
        return exc.code, {"_error": exc.read()[:400].decode("utf-8", "replace")}


def _source_matches(run: dict, check: dict) -> bool:
    app = run.get("app") or {}
    if check.get("app_slug") and app.get("slug") != check["app_slug"]:
        return False
    if check.get("app_id") is not None and app.get("id") != check["app_id"]:
        return False
    return True


def evaluate(pr_obj: dict, runs: list[dict], required: list[dict], expected_head: str,
             base_branch: str = "main") -> tuple[bool, list[str], dict]:
    """返回 (ok, problems, report)。纯函数，便于负向测试。"""
    problems: list[str] = []
    report: dict = {"expected_head_sha": expected_head, "checks": {}, "stale_runs": []}

    if pr_obj.get("state") != "open":
        problems.append(f"PR 状态为 {pr_obj.get('state')!r}，非 open")
    if pr_obj.get("merged"):
        problems.append("PR 已被合并")
    base_ref = (pr_obj.get("base") or {}).get("ref")
    if base_ref != base_branch:
        problems.append(f"PR 目标分支为 {base_ref!r}，非 {base_branch!r}")
    head_sha = (pr_obj.get("head") or {}).get("sha")
    report["pr_head_sha"] = head_sha
    if head_sha != expected_head:
        problems.append(f"PR head SHA {head_sha} != 预期 {expected_head}（head 漂移）")

    for check in required:
        name = check["name"]
        same_name = [r for r in runs if r.get("name") == name]
        on_head = [r for r in same_name if r.get("head_sha") == expected_head]
        matching = [r for r in on_head if _source_matches(r, check)]
        info: dict = {"found": bool(matching),
                      "expected_source": {"app_slug": check.get("app_slug"), "app_id": check.get("app_id")}}
        if not matching:
            if on_head:
                problems.append(
                    f"{name}: 该 head SHA 上存在同名检查但来源不符（拒绝采信）；"
                    f"实际来源={[(r.get('app') or {}).get('slug') for r in on_head]}")
                info["state"] = "wrong-source"
            else:
                problems.append(f"{name}: 在该 head SHA 上未找到 check run")
                info["state"] = "missing"
            report["checks"][name] = info
            continue
        run = sorted(matching, key=lambda r: (r.get("started_at") or "", r.get("id") or 0))[-1]
        app = run.get("app") or {}
        info.update({"state": "found", "status": run.get("status"), "conclusion": run.get("conclusion"),
                     "app_slug": app.get("slug"), "app_id": app.get("id"),
                     "details_url": run.get("details_url"), "completed_at": run.get("completed_at"),
                     "superseded_runs": [{"conclusion": r.get("conclusion"), "started_at": r.get("started_at")}
                                         for r in matching if r is not run]})
        if run.get("status") != "completed":
            problems.append(f"{name}: 尚未完成（status={run.get('status')}）")
            info["state"] = "in-progress"
        elif run.get("conclusion") != STRICT_CONCLUSION:
            extra = "（被跳过/中性一律视为不满足）" if run.get("conclusion") in ("skipped", "neutral") else ""
            problems.append(f"{name}: 结论为 {run.get('conclusion')!r}，严格口径仅接受 "
                            f"{STRICT_CONCLUSION!r}{extra}")
            info["state"] = "not-success"
        else:
            info["state"] = "success"
        report["checks"][name] = info

    for run in runs:
        if run.get("head_sha") != expected_head:
            report["stale_runs"].append({"name": run.get("name"), "head_sha": run.get("head_sha"),
                                         "conclusion": run.get("conclusion")})
    return (not problems), problems, report


def verify(*, repo: str, pr: int, expected_head: str, required_manifest: str = DEFAULT_MANIFEST,
           token: str, base_branch: str = "main", api_base: str = "https://api.github.com",
           getter=http_get_json, log=print) -> tuple[bool, dict, list[str]]:
    api_base = api_base.rstrip("/")
    try:
        required = load_manifest(required_manifest)
    except ManifestError as exc:
        return False, {"error": str(exc)}, [str(exc)]

    def get(path: str, what: str) -> tuple[dict | None, list[str]]:
        status, body = getter(f"{api_base}{path}", token)
        if status != 200 or not isinstance(body, dict) or "_error" in body:
            msg = f"{what}: HTTP {status} {str(body)[:200]}"
            log(f"❌ {msg}")
            return None, [msg]
        return body, []

    pr_obj, problems = get(f"/repos/{repo}/pulls/{pr}", "GET PR")
    if pr_obj is None:
        return False, {"error": problems[0]}, problems
    runs_body, problems2 = get(f"/repos/{repo}/commits/{expected_head}/check-runs?per_page=100",
                               "GET check-runs")
    if runs_body is None:
        return False, {"error": problems2[0]}, problems2

    ok, eval_problems, report = evaluate(pr_obj, list(runs_body.get("check_runs") or []), required,
                                         expected_head, base_branch)
    report["repo"] = repo
    report["pr"] = int(pr)
    report["required"] = [c["name"] for c in required]
    report["writes_performed"] = []
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
        print("✅ 全部必需检查在该 head SHA 上真实 success，且来源与目标分支符合预期")
        return 0
    print(f"❌ 必需检查未满足（{len(problems)} 项）：{'；'.join(problems[:5])}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
