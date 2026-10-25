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


class MalformedAssociationTest(unittest.TestCase):
    """malformed `pull_requests[]` 必须**整条**失效，不得丢弃异常元素后继续采信。

    真实风险：若输入同时含一条有效关联和一个畸形元素，宽容实现会丢掉畸形元素、只返回有效编号，
    于是 `run_belongs_to_pr()` 会把「本已不完整/格式异常的关联信息」当成唯一关联而放行。
    因此 `run_pr_numbers()` 必须严格：任一元素不可验证 ⇒ 返回 []，由上层 fail-closed。

    每个用例都同时断言 (a) 解析结果 与 (b) **经过生产来源认证调用链**的判定，
    不只测试孤立 helper。
    """

    def assert_rejected(self, pull_requests):
        run = run_for_pr(PR, pull_requests=pull_requests)
        self.assertEqual(cp.run_pr_numbers(run), [], f"malformed 关联必须整条失效：{pull_requests!r}")
        per, _ = select(fx.all_checks(), [run])
        self.assertEqual(per["general-pr-verify"]["state"], cp.STATE_UNVERIFIED,
                         f"malformed 关联不得通过来源认证：{pull_requests!r}")
        self.assertNotEqual(cp.overall_state(per), "success")
        belongs, why = cp.run_belongs_to_pr(run, PR)
        self.assertFalse(belongs, why)

    def test_valid_plus_non_object_element(self):
        """用例 A：有效关联 + 非对象元素。"""
        self.assert_rejected([{"number": PR}, "malformed"])

    def test_valid_plus_object_without_number(self):
        """用例 B：有效关联 + 缺少编号的对象。"""
        self.assert_rejected([{"number": PR}, {"unexpected": "missing number"}])

    def test_valid_plus_string_number(self):
        """用例 C：有效关联 + 字符串编号（不得自动转成整数留下有效编号）。"""
        self.assert_rejected([{"number": PR}, {"number": "36"}])

    def test_valid_plus_boolean_number(self):
        """用例 D：有效关联 + 布尔编号（True 不得被当作 1）。"""
        self.assert_rejected([{"number": PR}, {"number": True}])

    def test_valid_plus_zero_and_negative(self):
        """正整数的边界：0 与负数必须整条失效。"""
        self.assert_rejected([{"number": PR}, {"number": 0}])
        self.assert_rejected([{"number": PR}, {"number": -1}])

    def test_non_list_and_empty_are_invalid(self):
        for raw in ({}, {"number": PR}, "35", 35, None, []):
            with self.subTest(raw=raw):
                run = run_for_pr(PR, pull_requests=raw)
                self.assertEqual(cp.run_pr_numbers(run), [])

    def test_multiple_valid_numbers_preserved_for_rejection(self):
        """用例 E：多个有效编号必须**保留多编号**，由上层判为多关联并拒绝。"""
        run = run_for_pr(PR, pull_requests=[{"number": PR}, {"number": 36}])
        self.assertEqual(cp.run_pr_numbers(run), [PR, 36], "不得静默缩减为单一编号")
        belongs, why = cp.run_belongs_to_pr(run, PR)
        self.assertFalse(belongs)
        self.assertIn("多个 PR", why)
        per, _ = select(fx.all_checks(), [run])
        self.assertEqual(per["general-pr-verify"]["state"], cp.STATE_UNVERIFIED)

    def test_normal_single_association_not_regressed(self):
        """用例 F：正常单一关联不得回归。"""
        run = run_for_pr(PR)
        self.assertEqual(cp.run_pr_numbers(run), [PR])
        per, problems = select(fx.all_checks(), [run])
        self.assertEqual(problems, [])
        self.assertEqual(cp.overall_state(per), "success")


