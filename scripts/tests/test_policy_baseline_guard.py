"""可信基线 guard 的定向测试（scripts/ci/verify_policy_baseline.py）。

在临时 git 仓库里构造 base/head 两个提交，验证：
  * 非安全敏感变更不误报。
  * 削弱行为硬失败：删除必需检查、删除 job、job 名漂移、新增 pull_request_target、
    新增直推 main、旧直推 workflow 被重新武装、删除测试文件、测试用例数缩小。
  * **base 版本 guard 逻辑确实被用来校验候选树**（候选无法用自身新规则给自己放行）。
  * CLI 退出码：合规 0、违规 1。

不触碰真实仓库与网络。
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

import verify_policy_baseline as vb  # noqa: E402

SCRIPT = ROOT / "scripts" / "ci" / "verify_policy_baseline.py"

PR_VERIFY = """name: General PR verification
on:
  pull_request:
    branches: [main]
permissions:
  contents: read
jobs:
  general-pr-verify:
    name: general-pr-verify
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - run: python3 scripts/ci/verify_general_pr.py
  workflow-policy-guard:
    name: workflow-policy-guard
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - run: python3 scripts/ci/verify_workflow_policy.py
  trusted-baseline-guard:
    name: trusted-baseline-guard
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
"""

LEGACY = """name: 每日规则同步
on:
  schedule:
    - cron: '0 22 * * *'
permissions:
  contents: write
jobs:
  sync:
    runs-on: ubuntu-latest
    steps:
      - run: git push origin HEAD:${GITHUB_REF_NAME}
"""

REQUIRED = json.dumps({
    "schema": "mihomo-rules-required-checks",
    "version": 1,
    "workflow": ".github/workflows/pr-verify.yml",
    "required": ["general-pr-verify", "workflow-policy-guard", "trusted-baseline-guard"],
}, ensure_ascii=False)

BASE_GUARD = """#!/usr/bin/env python3
import argparse, sys
from pathlib import Path
ap = argparse.ArgumentParser(); ap.add_argument("--root", default=".")
a = ap.parse_args()
bad = []
for p in sorted((Path(a.root) / ".github" / "workflows").glob("*.yml")):
    t = p.read_text(encoding="utf-8")
    if "pull_request_target" in t:
        bad.append(p.name)
if bad:
    print("base guard: forbidden pull_request_target in", bad); sys.exit(1)
