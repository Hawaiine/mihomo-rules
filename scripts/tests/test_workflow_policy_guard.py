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
      - uses: actions/checkout@v7
        with:
          ref: ${{ github.event.pull_request.head.sha }}
          persist-credentials: false
      - run: python3 scripts/ci/verify_general_pr.py
  workflow-policy-guard:
    name: workflow-policy-guard
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
        with:
          persist-credentials: false
      - run: python3 scripts/ci/verify_workflow_policy.py
  trusted-baseline-guard:
    name: trusted-baseline-guard
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
        with:
          persist-credentials: false
      - run: python3 scripts/ci/verify_policy_baseline.py --base-sha a --head-sha b
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
    steps:
      - uses: actions/checkout@v7
        with:
          ref: main
          persist-credentials: false
      - run: python3 scripts/ci/await_checks.py --repo o/r --pr 1 --expected-head-sha s
      - run: python3 scripts/ci/verify_required_checks.py --repo o/r --pr 1 --head-sha s
      - run: python3 scripts/ci/request_automerge.py --repo o/r --pr 1 --head-sha s
"""


def build_root(tmp: str, pr_verify: str = PR_VERIFY, candidate: str = CANDIDATE,
               manifest: object = None, legacy: str | None = None) -> Path:
    root = Path(tmp)
    (root / ".github" / "workflows").mkdir(parents=True, exist_ok=True)
    (root / ".github" / "required-checks.json").write_text(
        json.dumps(MANIFEST if manifest is None else manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (root / ".github" / "workflows" / "pr-verify.yml").write_text(pr_verify, encoding="utf-8")
    (root / ".github" / "workflows" / "candidate-pr-sync.yml").write_text(candidate, encoding="utf-8")
    if legacy is not None:
        (root / ".github" / "workflows" / "daily-sync.yml").write_text(legacy, encoding="utf-8")
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
        pr_verify = PR_VERIFY.replace("      - run: python3 scripts/ci/verify_general_pr.py\n", "")
        problems = self.problems(pr_verify=pr_verify)
        self.assertTrue(any("verify_general_pr" in p or "必需检查" in p for p in problems), problems)

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
        candidate = CANDIDATE.replace(
            "      - run: python3 scripts/ci/verify_required_checks.py --repo o/r --pr 1 --head-sha s\n", "")
        self.assertProblem("verify_required_checks.py", candidate=candidate)

    def test_missing_await_call_rejected(self):
        candidate = CANDIDATE.replace(
            "      - run: python3 scripts/ci/await_checks.py --repo o/r --pr 1 --expected-head-sha s\n", "")
        self.assertProblem("await_checks.py", candidate=candidate)

    def test_mutation_before_verification_rejected(self):
        candidate = CANDIDATE.replace(
            "      - run: python3 scripts/ci/await_checks.py --repo o/r --pr 1 --expected-head-sha s\n"
            "      - run: python3 scripts/ci/verify_required_checks.py --repo o/r --pr 1 --head-sha s\n"
            "      - run: python3 scripts/ci/request_automerge.py --repo o/r --pr 1 --head-sha s\n",
            "      - run: python3 scripts/ci/request_automerge.py --repo o/r --pr 1 --head-sha s\n"
            "      - run: python3 scripts/ci/await_checks.py --repo o/r --pr 1 --expected-head-sha s\n"
            "      - run: python3 scripts/ci/verify_required_checks.py --repo o/r --pr 1 --head-sha s\n")
        self.assertProblem("必须位于 auto-merge mutation 之前", candidate=candidate)

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
            "      - run: python3 scripts/ci/request_automerge.py --repo o/r --pr 1 --head-sha s",
            "      - run: gh pr merge 1 --squash")
        self.assertProblem("gh pr merge", candidate=candidate)

    def test_rest_merge_endpoint_rejected(self):
        candidate = CANDIDATE.replace(
            "      - run: python3 scripts/ci/request_automerge.py --repo o/r --pr 1 --head-sha s",
            "      - run: curl -X PUT https://api.github.com/repos/o/r/pulls/1/merge")
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
        pr_verify = PR_VERIFY.replace(
            "      - run: python3 scripts/ci/verify_general_pr.py",
            "      - uses: actions/checkout@v7\n        with:\n          repository: Hawaiine/Oasisic-Icons\n"
            "          ref: main\n          persist-credentials: false\n"
            "      - run: python3 scripts/ci/verify_general_pr.py")
        self.assertProblem("已批准 pin", pr_verify=pr_verify)

    def test_icons_checkout_with_pin_passes(self):
        pr_verify = PR_VERIFY.replace(
            "      - run: python3 scripts/ci/verify_general_pr.py",
            f"      - uses: actions/checkout@v7\n        with:\n          repository: Hawaiine/Oasisic-Icons\n"
            f"          ref: {ICONS_SHA}\n          persist-credentials: false\n"
            "      - run: python3 scripts/ci/verify_general_pr.py")
        self.assertEqual(self.problems(pr_verify=pr_verify), [])


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
