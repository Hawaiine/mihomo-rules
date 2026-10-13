#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""workflow 策略 guard（只读，基于**解析后的结构**而非关键词出现）。

设计原则：不接受「注释里出现了 CANDIDATE_SYNC_ENABLED 就算门控正确」这类脆弱判定。
所有断言都作用在 YAML 解析结果（job 的 `if`、permissions、needs、steps 顺序）上。

检查项（任一失败即 exit 1）：
  P1  pull_request_target 一律禁止。
  P2  必须显式声明 permissions；禁止 write-all；写权限 scope 必须在允许集合内。
  P3  带 schedule 触发且持写权限的 job 必须被**真实条件表达式**门控
      （job-level `if` 引用 needs.gate.outputs.enabled 或 vars.CANDIDATE_SYNC_ENABLED）。
  P3b 旧直推 workflow daily-sync.yml 必须保持**逐字节不变**（pin sha256）：禁止重新武装。
  P4  pr-verify.yml 的 job 集合必须与必需检查清单一致，job name == job id；
      必需检查 job **不得**有 job-level `if`，其验证步骤不得带可跳过的 `if`。
  P4b 必需检查清单必须声明预期来源（GitHub Actions integration：app_slug/app_id）。
  P5  自动合并必须：同 job 内先执行 verify_required_checks.py（且在 mutation 之前），
      权限恰为 {contents: read, pull-requests: write, checks: read}。
  P6  候选更新 workflow：顶层只读；publish 不得持有 actions: write、不得检出 PR head、
      不得执行候选内容（apply 之后不得运行仓库脚本）；禁止任何 push main 路径。
  P7  检出 PR head 的 job 不得同时持有 contents: write。
  P8  每个 checkout 必须 persist-credentials: false。
  P9  pr-verify.yml 必须由 pull_request 触发，且**不得**声明 workflow_dispatch
      （官方文档：dispatch 触发的检查不满足 ruleset 必需检查）。
  P10 pr-verify.yml 的 permissions 恰为 contents: read。
  P11 禁止直接改写 main 的常见路径（gh api/curl 改 refs、gh pr merge、push main、强制推送）。
  P12 图标仓库检出必须使用已批准 pin（不得用分支/标签）。

用法：
  python3 scripts/ci/verify_workflow_policy.py [--root <仓库根>] [--json <输出路径>]
