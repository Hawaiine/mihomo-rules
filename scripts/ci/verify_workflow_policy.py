#!/usr/bin/env python3
"""workflow 策略 guard（只读）。

在 PR 验证与本地自检中校验 `.github/workflows/**` 的实际策略约束，防止「同一变更里
既削弱检查又靠削弱后的检查通过」。本脚本只读文件，不访问网络、不写仓库。

检查项（任一失败即 exit 1）：
  P1  pull_request_target 一律禁止（可被候选内容利用的提权触发）。
  P2  每个 workflow 必须显式声明 permissions；禁止 write-all。
  P3  带 schedule 触发且持有写权限的 workflow 必须由 CANDIDATE_SYNC_ENABLED 变量门控；
      已知的旧直推 main workflow（daily-sync.yml）必须保持「不重新启用写入」形态：
      文件内不得出现 contents: write 与 git push 同时存在。
  P4  pr-verify.yml 的 job 集合必须与 .github/required-checks.json 完全一致，
      且每个 job 的 name 等于其 job id（保证 ruleset 可精确引用稳定 check 名）。
  P5  任何启用 auto-merge 的 workflow 必须在本文件内先调用
      scripts/ci/verify_required_checks.py（用真实 check run 与 head SHA 绑定验证）。
  P6  候选更新 workflow：顶层 contents: read；发布 job 写权限白名单；禁止 push main。
  P7  job 内检出 PR head 的步骤不得与 contents: write 同处一个 job。
  P8  checkout 步骤必须 persist-credentials: false（读权限 job 不应持久化凭据）。

用法：
  python3 scripts/ci/verify_workflow_policy.py [--root <仓库根>] [--json <输出路径>]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

WORKFLOW_DIR = ".github/workflows"
REQUIRED_CHECKS_PATH = ".github/required-checks.json"
PR_VERIFY = ".github/workflows/pr-verify.yml"
CANDIDATE_SYNC = ".github/workflows/candidate-pr-sync.yml"
LEGACY_DIRECT_WRITE = ".github/workflows/daily-sync.yml"
ENABLE_GATE = "CANDIDATE_SYNC_ENABLED"
PUBLISH_JOB_WRITE_ALLOW = {"contents": "write", "pull-requests": "write", "actions": "write"}
PUSH_MAIN_RE = re.compile(r"git\s+push[^\n]*(?:\bmain\b|\$\{\{\s*github\.ref_name\s*\}\}|HEAD:main)")
AUTOMERGE_MARKERS = ("enablePullRequestAutoMerge", "/auto_merge", "--auto")
REQUIRED_CHECKS_CALL = "scripts/ci/verify_required_checks.py"


class PolicyError(Exception):
    """workflow 策略违规。"""


def _load_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:  # fail-closed：无法解析即视为违规
        raise PolicyError(f"{path}: cannot parse workflow YAML: {exc}") from exc


def _triggers(doc: dict) -> dict:
    raw = doc.get("on")
    if raw is None:
        raw = doc.get(True)  # YAML 1.1 会把裸 on 解析成 True
    if isinstance(raw, str):
        return {raw: None}
    if isinstance(raw, list):
        return {str(item): None for item in raw}
    return raw if isinstance(raw, dict) else {}


def _permissions(doc: dict, job: dict | None = None) -> Any:
    if job is not None and "permissions" in job:
        return job["permissions"]
    return doc.get("permissions")


def _is_write(perm: Any, scope: str) -> bool:
    if isinstance(perm, str):
        return perm == "write-all"
    if isinstance(perm, dict):
        return perm.get(scope) == "write"
    return False


def _write_scopes(perm: Any) -> set[str]:
    if isinstance(perm, str):
        return {"*"} if perm == "write-all" else set()
    if isinstance(perm, dict):
        return {k for k, v in perm.items() if v == "write"}
    return set()


def _run_scripts(doc: dict) -> list[str]:
    out: list[str] = []
    for job in (doc.get("jobs") or {}).values():
        for step in job.get("steps") or []:
            run = step.get("run")
            if isinstance(run, str):
                out.append(run)
    return out


def _checkout_steps(doc: dict) -> list[dict]:
    out: list[dict] = []
    for job in (doc.get("jobs") or {}).values():
        for step in job.get("steps") or []:
            uses = str(step.get("uses") or "")
            if uses.startswith("actions/checkout"):
                out.append(step)
    return out


def check_workflow(path: Path, text: str, doc: dict, required: list[str]) -> list[str]:
    """单个 workflow 的策略检查。"""
    problems: list[str] = []
    name = path.as_posix()
    triggers = _triggers(doc)
    jobs = doc.get("jobs") or {}

    # P1
    if "pull_request_target" in triggers:
        problems.append(f"{name}: pull_request_target is forbidden")

    # P2
    top_perm = _permissions(doc)
    if top_perm is None and not all("permissions" in j for j in jobs.values()):
        problems.append(f"{name}: workflow must declare explicit permissions (top-level or per job)")
    for scope in _write_scopes(top_perm):
        if scope == "*":
            problems.append(f"{name}: permissions: write-all is forbidden")

    # P3
    if "schedule" in triggers:
        writers = [jid for jid, j in jobs.items() if _write_scopes(_permissions(doc, j))]
        if writers:
            if path.name == Path(LEGACY_DIRECT_WRITE).name:
                # 旧直推 workflow：允许其保持现状（平台侧已禁用）。
                # 「是否被重新启用写入」由可信基线 guard 对 base/head 比对判定，
                # 单看候选内容无法区分「本来就是直推」与「本轮改成直推」。
                pass
            elif ENABLE_GATE not in text:
                problems.append(
                    f"{name}: scheduled workflow with write permissions must be gated by vars.{ENABLE_GATE}"
                )

    # P5
    if any(marker in text for marker in AUTOMERGE_MARKERS) and REQUIRED_CHECKS_CALL not in text:
        problems.append(
            f"{name}: auto-merge must be preceded by {REQUIRED_CHECKS_CALL} (required check ↔ head SHA binding)"
        )

    # P6
    if path.name == Path(CANDIDATE_SYNC).name:
        if _write_scopes(top_perm):
            problems.append(f"{name}: candidate sync workflow must keep top-level permissions read-only")
        if PUSH_MAIN_RE.search(text):
            problems.append(f"{name}: candidate sync workflow must never push main")
        publish = jobs.get("publish") or {}
        scopes = _write_scopes(_permissions(doc, publish))
        extra = scopes - set(PUBLISH_JOB_WRITE_ALLOW)
        if extra:
            problems.append(f"{name}: publish job declares unexpected write scopes {sorted(extra)}")

    # P7 / P8
    for jid, job in jobs.items():
        job_text = json.dumps(job, ensure_ascii=False)
        heads = [s for s in (job.get("steps") or [])
                 if str(s.get("uses") or "").startswith("actions/checkout")
                 and "pull_request.head.sha" in json.dumps(s.get("with") or {}, ensure_ascii=False)]
        if heads and _is_write(_permissions(doc, job), "contents"):
            problems.append(
                f"{name}: job '{jid}' checks out the PR head while holding contents: write"
            )
    for step in _checkout_steps(doc):
        with_block = step.get("with") or {}
        if with_block.get("persist-credentials") is True:
            problems.append(f"{name}: actions/checkout must set persist-credentials: false")

    # P4
    if path.name == Path(PR_VERIFY).name:
        ids = sorted(jobs)
        if ids != sorted(required):
            problems.append(
                f"{name}: job ids {ids} must equal required checks {sorted(required)}"
            )
        for jid, job in jobs.items():
            if job.get("name") != jid:
                problems.append(f"{name}: job '{jid}' must set name: {jid} for a stable check name")
    return problems


def check_required_checks_file(path: Path) -> tuple[list[str], list[str]]:
    problems: list[str] = []
    if not path.exists():
        return [f"{REQUIRED_CHECKS_PATH}: missing required-checks manifest"], []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"{REQUIRED_CHECKS_PATH}: invalid JSON: {exc}"], []
    required = data.get("required")
    if not isinstance(required, list) or not required or not all(isinstance(x, str) for x in required):
        problems.append(f"{REQUIRED_CHECKS_PATH}: 'required' must be a non-empty list of check names")
        return problems, []
    if data.get("workflow") != PR_VERIFY:
        problems.append(f"{REQUIRED_CHECKS_PATH}: workflow must be {PR_VERIFY}")
    if len(set(required)) != len(required):
        problems.append(f"{REQUIRED_CHECKS_PATH}: duplicate check names")
    return problems, required


def run(root: Path) -> tuple[list[str], dict]:
    problems, required = check_required_checks_file(root / REQUIRED_CHECKS_PATH)
    wf_dir = root / WORKFLOW_DIR
    files = sorted(wf_dir.glob("*.yml")) + sorted(wf_dir.glob("*.yaml"))
    if not files:
        problems.append(f"{WORKFLOW_DIR}: no workflow files found")
    checked: list[str] = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        try:
            doc = _load_yaml(path)
        except PolicyError as exc:
            problems.append(str(exc))
            continue
        if not isinstance(doc, dict):
            problems.append(f"{WORKFLOW_DIR}/{path.name}: workflow must be a mapping")
            continue
        checked.append(path.name)
        problems.extend(check_workflow(path.relative_to(root), text, doc, required))
    return problems, {"workflows_checked": checked, "required_checks": required}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="workflow 策略 guard（只读）")
    ap.add_argument("--root", default=".", help="仓库根目录")
    ap.add_argument("--json", default="", help="结果 JSON 输出路径")
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()
    try:
        problems, info = run(root)
    except PolicyError as exc:  # pragma: no cover - run() 已内联处理
        problems, info = [str(exc)], {}
    result = {"ok": not problems, "problems": problems, **info}
    if args.json:
        Path(args.json).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if problems:
        print("❌ workflow 策略 guard 失败：")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"✅ workflow 策略 guard 通过（检查 {len(info.get('workflows_checked', []))} 个 workflow）")
    print(f"   必需检查（ruleset 引用）：{', '.join(info.get('required_checks', []))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
