#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R-2：必需检查必须绑定**当前 PR 事件**，不得复用同一 SHA 上的历史成功。

真实事故（2026-10-23）：PR #33 与 PR #35 共用 head `b767503a…`。
PR #33 的 `pr-verify` run 39868393944 产出了 4 个 success 检查；
PR #35 自己的 run 39941736202 处于 `action_required`，**jobs=0、零检查**。
按当时的 provenance 逻辑（只比对 name/app/head_sha/workflow path/event），
PR #33 的检查**完整通过认证**，于是 PR #35 会被判为「必需检查 success」——
这正是「历史成功冒充当前事件成功」。

修复依据（真实 API 字段，已实测）：workflow run 对象带 `pull_requests[]`，
run 39868393944 → `number=33`；run 39941736202 → `number=35`。
因此来源认证必须再绑定 PR 编号，并且必须以**该 PR 最新的一次验证运行**为准。

**mock 逻辑测试，不代表真实 GitHub 平台端到端成功。**
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))
sys.path.insert(0, str(ROOT / "scripts" / "tests"))

import check_provenance as cp  # noqa: E402
import prov_fixture as fx  # noqa: E402

MANIFEST = str(ROOT / ".github" / "required-checks.json")
PR = 35
OTHER_PR = 33


def manifest():
    return cp.load_manifest(MANIFEST)


def run_for_pr(pr_number, *, rid=fx.RUN_ID, sha=fx.HEAD, suite=fx.SUITE,
               event="pull_request", path=fx.PR_VERIFY_PATH, created="2026-10-23T00:00:00Z",
               attempt=1, pull_requests="default"):
    run = fx.workflow_run(rid=rid, path=path, event=event, sha=sha, suite=suite)
    run["created_at"] = created
    run["run_attempt"] = attempt
    if pull_requests == "default":
        run["pull_requests"] = [{"number": pr_number, "head": {"sha": sha}, "base": {"ref": "main"}}]
    elif pull_requests is None:
        run.pop("pull_requests", None)
    else:
        run["pull_requests"] = pull_requests
    return run


def select(check_runs, workflow_runs, pr_number=PR, head=fx.HEAD):
    return cp.select_checks(check_runs=check_runs, manifest=manifest(),
                            workflow_runs=workflow_runs,
                            jobs_by_id=fx.jobs_index(fx.jobs_for(check_runs)
                                                     if check_runs else []),
                            expected_head=head, pr_head=head,
                            expected_pr_number=pr_number)


class SameShaOtherPrTest(unittest.TestCase):
    """核心事故复现：同 SHA、同名称、同 App，但 run 属于另一个 PR。"""

    def test_historical_success_from_other_pr_rejected(self):
        checks = fx.all_checks()
        runs = [run_for_pr(OTHER_PR, suite=fx.SUITE)]
        per, problems = select(checks, runs, pr_number=PR)
        self.assertEqual(per["general-pr-verify"]["state"], cp.STATE_UNVERIFIED)
        self.assertTrue(any("PR #33" in p or "PR #33" in str(problems) or "非期望" in p
                            for p in problems + [
                                x for r in per["general-pr-verify"]["rejected"]
                                for x in r["problems"]]), per)
        self.assertNotEqual(cp.overall_state(per), "success")

    def test_same_pr_same_run_passes(self):
        checks = fx.all_checks()
        per, problems = select(checks, [run_for_pr(PR)])
        self.assertEqual(problems, [])
        self.assertEqual(cp.overall_state(per), "success")

    def test_missing_pull_requests_field_fails_closed(self):
        checks = fx.all_checks()
        per, _ = select(checks, [run_for_pr(PR, pull_requests=None)])
        self.assertEqual(per["general-pr-verify"]["state"], cp.STATE_UNVERIFIED)

    def test_empty_pull_requests_fails_closed(self):
        checks = fx.all_checks()
        per, _ = select(checks, [run_for_pr(PR, pull_requests=[])])
        self.assertEqual(per["general-pr-verify"]["state"], cp.STATE_UNVERIFIED)

    def test_multiple_pr_associations_rejected(self):
        checks = fx.all_checks()
        runs = [run_for_pr(PR, pull_requests=[{"number": PR}, {"number": OTHER_PR}])]
        per, _ = select(checks, runs)
        self.assertEqual(per["general-pr-verify"]["state"], cp.STATE_UNVERIFIED)

    def test_non_pull_request_event_rejected_even_with_correct_pr(self):
        checks = fx.all_checks()
        per, _ = select(checks, [run_for_pr(PR, event="workflow_dispatch")])
        self.assertEqual(per["general-pr-verify"]["state"], cp.STATE_UNVERIFIED)


