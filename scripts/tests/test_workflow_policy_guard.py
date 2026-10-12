"""workflow 策略 guard 的定向测试（scripts/ci/verify_workflow_policy.py）。

覆盖：
  * 正向：合规的候选更新 + 必需检查集合通过。
  * 负向：pull_request_target / write-all / 未门控的定时写入 / 必需检查缺失或漂移 /
    auto-merge 未先验证 / 候选 workflow 推送 main / 检出 PR head 且持写权限 /
    persist-credentials 未关闭 / YAML 不可解析（fail-closed）。

测试只在临时目录里构造 workflow 文本，不触碰真实仓库、网络与 git ref。
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

import verify_workflow_policy as wp  # noqa: E402

REQUIRED = ["general-pr-verify", "workflow-policy-guard", "trusted-baseline-guard"]

PR_VERIFY_TEMPLATE = """name: General PR verification
on:
  pull_request:
    branches:
      - main
  workflow_dispatch:
    inputs:
      pr_number:
        required: true
        type: string
permissions:
  contents: read
jobs:
  general-pr-verify:
    name: general-pr-verify
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
        with:
          ref: ${{ steps.pr.outputs.head }}
          persist-credentials: false
  workflow-policy-guard:
    name: workflow-policy-guard
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
        with:
          persist-credentials: false
  trusted-baseline-guard:
    name: trusted-baseline-guard
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
        with:
          persist-credentials: false
"""

CANDIDATE_TEMPLATE = """name: 候选分支更新（PR-first）
on:
  schedule:
    - cron: '45 22 * * *'
  workflow_dispatch:
permissions:
  contents: read
jobs:
  gate:
    runs-on: ubuntu-latest
    steps:
      - run: echo "${{ vars.CANDIDATE_SYNC_ENABLED }}"
  publish:
    runs-on: ubuntu-latest
    permissions:
      contents: write
      pull-requests: write
      actions: write
    steps:
      - uses: actions/checkout@v7
        with:
          ref: main
          persist-credentials: false
      - run: git push --force-with-lease origin "HEAD:refs/heads/candidate/rules-update"
"""

LEGACY_TEMPLATE = """name: 每日规则同步
on:
  schedule:
    - cron: '0 22 * * *'
  workflow_dispatch:
permissions:
  contents: write
jobs:
  sync:
    runs-on: ubuntu-latest
    steps:
      - run: git push origin HEAD:${{ github.ref_name }}
"""


def build_root(pr_verify: str = PR_VERIFY_TEMPLATE, candidate: str = CANDIDATE_TEMPLATE,
               legacy: str = LEGACY_TEMPLATE, required: list[str] | None = None,
               required_raw: str | None = None) -> Path:
    root = Path(tempfile.mkdtemp())
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "workflows" / "pr-verify.yml").write_text(pr_verify, encoding="utf-8")
    (root / ".github" / "workflows" / "candidate-pr-sync.yml").write_text(candidate, encoding="utf-8")
    (root / ".github" / "workflows" / "daily-sync.yml").write_text(legacy, encoding="utf-8")
    if required_raw is None:
        payload = {"schema": "mihomo-rules-required-checks", "version": 1,
                   "workflow": ".github/workflows/pr-verify.yml",
                   "required": required or REQUIRED}
        required_raw = json.dumps(payload, ensure_ascii=False)
    (root / ".github" / "required-checks.json").write_text(required_raw, encoding="utf-8")
    return root


class WorkflowPolicyGuardTest(unittest.TestCase):
    def problems(self, root: Path) -> list[str]:
        problems, _info = wp.run(root)
        return problems

    def test_valid_layout_passes(self) -> None:
        self.assertEqual(self.problems(build_root()), [])

    def test_pull_request_target_is_rejected(self) -> None:
        bad = PR_VERIFY_TEMPLATE.replace("  pull_request:\n", "  pull_request_target:\n")
        problems = self.problems(build_root(pr_verify=bad))
        self.assertTrue(any("pull_request_target" in p for p in problems), problems)

    def test_write_all_is_rejected(self) -> None:
        bad = CANDIDATE_TEMPLATE.replace("permissions:\n  contents: read", "permissions: write-all")
        problems = self.problems(build_root(candidate=bad))
        self.assertTrue(any("write-all" in p for p in problems), problems)

    def test_scheduled_writer_without_gate_is_rejected(self) -> None:
        bad = CANDIDATE_TEMPLATE.replace("CANDIDATE_SYNC_ENABLED", "SOME_OTHER_FLAG")
        problems = self.problems(build_root(candidate=bad))
        self.assertTrue(any("CANDIDATE_SYNC_ENABLED" in p for p in problems), problems)

    def test_missing_required_check_is_rejected(self) -> None:
        bad = PR_VERIFY_TEMPLATE.replace("""  trusted-baseline-guard:
    name: trusted-baseline-guard
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
        with:
          persist-credentials: false
