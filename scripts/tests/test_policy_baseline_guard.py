#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""可信基线 guard 的测试（临时 git 仓库；不触碰真实仓库/网络）。

覆盖：必需检查被删除 / 来源绑定被削弱 / job 删除或改名 / 必需 job 新增可跳过的 if /
新增 workflow_dispatch / 新增提权触发 / 新增直推 main / 测试删除或数量缩小 /
旧直推 workflow 被重新武装，以及 base 版本 guard 对候选树的实际判定。
"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

import verify_policy_baseline as bl  # noqa: E402

NAMES = ["general-pr-verify", "workflow-policy-guard", "trusted-baseline-guard"]

MANIFEST = {
    "schema": "mihomo-rules-required-checks", "version": 2,
    "workflow": ".github/workflows/pr-verify.yml",
    "checks": [{"name": n, "job_id": n, "workflow": ".github/workflows/pr-verify.yml",
                "source": {"app_slug": "github-actions", "app_id": 15368}} for n in NAMES],
}

PR_VERIFY = """name: General PR verification
on:
  pull_request:
    branches:
      - main
permissions:
  contents: read
jobs:
  general-pr-verify:
    name: general-pr-verify
    runs-on: ubuntu-latest
    steps:
      - name: sha
        run: |
          set -euo pipefail
          printf '%s' "a" | grep -Eq '^[0-9a-f]{40}$' || { echo bad; exit 1; }
      - uses: actions/checkout@v7
        with:
          ref: ${{ steps.pr.outputs.head }}
          persist-credentials: false
      - uses: actions/checkout@v7
        with:
          repository: Hawaiine/Oasisic-Icons
          ref: f0f3bc2a44616885682ee5f0e5921540b964e2d8
          path: Oasisic-Icons
          persist-credentials: false
      - run: python3 -m pip install pyyaml
      - name: guard
        env:
          MIHOMO_ICON_REPO: ${{ github.workspace }}/Oasisic-Icons
        run: python3 scripts/ci/verify_general_pr.py
      - name: configs
        env:
          MIHOMO_ICON_REPO: ${{ github.workspace }}/Oasisic-Icons
        run: python3 scripts/verify_configs.py
      - run: python3 scripts/verify_rulesets.py
      - run: python3 scripts/icon_urls.py
      - run: python3 scripts/readme_stats.py --check
      - name: tests
        env:
          MIHOMO_ICON_REPO: ${{ github.workspace }}/Oasisic-Icons
        run: python3 -m unittest discover -s scripts/tests
  workflow-policy-guard:
    name: workflow-policy-guard
    runs-on: ubuntu-latest
    steps:
      - name: sha
        run: printf '%s' "a" | grep -Eq '^[0-9a-f]{40}$'
      - uses: actions/checkout@v7
        with:
          ref: ${{ steps.pr.outputs.head }}
          persist-credentials: false
      - run: python3 -m pip install pyyaml
      - run: python3 scripts/ci/verify_workflow_policy.py
  trusted-baseline-guard:
    name: trusted-baseline-guard
    runs-on: ubuntu-latest
    steps:
      - name: sha
        run: printf '%s' "a" | grep -Eq '^[0-9a-f]{40}$'
      - uses: actions/checkout@v7
        with:
          ref: ${{ steps.pr.outputs.head }}
          persist-credentials: false
      - run: python3 -m pip install pyyaml
      - name: baseline
        run: |
          set -euo pipefail
          git cat-file -e "${PR_BASE_SHA}^{commit}"
          python3 scripts/ci/verify_policy_baseline.py --base-sha "${PR_BASE_SHA}" --head-sha "${PR_HEAD_SHA}"
"""

# 夹具使用**真实的** daily-sync.yml 内容（若可用），使 base-guard 端到端判定只针对被测改动。
_REAL_LEGACY = ROOT / ".github" / "workflows" / "daily-sync.yml"
LEGACY = (_REAL_LEGACY.read_text(encoding="utf-8") if _REAL_LEGACY.exists() else "") or """name: daily sync
on:
  schedule:
    - cron: '0 22 * * *'
permissions:
  contents: write
jobs:
  sync:
    runs-on: ubuntu-latest
    steps:
      - run: git push origin HEAD:${{ github.ref_name }}
"""

SAMPLE_TEST = "def test_one():\n    assert True\n\n\ndef test_two():\n    assert True\n\n\ndef test_three():\n    assert True\n"


