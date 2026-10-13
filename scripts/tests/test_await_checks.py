#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自动合并前「有界等待 + 严格检查判定 + base SHA 漂移闭环」的 mock 测试。

覆盖：尚未出现 / queued / in-progress / 全部成功 / 明确失败 / 超时 / head SHA 漂移 /
陈旧检查（其他 SHA）/ API 403 / 429 / 5xx / 来源不符 / 前置状态非法 /
**同名伪造成功检查（另一 workflow）** / **base SHA 前进** / **main ref 查询错误** /
**PR base 被改为其他分支** / 只读性（无写请求）。

**这些是 mock 逻辑测试，不代表真实 GitHub 平台端到端成功。**
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))
sys.path.insert(0, str(ROOT / "scripts" / "tests"))

import await_checks as ac  # noqa: E402
import check_provenance as cp  # noqa: E402
import prov_fixture as fx  # noqa: E402

MANIFEST = str(ROOT / ".github" / "required-checks.json")
HEAD, OTHER_SHA, NAMES = fx.HEAD, fx.OTHER_SHA, fx.NAMES


def manifest():
    return cp.load_manifest(MANIFEST)


def poll(api, clock=None, timeout=5.0, interval=1.0, max_interval=2.0):
    clock = clock or fx.FakeClock()
    p = ac.Poller(repo="o/r", pr=29, expected_head=HEAD, base_branch="main", required=manifest(),
                  token="x", timeout=timeout, interval=interval, max_interval=max_interval,
                  fetcher=api, sleeper=clock.sleep, clock=clock, log=lambda *a, **k: None)
    return p.run(), clock


def api(**kw):
    return fx.FakeApi(**kw)


class EvaluateTest(unittest.TestCase):
    def setUp(self):
        self.required = manifest()

    def _eval(self, checks, runs=None, jobs=None, head=HEAD):
        return ac.evaluate(checks, runs if runs is not None else [fx.workflow_run()],
                           fx.jobs_index(jobs if jobs is not None else fx.jobs_for(checks)),
                           self.required, head, head)

    def test_all_success(self):
        checks = fx.all_checks()
        status, per, problems = self._eval(checks)
        self.assertEqual(status, ac.STATUS_SUCCESS)
        self.assertEqual(problems, [])
        self.assertTrue(all(v["state"] == "success" for v in per.values()))

    def test_skipped_is_not_success(self):
        checks = [fx.check_run("general-pr-verify", "skipped")] + fx.all_checks()[1:]
        status, _, problems = self._eval(checks)
        self.assertEqual(status, ac.STATUS_FAILED)
        self.assertTrue(any("skipped" in p for p in problems), problems)

    def test_neutral_is_not_success(self):
        checks = [fx.check_run("general-pr-verify", "neutral")] + fx.all_checks()[1:]
        status, _, _ = self._eval(checks)
        self.assertEqual(status, ac.STATUS_FAILED)

    def test_in_progress_is_pending(self):
        checks = [fx.check_run("general-pr-verify", None, status="in_progress")] + fx.all_checks()[1:]
        status, _, _ = self._eval(checks)
        self.assertEqual(status, ac.STATUS_PENDING)

    def test_missing_is_pending(self):
        status, _, _ = self._eval([])
        self.assertEqual(status, ac.STATUS_PENDING)

    def test_wrong_source_is_not_trusted(self):
        checks = [fx.check_run("general-pr-verify", app={"slug": "evil-app", "id": 4242})]
        status, per, problems = self._eval(checks)
        self.assertEqual(status, ac.STATUS_FAILED)
        self.assertEqual(per["general-pr-verify"]["state"], "unverified")
        self.assertTrue(any("身份认证失败" in p for p in problems), problems)

    def test_stale_runs_on_other_sha_are_never_accepted(self):
        """head SHA 错误的同名检查一律不得采信（fail-closed，禁止合并）。"""
        checks = fx.all_checks(sha=OTHER_SHA)
        status, per, problems = self._eval(checks)
        self.assertEqual(status, ac.STATUS_FAILED)
        self.assertNotIn("success", {v["state"] for v in per.values()})
        self.assertTrue(any("身份认证失败" in p for p in problems), problems)

    # ---- Fix A：同名伪造成功检查不得覆盖 ----
    def test_fake_same_name_success_from_other_workflow_is_rejected(self):
        real = [fx.check_run("general-pr-verify", "failure", started="2026-10-13T01:00:00Z")]
        fake = [fx.check_run("general-pr-verify", "success", suite=fx.OTHER_SUITE, rid=424242,
                             started="2026-10-13T02:00:00Z")]
        runs = [fx.workflow_run(), fx.workflow_run(rid=fx.OTHER_RUN_ID, path=fx.OTHER_WORKFLOW_PATH,
                                                   suite=fx.OTHER_SUITE)]
        status, per, problems = self._eval(real + fake, runs, fx.jobs_for(real) + fx.jobs_for(fake))
        self.assertEqual(status, ac.STATUS_FAILED)
        self.assertEqual(per["general-pr-verify"]["conclusion"], "failure")
        self.assertTrue(per["general-pr-verify"]["rejected"], per["general-pr-verify"])

    def test_fake_success_only_from_other_workflow_is_rejected(self):
        fake = [fx.check_run("general-pr-verify", "success", suite=fx.OTHER_SUITE, rid=424242)]
        runs = [fx.workflow_run(rid=fx.OTHER_RUN_ID, path=fx.OTHER_WORKFLOW_PATH, suite=fx.OTHER_SUITE)]
        status, per, _ = self._eval(fake, runs, fx.jobs_for(fake))
        self.assertEqual(status, ac.STATUS_FAILED)
        self.assertEqual(per["general-pr-verify"]["state"], "unverified")

    def test_other_workflow_cannot_supersede_verified_failure(self):
        real = [fx.check_run("general-pr-verify", "failure", started="2026-10-13T05:00:00Z")]
        fake = [fx.check_run("general-pr-verify", "success", suite=fx.OTHER_SUITE, rid=424242,
                             started="2026-10-13T06:00:00Z")]
        runs = [fx.workflow_run(), fx.workflow_run(rid=fx.OTHER_RUN_ID, path=fx.OTHER_WORKFLOW_PATH,
                                                   suite=fx.OTHER_SUITE)]
        _status, per, _ = self._eval(real + fake, runs, fx.jobs_for(real) + fx.jobs_for(fake))
        self.assertEqual(per["general-pr-verify"].get("superseded_verified_runs"), [])