""", "")
        problems = self.problems(build_root(pr_verify=bad))
        self.assertTrue(any("must equal required checks" in p for p in problems), problems)

    def test_job_name_drift_is_rejected(self) -> None:
        bad = PR_VERIFY_TEMPLATE.replace("    name: workflow-policy-guard", "    name: policy-guard")
        problems = self.problems(build_root(pr_verify=bad))
        self.assertTrue(any("must set name: workflow-policy-guard" in p for p in problems), problems)

    def test_auto_merge_without_binding_verification_is_rejected(self) -> None:
        bad = CANDIDATE_TEMPLATE.replace(
            '      - run: git push --force-with-lease origin "HEAD:refs/heads/candidate/rules-update"',
            '      - run: git push --force-with-lease origin "HEAD:refs/heads/candidate/rules-update"\n'
            '      - run: echo enablePullRequestAutoMerge',
        )
        problems = self.problems(build_root(candidate=bad))
        self.assertTrue(any("verify_required_checks.py" in p for p in problems), problems)

    def test_candidate_pushing_main_is_rejected(self) -> None:
        bad = CANDIDATE_TEMPLATE.replace('"HEAD:refs/heads/candidate/rules-update"', "HEAD:main")
        problems = self.problems(build_root(candidate=bad))
        self.assertTrue(any("must never push main" in p for p in problems), problems)

    def test_candidate_top_level_write_is_rejected(self) -> None:
        bad = CANDIDATE_TEMPLATE.replace("permissions:\n  contents: read", "permissions:\n  contents: write")
        problems = self.problems(build_root(candidate=bad))
        self.assertTrue(any("top-level permissions read-only" in p for p in problems), problems)

    def test_pr_head_checkout_with_write_permission_is_rejected(self) -> None:
        bad = CANDIDATE_TEMPLATE.replace("""    permissions:
      contents: write
      pull-requests: write
      actions: write""", """    permissions:
      contents: write""").replace("          ref: main\n", "          ref: ${{ github.event.pull_request.head.sha }}\n")
        problems = self.problems(build_root(candidate=bad))
        self.assertTrue(any("while holding contents: write" in p for p in problems), problems)

    def test_persisted_credentials_are_rejected(self) -> None:
        bad = PR_VERIFY_TEMPLATE.replace("persist-credentials: false", "persist-credentials: true", 1)
        problems = self.problems(build_root(pr_verify=bad))
        self.assertTrue(any("persist-credentials" in p for p in problems), problems)

    def test_unparsable_workflow_fails_closed(self) -> None:
        bad = "name: broken\non: [\n  pull_request\n"
        problems = self.problems(build_root(pr_verify=bad))
        self.assertTrue(any("cannot parse workflow YAML" in p for p in problems), problems)

    def test_invalid_required_checks_manifest_fails_closed(self) -> None:
        problems = self.problems(build_root(required_raw='{"required": "not-a-list"}'))
        self.assertTrue(any("'required' must be a non-empty list" in p for p in problems), problems)

    def test_duplicate_required_checks_fail_closed(self) -> None:
        problems = self.problems(build_root(required=["a", "a", "b"]))
        self.assertTrue(any("duplicate check names" in p for p in problems), problems)

    def test_missing_required_checks_file_fails_closed(self) -> None:
        root = build_root()
        (root / ".github" / "required-checks.json").unlink()
        problems = self.problems(root)
        self.assertTrue(any("missing required-checks manifest" in p for p in problems), problems)


if __name__ == "__main__":
    unittest.main()