class StaleSuccessVsMalformedNewerRunTest(unittest.TestCase):
    """更新的运行**明确提及目标 PR**但关联不可验证/非唯一时，不得忽略它并沿用旧成功。

    缺口：`_newest_verification_run()` 只把 `run_pr_numbers(run) == [expected_pr]` 的运行列为候选。
    若更新的运行原始 `pull_requests` 里明明含目标 PR（如 `[{"number":36}, "malformed"]`），
    整条解析为 `[]` 后被完全排除，于是更旧的、已验证成功的运行仍被当成「最新」，
    旧 success 被沿用——与既有 R-2 语义（更新运行尚未产出有效检查时旧成功不得沿用）冲突。

    语义边界（不得越界）：仅凭相同 SHA / 相同 workflow path / 相同 event，**不能**把一条属于
    其他 PR 的正常运行当成目标 PR 的运行；只有原始数据**明确提及**目标 PR 才进入本判定。
    """

    def runs_with_newer(self, newer_pull_requests, *, newer_created="2026-10-25T02:00:00Z",
                        older_created="2026-10-25T00:00:00Z", older_pr=PR):
        older = run_for_pr(older_pr, rid=7000001, suite=7001, created=older_created)
        newer = run_for_pr(PR, rid=7000002, suite=7002, created=newer_created,
                           pull_requests=newer_pull_requests)
        return older, newer

    def old_success_checks(self):
        return [fx.check_run(n, suite=7001, rid=fx.JOB_IDS[n]) for n in fx.NAMES]

    def test_case_a_newer_malformed_run_blocks_old_success(self):
        older, newer = self.runs_with_newer([{"number": PR}, "malformed"])
        per, problems = select(self.old_success_checks(), [older, newer])
        self.assertNotEqual(cp.overall_state(per), "success",
                            "更新的畸形关联运行不得被忽略")
        state = per["general-pr-verify"]["state"]
        self.assertEqual(state, cp.STATE_UNVERIFIED)
        joined = " ".join(problems)
        self.assertTrue("关联" in joined or "不可验证" in joined, problems)
        info = per["general-pr-verify"]
        # 判定必须能区分「有更新但关联畸形」与「根本没有运行」：两者都不可采信，
        # 但审计字段必须指明被考虑过的旧检查与更新的声明运行。
        self.assertEqual(info.get("newest_claiming_run_id"), 7000002)
        self.assertEqual(info.get("considered_workflow_run_id"), 7000001)
        self.assertIsNotNone(info.get("considered_check_run_id"))
        self.assertIn("7000002", info.get("detail", ""))

    def test_case_a2_newer_missing_number_element_blocks_old_success(self):
        older, newer = self.runs_with_newer([{"number": PR}, {"unexpected": "x"}])
        per, _ = select(self.old_success_checks(), [older, newer])
        self.assertNotEqual(cp.overall_state(per), "success")

    def test_case_d_multi_association_newer_run_blocks_old_success(self):
        """多关联运行不得被当作唯一有效运行，也不得被忽略后无条件恢复旧成功。"""
        older, newer = self.runs_with_newer([{"number": PR}, {"number": 37}])
        per, problems = select(self.old_success_checks(), [older, newer])
        self.assertNotEqual(cp.overall_state(per), "success")
        self.assertEqual(per["general-pr-verify"]["state"], cp.STATE_UNVERIFIED)

    def test_case_b_runs_belonging_to_other_pr_are_not_borrowed(self):
        """同 SHA 但只属于 PR #33 的运行，不得被判为目标 PR #35 的运行。"""
        older = run_for_pr(33, rid=7000011, suite=7011, created="2026-10-25T00:00:00Z")
        ignored_newer = run_for_pr(33, rid=7000012, suite=7012, created="2026-10-25T02:00:00Z")
        per, problems = select(self.old_success_checks(), [older, ignored_newer],
                               pr_number=PR)          # PR=35
        self.assertNotEqual(cp.overall_state(per), "success")
        self.assertEqual(per["general-pr-verify"]["state"], cp.STATE_UNVERIFIED)
        self.assertIsNone(per["general-pr-verify"].get("newest_claiming_run_id"))

    def test_case_c_normal_newest_run_still_passes(self):
        older, newer = self.runs_with_newer([{"number": PR}])
        checks = [fx.check_run(n, suite=7002, rid=fx.JOB_IDS[n]) for n in fx.NAMES]
        per, problems = select(checks, [older, newer])
        self.assertEqual(problems, [])
        self.assertEqual(cp.overall_state(per), "success")
        self.assertEqual(per["general-pr-verify"]["newest_pr_run_id"], 7000002)

    def test_claims_helper_is_strict(self):
        self.assertTrue(cp.run_claims_pr(run_for_pr(PR), PR))
        self.assertTrue(cp.run_claims_pr(run_for_pr(PR, pull_requests=[{"number": PR}, "x"]), PR))
        self.assertFalse(cp.run_claims_pr(run_for_pr(PR, pull_requests=[{"number": "36"}]), PR))
        self.assertFalse(cp.run_claims_pr(run_for_pr(PR, pull_requests=[{"number": True}]), PR))
        self.assertFalse(cp.run_claims_pr(run_for_pr(PR, pull_requests=[]), PR))
        self.assertFalse(cp.run_claims_pr(run_for_pr(PR, pull_requests=None), PR))