"""
from __future__ import annotations

import argparse
import hashlib
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
AUTOMERGE_GATE = "CANDIDATE_AUTOMERGE_ENABLED"
GATE_JOB = "gate"
GATE_OUTPUT = "needs.gate.outputs.enabled"

# 旧直推 workflow 的固定内容（sha256）。任何改动（包括「重新启用写入」）都必须显式修改
# 本常量——该修改会出现在 PR diff 中并接受审阅，而不是悄悄生效。
PINNED_LEGACY_DAILY_SYNC_SHA256 = "da5c128fcb301138441d405e3443ca68d828067f1a2a371483bd80007b087951"

# 预期 check 来源：GitHub Actions integration。
# 依据：GET /apps/github-actions → {"id": 15368, "slug": "github-actions", "owner": {"login": "github"}}
# （2026-10-13 独立核实）。创建 ruleset 时每个 required check 必须绑定该 integration。
EXPECTED_CHECK_APP_SLUG = "github-actions"
EXPECTED_CHECK_APP_ID = 15368

PINNED_ICONS_SHA = "f0f3bc2a44616885682ee5f0e5921540b964e2d8"
ICONS_REPO = "Hawaiine/Oasisic-Icons"

PR_VERIFY_WRITE_ALLOW: dict[str, str] = {"contents": "read"}
AUTOMERGE_WRITE_ALLOW: dict[str, str] = {"contents": "read", "pull-requests": "write", "checks": "read"}
PUBLISH_WRITE_ALLOW: dict[str, str] = {"contents": "read"}

AUTOMERGE_MARKERS = ("enablePullRequestAutoMerge", "request_automerge.py", "/auto_merge")
REQUIRED_CHECKS_CALL = "scripts/ci/verify_required_checks.py"
AWAIT_CALL = "scripts/ci/await_checks.py"
AUTOMERGE_CALL = "scripts/ci/request_automerge.py"

# 直接改写 main 的常见路径（结构无关的显式禁止）
DIRECT_MAIN_PATTERNS = (
    (re.compile(r"git\s+push[^\n]*(?:\bmain\b|HEAD:main|\$\{\{\s*github\.ref_name\s*\}\})"),
     "git push 到 main / 动态 ref"),
    (re.compile(r"git\s+push[^\n]*--force(?![-\w])"), "git push --force（仅允许 --force-with-lease 到候选分支）"),
    (re.compile(r"gh\s+api[^\n]*(-X|--method)\s*(PATCH|PUT|POST|DELETE)[^\n]*refs"), "gh api 改写 git refs"),
    (re.compile(r"curl[^\n]*(-X|--request)\s*(PATCH|PUT|POST|DELETE)[^\n]*/git/refs"), "REST 改写 git refs"),
    (re.compile(r"gh\s+pr\s+merge\b"), "gh pr merge（绕过 ruleset 的合并路径）"),
    (re.compile(r"/pulls/[^\n]*/merge"), "REST merge 端点"),
)
CANDIDATE_PUSH_RE = re.compile(r"git\s+push[^\n]*refs/heads/\$(?:\{)?CANDIDATE_BRANCH(?:\})?")
REPO_SCRIPT_RE = re.compile(r"(?:^|[\s;&|])(?:python3?|bash|sh|\./)\s*[\"']?(?:\./)?scripts/")


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


def _write_scopes(perm: Any) -> set[str]:
    if isinstance(perm, str):
        return {"*"} if perm == "write-all" else set()
    if isinstance(perm, dict):
        return {k for k, v in perm.items() if v == "write"}
    return set()


def _perm_map(perm: Any) -> dict[str, str]:
    return dict(perm) if isinstance(perm, dict) else ({} if perm is None else {"__raw__": str(perm)})


def _job_if(job: dict) -> str:
    """job-level `if` 的**解析后**内容（注释不可能出现在这里）。"""
    val = job.get("if")
    if val is None:
        return ""
    return str(val)


def _steps(job: dict) -> list[dict]:
    return [s for s in (job.get("steps") or []) if isinstance(s, dict)]


def _step_text(step: dict) -> str:
    out = str(step.get("run") or "")
    with_block = step.get("with") or {}
    out += " " + json.dumps(with_block, ensure_ascii=False)
    out += " " + str(step.get("uses") or "")
    return out


def _run_scripts(doc: dict) -> list[str]:
    return [str(s.get("run")) for job in (doc.get("jobs") or {}).values() for s in _steps(job)
            if isinstance(s.get("run"), str)]


def _checkout_steps(doc: dict) -> list[dict]:
    return [s for job in (doc.get("jobs") or {}).values() for s in _steps(job)
            if str(s.get("uses") or "").startswith("actions/checkout")]


def _is_pr_head_checkout(step: dict) -> bool:
    return "pull_request.head.sha" in json.dumps(step.get("with") or {}, ensure_ascii=False)


def check_workflow(path: Path, text: str, doc: dict, required: list[str],
                   waive_legacy_patterns: bool = False) -> list[str]:
    """单个 workflow 的策略检查（结构判定）。

    `waive_legacy_patterns` 仅用于**已按 sha256 固定**的旧直推 workflow（daily-sync.yml）：
    它的内容由 P3b 的逐字节 pin 保证不变，因此不再对它重复施加 P8/P11 的内容形态规则
    （它本来就是直推形态）。该豁免只在 pin 校验通过时生效，且在报告里显式列出。
    """
    problems: list[str] = []
    name = path.as_posix()
    triggers = _triggers(doc)
    jobs = doc.get("jobs") or {}
    top_perm = _permissions(doc)
    is_pr_verify = path.name == Path(PR_VERIFY).name
    is_candidate = path.name == Path(CANDIDATE_SYNC).name
    is_legacy = path.name == Path(LEGACY_DIRECT_WRITE).name

    # P1
    if "pull_request_target" in triggers:
        problems.append(f"{name}: pull_request_target 一律禁止")

    # P2
    if top_perm is None and not all("permissions" in j for j in jobs.values()):
        problems.append(f"{name}: 必须显式声明 permissions（顶层或每个 job）")
    if isinstance(top_perm, str) and top_perm == "write-all":
        problems.append(f"{name}: permissions: write-all 禁止")

    # P3：定时 + 写权限 ⇒ 必须被真实条件表达式门控（不认注释）
    if "schedule" in triggers and not is_legacy:
        for jid, job in jobs.items():
            if not _write_scopes(_permissions(doc, job)):
                continue
            cond = _job_if(job)
            if GATE_OUTPUT not in cond and f"vars.{ENABLE_GATE}" not in cond:
                problems.append(
                    f"{name}: job '{jid}' 定时触发且持有写权限，其 job-level if 必须门控于 "
                    f"vars.{ENABLE_GATE} / {GATE_OUTPUT}（实际 if={cond!r}）")

    # P4b：必需检查清单
    # （由 check_required_checks_file 负责，此处仅校验 job 侧一致性）

    # P4：pr-verify 结构
    if is_pr_verify:
        ids = sorted(jobs)
        if ids != sorted(required):
            problems.append(f"{name}: job ids {ids} 必须等于必需检查 {sorted(required)}")
        for jid, job in jobs.items():
            if job.get("name") != jid:
                problems.append(f"{name}: job '{jid}' 必须设置 name: {jid}（check 名必须稳定）")
            # 必需检查 job 不得有 job-level if（否则可能被跳过；跳过在状态检查体系中可能算成功）
            if _job_if(job):
                problems.append(f"{name}: 必需检查 job '{jid}' 不得设置 job-level if（可能跳过检查）"
                                f"（实际 if={_job_if(job)!r}）")
            for step in _steps(job):
                run = str(step.get("run") or "")
                if REPO_SCRIPT_RE.search(run) and step.get("if") is not None:
                    problems.append(f"{name}: job '{jid}' 的验证步骤不得带 if 条件（可能跳过验证）"
                                    f"（step={step.get('name')!r} if={step.get('if')!r}）")
            # 必需检查 job 不得被掏空（缩小验证范围）：必须至少有一个真正执行验证命令的步骤
            if not any(re.search(r"\b(python3?|bash|sh)\b", str(s.get("run") or "")) for s in _steps(job)):
                problems.append(f"{name}: 必需检查 job '{jid}' 必须至少有一个执行验证命令的步骤"
                                f"（不得缩小验证范围至空 job）")

        # P9：必须由 pull_request 触发，且不得声明 workflow_dispatch
        if "pull_request" not in triggers:
            problems.append(f"{name}: 必须由 pull_request 触发（ruleset 必需检查只能由该事件产生）")
        if "workflow_dispatch" in triggers:
            problems.append(
                f"{name}: 不得声明 workflow_dispatch（官方文档：dispatch 触发的检查不出现在 PR checks 中、"
                f"不满足 ruleset 必需检查）")

        # P10：权限恰为 contents: read
        if _perm_map(top_perm) != PR_VERIFY_WRITE_ALLOW:
            problems.append(f"{name}: permissions 必须恰为 {PR_VERIFY_WRITE_ALLOW}（实际 {top_perm!r}）")

    # P5：自动合并（结构：同 job 内先验证，权限恰为允许集合）
    for jid, job in jobs.items():
        job_text = " ".join(_step_text(s) for s in _steps(job))
        if not any(m in job_text for m in AUTOMERGE_MARKERS):
            continue
        runs = [str(s.get("run") or "") for s in _steps(job)]
        idx_verify = next((i for i, r in enumerate(runs) if REQUIRED_CHECKS_CALL in r), None)
        idx_mutate = next((i for i, s in enumerate(_steps(job))
                           if any(m in _step_text(s) for m in AUTOMERGE_MARKERS)), None)
        if idx_verify is None:
            problems.append(f"{name}: job '{jid}' 启用 auto-merge 前必须调用 {REQUIRED_CHECKS_CALL}")
        elif idx_mutate is not None and idx_verify > idx_mutate:
            problems.append(f"{name}: job '{jid}' 的必需检查验证必须位于 auto-merge mutation 之前")
        if AWAIT_CALL not in job_text:
            problems.append(f"{name}: job '{jid}' 必须先调用 {AWAIT_CALL}（有界等待，不得在检查完成前请求合并）")
        if AUTOMERGE_CALL not in job_text:
            problems.append(f"{name}: job '{jid}' 必须通过 {AUTOMERGE_CALL} 请求 auto-merge（严格三态区分）")
        perms = _perm_map(_permissions(doc, job))
        if perms != AUTOMERGE_WRITE_ALLOW:
            problems.append(f"{name}: job '{jid}' permissions 必须恰为 {AUTOMERGE_WRITE_ALLOW}（实际 {perms!r}）")

    # P6：候选更新 workflow
    if is_candidate:
        if _write_scopes(top_perm):
            problems.append(f"{name}: 候选更新 workflow 顶层必须只读")
        if "actions: write" in text:
            problems.append(f"{name}: 不得声明 actions: write")
        publish = jobs.get("publish") or {}
        pperms = _perm_map(_permissions(doc, publish))
        if pperms != PUBLISH_WRITE_ALLOW:
            problems.append(f"{name}: publish 权限必须恰为 {PUBLISH_WRITE_ALLOW}（写操作交给 App token；"
                            f"实际 {pperms!r}）")
        # publish 不得检出 PR head
        for step in _steps(publish):
            if _is_pr_head_checkout(step):
                problems.append(f"{name}: publish 不得检出 PR head（只应检出可信基线）")
        # publish 不得执行候选内容：apply 补丁之后的步骤不得运行仓库脚本
        apply_idx = next((i for i, s in enumerate(_steps(publish))
                          if "git apply" in str(s.get("run") or "")), None)
        if apply_idx is not None:
            for step in _steps(publish)[apply_idx + 1:]:
                if REPO_SCRIPT_RE.search(str(step.get("run") or "")):
                    problems.append(
                        f"{name}: publish 在应用候选补丁后不得执行仓库脚本（避免执行候选内容）："
                        f"step={step.get('name')!r}")
        # 自动合并 job 必须同时受两个 gate 约束
        am_job = jobs.get("request-auto-merge") or {}
        cond = _job_if(am_job)
        if GATE_OUTPUT not in cond and f"vars.{ENABLE_GATE}" not in cond:
            problems.append(f"{name}: request-auto-merge 必须门控于 {GATE_OUTPUT}/vars.{ENABLE_GATE}"
                            f"（实际 if={cond!r}）")
        if f"vars.{AUTOMERGE_GATE}" not in cond:
            problems.append(f"{name}: request-auto-merge 必须由独立的 vars.{AUTOMERGE_GATE} 二次门控"
                            f"（实际 if={cond!r}）")
        # 任何 git push 必须指向候选分支变量
        for run in _run_scripts(doc):
            for line in run.splitlines():
                if "git push" in line and not CANDIDATE_PUSH_RE.search(line):
                    problems.append(f"{name}: git push 必须显式指向 refs/heads/$CANDIDATE_BRANCH：{line.strip()!r}")

    # P7
    for jid, job in jobs.items():
        if any(_is_pr_head_checkout(s) for s in _steps(job)) and \
                "write" in _perm_map(_permissions(doc, job)).get("contents", ""):
            problems.append(f"{name}: job '{jid}' 在检出 PR head 的同时持有 contents: write")

    # P8（旧直推 workflow 由 pin 保证内容不变，见 waive_legacy_patterns）
    if not waive_legacy_patterns:
        for step in _checkout_steps(doc):
            with_block = step.get("with") or {}
            if with_block.get("persist-credentials") is not False:
                problems.append(f"{name}: actions/checkout 必须设置 persist-credentials: false"
                                f"（step={step.get('name')!r}）")

    # P12：图标仓库必须 pin
    for step in _checkout_steps(doc):
        with_block = step.get("with") or {}
        if with_block.get("repository") == ICONS_REPO:
            ref = str(with_block.get("ref") or "")
            if ref != PINNED_ICONS_SHA:
                problems.append(f"{name}: {ICONS_REPO} 检出必须使用已批准 pin {PINNED_ICONS_SHA}"
                                f"（实际 ref={ref!r}）")

    # P11：直接改写 main 的路径（全部 workflow 生效；旧直推 workflow 由 pin 保证不变）
    if not waive_legacy_patterns:
        for run in _run_scripts(doc):
            for pattern, why in DIRECT_MAIN_PATTERNS:
                for line in run.splitlines():
                    if pattern.search(line):
                        problems.append(f"{name}: 禁止直接改写 main 的路径（{why}）：{line.strip()!r}")
    return problems


def check_required_checks_file(path: Path) -> tuple[list[str], list[str], list[dict]]:
    """校验必需检查清单（含预期来源绑定）。返回 (problems, names, checks)。"""
    problems: list[str] = []
    if not path.exists():
        return [f"{REQUIRED_CHECKS_PATH}: 缺少必需检查清单"], [], []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"{REQUIRED_CHECKS_PATH}: JSON 非法：{exc}"], [], []
    if not isinstance(data, dict):
        return [f"{REQUIRED_CHECKS_PATH}: 顶层必须是对象"], [], []
    if data.get("workflow") not in (None, PR_VERIFY):
        problems.append(f"{REQUIRED_CHECKS_PATH}: workflow 必须为 {PR_VERIFY}")
    checks_raw = data.get("checks")
    names: list[str] = []
    checks: list[dict] = []
    if isinstance(checks_raw, list) and checks_raw:
        for item in checks_raw:
            if isinstance(item, str):
                problems.append(f"{REQUIRED_CHECKS_PATH}: 检查项必须声明 source（禁止仅字符串形态）：{item!r}")
                continue
            if not isinstance(item, dict) or not item.get("name"):
                problems.append(f"{REQUIRED_CHECKS_PATH}: 检查项结构非法：{item!r}")
                continue
            cname = str(item["name"])
            names.append(cname)
            src = item.get("source") or {}
            if src.get("app_slug") != EXPECTED_CHECK_APP_SLUG or src.get("app_id") != EXPECTED_CHECK_APP_ID:
                problems.append(
                    f"{REQUIRED_CHECKS_PATH}: {cname} 的 source 必须为 "
                    f"{{'app_slug': {EXPECTED_CHECK_APP_SLUG!r}, 'app_id': {EXPECTED_CHECK_APP_ID}}}"
                    f"（实际 {src!r}）")
            if item.get("job_id") != cname:
                problems.append(f"{REQUIRED_CHECKS_PATH}: {cname} 的 job_id 必须等于 name")
            if item.get("workflow") not in (None, PR_VERIFY):
                problems.append(f"{REQUIRED_CHECKS_PATH}: {cname} 的 workflow 必须为 {PR_VERIFY}")
            checks.append(dict(item))
    else:
        legacy = data.get("required")
        if isinstance(legacy, list) and legacy and all(isinstance(x, str) for x in legacy):
            problems.append(f"{REQUIRED_CHECKS_PATH}: 旧形态 required[] 缺少来源绑定，必须迁移为 checks[]+source")
            names = list(legacy)
        else:
            problems.append(f"{REQUIRED_CHECKS_PATH}: 需要非空 checks[] 列表（每项含 name/source）")
    if len(set(names)) != len(names):
        problems.append(f"{REQUIRED_CHECKS_PATH}: 检查名重复")
    return problems, names, checks


def check_legacy_daily_sync(root: Path) -> list[str]:
    """P3b：旧直推 workflow 必须逐字节不变（pin sha256）。"""
    path = root / LEGACY_DIRECT_WRITE
    if not path.exists():
        return []  # 删除它也是「不重新启用」，且会出现在 diff 中
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != PINNED_LEGACY_DAILY_SYNC_SHA256:
        return [f"{LEGACY_DIRECT_WRITE}: 内容与固定 pin 不一致（sha256 {digest} != "
                f"{PINNED_LEGACY_DAILY_SYNC_SHA256}）——旧直推 main workflow 禁止被修改/重新武装"]
    return []


def run(root: Path) -> tuple[list[str], dict]:
    problems, required, checks = check_required_checks_file(root / REQUIRED_CHECKS_PATH)
    problems.extend(check_legacy_daily_sync(root))
    legacy_path = root / LEGACY_DIRECT_WRITE
    legacy_pinned = (legacy_path.exists()
                     and hashlib.sha256(legacy_path.read_bytes()).hexdigest() == PINNED_LEGACY_DAILY_SYNC_SHA256)
    wf_dir = root / WORKFLOW_DIR
    files = sorted(wf_dir.glob("*.yml")) + sorted(wf_dir.glob("*.yaml"))
    if not files:
        problems.append(f"{WORKFLOW_DIR}: 未找到 workflow 文件")
    checked: list[str] = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        try:
            doc = _load_yaml(path)
        except PolicyError as exc:
            problems.append(str(exc))
            continue
        if not isinstance(doc, dict):
            problems.append(f"{WORKFLOW_DIR}/{path.name}: workflow 必须是映射")
            continue
        checked.append(path.name)
        problems.extend(check_workflow(path.relative_to(root), text, doc, required,
                                       waive_legacy_patterns=(legacy_pinned and path == legacy_path)))
    info = {"workflows_checked": checked, "required_checks": required,
            "expected_check_source": {"app_slug": EXPECTED_CHECK_APP_SLUG, "app_id": EXPECTED_CHECK_APP_ID},
            "legacy_daily_sync_pin": PINNED_LEGACY_DAILY_SYNC_SHA256,
            "legacy_daily_sync_pinned_ok": legacy_pinned,
            "legacy_content_rules_waived": (["P8", "P11"] if legacy_pinned else []),
            "legacy_waiver_reason": ("daily-sync.yml 由 sha256 pin（P3b）保证逐字节不变、平台侧已禁用，"
                                     "故不再重复施加内容形态规则；一旦内容变化，P3b 立即失败且内容规则重新生效"
                                     if legacy_pinned else None)}
    return problems, info


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="workflow 策略 guard（只读，结构化判定）")
    ap.add_argument("--root", default=".", help="仓库根目录")
    ap.add_argument("--json", default="", help="结果 JSON 输出路径")
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()
    try:
        problems, info = run(root)
    except PolicyError as exc:  # pragma: no cover
        problems, info = [str(exc)], {}
    result = {"ok": not problems, "problems": problems, **info}
    if args.json:
        Path(args.json).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if problems:
        print("❌ workflow 策略 guard 失败：")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"✅ workflow 策略 guard 通过（检查 {len(info.get('workflows_checked', []))} 个 workflow，结构化判定）")
    print(f"   必需检查（ruleset 引用）：{', '.join(info.get('required_checks', []))}")
    print(f"   预期来源：{info.get('expected_check_source')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