print("base guard ok"); sys.exit(0)
"""


def _run(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    if proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {proc.stderr}")
    return proc.stdout


def write_files(repo: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def make_repo(base_files: dict[str, str], head_files: dict[str, str]) -> tuple[Path, str, str]:
    repo = Path(tempfile.mkdtemp())
    _run(repo, "init", "-q")
    _run(repo, "config", "user.email", "t@example.invalid")
    _run(repo, "config", "user.name", "tester")
    _run(repo, "config", "commit.gpgsign", "false")
    write_files(repo, base_files)
    _run(repo, "add", "-A")
    _run(repo, "commit", "-q", "-m", "base")
    base = _run(repo, "rev-parse", "HEAD").strip()
    write_files(repo, head_files)
    _run(repo, "add", "-A")
    _run(repo, "commit", "-q", "-m", "head")
    head = _run(repo, "rev-parse", "HEAD").strip()
    return repo, base, head


def base_files() -> dict[str, str]:
    return {
        ".github/workflows/pr-verify.yml": PR_VERIFY,
        ".github/workflows/daily-sync.yml": LEGACY,
        ".github/required-checks.json": REQUIRED,
        "scripts/ci/verify_workflow_policy.py": BASE_GUARD,
        "scripts/tests/test_dummy.py": "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_one(self):\n        self.assertTrue(True)\n",
    }


class PolicyBaselineGuardTest(unittest.TestCase):
    def problems(self, head_overrides: dict[str, str]) -> list[str]:
        repo, base, head = make_repo(base_files(), head_overrides)
        return vb.weakening_problems(base, head, repo)

    def test_non_sensitive_change_has_no_problems(self) -> None:
        self.assertEqual(self.problems({"README.md": "hello\n"}), [])

    def test_required_check_removal_fails(self) -> None:
        weakened = json.dumps({**json.loads(REQUIRED), "required": ["general-pr-verify"]}, ensure_ascii=False)
        problems = self.problems({".github/required-checks.json": weakened})
        self.assertTrue(any("required check" in p and "removed" in p for p in problems), problems)

    def test_job_removal_fails(self) -> None:
        trimmed = PR_VERIFY.replace("""  trusted-baseline-guard:
    name: trusted-baseline-guard
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
""", "")
        problems = self.problems({".github/workflows/pr-verify.yml": trimmed})
        self.assertTrue(any("job 'trusted-baseline-guard' removed" in p for p in problems), problems)

    def test_job_name_drift_fails(self) -> None:
        drifted = PR_VERIFY.replace("    name: workflow-policy-guard", "    name: policy-guard")
        problems = self.problems({".github/workflows/pr-verify.yml": drifted})
        self.assertTrue(any("must stay stable" in p for p in problems), problems)

    def test_added_pull_request_target_fails(self) -> None:
        bad = PR_VERIFY.replace("  pull_request:\n", "  pull_request_target:\n")
        problems = self.problems({".github/workflows/pr-verify.yml": bad})
        self.assertTrue(any("adds pull_request_target" in p for p in problems), problems)

    def test_added_push_to_main_fails(self) -> None:
        bad = PR_VERIFY.replace("      - uses: actions/checkout@v7\n",
                                "      - uses: actions/checkout@v7\n      - run: git push origin HEAD:main\n", 1)
        problems = self.problems({".github/workflows/pr-verify.yml": bad})
        self.assertTrue(any("adds a push to main" in p for p in problems), problems)

    def test_legacy_direct_write_rearm_fails(self) -> None:
        problems = self.problems({".github/workflows/daily-sync.yml": LEGACY + "# touched\n"})
        self.assertTrue(any("only be weakened, never re-armed" in p for p in problems), problems)

    def test_legacy_weakening_is_allowed(self) -> None:
        weakened = LEGACY.replace("permissions:\n  contents: write\n", "permissions:\n  contents: read\n") \
                         .replace("      - run: git push origin HEAD:${GITHUB_REF_NAME}\n", "")
        self.assertEqual(self.problems({".github/workflows/daily-sync.yml": weakened}), [])

    def test_test_file_deletion_fails(self) -> None:
        repo, base, head = make_repo(base_files(), {"README.md": "x\n"})
        (repo / "scripts" / "tests" / "test_dummy.py").unlink()
        _run(repo, "add", "-A")
        _run(repo, "commit", "-q", "-m", "delete test")
        head2 = _run(repo, "rev-parse", "HEAD").strip()
        problems = vb.weakening_problems(base, head2, repo)
        self.assertTrue(any("test files deleted" in p for p in problems), problems)

    def test_test_count_shrink_fails(self) -> None:
        shrunk = "import unittest\n\n\nclass T(unittest.TestCase):\n    pass\n"
        problems = self.problems({"scripts/tests/test_dummy.py": shrunk})
        self.assertTrue(any("test case count shrank" in p for p in problems), problems)

    def test_base_guard_logic_is_applied_to_candidate_tree(self) -> None:
        """候选树含 base guard 明令禁止的内容 → 必须由 base 逻辑拦下。"""
        bad = PR_VERIFY.replace("  pull_request:\n", "  pull_request_target:\n")
        repo, base, head = make_repo(base_files(), {".github/workflows/pr-verify.yml": bad})
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            have = vb.extract_base_tree(base, repo, dest)
            self.assertTrue(have, "fixture base 应包含 guard")
            candidate = dest / "candidate"
            candidate.mkdir()
            vb.extract_tree(head, repo, candidate)
            ok, output = vb.run_base_guard(dest, candidate)
        self.assertFalse(ok)
        self.assertIn("forbidden pull_request_target", output)

    def test_cli_exit_codes(self) -> None:
        repo, base, head = make_repo(base_files(), {"README.md": "benign\n"})
        ok = subprocess.run([sys.executable, str(SCRIPT), "--base-sha", base, "--head-sha", head,
                             "--repo-root", str(repo)], capture_output=True, text=True)
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)

        weakened = json.dumps({**json.loads(REQUIRED), "required": ["general-pr-verify"]}, ensure_ascii=False)
        repo2, base2, head2 = make_repo(base_files(), {".github/required-checks.json": weakened})
        bad = subprocess.run([sys.executable, str(SCRIPT), "--base-sha", base2, "--head-sha", head2,
                              "--repo-root", str(repo2)], capture_output=True, text=True)
        self.assertEqual(bad.returncode, 1, bad.stdout + bad.stderr)
        self.assertIn("可信基线 guard 失败", bad.stdout)


if __name__ == "__main__":
    unittest.main()