def git(args, cwd):
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)}: {proc.stderr}")
    return proc.stdout


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = Path(self.tmp)
        git(["init", "-q", "-b", "main"], self.root)
        git(["config", "user.email", "t@example.invalid"], self.root)
        git(["config", "user.name", "t"], self.root)
        (self.root / ".github" / "workflows").mkdir(parents=True)
        (self.root / "scripts" / "ci").mkdir(parents=True)
        (self.root / "scripts" / "tests").mkdir(parents=True)
        self.write({".github/required-checks.json": json.dumps(MANIFEST, ensure_ascii=False, indent=2),
                    ".github/workflows/pr-verify.yml": PR_VERIFY,
                    ".github/workflows/daily-sync.yml": LEGACY,
                    "scripts/tests/test_sample.py": SAMPLE_TEST,
                    "scripts/ci/verify_workflow_policy.py": (ROOT / "scripts" / "ci" / "verify_workflow_policy.py").read_text(encoding="utf-8"),
                    "README.md": "base\n"})
        self.base = self.commit("base")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, files: dict[str, str | None]):
        for rel, content in files.items():
            path = self.root / rel
            if content is None:
                path.unlink(missing_ok=True)
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

    def commit(self, message: str) -> str:
        git(["add", "-A"], self.root)
        git(["commit", "-q", "-m", message], self.root)
        return git(["rev-parse", "HEAD"], self.root).strip()

    def problems_after(self, files: dict[str, str | None]) -> list[str]:
        self.write(files)
        head = self.commit("change")
        return bl.weakening_problems(self.base, head, self.root)

    def assertProblem(self, needle: str, files: dict[str, str | None]):
        problems = self.problems_after(files)
        self.assertTrue(any(needle in p for p in problems), f"未命中 {needle!r}：{problems}")
        return problems


class WeakeningTest(Fixture):
    def test_benign_change_has_no_problems(self):
        self.assertEqual(self.problems_after({"README.md": "base\nmore\n"}), [])

    def test_required_check_removed(self):
        manifest = json.loads(json.dumps(MANIFEST))
        manifest["checks"] = manifest["checks"][:2]
        self.assertProblem("removed", {".github/required-checks.json": json.dumps(manifest)})

    def test_source_binding_changed(self):
        manifest = json.loads(json.dumps(MANIFEST))
        manifest["checks"][0]["source"]["app_id"] = 1
        self.assertProblem("预期来源被改变", {".github/required-checks.json": json.dumps(manifest)})

    def test_source_binding_removed(self):
        manifest = json.loads(json.dumps(MANIFEST))
        manifest["checks"] = NAMES
        self.assertProblem("预期来源被改变", {".github/required-checks.json": json.dumps(manifest)})

    def test_manifest_deleted(self):
        self.assertProblem("must not disappear", {".github/required-checks.json": None})

    def test_job_removed(self):
        self.assertProblem("job 'trusted-baseline-guard' removed",
                           {".github/workflows/pr-verify.yml": PR_VERIFY.replace(
                               "  trusted-baseline-guard:\n    name: trusted-baseline-guard\n"
                               "    runs-on: ubuntu-latest\n    steps:\n"
                               "      - name: sha\n"
                               "        run: printf '%s' \"a\" | grep -Eq '^[0-9a-f]{40}$'\n"
                               "      - uses: actions/checkout@v7\n        with:\n"
                               "          ref: ${{ steps.pr.outputs.head }}\n"
                               "          persist-credentials: false\n"
                               "      - run: python3 -m pip install pyyaml\n"
                               "      - name: baseline\n        run: |\n"
                               "          set -euo pipefail\n"
                               "          git cat-file -e \"${PR_BASE_SHA}^{commit}\"\n"
                               "          python3 scripts/ci/verify_policy_baseline.py "
                               "--base-sha \"${PR_BASE_SHA}\" --head-sha \"${PR_HEAD_SHA}\"\n", "")})

    def test_job_name_changed(self):
        self.assertProblem("name changed",
                           {".github/workflows/pr-verify.yml": PR_VERIFY.replace(
                               "  general-pr-verify:\n    name: general-pr-verify",
                               "  general-pr-verify:\n    name: general-verify-renamed")})

    def test_job_gains_skippable_if(self):
        self.assertProblem("新增 job-level if", {".github/workflows/pr-verify.yml": PR_VERIFY.replace(
            "  workflow-policy-guard:\n    name: workflow-policy-guard\n",
            "  workflow-policy-guard:\n    name: workflow-policy-guard\n    if: false\n")})

    def test_workflow_dispatch_added(self):
        self.assertProblem("新增 workflow_dispatch", {".github/workflows/pr-verify.yml": PR_VERIFY.replace(
            "on:\n  pull_request:\n", "on:\n  workflow_dispatch:\n  pull_request:\n")})

    def test_write_all_added(self):
        self.assertProblem("write-all", {".github/workflows/pr-verify.yml": PR_VERIFY.replace(
            "permissions:\n  contents: read\n", "permissions: write-all\n")})

    def test_pull_request_target_added(self):
        self.assertProblem("pull_request_target", {".github/workflows/pr-verify.yml": PR_VERIFY.replace(
            "on:\n  pull_request:\n", "on:\n  pull_request_target:\n")})

    def test_push_to_main_added(self):
        self.assertProblem("adds a push to main", {".github/workflows/pr-verify.yml": PR_VERIFY.replace(
            "        run: python3 scripts/ci/verify_general_pr.py",
            "        run: git push origin main\n      - name: guard\n        run: python3 scripts/ci/verify_general_pr.py")})

    def test_legacy_direct_write_rearmed(self):
        self.assertProblem("legacy direct-write workflow",
                           {".github/workflows/daily-sync.yml": LEGACY + "\n# re-armed\n"})

    def test_test_file_deleted(self):
        self.assertProblem("test files deleted", {"scripts/tests/test_sample.py": None})

    def test_test_count_shrunk(self):
        self.assertProblem("test case count shrank", {"scripts/tests/test_sample.py": "def test_one():\n    assert True\n"})

    def test_guard_invocation_removed(self):
        self.assertProblem("guard invocation", {".github/workflows/pr-verify.yml": PR_VERIFY.replace(
            "        run: python3 scripts/ci/verify_general_pr.py\n", "")})