class PollerTest(unittest.TestCase):
    def test_not_yet_present_then_success(self):
        report, clock = poll(api(check_runs_seq=[[], fx.all_checks()]))
        self.assertEqual(report["status"], ac.STATUS_SUCCESS)
        self.assertEqual(report["polls"], 2)
        self.assertEqual(clock.waits, [1.0])

    def test_queued_then_success_with_increasing_interval(self):
        seq = [[fx.check_run(n, None, status="queued") for n in NAMES],
               [fx.check_run(n, None, status="in_progress") for n in NAMES], fx.all_checks()]
        report, clock = poll(api(check_runs_seq=seq))
        self.assertEqual(report["status"], ac.STATUS_SUCCESS)
        self.assertEqual(report["polls"], 3)
        self.assertEqual(clock.waits, [1.0, 2.0])

    def test_failure_terminates_immediately(self):
        checks = [fx.check_run("general-pr-verify", "failure")] + fx.all_checks()[1:]
        report, clock = poll(api(check_runs_seq=[checks, fx.all_checks()]))
        self.assertEqual(report["status"], ac.STATUS_FAILED)
        self.assertEqual(report["polls"], 1)
        self.assertEqual(clock.waits, [])
        self.assertEqual(report["checks"]["general-pr-verify"]["conclusion"], "failure")

    def test_fake_same_name_success_terminates_as_failure_without_waiting(self):
        real = [fx.check_run("general-pr-verify", "failure")] + fx.all_checks()[1:]
        fake = fx.check_run("general-pr-verify", "success", suite=fx.OTHER_SUITE, rid=424242,
                            started="2026-10-13T09:00:00Z")
        runs = [fx.workflow_run(), fx.workflow_run(rid=fx.OTHER_RUN_ID, path=fx.OTHER_WORKFLOW_PATH,
                                                   suite=fx.OTHER_SUITE)]
        report, clock = poll(api(check_runs_seq=[real + [fake]], workflow_runs=runs,
                                 jobs_by_run={fx.RUN_ID: fx.jobs_for(real),
                                              fx.OTHER_RUN_ID: fx.jobs_for([fake])}))
        self.assertEqual(report["status"], ac.STATUS_FAILED)
        self.assertEqual(report["polls"], 1)
        self.assertEqual(clock.waits, [])

    def test_skipped_terminates_as_failure(self):
        checks = [fx.check_run("general-pr-verify", "skipped")] + fx.all_checks()[1:]
        report, _ = poll(api(check_runs_seq=[checks]))
        self.assertEqual(report["status"], ac.STATUS_FAILED)

    def test_timeout_when_never_completes(self):
        report, clock = poll(api(check_runs_seq=[[]]), timeout=3.0, interval=1.0, max_interval=2.0)
        self.assertEqual(report["status"], ac.STATUS_TIMEOUT)
        self.assertGreaterEqual(sum(clock.waits), 3.0)

    def test_head_drift_after_success_fails(self):
        report, _ = poll(api(check_runs_seq=[fx.all_checks()], pr_after=fx.pr(head=OTHER_SHA)))
        self.assertEqual(report["status"], ac.STATUS_FAILED)
        self.assertTrue(any("漂移" in p for p in report["problems"]), report)

    def test_closed_pr_fails_preflight_without_polling(self):
        report, _ = poll(api(pr_before=fx.pr(state="closed")))
        self.assertEqual(report["status"], ac.STATUS_FAILED)
        self.assertEqual(report["polls"], 0)
        self.assertTrue(any("非 open" in p for p in report["problems"]), report)

    def test_wrong_base_branch_fails_preflight(self):
        report, _ = poll(api(pr_before=fx.pr(base="develop")))
        self.assertEqual(report["status"], ac.STATUS_FAILED)

    # ---- Fix C：base SHA 漂移闭环 ----
    def test_base_sha_mismatch_at_preflight_fails(self):
        report, _ = poll(api(pr_before=fx.pr(base_sha="c" * 40)))
        self.assertEqual(report["status"], ac.STATUS_FAILED)
        self.assertEqual(report["polls"], 0)
        self.assertTrue(any("base 漂移" in p for p in report["problems"]), report)

    def test_base_sha_advances_during_checks_fails_after_wait(self):
        """检查期间 main 前进（PR base 落后）⇒ 等待结束后复核失败，禁止合并。"""
        report, _ = poll(api(check_runs_seq=[fx.all_checks()],
                             main_sha_seq=[fx.MAIN_SHA, "9" * 40]))
        self.assertEqual(report["status"], ac.STATUS_FAILED)
        self.assertTrue(any("base 漂移" in p for p in report["problems"]), report)
        self.assertEqual(report["writes_performed"], [])

    def test_pr_base_changed_to_other_branch_after_checks_fails(self):
        report, _ = poll(api(check_runs_seq=[fx.all_checks()],
                             pr_after=fx.pr(base="release/2.x")))
        self.assertEqual(report["status"], ac.STATUS_FAILED)
        self.assertTrue(any("目标分支" in p for p in report["problems"]), report)

    def test_main_ref_query_error_is_fail_closed(self):
        report, _ = poll(api(errors={"/git/ref/heads/": [500] * 8}))
        self.assertEqual(report["status"], ac.STATUS_ERROR)
        self.assertEqual(report["polls"], 0)

    def test_main_ref_missing_sha_is_fail_closed(self):
        report, _ = poll(api(main_sha=""))
        self.assertEqual(report["status"], ac.STATUS_FAILED)

    def test_api_403_after_retries_is_error(self):
        report, _ = poll(api(errors={"/check-runs": [403] * 8}))
        self.assertEqual(report["status"], ac.STATUS_ERROR)

    def test_api_429_then_success_recovers(self):
        report, _ = poll(api(check_runs_seq=[fx.all_checks()], errors={"/check-runs": [429]}))
        self.assertEqual(report["status"], ac.STATUS_SUCCESS)
        self.assertGreaterEqual(report["http_requests"], 3)

    def test_api_5xx_is_error(self):
        report, _ = poll(api(errors={"/check-runs": [500] * 8}))
        self.assertEqual(report["status"], ac.STATUS_ERROR)

    def test_workflow_runs_api_error_is_fail_closed(self):
        report, _ = poll(api(check_runs_seq=[fx.all_checks()], errors={"/actions/runs": [500] * 8}))
        self.assertEqual(report["status"], ac.STATUS_ERROR)

    def test_jobs_api_error_is_fail_closed(self):
        report, _ = poll(api(check_runs_seq=[fx.all_checks()], errors={"/jobs": [500] * 8}))
        self.assertEqual(report["status"], ac.STATUS_ERROR)

    def test_no_write_requests_issued(self):
        a = api(check_runs_seq=[[], fx.all_checks()])
        poll(a)
        for url in a.calls:
            self.assertTrue(any(k in url for k in ("/pulls/", "/check-runs", "/git/ref/", "/actions/runs")),
                            url)


class ManifestTest(unittest.TestCase):
    def test_real_manifest_names_and_sources(self):
        required = manifest()
        self.assertEqual([r["name"] for r in required], NAMES)
        self.assertTrue(all(r["app_slug"] == "github-actions" and r["app_id"] == 15368 for r in required))

    def test_legacy_required_list_is_rejected(self):
        import json
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump({"required": ["a", "b"]}, fh)
            path = fh.name
        with self.assertRaises(cp.ManifestError):
            ac.load_manifest(path)

    def test_unreadable_manifest_fails_closed(self):
        with self.assertRaises(cp.ManifestError):
            ac.load_manifest("/nonexistent/manifest.json")

    def test_await_and_verify_share_one_loader(self):
        import verify_required_checks as vrc
        self.assertIs(ac.load_manifest, cp.load_manifest)
        self.assertIs(vrc.load_manifest, cp.load_manifest)


if __name__ == "__main__":
    unittest.main()
