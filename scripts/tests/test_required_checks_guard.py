#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""严格必需检查验证器的测试（success 白名单 + **来源/身份认证** + base SHA + fail-closed）。

关键新增（Fix A）：同名伪造检查必须被拒绝——调用 `verify_required_checks.evaluate` /
`verify` 的**实际判定逻辑**，而不是对 JSON 做字符串判断。
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))
sys.path.insert(0, str(ROOT / "scripts" / "tests"))

import check_provenance as cp  # noqa: E402
import prov_fixture as fx  # noqa: E402
import verify_required_checks as vrc  # noqa: E402

MANIFEST = str(ROOT / ".github" / "required-checks.json")
HEAD, OTHER, NAMES = fx.HEAD, fx.OTHER_SHA, fx.NAMES


class EvaluateTest(unittest.TestCase):
    def setUp(self):
        self.required = vrc.load_manifest(MANIFEST)

    def ev(self, checks, pr_obj=None, head=HEAD, runs=None, jobs=None, main_sha=fx.MAIN_SHA):
        return vrc.evaluate(pr_obj or fx.pr(), checks,
                            runs if runs is not None else [fx.workflow_run()],
                            fx.jobs_index(jobs if jobs is not None else fx.jobs_for(checks)),
                            self.required, head, "main", main_sha)

    def test_all_success_passes(self):
        checks = fx.all_checks()
        ok, problems, report = self.ev(checks)
        self.assertTrue(ok, problems)
        info = report["checks"]["general-pr-verify"]
        self.assertEqual(info["state"], "success")
        self.assertEqual(info["job_id"], fx.JOB_IDS["general-pr-verify"])
        self.assertEqual(info["workflow_run_id"], fx.RUN_ID)
        self.assertEqual(report["provenance_state"], "success")

    def test_skipped_fails(self):
        checks = [fx.check_run("general-pr-verify", "skipped")] + fx.all_checks()[1:]
        ok, problems, report = self.ev(checks)
        self.assertFalse(ok)
        self.assertEqual(report["checks"]["general-pr-verify"]["state"], "not-success")
        self.assertTrue(any("skipped" in p for p in problems), problems)

    def test_neutral_cancelled_timed_out_failure_all_fail(self):
        for conclusion in ("neutral", "cancelled", "timed_out", "action_required", "stale", "failure"):
            with self.subTest(conclusion=conclusion):
                checks = [fx.check_run("general-pr-verify", conclusion)] + fx.all_checks()[1:]
                ok, _, _ = self.ev(checks)
                self.assertFalse(ok)

    def test_queued_and_in_progress_fail(self):
        for status in ("queued", "in_progress"):
            with self.subTest(status=status):
                checks = [fx.check_run("general-pr-verify", None, status=status)] + fx.all_checks()[1:]
                ok, _, report = self.ev(checks)
                self.assertFalse(ok)
                self.assertEqual(report["checks"]["general-pr-verify"]["state"], "in-progress")

    def test_missing_fails(self):
        ok, problems, report = self.ev(fx.all_checks()[1:])
        self.assertFalse(ok)
        self.assertEqual(report["checks"]["general-pr-verify"]["state"], "missing")

    def test_wrong_app_slug_and_id_rejected(self):
        for app in ({"slug": "evil-actions", "id": 15368}, {"slug": "github-actions", "id": 99999}):
            with self.subTest(app=app):
                checks = [fx.check_run("general-pr-verify", app=app)] + fx.all_checks()[1:]
                ok, problems, report = self.ev(checks)
                self.assertFalse(ok)
                self.assertEqual(report["checks"]["general-pr-verify"]["state"], "unverified")
                self.assertTrue(any("来源 App 不符" in p for p in problems), problems)

    def test_head_drift_fails(self):
        ok, problems, _ = self.ev(fx.all_checks(), pr_obj=fx.pr(head=OTHER))
        self.assertFalse(ok)
        self.assertTrue(any("漂移" in p for p in problems), problems)

    def test_base_sha_mismatch_with_main_fails(self):
        ok, problems, _ = self.ev(fx.all_checks(), main_sha="9" * 40)
        self.assertFalse(ok)
        self.assertTrue(any("base 漂移" in p for p in problems), problems)

    def test_closed_or_merged_pr_fails(self):
        for kwargs in ({"state": "closed"}, {"merged": True}):
            with self.subTest(**kwargs):
                ok, _, _ = self.ev(fx.all_checks(), pr_obj=fx.pr(**kwargs))
                self.assertFalse(ok)

    def test_wrong_base_branch_fails(self):
        ok, _, _ = self.ev(fx.all_checks(), pr_obj=fx.pr(base="develop"))
        self.assertFalse(ok)

    def test_runs_on_other_sha_are_recorded_as_stale_and_fail(self):
        ok, _, report = self.ev(fx.all_checks(sha=OTHER))
        self.assertFalse(ok)
        self.assertTrue(report["stale_runs"])

    # ---- Fix A：同名伪造成功检查 ----
    def test_fake_same_name_success_from_other_workflow_does_not_override_failure(self):
        real = [fx.check_run("general-pr-verify", "failure", started="2026-10-13T01:00:00Z")]
        fake = [fx.check_run("general-pr-verify", "success", suite=fx.OTHER_SUITE, rid=424242,
                             started="2026-10-13T02:00:00Z")]
        runs = [fx.workflow_run(), fx.workflow_run(rid=fx.OTHER_RUN_ID, path=fx.OTHER_WORKFLOW_PATH,
                                                   suite=fx.OTHER_SUITE)]
        ok, problems, report = self.ev(real + fake, runs=runs,
                                       jobs=fx.jobs_for(real) + fx.jobs_for(fake))
        self.assertFalse(ok)
        self.assertEqual(report["checks"]["general-pr-verify"]["conclusion"], "failure")
        self.assertTrue(report["checks"]["general-pr-verify"]["rejected"])

    def test_fake_success_only_is_unverified_not_success(self):
        fake = [fx.check_run("general-pr-verify", "success", suite=fx.OTHER_SUITE, rid=424242)]
        runs = [fx.workflow_run(rid=fx.OTHER_RUN_ID, path=fx.OTHER_WORKFLOW_PATH, suite=fx.OTHER_SUITE)]
        ok, problems, report = self.ev(fake, runs=runs, jobs=fx.jobs_for(fake))
        self.assertFalse(ok)
        self.assertEqual(report["checks"]["general-pr-verify"]["state"], "unverified")

    def test_latest_run_wins_only_within_verified_same_job(self):
        checks = [fx.check_run("general-pr-verify", "failure", started="2026-10-13T00:00:00Z"),
                  fx.check_run("general-pr-verify", "success", rid=115069523999,
                               started="2026-10-13T01:00:00Z")] + fx.all_checks()[1:]
        ok, problems, report = self.ev(checks)
        self.assertTrue(ok, problems)
        info = report["checks"]["general-pr-verify"]
        self.assertEqual(info["state"], "success")
        self.assertEqual(info["superseded_verified_runs"][0]["conclusion"], "failure")


