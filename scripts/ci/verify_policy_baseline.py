#!/usr/bin/env python3
"""可信基线 guard（只读）：用 PR **base** 版本的 guard 逻辑校验候选版本，并对「削弱」硬失败。

动机：同一个 PR 既改检查规则又靠改后的规则通过，会形成自证循环。因此：
  1. 若变更触及安全敏感路径，则把 **base 版本** 的 `scripts/ci/*` 取出来，
     用 base 逻辑去校验 **候选树** 的 workflow（候选无法用自己的新规则给自己放行）。
  2. 再对「削弱」做硬失败断言（删除必需检查 / 新增提权触发 / 新增直推 main /
     测试范围缩小 / 移除 guard 调用 等）。

只读：不写仓库、不访问网络。base 版本通过 `git archive` 取出到临时目录。

用法：
  python3 scripts/ci/verify_policy_baseline.py --base-sha <sha> --head-sha <sha> \
      [--repo-root .] [--json out.json]
"""
from __future__ import annotations

import argparse
import io
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

SENSITIVE_PREFIXES = (
    ".github/workflows/",
    ".github/required-checks.json",
    "scripts/ci/",
    "scripts/verify_configs.py",
    "scripts/verify_rulesets.py",
    "scripts/readme_stats.py",
    "scripts/lib/policy.py",
    "scripts/lib/validators.py",
    "scripts/batch_update.py",
    "scripts/commit_writer.py",
    "scripts/tests/",
)
REQUIRED_CHECKS_PATH = ".github/required-checks.json"
PR_VERIFY = ".github/workflows/pr-verify.yml"
LEGACY_DIRECT_WRITE = ".github/workflows/daily-sync.yml"
BASE_GUARD = "scripts/ci/verify_workflow_policy.py"
PUSH_MAIN_RE = re.compile(r"git\s+push[^\n]*(?:\bmain\b|\$\{\{\s*github\.ref_name\s*\}\}|HEAD:main)")
TEST_DEF_RE = re.compile(r"^\s*def test_", re.MULTILINE)


class BaselineError(Exception):
    """无法判定 → fail-closed。"""


def git(args: list[str], cwd: Path) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise BaselineError(f"git {' '.join(args)} failed: {proc.stderr.strip()[:300]}")
    return proc.stdout


def git_grep(pattern: str, revision: str, path: str, cwd: Path) -> str:
    """git grep 的容错封装：退出码 1 = 无匹配（返回空串），其他非 0 仍视为失败。"""
    proc = subprocess.run(["git", "grep", "-h", "-E", pattern, revision, "--", path],
                          cwd=cwd, capture_output=True, text=True)
    if proc.returncode == 0:
        return proc.stdout
    if proc.returncode == 1:
        return ""
    raise BaselineError(f"git grep failed: {proc.stderr.strip()[:300]}")


def changed_files(base: str, head: str, root: Path) -> list[str]:
    out = git(["diff", "--name-only", "--diff-filter=ACDMRT", f"{base}..{head}"], root)
    if not out.strip():
        out = git(["diff", "--name-only", "--diff-filter=ACDMRT", f"{base}...{head}"], root)
    return [line.strip() for line in out.splitlines() if line.strip()]


def sensitive(files: list[str]) -> list[str]:
    return [f for f in files if f.startswith(SENSITIVE_PREFIXES)]


def file_at(revision: str, path: str, root: Path) -> str | None:
    proc = subprocess.run(["git", "show", f"{revision}:{path}"], cwd=root, capture_output=True, text=True)
    return proc.stdout if proc.returncode == 0 else None


def extract_base_tree(base: str, root: Path, dest: Path) -> bool:
    """取出 base 版本的 scripts/ci 与 scripts/lib 到临时目录；guard 不存在则返回 False。

    逐路径归档：某个路径在 base 不存在（例如 scripts/lib 曾改名）不应导致整体失败。
    """
    for path in ("scripts/ci", "scripts/lib"):
        proc = subprocess.run(["git", "archive", "--format=tar", base, path],
                              cwd=root, capture_output=True)
        if proc.returncode != 0:
            continue
        with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as tar:
            tar.extractall(dest / "base")
    return (dest / "base" / BASE_GUARD).exists()


def extract_tree(revision: str, root: Path, dest: Path) -> None:
    """把某个 revision 的完整树取出到 dest（用于用 base 逻辑校验候选树）。"""
    proc = subprocess.run(["git", "archive", "--format=tar", revision], cwd=root, capture_output=True)
    if proc.returncode != 0:
        raise BaselineError(f"git archive {revision} failed: {proc.stderr.decode('utf-8', 'replace')[:300]}")
    archive = dest / f"tree-{revision[:12]}.tar"
    archive.write_bytes(proc.stdout)
    with tarfile.open(archive) as tar:
        tar.extractall(dest)