class NewerRunForSamePrTest(unittest.TestCase):
    """同一 PR 已有更新的运行（尚未产出成功检查）时，旧运行的 success 不得沿用。"""

    def test_older_success_superseded_by_newer_run_without_checks(self):
        checks = fx.all_checks()
        runs = [run_for_pr(PR, rid=fx.RUN_ID, suite=fx.SUITE, created="2026-10-23T00:00:00Z"),
                run_for_pr(PR, rid=5550001, suite=8880001, created="2026-10-23T02:00:00Z")]
        per, problems = select(checks, runs)
        self.assertEqual(per["general-pr-verify"]["state"], cp.STATE_UNVERIFIED)
        self.assertTrue(any("更新的验证运行" in p for p in problems), problems)
        self.assertNotEqual(cp.overall_state(per), "success")

    def test_newer_run_with_its_own_success_is_accepted(self):
        checks = [fx.check_run("general-pr-verify", suite=8880001,
                               rid=fx.JOB_IDS["general-pr-verify"]),
                  fx.check_run("workflow-policy-guard", suite=8880001,
                               rid=fx.JOB_IDS["workflow-policy-guard"]),
                  fx.check_run("trusted-baseline-guard", suite=8880001,
                               rid=fx.JOB_IDS["trusted-baseline-guard"])]
        runs = [run_for_pr(PR, rid=fx.RUN_ID, suite=fx.SUITE, created="2026-10-23T00:00:00Z"),
                run_for_pr(PR, rid=5550001, suite=8880001, created="2026-10-23T02:00:00Z")]
        per, problems = select(checks, runs)
        self.assertEqual(problems, [])
        self.assertEqual(cp.overall_state(per), "success")
        self.assertEqual(per["general-pr-verify"]["workflow_run_id"], 5550001)

    def test_rerun_higher_attempt_wins(self):
        """同一 run id 的更高 attempt 视为更新；旧 attempt 的检查不得采信。"""
        checks = fx.all_checks()
        runs = [run_for_pr(PR, rid=fx.RUN_ID, suite=8880001, created="2026-10-23T00:00:00Z",
                           attempt=2)]
        per, _ = select(checks, runs)
        # 旧 attempt 的 suite 不在列表里 → 无法映射 → 拒绝（fail-closed）
        self.assertEqual(per["general-pr-verify"]["state"], cp.STATE_UNVERIFIED)


class ActionRequiredCurrentEventTest(unittest.TestCase):
    """当前事件为 action_required、jobs=0 时，只有历史 success 可用 ⇒ 必须拒绝。"""

    def test_action_required_current_run_does_not_borrow_history(self):
        old_checks = fx.all_checks()                       # 属于 PR #33 的 run
        runs = [run_for_pr(OTHER_PR, rid=39868393944, suite=fx.SUITE,
                           created="2026-10-22T22:55:47Z"),
                run_for_pr(PR, rid=39941736202, suite=7770001,
                           created="2026-10-23T10:48:55Z")]
        for r in runs:
            if r["id"] == 39941736202:
                r["status"] = "completed"
                r["conclusion"] = "action_required"
        per, problems = select(old_checks, runs, pr_number=PR)
        self.assertEqual(per["general-pr-verify"]["state"], cp.STATE_UNVERIFIED)
        self.assertNotEqual(cp.overall_state(per), "success")

    def test_missing_expected_pr_number_fails_closed(self):
        checks = fx.all_checks()
        with self.assertRaises(TypeError):
            cp.select_checks(check_runs=checks, manifest=manifest(),
                             workflow_runs=[run_for_pr(PR)],
                             jobs_by_id=fx.jobs_index(fx.all_jobs()),
                             expected_head=fx.HEAD, pr_head=fx.HEAD)


class ProvenanceReportFieldsTest(unittest.TestCase):
    def test_report_exposes_pr_binding(self):
        per, _ = select(fx.all_checks(), [run_for_pr(PR)])
        info = per["general-pr-verify"]
        self.assertEqual(info["workflow_run_pr_number"], PR)
        self.assertEqual(info["workflow_run_id"], fx.RUN_ID)


if __name__ == "__main__":
    unittest.main()