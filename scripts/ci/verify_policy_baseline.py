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


def weakening_problems(base: str, head: str, root: Path) -> list[str]:
    problems: list[str] = []

    # 必需检查集合不得缩小
    base_req = file_at(base, REQUIRED_CHECKS_PATH, root)
    head_req = file_at(head, REQUIRED_CHECKS_PATH, root)
    if base_req is not None:
        try:
            base_names = set(json.loads(base_req).get("required") or [])
        except json.JSONDecodeError as exc:
            raise BaselineError(f"base {REQUIRED_CHECKS_PATH} unreadable: {exc}") from exc
        if head_req is None:
            problems.append(f"{REQUIRED_CHECKS_PATH}: removed (required checks must not disappear)")
        else:
            try:
                head_names = set(json.loads(head_req).get("required") or [])
            except json.JSONDecodeError as exc:
                raise BaselineError(f"head {REQUIRED_CHECKS_PATH} unreadable: {exc}") from exc
            for missing in sorted(base_names - head_names):
                problems.append(f"{REQUIRED_CHECKS_PATH}: required check {missing!r} removed")

    # pr-verify job 不得删除；job 名必须稳定
    base_prv = file_at(base, PR_VERIFY, root)
    head_prv = file_at(head, PR_VERIFY, root)
    if base_prv is not None and head_prv is not None:
        import yaml  # 局部导入：仅此处需要

        def job_ids(text: str) -> dict[str, object]:
            doc = yaml.safe_load(text) or {}
            return {str(k): (v or {}).get("name") for k, v in (doc.get("jobs") or {}).items()}

        base_jobs, head_jobs = job_ids(base_prv), job_ids(head_prv)
        for job in sorted(set(base_jobs) - set(head_jobs)):
            problems.append(f"{PR_VERIFY}: job {job!r} removed")
        for job, name in head_jobs.items():
            if name != job:
                problems.append(f"{PR_VERIFY}: job {job!r} name changed to {name!r} (check name must stay stable)")

    # 禁止新增提权触发 / write-all / 直推 main
    base_files = changed_files(base, head, root)
    for path in base_files:
        if not path.endswith((".yml", ".yaml")):
            continue
        before = file_at(base, path, root) or ""
        after = file_at(head, path, root)
        if after is None:
            continue
        if "pull_request_target" not in before and "pull_request_target" in after:
            problems.append(f"{path}: adds pull_request_target (forbidden)")
        if "write-all" not in before and "write-all" in after:
            problems.append(f"{path}: adds permissions: write-all (forbidden)")
        if not PUSH_MAIN_RE.search(before) and PUSH_MAIN_RE.search(after):
            problems.append(f"{path}: adds a push to main (forbidden)")
        if path == LEGACY_DIRECT_WRITE:
            if "contents: write" in after or PUSH_MAIN_RE.search(after):
                problems.append(f"{path}: legacy direct-write workflow must only be weakened, never re-armed")

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