def run_base_guard(dest: Path, candidate_root: Path) -> tuple[bool, str]:
    """用 base 版本的 guard 校验**候选树**的 workflow。"""
    script = dest / "base" / BASE_GUARD
    proc = subprocess.run(
        [sys.executable, str(script), "--root", str(candidate_root)],
        capture_output=True, text=True, cwd=str(candidate_root),
    )
    return proc.returncode == 0, (proc.stdout + proc.stderr).strip()


def check_entries(text: str) -> dict[str, dict]:
    """把必需检查清单解析成 {name: source}，兼容 checks[] 与旧 required[]。

    解析失败一律 fail-closed（BaselineError），不静默降级为「无检查」。
    """
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise BaselineError(f"required-checks manifest unreadable: {exc}") from exc
    if isinstance(doc, dict) and isinstance(doc.get("checks"), list):
        out: dict[str, dict] = {}
        for item in doc["checks"]:
            if isinstance(item, dict) and item.get("name"):
                out[str(item["name"])] = dict(item.get("source") or {})
            elif isinstance(item, str):
                out[item] = {}
        return out
    return {str(n): {} for n in ((doc or {}).get("required") or [])}


def weakening_problems(base: str, head: str, root: Path) -> list[str]:
    problems: list[str] = []

    # 结构化辅助（一律基于解析结果，不认注释里的关键词）
    def triggers_of(text: str) -> set[str]:
        doc = yaml.safe_load(text) or {}
        raw = doc.get("on", doc.get(True))
        if isinstance(raw, str):
            return {raw}
        if isinstance(raw, list):
            return {str(x) for x in raw}
        if isinstance(raw, dict):
            return {str(k) for k in raw}
        return set()

    def write_all(text: str) -> bool:
        doc = yaml.safe_load(text) or {}
        return doc.get("permissions") == "write-all" or any(
            (job or {}).get("permissions") == "write-all" for job in (doc.get("jobs") or {}).values())

    def run_scripts(text: str) -> list[str]:
        doc = yaml.safe_load(text) or {}
        return [str(step.get("run")) for job in (doc.get("jobs") or {}).values()
                for step in ((job or {}).get("steps") or []) if isinstance(step.get("run"), str)]

    def pushes_main(text: str) -> bool:
        return any(PUSH_MAIN_RE.search(line) for run in run_scripts(text) for line in run.splitlines())

    # 必需检查集合与**来源绑定**不得被削弱
    base_req = file_at(base, REQUIRED_CHECKS_PATH, root)
    head_req = file_at(head, REQUIRED_CHECKS_PATH, root)
    if base_req is not None:
        base_checks = check_entries(base_req)
        if head_req is None:
            problems.append(f"{REQUIRED_CHECKS_PATH}: removed (required checks must not disappear)")
        else:
            head_checks = check_entries(head_req)
            for missing in sorted(set(base_checks) - set(head_checks)):
                problems.append(f"{REQUIRED_CHECKS_PATH}: required check {missing!r} removed")
            for name, base_src in sorted(base_checks.items()):
                if name not in head_checks:
                    continue
                head_src = head_checks[name]
                if base_src.get("app_id") is not None:
                    if head_src.get("app_id") != base_src.get("app_id") or \
                            head_src.get("app_slug") != base_src.get("app_slug"):
                        problems.append(
                            f"{REQUIRED_CHECKS_PATH}: {name} 的预期来源被改变/移除"
                            f"（base={base_src} head={head_src}）——来源绑定不得削弱")
                elif not head_src:
                    problems.append(f"{REQUIRED_CHECKS_PATH}: {name} 缺少来源绑定（source）")

    # pr-verify job 不得删除；job 名必须稳定；必需 job 不得获得可跳过的 job-level if
    base_prv = file_at(base, PR_VERIFY, root)
    head_prv = file_at(head, PR_VERIFY, root)
    if base_prv is not None and head_prv is not None:
        import yaml  # 局部导入：仅此处需要

        def job_ids(text: str) -> dict[str, object]:
            doc = yaml.safe_load(text) or {}
            return {str(k): (v or {}).get("name") for k, v in (doc.get("jobs") or {}).items()}

        def job_conditions(text: str) -> dict[str, str]:
            doc = yaml.safe_load(text) or {}
            out: dict[str, str] = {}
            for key, job in (doc.get("jobs") or {}).items():
                value = (job or {}).get("if")
                # 注意：`if: false` 会被 YAML 解析成布尔 False，`False or ""` 会丢信息，
                # 因此必须显式判 None。
                out[str(key)] = "" if value is None else str(value)
            return out

        base_jobs, head_jobs = job_ids(base_prv), job_ids(head_prv)
        for job in sorted(set(base_jobs) - set(head_jobs)):
            problems.append(f"{PR_VERIFY}: job {job!r} removed")
        for job, name in head_jobs.items():
            if name != job:
                problems.append(f"{PR_VERIFY}: job {job!r} name changed to {name!r} (check name must stay stable)")
        base_conds, head_conds = job_conditions(base_prv), job_conditions(head_prv)
        for job, cond in head_conds.items():
            if cond and not base_conds.get(job):
                problems.append(f"{PR_VERIFY}: job {job!r} 新增 job-level if ({cond!r}) —— "
                                f"必需检查不得变得可被跳过（跳过的检查可能被算作成功）")
        if "workflow_dispatch" not in triggers_of(base_prv) and "workflow_dispatch" in triggers_of(head_prv):
            problems.append(f"{PR_VERIFY}: 新增 workflow_dispatch —— dispatch 触发的检查不满足 "
                            f"ruleset 必需检查（官方文档）")

    # 禁止新增提权触发 / write-all / 直推 main —— 一律基于**解析后的结构**判定，
    # 不接受「注释里出现关键词」这种脆弱信号。
    for path in changed_files(base, head, root):
        if not path.endswith((".yml", ".yaml")):
            continue
        before = file_at(base, path, root) or ""
        after = file_at(head, path, root)
        if after is None:
            continue
        if path == LEGACY_DIRECT_WRITE:
            # 旧直推 workflow：任何修改都视为「重新武装」风险（内容同时被策略 guard 的
            # sha256 pin 锁定），不依赖关键词出现。
            problems.append(f"{path}: legacy direct-write workflow must never be modified or re-armed")
            continue
        if "pull_request_target" not in triggers_of(before) and "pull_request_target" in triggers_of(after):
            problems.append(f"{path}: adds pull_request_target trigger (forbidden)")
        if not write_all(before) and write_all(after):
            problems.append(f"{path}: adds permissions: write-all (forbidden)")
        if not pushes_main(before) and pushes_main(after):
            problems.append(f"{path}: adds a push to main (forbidden)")

    # 测试范围不得缩小
    base_tests = sorted(p for p in changed_files(base, head, root) if p.startswith("scripts/tests/"))
    deleted_tests = [p for p in base_tests if file_at(head, p, root) is None]
    if deleted_tests:
        problems.append(f"scripts/tests: test files deleted: {sorted(deleted_tests)}")
    base_count = len(TEST_DEF_RE.findall(git_grep(r"^\s*def test_", base, "scripts/tests", root)))
    head_count = len(TEST_DEF_RE.findall(git_grep(r"^\s*def test_", head, "scripts/tests", root)))
    if head_count < base_count:
        problems.append(f"scripts/tests: test case count shrank ({base_count} -> {head_count})")

    # guard 调用不得被移除
    if head_prv is not None:
        for script in ("scripts/ci/verify_general_pr.py", BASE_GUARD):
            if script not in head_prv:
                problems.append(f"{PR_VERIFY}: guard invocation {script} missing")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="可信基线 guard（只读）")
    ap.add_argument("--base-sha", required=True)
    ap.add_argument("--head-sha", required=True)
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--json", default="")
    args = ap.parse_args(argv)
    root = Path(args.repo_root).resolve()
    problems: list[str] = []
    info: dict = {}
    try:
        files = changed_files(args.base_sha, args.head_sha, root)
        touched = sensitive(files)
        info = {"base_sha": args.base_sha, "head_sha": args.head_sha,
                "changed_files": len(files), "sensitive_files": touched}
        if not touched:
            info["base_guard_applied"] = False
            info["note"] = "无安全敏感路径变更；仅执行削弱断言"
        else:
            with tempfile.TemporaryDirectory() as tmp:
                dest = Path(tmp)
                have_base_guard = extract_base_tree(args.base_sha, root, dest)
                info["base_guard_applied"] = have_base_guard
                if have_base_guard:
                    candidate = dest / "candidate"
                    candidate.mkdir()
                    extract_tree(args.head_sha, root, candidate)
                    ok, output = run_base_guard(dest, candidate)
                    info["base_guard_output_tail"] = output[-2000:]
                    if not ok:
                        problems.append(
                            "base 版本 guard 判定候选树不合规（候选不得用自身新规则给自己放行）：\n" + output[-1200:]
                        )
                else:
                    info["base_guard_note"] = "base 版本尚无该 guard（本轮首次引入），改用内置削弱断言"
        problems.extend(weakening_problems(args.base_sha, args.head_sha, root))
    except BaselineError as exc:
        problems.append(f"fail-closed: {exc}")
    result = {"ok": not problems, "problems": problems, **info}
    if args.json:
        Path(args.json).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if problems:
        print("❌ 可信基线 guard 失败：")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"✅ 可信基线 guard 通过（变更 {info.get('changed_files', 0)} 个文件，"
          f"安全敏感 {len(info.get('sensitive_files', []))} 个；base 逻辑校验={info.get('base_guard_applied')}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
