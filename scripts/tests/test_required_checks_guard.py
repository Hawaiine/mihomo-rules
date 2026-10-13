#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""严格必需检查验证器的测试（success 白名单 + 来源绑定 + head SHA 绑定 + fail-closed）。

包括「把必需 job 改成 if: false / 等价跳过」的**实际失败行为**断言：跳过的 job 在状态检查
体系中可能被算作成功，因此这里必须证明 `skipped` 会导致自动合并前置验证失败。
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

import verify_required_checks as vrc  # noqa: E402

MANIFEST = str(ROOT / ".github" / "required-checks.json")
HEAD = "a" * 40
OTHER = "b" * 40
NAMES = ["general-pr-verify", "workflow-policy-guard", "trusted-baseline-guard"]


def run(name, conclusion="success", status="completed", sha=HEAD, slug="github-actions",
        app_id=15368, rid=1, started="2026-10-13T00:00:00Z"):
    return {"name": name, "conclusion": conclusion, "status": status, "head_sha": sha,
            "app": {"slug": slug, "id": app_id}, "id": rid, "started_at": started,
            "completed_at": started, "details_url": "https://example.invalid/" + name}


def all_success(**kw):
    return [run(n, **kw) for n in NAMES]


def pr(head=HEAD, state="open", base="main", merged=False):
    return {"state": state, "merged": merged, "base": {"ref": base, "sha": "f" * 40},
            "head": {"sha": head}, "number": 7, "node_id": "PR_node"}


class EvaluateTest(unittest.TestCase):
    def setUp(self):
        self.required = vrc.load_manifest(MANIFEST)

    def ev(self, runs, pr_obj=None, head=HEAD):
        return vrc.evaluate(pr_obj or pr(), runs, self.required, head)

    def test_all_success_passes(self):
        ok, problems, report = self.ev(all_success())
        self.assertTrue(ok, problems)
        self.assertEqual(report["checks"]["general-pr-verify"]["state"], "success")
        self.assertEqual(report["checks"]["general-pr-verify"]["app_id"], 15368)

    def test_skipped_fails(self):
        ok, problems, report = self.ev([run("general-pr-verify", "skipped")] + all_success()[1:])
        self.assertFalse(ok)
        self.assertEqual(report["checks"]["general-pr-verify"]["state"], "not-success")
        self.assertTrue(any("skipped" in p for p in problems), problems)

    def test_neutral_fails(self):
        ok, problems, _ = self.ev([run("general-pr-verify", "neutral")] + all_success()[1:])
        self.assertFalse(ok)
        self.assertTrue(any("neutral" in p for p in problems), problems)

    def test_cancelled_and_timed_out_fail(self):
        for conclusion in ("cancelled", "timed_out", "action_required", "stale", "failure"):
            with self.subTest(conclusion=conclusion):
                ok, _, _ = self.ev([run("general-pr-verify", conclusion)] + all_success()[1:])
                self.assertFalse(ok)

    def test_queued_and_in_progress_fail(self):
        for status in ("queued", "in_progress"):
            with self.subTest(status=status):
                ok, _, report = self.ev([run("general-pr-verify", None, status=status)]
                                        + all_success()[1:])
                self.assertFalse(ok)
                self.assertEqual(report["checks"]["general-pr-verify"]["state"], "in-progress")

    def test_missing_fails(self):
        ok, problems, report = self.ev(all_success()[1:])
        self.assertFalse(ok)
        self.assertEqual(report["checks"]["general-pr-verify"]["state"], "missing")

    def test_wrong_app_slug_is_rejected(self):
        ok, problems, report = self.ev(all_success(slug="evil-actions"))
        self.assertFalse(ok)
        self.assertEqual(report["checks"]["general-pr-verify"]["state"], "wrong-source")
        self.assertTrue(any("来源不符" in p for p in problems), problems)

    def test_wrong_app_id_is_rejected(self):
        ok, _, report = self.ev(all_success(app_id=99999))
        self.assertFalse(ok)
        self.assertEqual(report["checks"]["general-pr-verify"]["state"], "wrong-source")

    def test_head_drift_fails(self):
        ok, problems, _ = self.ev(all_success(), pr_obj=pr(head=OTHER))
        self.assertFalse(ok)
        self.assertTrue(any("漂移" in p for p in problems), problems)

    def test_runs_on_other_sha_are_recorded_as_stale_and_fail(self):
        ok, _, report = self.ev(all_success(sha=OTHER))
        self.assertFalse(ok)
        self.assertTrue(report["stale_runs"])

    def test_closed_or_merged_pr_fails(self):
        for kwargs in ({"state": "closed"}, {"merged": True}):
            with self.subTest(**kwargs):
                ok, _, _ = self.ev(all_success(), pr_obj=pr(**kwargs))
                self.assertFalse(ok)

    def test_wrong_base_branch_fails(self):
        ok, _, _ = self.ev(all_success(), pr_obj=pr(base="develop"))
        self.assertFalse(ok)

    def test_latest_run_wins_but_superseded_is_recorded(self):
        runs = [run("general-pr-verify", "failure", rid=1, started="2026-10-13T00:00:00Z"),
                run("general-pr-verify", "success", rid=2, started="2026-10-13T01:00:00Z")] + all_success()[1:]
        ok, problems, report = self.ev(runs)
        self.assertTrue(ok, problems)
        info = report["checks"]["general-pr-verify"]
        self.assertEqual(info["state"], "success")
        self.assertEqual(info["superseded_runs"][0]["conclusion"], "failure")