class PrNumberValidationTest(unittest.TestCase):
    """目标 PR 编号必须是**严格正整数**：bool/零/负数/字符串/浮点/None 一律 fail-closed。

    Python 中 `True == 1`、`0 == 0` 都成立，因此若不在入口校验编号参数本身，
    布尔值或 0 可能通过相等比较而进入来源认证调用链。这不是说真实 PR #36 触发过该问题，
    而是用负向测试把参数边界封闭。

    每个断言都作用于**生产函数**（`run_claims_pr` / `run_belongs_to_pr` / `select_checks`），
    不只测试辅助函数或夹具。
    """

    INVALID_NUMBERS = (True, False, 0, -1, "36", 36.0, None)
    INVALID_SOURCES = ({"number": True}, {"number": False}, {"number": 0},
                       {"number": -1}, {"number": "36"}, {"number": 36.0})

    def test_claims_rejects_invalid_expected_pr_number(self):
        run = run_for_pr(PR)                      # 原始关联明确含 35
        for bad in self.INVALID_NUMBERS:
            with self.subTest(expected=bad):
                self.assertFalse(cp.run_claims_pr(run, bad))

    def test_claims_ignores_invalid_source_numbers(self):
        for item in self.INVALID_SOURCES:
            with self.subTest(item=item):
                run = run_for_pr(PR, pull_requests=[item])
                self.assertFalse(cp.run_claims_pr(run, PR))
                self.assertFalse(cp.run_claims_pr(run, 1))     # True 不得被当作 1

    def test_claims_keeps_detection_semantics(self):
        self.assertTrue(cp.run_claims_pr(run_for_pr(PR), PR))
        self.assertTrue(cp.run_claims_pr(run_for_pr(PR, pull_requests=[{"number": PR}, "malformed"]), PR))
        self.assertFalse(cp.run_claims_pr(run_for_pr(PR, pull_requests=[]), PR))
        self.assertFalse(cp.run_claims_pr(run_for_pr(PR, pull_requests=None), PR))

    def test_belongs_to_pr_rejects_invalid_expected_number(self):
        run = run_for_pr(1, pull_requests=[{"number": 1}])
        for bad in self.INVALID_NUMBERS:
            with self.subTest(expected=bad):
                belongs, reason = cp.run_belongs_to_pr(run, bad)
                self.assertFalse(belongs)
                self.assertIn("无效", reason)

    def test_belongs_to_pr_keeps_strict_association(self):
        ok, why = cp.run_belongs_to_pr(run_for_pr(PR), PR)
        self.assertTrue(ok, why)
        for raw in ([{"number": PR}, {"number": 36}], [{"number": PR}, "malformed"],
                    [], [{"number": "36"}], [{"number": True}]):
            with self.subTest(raw=raw):
                run = run_for_pr(PR, pull_requests=raw)
                belongs, why = cp.run_belongs_to_pr(run, PR)
                self.assertFalse(belongs, f"{raw!r} 不应通过：{why}")
        belongs, why = cp.run_belongs_to_pr(run_for_pr(OTHER_PR), PR)
        self.assertFalse(belongs, why)

    def test_select_checks_rejects_invalid_expected_number(self):
        for bad in self.INVALID_NUMBERS:
            with self.subTest(expected=bad):
                per, problems = select(fx.all_checks(), [run_for_pr(PR)], pr_number=bad)
                self.assertNotEqual(cp.overall_state(per), "success")
                self.assertEqual(sorted(per), sorted(fx.NAMES))
                for name in fx.NAMES:
                    self.assertEqual(per[name]["state"], cp.STATE_UNVERIFIED, name)
                self.assertTrue(any("无效" in p for p in problems), problems)

    def test_select_checks_invalid_pr_precedes_malformed_workflow_run(self):
        """入口编号错误时，必须先 fail-closed；不得先对畸形 workflow run 做 int 转换。"""
        manifest = cp.load_manifest(MANIFEST)
        malformed_run = {"check_suite_id": "not-an-integer", "id": "also-not-an-integer",
                         "pull_requests": [{"number": 36}]}
        for bad in self.INVALID_NUMBERS:
            with self.subTest(expected=bad):
                per, problems = cp.select_checks(
                    check_runs=fx.all_checks(), manifest=manifest,
                    workflow_runs=[malformed_run], jobs_by_id=fx.jobs_index(fx.all_jobs()),
                    expected_head=fx.HEAD, pr_head=fx.HEAD, expected_pr_number=bad)
                self.assertNotEqual(cp.overall_state(per), "success")
                self.assertEqual(sorted(per), sorted(fx.NAMES))
                self.assertTrue(all(v["state"] == cp.STATE_UNVERIFIED for v in per.values()))
                self.assertTrue(any("编号无效" in p for p in problems), problems)

    def test_belongs_invalid_pr_precedes_source_parsing(self):
        malformed_run = {"pull_requests": 35}
        for bad in self.INVALID_NUMBERS:
            with self.subTest(expected=bad):
                belongs, reason = cp.run_belongs_to_pr(malformed_run, bad)
                self.assertFalse(belongs)
                self.assertIn("编号无效", reason)

    def test_claims_invalid_pr_precedes_source_parsing(self):
        class ExplodingSource(dict):
            def get(self, key, default=None):
                raise AssertionError("invalid expected PR must be rejected before reading run data")
        for bad in self.INVALID_NUMBERS:
            with self.subTest(expected=bad):
                self.assertFalse(cp.run_claims_pr(ExplodingSource(), bad))

    def test_select_checks_normal_positive_number_unchanged(self):
        per, problems = select(fx.all_checks(), [run_for_pr(PR)], pr_number=PR)
        self.assertEqual(problems, [])
        self.assertEqual(cp.overall_state(per), "success")


class ProvenanceReportFieldsTest(unittest.TestCase):
    def test_report_exposes_pr_binding(self):
        per, _ = select(fx.all_checks(), [run_for_pr(PR)])
        info = per["general-pr-verify"]
        self.assertEqual(info["workflow_run_pr_number"], PR)
        self.assertEqual(info["workflow_run_id"], fx.RUN_ID)


if __name__ == "__main__":
    unittest.main()