class VerifyHttpTest(unittest.TestCase):
    def test_success_path(self):
        ok, report, problems = vrc.verify(repo="o/r", pr=29, expected_head=HEAD, token="t",
                                          required_manifest=MANIFEST, getter=fx.FakeApi(),
                                          log=lambda *a, **k: None)
        self.assertTrue(ok, problems)
        self.assertEqual(report["writes_performed"], [])
        self.assertEqual(report["main_sha"], fx.MAIN_SHA)

    def test_http_403_on_check_runs_fails_closed(self):
        ok, _, problems = vrc.verify(repo="o/r", pr=29, expected_head=HEAD, token="t",
                                     required_manifest=MANIFEST,
                                     getter=fx.FakeApi(errors={"/check-runs": [403]}),
                                     log=lambda *a, **k: None)
        self.assertFalse(ok)
        self.assertTrue(any("HTTP 403" in p for p in problems), problems)

    def test_http_500_on_pr_fails_closed(self):
        ok, _, problems = vrc.verify(repo="o/r", pr=29, expected_head=HEAD, token="t",
                                     required_manifest=MANIFEST,
                                     getter=fx.FakeApi(errors={"/pulls/": [500]}),
                                     log=lambda *a, **k: None)
        self.assertFalse(ok)
        self.assertTrue(any("HTTP 500" in p for p in problems), problems)

    def test_main_ref_error_fails_closed(self):
        ok, _, problems = vrc.verify(repo="o/r", pr=29, expected_head=HEAD, token="t",
                                     required_manifest=MANIFEST,
                                     getter=fx.FakeApi(errors={"/git/ref/heads/": [500]}),
                                     log=lambda *a, **k: None)
        self.assertFalse(ok)
        self.assertTrue(any("refs/heads/main" in p for p in problems), problems)

    def test_workflow_runs_error_fails_closed(self):
        ok, _, problems = vrc.verify(repo="o/r", pr=29, expected_head=HEAD, token="t",
                                     required_manifest=MANIFEST,
                                     getter=fx.FakeApi(errors={"/actions/runs": [500]}),
                                     log=lambda *a, **k: None)
        self.assertFalse(ok)
        self.assertTrue(any("provenance 采集失败" in p for p in problems), problems)


class ManifestTest(unittest.TestCase):
    def test_real_manifest_declares_expected_source_and_identity(self):
        for c in vrc.load_manifest(MANIFEST):
            self.assertEqual(c["app_slug"], "github-actions")
            self.assertEqual(c["app_id"], 15368)
            self.assertEqual(c["job_id"], c["name"])
            self.assertEqual(c["workflow"], fx.PR_VERIFY_PATH)

    def test_manifest_without_source_is_rejected_by_policy_guard(self):
        import verify_workflow_policy as pol
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "required-checks.json"
            p.write_text(json.dumps({"required": ["a"]}), encoding="utf-8")
            problems, _names, _checks, _info = pol.check_required_checks_file(p)
            self.assertTrue(any("来源绑定" in x for x in problems), problems)

    def test_legacy_manifest_rejected_by_loader(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump({"required": ["a"]}, fh)
            path = fh.name
        with self.assertRaises(vrc.ManifestError):
            vrc.load_manifest(path)

    def test_duplicate_names_rejected(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump({"checks": [{"name": "a", "job_id": "a", "workflow": fx.PR_VERIFY_PATH,
                                   "source": {"app_slug": "github-actions", "app_id": 15368}},
                                  {"name": "a", "job_id": "a", "workflow": fx.PR_VERIFY_PATH,
                                   "source": {"app_slug": "github-actions", "app_id": 15368}}]}, fh)
            path = fh.name
        with self.assertRaises(vrc.ManifestError):
            vrc.load_manifest(path)

    def test_loader_is_the_shared_single_implementation(self):
        self.assertIs(vrc.load_manifest, cp.load_manifest)


if __name__ == "__main__":
    unittest.main()