class MainTest(Fixture):
    def test_main_ok_and_writes_json(self):
        self.write({"README.md": "base\nnoop\n"})
        head = self.commit("noop")
        out = self.root / "out.json"
        code = bl.main(["--base-sha", self.base, "--head-sha", head, "--repo-root", str(self.root),
                        "--json", str(out)])
        self.assertEqual(code, 0)
        data = json.loads(out.read_text(encoding="utf-8"))
        self.assertTrue(data["ok"])
        self.assertIn("base_guard_applied", data)

    def test_main_fails_on_weakening(self):
        self.write({".github/workflows/daily-sync.yml": LEGACY + "# tampered\n"})
        head = self.commit("weaken")
        code = bl.main(["--base-sha", self.base, "--head-sha", head, "--repo-root", str(self.root)])
        self.assertEqual(code, 1)

    def test_base_guard_rejects_non_compliant_candidate_tree(self):
        """base 版本 guard 判定候选树不合规（候选不得用自身新规则给自己放行）。"""
        self.write({".github/workflows/pr-verify.yml": PR_VERIFY.replace(
            "on:\n  pull_request:\n", "on:\n  workflow_dispatch:\n  pull_request:\n")})
        head = self.commit("add dispatch")
        out = self.root / "o.json"
        code = bl.main(["--base-sha", self.base, "--head-sha", head, "--repo-root", str(self.root),
                        "--json", str(out)])
        self.assertEqual(code, 1)
        data = json.loads(out.read_text(encoding="utf-8"))
        self.assertTrue(any("base 版本 guard 判定候选树不合规" in p for p in data["problems"]), data["problems"])

    def test_check_entries_parses_both_shapes(self):
        self.assertEqual(set(bl.check_entries(json.dumps(MANIFEST))), set(NAMES))
        self.assertEqual(set(bl.check_entries(json.dumps({"required": NAMES}))), set(NAMES))

    def test_unreadable_manifest_fails_closed(self):
        with self.assertRaises(bl.BaselineError):
            bl.check_entries("{not json")


if __name__ == "__main__":
    unittest.main()


# --------------------------------------------------------------------------- Fix B
JOB_COMMANDS = [
    ("general-pr-verify", "        run: ", "python3 scripts/ci/verify_general_pr.py"),
    ("workflow-policy-guard", "      - run: ", "python3 scripts/ci/verify_workflow_policy.py"),
    ("trusted-baseline-guard", "          ",
     'python3 scripts/ci/verify_policy_baseline.py --base-sha "${PR_BASE_SHA}" --head-sha "${PR_HEAD_SHA}"'),
]
CONFIGS_CMD = "run: python3 scripts/verify_configs.py"
UNITTEST_CMD = "run: python3 -m unittest discover -s scripts/tests"
PIP_CMD = "run: python3 -m pip install pyyaml"
CATFILE_CMD = 'git cat-file -e "${PR_BASE_SHA}^{commit}"'


def swap(text: str, needle: str, replacement: str) -> str:
    assert needle in text, f"fixture 未命中：{needle!r}"
    return text.replace(needle, replacement)


