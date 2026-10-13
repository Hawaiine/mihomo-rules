#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""workflow 策略 guard 的结构化检查测试（含负向案例）。

覆盖（每条负向案例都断言**实际失败行为**，而不是「文本里出现了某个词」）：
  * 只在注释里写 CANDIDATE_SYNC_ENABLED（无真实 job-level if）必须失败；
  * 改变门控条件表达式必须失败；
  * 删除验证步骤必须失败；
  * 绕过 required check（例如 gh pr merge / 直推 main）必须失败；
  * 必需 job 加 job-level if 或给验证步骤加可跳过的 if 必须失败；
  * 权限、来源绑定、图标 pin、persist-credentials 等约束必须生效。
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

import verify_workflow_policy as pol  # noqa: E402

ICONS_SHA = "f0f3bc2a44616885682ee5f0e5921540b964e2d8"

MANIFEST = {
    "schema": "mihomo-rules-required-checks",
    "version": 2,
    "workflow": ".github/workflows/pr-verify.yml",
    "checks": [
        {"name": n, "job_id": n, "workflow": ".github/workflows/pr-verify.yml",
         "source": {"app_slug": "github-actions", "app_id": 15368}}
        for n in ("general-pr-verify", "workflow-policy-guard", "trusted-baseline-guard")
    ],
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

CANDIDATE = """name: 候选分支更新（PR-first）
on:
  schedule:
    - cron: '45 22 * * *'
  workflow_dispatch:
permissions:
  contents: read
env:
  CANDIDATE_BRANCH: candidate/rules-update
  BASE_BRANCH: main
jobs:
  gate:
    name: gate
    runs-on: ubuntu-latest
    outputs:
      enabled: ${{ steps.check.outputs.enabled }}
    steps:
      - id: check
        run: echo "enabled=false" >> "$GITHUB_OUTPUT"
  publish:
    name: publish
    needs: [gate]
    if: needs.gate.outputs.enabled == 'true'
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@v7
        with:
          ref: main
          persist-credentials: false
      - run: git apply "$RUNNER_TEMP/candidate.patch"
      - run: python3 "$RUNNER_TEMP/trusted/candidate_sha.py" decide
      - run: git push origin "HEAD:refs/heads/${CANDIDATE_BRANCH}"
  request-auto-merge:
    name: request-auto-merge
    needs: [gate, publish]
    if: needs.gate.outputs.enabled == 'true' && vars.CANDIDATE_AUTOMERGE_ENABLED == 'true'
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
        run: python3 scripts/ci/await_checks.py --repo o/r --pr 1 --expected-head-sha s
      - name: verify
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: python3 scripts/ci/verify_required_checks.py --repo o/r --pr 1 --head-sha s
      - name: automerge
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: python3 scripts/ci/request_automerge.py --repo o/r --pr 1 --head-sha s
"""
AWAIT_STEP = ("      - name: wait\n        env:\n          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}\n"
              "        run: python3 scripts/ci/await_checks.py --repo o/r --pr 1 --expected-head-sha s\n")
VERIFY_STEP = ("      - name: verify\n        env:\n          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}\n"
               "        run: python3 scripts/ci/verify_required_checks.py --repo o/r --pr 1 --head-sha s\n")
AUTOMERGE_STEP = ("      - name: automerge\n        env:\n          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}\n"
                  "        run: python3 scripts/ci/request_automerge.py --repo o/r --pr 1 --head-sha s\n")


def build_root(tmp: str, pr_verify: str = PR_VERIFY, candidate: str = CANDIDATE,
               manifest: object = None, legacy: str | None = None,
               extra: dict | None = None) -> Path:
    root = Path(tmp)
    (root / ".github" / "workflows").mkdir(parents=True, exist_ok=True)
    (root / ".github" / "required-checks.json").write_text(
        json.dumps(MANIFEST if manifest is None else manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (root / ".github" / "workflows" / "pr-verify.yml").write_text(pr_verify, encoding="utf-8")
    (root / ".github" / "workflows" / "candidate-pr-sync.yml").write_text(candidate, encoding="utf-8")
    if legacy is not None:
        (root / ".github" / "workflows" / "daily-sync.yml").write_text(legacy, encoding="utf-8")
    for fname, text in (extra or {}).items():
        (root / ".github" / "workflows" / fname).write_text(text, encoding="utf-8")
    return root


class Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def problems(self, **kw) -> list[str]:
        root = build_root(self._tmp, **kw)
        problems, _ = pol.run(root)
        return problems

    def assertProblem(self, needle, **kw):
        problems = self.problems(**kw)
        self.assertTrue(any(needle in p for p in problems), f"未命中 {needle!r}：{problems}")
        return problems


class ValidLayoutTest(Fixture):
    def test_valid_layout_passes(self):
        self.assertEqual(self.problems(), [])

    def test_manifest_reports_expected_source(self):
        _, info = pol.run(build_root(self._tmp))
        self.assertEqual(info["required_checks"],
                         ["general-pr-verify", "workflow-policy-guard", "trusted-baseline-guard"])
        self.assertEqual(info["expected_check_source"], {"app_slug": "github-actions", "app_id": 15368})


class GateStructureTest(Fixture):
    def test_comment_only_gate_is_rejected(self):
        """注释里出现 CANDIDATE_SYNC_ENABLED 不算门控（必须作用在解析后的 job if 上）。"""
        candidate = CANDIDATE.replace(
            "    if: needs.gate.outputs.enabled == 'true' && vars.CANDIDATE_AUTOMERGE_ENABLED == 'true'",
            "    # gated by vars.CANDIDATE_SYNC_ENABLED and vars.CANDIDATE_AUTOMERGE_ENABLED")
        self.assertProblem("request-auto-merge 必须门控于", candidate=candidate)

    def test_changed_condition_expression_is_rejected(self):
        candidate = CANDIDATE.replace(
            "    if: needs.gate.outputs.enabled == 'true' && vars.CANDIDATE_AUTOMERGE_ENABLED == 'true'",
            "    if: true")
        problems = self.assertProblem("request-auto-merge 必须门控于", candidate=candidate)
        self.assertTrue(any("CANDIDATE_AUTOMERGE_ENABLED" in p for p in problems), problems)

    def test_scheduled_writer_without_gate_is_rejected(self):
        candidate = CANDIDATE.replace(
            "    if: needs.gate.outputs.enabled == 'true' && vars.CANDIDATE_AUTOMERGE_ENABLED == 'true'",
            "    if: github.event_name == 'workflow_dispatch'")
        self.assertProblem("定时触发且持有写权限", candidate=candidate)

    def test_publish_without_gate_is_rejected(self):
        candidate = CANDIDATE.replace("    if: needs.gate.outputs.enabled == 'true'\n    runs-on: ubuntu-latest\n    permissions:\n      contents: read",
                                      "    runs-on: ubuntu-latest\n    permissions:\n      contents: write")
        problems = self.problems(candidate=candidate)
        self.assertTrue(any("publish" in p for p in problems), problems)


class RequiredChecksTest(Fixture):
    def test_required_job_with_if_false_is_rejected(self):
        pr_verify = PR_VERIFY.replace("  workflow-policy-guard:\n    name: workflow-policy-guard\n",
                                      "  workflow-policy-guard:\n    name: workflow-policy-guard\n    if: false\n")
        self.assertProblem("不得设置 job-level if", pr_verify=pr_verify)

    def test_required_step_with_if_is_rejected(self):
        pr_verify = PR_VERIFY.replace(
            "      - run: python3 scripts/ci/verify_workflow_policy.py",
            "      - if: false\n        run: python3 scripts/ci/verify_workflow_policy.py")
        self.assertProblem("验证步骤不得带 if 条件", pr_verify=pr_verify)

    def test_removed_verification_step_is_rejected(self):
        pr_verify = PR_VERIFY.replace("        run: python3 scripts/ci/verify_general_pr.py\n", "")
        problems = self.problems(pr_verify=pr_verify)
        self.assertTrue(any("verify_general_pr" in p or "必需命令" in p for p in problems), problems)

    def test_job_id_mismatch_is_rejected(self):
        pr_verify = PR_VERIFY.replace("  general-pr-verify:\n    name: general-pr-verify\n",
                                      "  general-pr-verify:\n    name: general-verify-renamed\n")
        self.assertProblem("必须设置 name", pr_verify=pr_verify)

    def test_extra_job_is_rejected(self):
        pr_verify = PR_VERIFY + "  extra-job:\n    name: extra-job\n    runs-on: ubuntu-latest\n    steps: []\n"
        self.assertProblem("必须等于必需检查", pr_verify=pr_verify)

    def test_workflow_dispatch_in_pr_verify_is_rejected(self):
        pr_verify = PR_VERIFY.replace("on:\n  pull_request:\n", "on:\n  workflow_dispatch:\n  pull_request:\n")
        self.assertProblem("不得声明 workflow_dispatch", pr_verify=pr_verify)

    def test_non_exact_permissions_rejected(self):
        pr_verify = PR_VERIFY.replace("permissions:\n  contents: read\n",
                                      "permissions:\n  contents: read\n  pull-requests: read\n")
        self.assertProblem("permissions 必须恰为", pr_verify=pr_verify)

    def test_missing_required_check_in_manifest_rejected(self):
        manifest = dict(MANIFEST)
        manifest["checks"] = MANIFEST["checks"][:2]
        self.assertProblem("必须等于必需检查", manifest=manifest)


class SourceBindingTest(Fixture):
    def test_string_check_without_source_rejected(self):
        manifest = {"workflow": ".github/workflows/pr-verify.yml",
                    "checks": ["general-pr-verify", "workflow-policy-guard", "trusted-baseline-guard"]}
        self.assertProblem("必须声明 source", manifest=manifest)

    def test_wrong_app_id_rejected(self):
        manifest = json.loads(json.dumps(MANIFEST))
        manifest["checks"][0]["source"]["app_id"] = 1
        self.assertProblem("source 必须为", manifest=manifest)

    def test_wrong_app_slug_rejected(self):
        manifest = json.loads(json.dumps(MANIFEST))
        manifest["checks"][1]["source"]["app_slug"] = "evil"
        self.assertProblem("source 必须为", manifest=manifest)

    def test_legacy_required_list_rejected(self):
        manifest = {"workflow": ".github/workflows/pr-verify.yml",
                    "required": ["general-pr-verify", "workflow-policy-guard", "trusted-baseline-guard"]}
        self.assertProblem("缺少来源绑定", manifest=manifest)


class AutoMergeStructureTest(Fixture):
    def test_missing_verify_call_rejected(self):
        candidate = CANDIDATE.replace(VERIFY_STEP, "")
        self.assertProblem("verify_required_checks.py", candidate=candidate)

    def test_missing_await_call_rejected(self):
        candidate = CANDIDATE.replace(AWAIT_STEP, "")
        self.assertProblem("await_checks.py", candidate=candidate)

    def test_mutation_before_verification_rejected(self):
        candidate = CANDIDATE.replace(AWAIT_STEP + VERIFY_STEP + AUTOMERGE_STEP,
                                      AUTOMERGE_STEP + AWAIT_STEP + VERIFY_STEP)
        self.assertProblem("独立步骤", candidate=candidate)

    def test_wrong_automerge_permissions_rejected(self):
        candidate = CANDIDATE.replace("      pull-requests: write\n      checks: read\n", "      actions: write\n")
        problems = self.problems(candidate=candidate)
        self.assertTrue(any("permissions 必须恰为" in p or "actions: write" in p for p in problems), problems)


class PublishIsolationTest(Fixture):
    def test_publish_with_contents_write_rejected(self):
        candidate = CANDIDATE.replace("    permissions:\n      contents: read\n    steps:\n"
                                      "      - uses: actions/checkout@v7\n        with:\n          ref: main",
                                      "    permissions:\n      contents: write\n    steps:\n"
                                      "      - uses: actions/checkout@v7\n        with:\n          ref: main")
        self.assertProblem("publish 权限必须恰为", candidate=candidate)

    def test_publish_checking_out_pr_head_rejected(self):
        candidate = CANDIDATE.replace("      - uses: actions/checkout@v7\n        with:\n          ref: main\n"
                                      "          persist-credentials: false\n      - run: git apply",
                                      "      - uses: actions/checkout@v7\n        with:\n"
                                      "          ref: ${{ github.event.pull_request.head.sha }}\n"
                                      "          persist-credentials: false\n      - run: git apply")
        self.assertProblem("publish 不得检出 PR head", candidate=candidate)

    def test_publish_running_repo_script_after_apply_rejected(self):
        candidate = CANDIDATE.replace("      - run: python3 \"$RUNNER_TEMP/trusted/candidate_sha.py\" decide",
                                      "      - run: python3 scripts/ci/candidate_sha.py decide")
        self.assertProblem("不得执行仓库脚本", candidate=candidate)

    def test_push_not_targeting_candidate_branch_rejected(self):
        candidate = CANDIDATE.replace('      - run: git push origin "HEAD:refs/heads/${CANDIDATE_BRANCH}"',
                                      "      - run: git push origin HEAD:refs/heads/some-other-branch")
        self.assertProblem("必须显式指向 refs/heads/$CANDIDATE_BRANCH", candidate=candidate)

    def test_actions_write_rejected(self):
        candidate = CANDIDATE.replace("permissions:\n  contents: read\n", "permissions:\n  contents: read\n  actions: write\n")
        self.assertProblem("不得声明 actions: write", candidate=candidate)


class BypassTest(Fixture):
    def test_gh_pr_merge_rejected(self):
        candidate = CANDIDATE.replace(
            "        run: python3 scripts/ci/request_automerge.py --repo o/r --pr 1 --head-sha s",
            "        run: gh pr merge 1 --squash")
        self.assertProblem("gh pr merge", candidate=candidate)

    def test_rest_merge_endpoint_rejected(self):
        candidate = CANDIDATE.replace(
            "        run: python3 scripts/ci/request_automerge.py --repo o/r --pr 1 --head-sha s",
            "        run: curl -X PUT https://api.github.com/repos/o/r/pulls/1/merge")
        self.assertProblem("merge 端点", candidate=candidate)

    def test_pull_request_target_rejected(self):
        pr_verify = PR_VERIFY.replace("on:\n  pull_request:\n", "on:\n  pull_request_target:\n")
        self.assertProblem("pull_request_target 一律禁止", pr_verify=pr_verify)

    def test_write_all_rejected(self):
        pr_verify = PR_VERIFY.replace("permissions:\n  contents: read\n", "permissions: write-all\n")
        problems = self.problems(pr_verify=pr_verify)
        self.assertTrue(any("write-all" in p for p in problems), problems)

    def test_persist_credentials_true_rejected(self):
        pr_verify = PR_VERIFY.replace("          persist-credentials: false\n", "          persist-credentials: true\n")
        self.assertProblem("persist-credentials: false", pr_verify=pr_verify)

    def test_icons_checkout_must_be_pinned(self):
        pr_verify = PR_VERIFY.replace(f"          ref: {ICONS_SHA}", "          ref: main")
        self.assertProblem("已批准 pin", pr_verify=pr_verify)

    def test_icons_checkout_with_floating_tag_is_rejected(self):
        pr_verify = PR_VERIFY.replace(f"          ref: {ICONS_SHA}", "          ref: v1.2.3")
        self.assertProblem("已批准 pin", pr_verify=pr_verify)

    def test_icons_checkout_with_pin_passes(self):
        self.assertEqual(self.problems(), [])


class LegacyPinTest(Fixture):
    def _real_legacy(self) -> str:
        path = ROOT / ".github" / "workflows" / "daily-sync.yml"
        if not path.exists():
            self.skipTest("真实 daily-sync.yml 不存在")
        return path.read_text(encoding="utf-8")

    def test_pinned_legacy_file_passes_and_reports_waiver(self):
        legacy = self._real_legacy()
        _, info = pol.run(build_root(self._tmp, legacy=legacy))
        self.assertTrue(info["legacy_daily_sync_pinned_ok"])
        self.assertEqual(info["legacy_content_rules_waived"], ["P8", "P11"])
        self.assertEqual(pol.run(build_root(self._tmp, legacy=legacy))[0], [])

    def test_modified_legacy_file_is_rejected(self):
        legacy = self._real_legacy() + "\n# tampered\n"
        problems = pol.run(build_root(self._tmp, legacy=legacy))[0]
        self.assertTrue(any("固定 pin 不一致" in p for p in problems), problems)

    def test_rearmed_legacy_direct_write_is_rejected(self):
        legacy = self._real_legacy().replace("schedule:", "schedule:  # re-armed", 1)
        problems = pol.run(build_root(self._tmp, legacy=legacy))[0]
        self.assertTrue(any("禁止被修改" in p for p in problems), problems)


if __name__ == "__main__":
    unittest.main()


# --------------------------------------------------------------------------- P13
# (job, YAML 前缀, 命令体) —— 篡改只替换命令体，保留 YAML 结构合法
JOB_COMMANDS = [
    ("general-pr-verify", "        run: ", "python3 scripts/ci/verify_general_pr.py"),
    ("workflow-policy-guard", "      - run: ", "python3 scripts/ci/verify_workflow_policy.py"),
    ("trusted-baseline-guard", "          ",
     'python3 scripts/ci/verify_policy_baseline.py --base-sha "${PR_BASE_SHA}" --head-sha "${PR_HEAD_SHA}"'),
]
GENERAL_CMD = "run: python3 scripts/ci/verify_general_pr.py"
CONFIGS_CMD = "run: python3 scripts/verify_configs.py"
POLICY_CMD = "- run: python3 scripts/ci/verify_workflow_policy.py"
BASELINE_CMD = 'python3 scripts/ci/verify_policy_baseline.py --base-sha "${PR_BASE_SHA}" --head-sha "${PR_HEAD_SHA}"'
UNITTEST_CMD = "run: python3 -m unittest discover -s scripts/tests"
README_CMD = "run: python3 scripts/readme_stats.py --check"
PIP_CMD = "run: python3 -m pip install pyyaml"
CATFILE_CMD = 'git cat-file -e "${PR_BASE_SHA}^{commit}"'


def swap(text: str, needle: str, replacement: str) -> str:
    """替换（要求命中，避免静默无效的 fixture）。"""
    assert needle in text, f"fixture 未命中：{needle!r}"
    return text.replace(needle, replacement)


class CommandContractTest(Fixture):
    """Fix B：必需命令必须**真正执行**；空跑/执行语义绕过必须被拒绝（三个必需 job 逐一覆盖）。"""

    def each_job(self):
        return JOB_COMMANDS

    def expect_rejected(self, pr_verify: str, job: str):
        problems = self.problems(pr_verify=pr_verify)
        self.assertTrue(any("没有被实际执行" in p or "缺少必需命令" in p for p in problems),
                        f"{job}: {problems}")
        return problems

    def test_echo_not_executed_rejected_for_every_required_job(self):
        for job, prefix, body in self.each_job():
            with self.subTest(job=job):
                # YAML 侧用单引号包裹（值内含 ": "，否则不是合法 YAML 标量）
                repl = prefix + "'echo \"NOT EXECUTED: " + body + "\"'"
                self.expect_rejected(swap(PR_VERIFY, prefix + body, repl), job)

    def test_printf_placeholder_rejected_for_every_required_job(self):
        for job, prefix, body in self.each_job():
            with self.subTest(job=job):
                self.expect_rejected(swap(PR_VERIFY, prefix + body,
                                          prefix + 'printf "%s\\n" "placeholder"'), job)

    def test_comment_only_rejected_for_every_required_job(self):
        for job, prefix, body in self.each_job():
            with self.subTest(job=job):
                self.expect_rejected(swap(PR_VERIFY, prefix + body, prefix + f"# {body}"), job)

    def test_quoted_text_only_rejected_for_every_required_job(self):
        """把命令塞进引号里的说明文本（不执行）。"""
        for job, prefix, body in self.each_job():
            with self.subTest(job=job):
                repl = prefix + "'\"" + body + "\"'"
                self.expect_rejected(swap(PR_VERIFY, prefix + body, repl), job)

    def test_true_placeholder_rejected_for_every_required_job(self):
        for job, prefix, body in self.each_job():
            with self.subTest(job=job):
                problems = self.problems(pr_verify=swap(PR_VERIFY, prefix + body, prefix + "true"))
                self.assertTrue(any("缺少必需命令" in p for p in problems), f"{job}: {problems}")

    def test_pipe_true_swallowing_rejected_for_every_required_job(self):
        for job, prefix, body in self.each_job():
            with self.subTest(job=job):
                self.expect_rejected(swap(PR_VERIFY, prefix + body, prefix + body + " || true"), job)

    def test_conditional_branch_rejected(self):
        pr_verify = swap(PR_VERIFY, GENERAL_CMD,
                         "run: if false; then python3 scripts/ci/verify_general_pr.py; fi")
        problems = self.problems(pr_verify=pr_verify)
        self.assertTrue(any("没有被实际执行" in p for p in problems), problems)

    def test_heredoc_hidden_command_rejected(self):
        pr_verify = swap(PR_VERIFY, BASELINE_CMD,
                         "cat <<'EOF' > /tmp/x\n          python3 scripts/ci/verify_policy_baseline.py "
                         '--base-sha "${PR_BASE_SHA}" --head-sha "${PR_HEAD_SHA}"\n          EOF')
        problems = self.problems(pr_verify=pr_verify)
        self.assertTrue(any("没有被实际执行" in p for p in problems), problems)

    def test_fake_step_name_without_execution_rejected(self):
        """伪造步骤名称但实际不执行命令（§4.4.7）。"""
        pr_verify = swap(PR_VERIFY, "      - run: python3 scripts/ci/verify_workflow_policy.py",
                         "      - name: 🔐 workflow 策略 guard（结构化）\n        run: echo ok")
        problems = self.problems(pr_verify=pr_verify)
        self.assertTrue(any("缺少必需命令" in p for p in problems), problems)

    def test_unittest_replaced_by_noop_with_step_name_kept_rejected(self):
        pr_verify = swap(PR_VERIFY, UNITTEST_CMD,
                         'run: echo "python3 -m unittest discover -s scripts/tests"')
        problems = self.problems(pr_verify=pr_verify)
        self.assertTrue(any("全量单元测试" in p for p in problems), problems)

    def test_missing_configs_command_rejected(self):
        pr_verify = swap(PR_VERIFY, CONFIGS_CMD, "run: echo skipped")
        self.assertTrue(any("verify_configs.py" in p for p in self.problems(pr_verify=pr_verify)))

    def test_readme_check_flag_removed_rejected(self):
        pr_verify = swap(PR_VERIFY, README_CMD, "run: python3 scripts/readme_stats.py")
        problems = self.problems(pr_verify=pr_verify)
        self.assertTrue(any("README 统计口径" in p for p in problems), problems)

    def test_dependency_install_noop_rejected(self):
        pr_verify = swap(PR_VERIFY, PIP_CMD, 'run: echo "python3 -m pip install pyyaml"')
        self.assertTrue(any("安装依赖 pyyaml" in p for p in self.problems(pr_verify=pr_verify)))

    def test_sha_validation_noop_rejected(self):
        pr_verify = swap(PR_VERIFY, "run: printf '%s' \"a\" | grep -Eq '^[0-9a-f]{40}$'",
                         'run: echo "grep -Eq ^[0-9a-f]{40}$"')
        self.assertTrue(any("SHA 格式" in p for p in self.problems(pr_verify=pr_verify)))

    def test_base_availability_check_removed_rejected(self):
        pr_verify = swap(PR_VERIFY, CATFILE_CMD, "true")
        problems = self.problems(pr_verify=pr_verify)
        self.assertTrue(any("base/head 提交可用" in p for p in problems), problems)

    def test_base_check_moved_after_baseline_rejected(self):
        """把 base 可用性检查放到基线 guard **之后**必须失败（步骤级顺序合同）。"""
        pr_verify = PR_VERIFY.replace(
            "      - name: baseline\n        run: |\n          set -euo pipefail\n"
            "          git cat-file -e \"${PR_BASE_SHA}^{commit}\"\n"
            "          python3 scripts/ci/verify_policy_baseline.py --base-sha \"${PR_BASE_SHA}\" "
            "--head-sha \"${PR_HEAD_SHA}\"\n",
            "      - name: baseline\n        run: |\n          set -euo pipefail\n"
            "          python3 scripts/ci/verify_policy_baseline.py --base-sha \"${PR_BASE_SHA}\" "
            "--head-sha \"${PR_HEAD_SHA}\"\n"
            "      - name: late-base-check\n        run: git cat-file -e \"${PR_BASE_SHA}^{commit}\"\n")
        assert pr_verify != PR_VERIFY, "fixture 未命中：baseline 步骤块"
        problems = self.problems(pr_verify=pr_verify)
        self.assertTrue(any("必须位于" in p for p in problems), problems)

    def test_real_layout_satisfies_contract_for_all_three_jobs(self):
        self.assertEqual(self.problems(), [])


# --------------------------------------------------------------------------- P14
class CheckNameUniquenessTest(Fixture):
    """Fix A.2：必需检查名只允许出现在 pr-verify.yml 的预期 job 中。"""

    OTHER_JOB_SAME_ID = """name: other verification
on:
  pull_request:
permissions:
  contents: read
jobs:
  general-pr-verify:
    name: general-pr-verify
    runs-on: ubuntu-latest
    steps:
      - run: echo ok
"""

    OTHER_JOB_SAME_NAME = """name: other verification
on:
  pull_request:
permissions:
  contents: read
jobs:
  helper:
    name: general-pr-verify
    runs-on: ubuntu-latest
    steps:
      - run: echo ok
"""

    OTHER_JOB_BENIGN = """name: other verification
on:
  pull_request:
permissions:
  contents: read
jobs:
  lint:
    name: lint
    runs-on: ubuntu-latest
    steps:
      - run: echo ok
"""

    def test_other_workflow_declaring_required_job_id_rejected(self):
        problems = self.problems(extra={"other-verify.yml": self.OTHER_JOB_SAME_ID})
        self.assertTrue(any("声明了必需检查名" in p for p in problems), problems)

    def test_other_workflow_declaring_required_job_name_rejected(self):
        problems = self.problems(extra={"other-verify.yml": self.OTHER_JOB_SAME_NAME})
        self.assertTrue(any("声明了必需检查名" in p for p in problems), problems)

    def test_other_workflow_without_required_names_passes(self):
        self.assertEqual(self.problems(extra={"other-verify.yml": self.OTHER_JOB_BENIGN}), [])

    def test_all_three_names_are_protected(self):
        for name in ("general-pr-verify", "workflow-policy-guard", "trusted-baseline-guard"):
            with self.subTest(name=name):
                text = (f"name: x\non:\n  pull_request:\npermissions:\n  contents: read\n"
                        f"jobs:\n  {name}:\n    name: {name}\n"
                        f"    runs-on: ubuntu-latest\n    steps:\n      - run: echo ok\n")
                problems = self.problems(extra={"other-verify.yml": text})
                self.assertTrue(any("声明了必需检查名" in p for p in problems), problems)


class AutoMergePermissionsWithActionsReadTest(Fixture):
    def test_automerge_requires_actions_read_and_rejects_actions_write(self):
        good = self.problems()
        self.assertEqual(good, [])
        with_write = swap(CANDIDATE, "      actions: read", "      actions: write")
        problems = self.problems(candidate=with_write)
        self.assertTrue(any("permissions 必须恰为" in p for p in problems), problems)

    def test_missing_actions_read_rejected(self):
        without = swap(CANDIDATE, "      actions: read\n", "")
        problems = self.problems(candidate=without)
        self.assertTrue(any("permissions 必须恰为" in p for p in problems), problems)


# --------------------------------------------------------------------------- Fix A（第四轮）
def block(prefix: str, body_lines: list[str]) -> str:
    """把单行命令改写成多行 shell 块（保持 YAML 合法）。"""
    if "run:" in prefix:                     # 普通标量：必须显式块标量
        head, ind = prefix.rstrip() + " |", " " * (len(prefix) + 2)
    else:                                    # 已在块标量内
        head, ind = None, prefix
    return "\n".join(([head] if head else []) + [ind + ln for ln in body_lines])


class CommandExecutionProofTest(Fixture):
    """Fix A：`-m py_compile`、`-c`、跨行控制流、未调用函数等都不算「执行」。"""

    def expect_rejected(self, pr_verify: str, label: str):
        problems = self.problems(pr_verify=pr_verify)
        self.assertTrue(any("没有被实际执行" in p or "缺少必需命令" in p for p in problems),
                        f"{label}: {problems}")
        return problems

    def each(self):
        return JOB_COMMANDS

    def test_py_compile_substitution_rejected_for_every_job(self):
        for job, prefix, body in self.each():
            with self.subTest(job=job):
                repl = prefix + "python3 -m py_compile " + body[len("python3 "):]
                self.expect_rejected(swap(PR_VERIFY, prefix + body, repl), job)

    def test_inline_c_substitution_rejected_for_every_job(self):
        for job, prefix, body in self.each():
            with self.subTest(job=job):
                repl = prefix + "python3 -c \"print('noop')\" " + body[len("python3 "):]
                self.expect_rejected(swap(PR_VERIFY, prefix + body, repl), job)

    def test_cross_line_if_false_rejected_for_every_job(self):
        for job, prefix, body in self.each():
            with self.subTest(job=job):
                repl = block(prefix, ["if false; then", "  " + body, "fi"])
                self.expect_rejected(swap(PR_VERIFY, prefix + body, repl), job)

    def test_uncalled_shell_function_rejected_for_every_job(self):
        for job, prefix, body in self.each():
            with self.subTest(job=job):
                repl = block(prefix, ["run_guard() {", "  " + body, "}"])
                self.expect_rejected(swap(PR_VERIFY, prefix + body, repl), job)

    def test_case_branch_rejected(self):
        for job, prefix, body in self.each():
            with self.subTest(job=job):
                repl = block(prefix, ["case \"$X\" in", "  a) " + body + " ;;", "esac"])
                self.expect_rejected(swap(PR_VERIFY, prefix + body, repl), job)

    def test_for_loop_rejected(self):
        for job, prefix, body in self.each():
            with self.subTest(job=job):
                repl = block(prefix, ["for i in 1; do", "  " + body, "done"])
                self.expect_rejected(swap(PR_VERIFY, prefix + body, repl), job)

    def test_while_loop_rejected(self):
        for job, prefix, body in self.each():
            with self.subTest(job=job):
                repl = block(prefix, ["while false; do", "  " + body, "done"])
                self.expect_rejected(swap(PR_VERIFY, prefix + body, repl), job)

    def test_subshell_rejected(self):
        for job, prefix, body in self.each():
            with self.subTest(job=job):
                repl = block(prefix, ["(", "  " + body, ")"])
                self.expect_rejected(swap(PR_VERIFY, prefix + body, repl), job)

    def test_brace_group_rejected(self):
        for job, prefix, body in self.each():
            with self.subTest(job=job):
                repl = block(prefix, ["{", "  " + body, "}"])
                self.expect_rejected(swap(PR_VERIFY, prefix + body, repl), job)

    def test_single_line_brace_group_rejected(self):
        job, prefix, body = JOB_COMMANDS[2]          # baseline job（块标量内，无需引号）
        repl = prefix + "{ " + body + "; }"
        self.expect_rejected(swap(PR_VERIFY, prefix + body, repl), job)

    def test_real_layout_still_passes(self):
        self.assertEqual(self.problems(), [])


def replace_run(step: str, new_lines: list[str]) -> str:
    """把 step 的 `run: <cmd>` 换成块标量（避免 YAML 标量转义问题）。"""
    head, sep, tail = step.partition("        run: ")
    assert sep, step
    assert tail.endswith("\n")
    indent = " " * 10
    return head + "        run: |\n" + "".join(indent + ln + "\n" for ln in new_lines)


class AutoMergeCommandContractTest(Fixture):
    """Fix B：auto-merge 三个前置命令必须真正执行、顺序正确、独立步骤、带 env。"""

    def test_each_helper_replaced_by_echo_rejected(self):
        for label, step in (("await_checks.py", AWAIT_STEP), ("verify_required_checks.py", VERIFY_STEP),
                            ("request_automerge.py", AUTOMERGE_STEP)):
            with self.subTest(helper=label):
                body = ("echo \"NOT EXECUTED: " + label + "\" "
                        + step.split("        run: python3 scripts/ci/" + label + " ", 1)[1].strip())
                repl = replace_run(step, [body])
                problems = self.problems(candidate=CANDIDATE.replace(step, repl))
                self.assertTrue(any("没有被实际执行" in p or "缺少必需命令" in p for p in problems), problems)

    def test_helper_in_comment_rejected(self):
        repl = replace_run(AUTOMERGE_STEP,
                           ["# python3 scripts/ci/request_automerge.py --repo o/r --pr 1 --head-sha s"])
        problems = self.problems(candidate=CANDIDATE.replace(AUTOMERGE_STEP, repl))
        self.assertTrue(any("没有被实际执行" in p or "缺少必需命令" in p for p in problems), problems)

    def test_helper_moved_into_if_false_rejected(self):
        body = "python3 scripts/ci/request_automerge.py --repo o/r --pr 1 --head-sha s"
        repl = replace_run(AUTOMERGE_STEP, ["if false; then", "  " + body, "fi"])
        problems = self.problems(candidate=CANDIDATE.replace(AUTOMERGE_STEP, repl))
        self.assertTrue(any("没有被实际执行" in p for p in problems), problems)

    def test_helper_via_uncalled_function_rejected(self):
        body = "python3 scripts/ci/request_automerge.py --repo o/r --pr 1 --head-sha s"
        repl = replace_run(AUTOMERGE_STEP, ["am() {", "  " + body, "}"])
        problems = self.problems(candidate=CANDIDATE.replace(AUTOMERGE_STEP, repl))
        self.assertTrue(any("没有被实际执行" in p for p in problems), problems)

    def test_missing_expected_head_sha_arg_rejected(self):
        repl = AWAIT_STEP.replace(" --expected-head-sha s", "")
        problems = self.problems(candidate=CANDIDATE.replace(AWAIT_STEP, repl))
        self.assertTrue(any("await_checks.py" in p for p in problems), problems)

    def test_missing_env_rejected(self):
        repl = VERIFY_STEP.replace("        env:\n          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}\n", "")
        problems = self.problems(candidate=CANDIDATE.replace(VERIFY_STEP, repl))
        self.assertTrue(any("必须声明 env GITHUB_TOKEN" in p for p in problems), problems)

    def test_helpers_collapsed_into_one_step_rejected(self):
        merged = ("      - name: merged\n        env:\n          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}\n"
                  "        run: |\n"
                  "          python3 scripts/ci/await_checks.py --repo o/r --pr 1 --expected-head-sha s\n"
                  "          python3 scripts/ci/verify_required_checks.py --repo o/r --pr 1 --head-sha s\n"
                  "          python3 scripts/ci/request_automerge.py --repo o/r --pr 1 --head-sha s\n")
        problems = self.problems(candidate=CANDIDATE.replace(AWAIT_STEP + VERIFY_STEP + AUTOMERGE_STEP, merged))
        self.assertTrue(any("独立步骤" in p for p in problems), problems)

    def test_real_candidate_satisfies_automerge_contract(self):
        self.assertEqual(self.problems(), [])


class DirectAutoMergeMutationTest(Fixture):
    """Fix B3：workflow 内禁止直接发送 auto-merge / 合并 mutation（必须走 helper）。"""

    def inject(self, command: str) -> str:
        return CANDIDATE.replace(
            AUTOMERGE_STEP,
            replace_run(AUTOMERGE_STEP,
                        [command, "python3 scripts/ci/request_automerge.py --repo o/r --pr 1 --head-sha s"]))

    def assert_rejected(self, command: str, needle: str):
        problems = self.problems(candidate=self.inject(command))
        self.assertTrue(any(needle in p for p in problems), f"{command!r} -> {problems}")

    def test_gh_api_graphql_enable_auto_merge_rejected(self):
        self.assert_rejected(
            'gh api graphql -f query=\'mutation{enablePullRequestAutoMerge(input:{pullRequestId:"x",'
            'mergeMethod:MERGE})}\'', "直接 auto-merge mutation 标记")

    def test_gh_api_graphql_generic_rejected(self):
        self.assert_rejected("gh api graphql -f query='query{viewer{login}}'", "gh api graphql")

    def test_rest_merge_endpoint_rejected(self):
        self.assert_rejected('curl -sS -X PUT -H "Authorization: Bearer $TOKEN" '
                             '"https://api.github.com/repos/o/r/pulls/1/merge"', "REST merge 端点")

    def test_gh_pr_merge_rejected(self):
        self.assert_rejected("gh pr merge 1 --squash", "gh pr merge")

    def test_curl_graphql_rejected(self):
        self.assert_rejected('curl -sS https://api.github.com/graphql -d @payload.json', "HTTP 客户端直接发 GraphQL")

    def test_shell_wrapper_bypass_rejected(self):
        self.assert_rejected('bash -c "gh api graphql -f query=\'mutation{enablePullRequestAutoMerge}\'"',
                             "直接 auto-merge mutation 标记")

    def test_interpreter_inline_wrapper_rejected(self):
        self.assert_rejected('python3 -c "import json;print(\'enablePullRequestAutoMerge\')"',
                             "直接 auto-merge mutation 标记")

    def test_direct_mutation_in_pr_verify_rejected(self):
        """任何 workflow 都不得直接 mutation（含 pr-verify.yml）。"""
        pr_verify = swap(PR_VERIFY, "        run: python3 scripts/ci/verify_general_pr.py",
                         block("        run: ", ["gh api graphql -f query=\'mutation{"
                                                "enablePullRequestAutoMerge}\'"]))
        problems = self.problems(pr_verify=pr_verify)
        self.assertTrue(any("直接 auto-merge mutation 标记" in p for p in problems), problems)

    def test_real_candidate_has_no_direct_mutation(self):
        self.assertEqual(self.problems(), [])

    def test_readonly_auto_merge_field_is_not_a_violation(self):
        """读取 PR 对象的 auto_merge 字段不是 mutation（避免误伤只读回读）。"""
        candidate = CANDIDATE.replace(
            AUTOMERGE_STEP,
            replace_run(AUTOMERGE_STEP, [
                "python3 scripts/ci/request_automerge.py --repo o/r --pr 1 --head-sha s",
                'curl -sS "https://api.github.com/repos/o/r/pulls/1" | python3 -c '
                '\'import json,sys;d=json.load(sys.stdin);print(d.get("auto_merge"))\'']))
        self.assertEqual(self.problems(candidate=candidate), [])