class VerifyHttpTest(unittest.TestCase):
    def getter(self, pr_obj, runs, status=200, pr_status=200):
        def _get(url, token):
            if "/pulls/" in url:
                return pr_status, pr_obj if pr_status == 200 else {"_error": "boom"}
            if "/check-runs" in url:
                return status, {"check_runs": runs} if status == 200 else {"_error": "boom"}
            return 404, {"_error": "unexpected"}
        return _get

    def test_http_403_fails_closed(self):
        ok, report, problems = vrc.verify(repo="o/r", pr=7, expected_head=HEAD, token="t",
                                          required_manifest=MANIFEST,
                                          getter=self.getter(pr(), [], status=403), log=lambda *a: None)
        self.assertFalse(ok)
        self.assertTrue(any("HTTP 403" in p for p in problems), problems)

    def test_http_500_on_pr_fails_closed(self):
        ok, _, problems = vrc.verify(repo="o/r", pr=7, expected_head=HEAD, token="t",
                                     required_manifest=MANIFEST,
                                     getter=self.getter(pr(), all_success(), pr_status=500),
                                     log=lambda *a: None)
        self.assertFalse(ok)
        self.assertTrue(any("HTTP 500" in p for p in problems), problems)

    def test_success_path_reports_no_writes(self):
        ok, report, _ = vrc.verify(repo="o/r", pr=7, expected_head=HEAD, token="t",
                                   required_manifest=MANIFEST,
                                   getter=self.getter(pr(), all_success()), log=lambda *a: None)
        self.assertTrue(ok)
        self.assertEqual(report["writes_performed"], [])


class ManifestTest(unittest.TestCase):
    def test_real_manifest_declares_expected_source(self):
        checks = vrc.load_manifest(MANIFEST)
        self.assertEqual([c["name"] for c in checks], NAMES)
        for c in checks:
            self.assertEqual(c["app_slug"], "github-actions")
            self.assertEqual(c["app_id"], 15368)
            self.assertEqual(c["job_id"], c["name"])

    def test_manifest_without_source_is_rejected_by_policy_guard(self):
        """旧形态（仅字符串）必须被策略 guard 判为失败，而不是静默降级。"""
        import verify_workflow_policy as pol
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "required-checks.json"
            p.write_text(json.dumps({"required": ["a"]}), encoding="utf-8")
            problems, names, _ = pol.check_required_checks_file(p)
            self.assertTrue(any("来源绑定" in x for x in problems), problems)

    def test_duplicate_names_rejected(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump({"checks": [{"name": "a"}, {"name": "a"}]}, fh)
            path = fh.name
        with self.assertRaises(vrc.ManifestError):
            vrc.load_manifest(path)


if __name__ == "__main__":
    unittest.main()