class CommandContractInBaselineGuardTest(Fixture):
    """Fix B §4.3/§4.4：可信基线 guard 必须校验候选 workflow 的**实际执行命令**。

    该判定使用 guard 自带的**独立严格解析**（不 import 候选树代码），
    因此候选无法通过修改自己的 guard 来自证安全。
    """

    def expect_rejected(self, pr_verify: str, job: str):
        problems = self.problems_after({".github/workflows/pr-verify.yml": pr_verify})
        self.assertTrue(any("没有被真正执行" in p or "缺少必需命令" in p for p in problems),
                        f"{job}: {problems}")
        return problems

    def test_echo_not_executed_rejected_for_every_job(self):
        for job, prefix, body in JOB_COMMANDS:
            with self.subTest(job=job):
                repl = prefix + "'echo \"NOT EXECUTED: " + body + "\"'"
                self.expect_rejected(swap(PR_VERIFY, prefix + body, repl), job)

    def test_command_in_comment_rejected(self):
        for job, prefix, body in JOB_COMMANDS:
            with self.subTest(job=job):
                self.expect_rejected(swap(PR_VERIFY, prefix + body, prefix + f"# {body}"), job)

    def test_pipe_true_swallowing_rejected(self):
        for job, prefix, body in JOB_COMMANDS:
            with self.subTest(job=job):
                self.expect_rejected(swap(PR_VERIFY, prefix + body, prefix + body + " || true"), job)

    def test_conditional_branch_rejected(self):
        pr_verify = swap(PR_VERIFY, "        run: python3 scripts/ci/verify_general_pr.py",
                         "        run: if false; then python3 scripts/ci/verify_general_pr.py; fi")
        self.expect_rejected(pr_verify, "general-pr-verify")

    def test_heredoc_hidden_command_rejected(self):
        body = JOB_COMMANDS[2][2]
        pr_verify = swap(PR_VERIFY, "          " + body,
                         "          cat <<'EOF' > /tmp/x\n          " + body + "\n          EOF")
        self.expect_rejected(pr_verify, "trusted-baseline-guard")

    def test_fake_step_name_without_execution_rejected(self):
        pr_verify = swap(PR_VERIFY, "      - run: python3 scripts/ci/verify_workflow_policy.py",
                         "      - name: 🔐 workflow 策略 guard（结构化）\n        run: echo ok")
        problems = self.problems_after({".github/workflows/pr-verify.yml": pr_verify})
        self.assertTrue(any("缺少必需命令" in p for p in problems), problems)

    def test_missing_required_command_rejected(self):
        pr_verify = swap(PR_VERIFY, CONFIGS_CMD, "run: echo skipped")
        problems = self.problems_after({".github/workflows/pr-verify.yml": pr_verify})
        self.assertTrue(any("verify_configs.py" in p for p in problems), problems)

    def test_unittest_noop_rejected(self):
        pr_verify = swap(PR_VERIFY, UNITTEST_CMD,
                         'run: echo "python3 -m unittest discover -s scripts/tests"')
        problems = self.problems_after({".github/workflows/pr-verify.yml": pr_verify})
        self.assertTrue(any("unittest" in p for p in problems), problems)

    def test_dependency_install_noop_rejected(self):
        pr_verify = swap(PR_VERIFY, PIP_CMD, 'run: echo "python3 -m pip install pyyaml"')
        problems = self.problems_after({".github/workflows/pr-verify.yml": pr_verify})
        self.assertTrue(any("pip install pyyaml" in p for p in problems), problems)

    def test_base_availability_check_removed_rejected(self):
        pr_verify = swap(PR_VERIFY, CATFILE_CMD, "true")
        problems = self.problems_after({".github/workflows/pr-verify.yml": pr_verify})
        self.assertTrue(any("git cat-file" in p for p in problems), problems)

    def test_baseline_missing_base_sha_flag_rejected(self):
        pr_verify = swap(PR_VERIFY, "--base-sha", "--bogus")
        problems = self.problems_after({".github/workflows/pr-verify.yml": pr_verify})
        self.assertTrue(any("verify_policy_baseline.py" in p for p in problems), problems)

    def test_contract_parser_is_independent_of_candidate_code(self):
        """guard 自带解析实现，不 import 候选树模块（候选无法自证安全）。"""
        src = (ROOT / "scripts" / "ci" / "verify_policy_baseline.py").read_text(encoding="utf-8")
        self.assertNotIn("import workflow_command_contract", src)
        self.assertNotIn("from workflow_command_contract", src)
        self.assertTrue(hasattr(bl, "check_command_contract"))
        self.assertTrue(hasattr(bl, "executed_commands"))

    def test_benign_readme_change_still_passes(self):
        self.assertEqual(self.problems_after({"README.md": "base\nmore\n"}), [])

    def test_real_pr_verify_passes_contract(self):
        real = (ROOT / ".github" / "workflows" / "pr-verify.yml").read_text(encoding="utf-8")
        import yaml
        self.assertEqual(bl.check_command_contract(yaml.safe_load(real)), [])
