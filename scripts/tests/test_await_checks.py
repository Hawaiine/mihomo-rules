#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自动合并前「有界等待 + 严格检查判定」的 mock 测试。

覆盖：尚未出现 / queued / in-progress / 全部成功 / 明确失败 / 超时 / head SHA 漂移 /
陈旧检查（其他 SHA）/ API 403 / 429 / 5xx / 来源不符 / 前置状态非法。
**这些是 mock 逻辑测试，不代表真实 GitHub 平台端到端成功。**
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

import await_checks as ac  # noqa: E402

MANIFEST = str(ROOT / ".github" / "required-checks.json")
HEAD = "a" * 40
OTHER_SHA = "b" * 40
NAMES = ["general-pr-verify", "workflow-policy-guard", "trusted-baseline-guard"]


def run(name, conclusion="success", status="completed", sha=HEAD, app_slug="github-actions",
        app_id=15368, rid=1, started="2026-10-13T00:00:00Z"):
    return {"name": name, "conclusion": conclusion, "status": status, "head_sha": sha,
            "app": {"slug": app_slug, "id": app_id}, "started_at": started, "id": rid,
            "details_url": "https://example.invalid/run/" + name}


def all_runs(**kw):
    return [run(n, **kw) for n in NAMES]


def pr(head=HEAD, state="open", base="main"):
    return {"state": state, "base": {"ref": base, "sha": "f" * 40}, "head": {"sha": head},
            "number": 7, "merged": False}


class FakeApi:
    def __init__(self, pr_before=None, runs_seq=None, pr_after=None, ref_sha=HEAD, errors=None):
        self.pr_before = pr_before or pr()
        self.pr_after = pr_after or self.pr_before
        self.runs_seq = list(runs_seq or [all_runs()])
        self.ref_sha = ref_sha
        self.errors = {k: list(v) for k, v in (errors or {}).items()}
        self.calls = []

    def __call__(self, url, token):
        self.calls.append(url)
        for key, codes in self.errors.items():
            if key in url and codes:
                return codes.pop(0), {"_error": "simulated failure"}
        if "/pulls/" in url:
            seen = sum(1 for c in self.calls if "/pulls/" in c)
            return 200, (self.pr_before if seen == 1 else self.pr_after)
        if "/check-runs" in url:
            runs = self.runs_seq[0] if len(self.runs_seq) == 1 else self.runs_seq.pop(0)
            return 200, {"check_runs": runs}
        if "/git/ref/" in url:
            return 200, {"object": {"sha": self.ref_sha}}
        raise AssertionError("unexpected URL: " + url)


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.waits = []

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.waits.append(seconds)
        self.t += seconds


def poll(api, clock=None, timeout=5.0, interval=1.0, max_interval=2.0):
    clock = clock or FakeClock()
    required = ac.load_required(MANIFEST)
    p = ac.Poller(repo="o/r", pr=7, expected_head=HEAD, base_branch="main", required=required,
                  token="x", timeout=timeout, interval=interval, max_interval=max_interval,
                  fetcher=api, sleeper=clock.sleep, clock=clock, log=lambda *a, **k: None)
    return p.run(), clock


class EvaluateTest(unittest.TestCase):
    def setUp(self):
        self.required = ac.load_required(MANIFEST)

    def test_all_success(self):
        status, per, problems = ac.evaluate(all_runs(), self.required, HEAD)
        self.assertEqual(status, ac.STATUS_SUCCESS)
        self.assertEqual(problems, [])
        self.assertTrue(all(v["state"] == "success" for v in per.values()))

    def test_skipped_is_not_success(self):
        runs = [run("general-pr-verify", "skipped")] + all_runs()[1:]
        status, _, problems = ac.evaluate(runs, self.required, HEAD)
        self.assertEqual(status, ac.STATUS_FAILED)
        self.assertTrue(any("skipped" in p for p in problems), problems)

    def test_neutral_is_not_success(self):
        runs = all_runs()
        runs[0] = run("general-pr-verify", "neutral")
        status, _, _ = ac.evaluate(runs, self.required, HEAD)
        self.assertEqual(status, ac.STATUS_FAILED)

    def test_in_progress_is_pending(self):
        runs = all_runs()
        runs[0] = run("general-pr-verify", None, status="in_progress")
        status, _, _ = ac.evaluate(runs, self.required, HEAD)
        self.assertEqual(status, ac.STATUS_PENDING)

    def test_missing_is_pending(self):
        status, _, _ = ac.evaluate([], self.required, HEAD)
        self.assertEqual(status, ac.STATUS_PENDING)

    def test_wrong_source_is_not_trusted(self):
        status, _, problems = ac.evaluate(all_runs(app_slug="evil-app"), self.required, HEAD)
        self.assertEqual(status, ac.STATUS_PENDING)
        self.assertTrue(any("来源" in p for p in problems), problems)

    def test_stale_runs_on_other_sha_do_not_satisfy(self):
        status, per, _ = ac.evaluate(all_runs(sha=OTHER_SHA), self.required, HEAD)
        self.assertEqual(status, ac.STATUS_PENDING)
        self.assertTrue(all(v["state"] == "missing" for v in per.values()))


class PollerTest(unittest.TestCase):
    def test_not_yet_present_then_success(self):
        api = FakeApi(runs_seq=[[], all_runs()])
        report, clock = poll(api)
        self.assertEqual(report["status"], ac.STATUS_SUCCESS)
        self.assertEqual(report["polls"], 2)
        self.assertEqual(clock.waits, [1.0])

    def test_queued_then_success_with_increasing_interval(self):
        seq = [[run(n, None, status="queued") for n in NAMES],
               [run(n, None, status="in_progress") for n in NAMES], all_runs()]
        report, clock = poll(FakeApi(runs_seq=seq))
        self.assertEqual(report["status"], ac.STATUS_SUCCESS)
        self.assertEqual(report["polls"], 3)
        self.assertEqual(clock.waits, [1.0, 2.0])  # 递增（1 → 2，受 max_interval 限制）

    def test_failure_terminates_immediately(self):
        api = FakeApi(runs_seq=[[run("general-pr-verify", "failure")] + all_runs()[1:], all_runs()])
        report, clock = poll(api)
        self.assertEqual(report["status"], ac.STATUS_FAILED)
        self.assertEqual(report["polls"], 1)          # 不再继续轮询
        self.assertEqual(clock.waits, [])
        self.assertEqual(report["checks"]["general-pr-verify"]["conclusion"], "failure")

    def test_skipped_terminates_as_failure(self):
        api = FakeApi(runs_seq=[[run("general-pr-verify", "skipped")] + all_runs()[1:]])
        report, _ = poll(api)
        self.assertEqual(report["status"], ac.STATUS_FAILED)

    def test_timeout_when_never_completes(self):
        report, clock = poll(FakeApi(runs_seq=[[]]), timeout=3.0, interval=1.0, max_interval=2.0)
        self.assertEqual(report["status"], ac.STATUS_TIMEOUT)
        self.assertGreaterEqual(sum(clock.waits), 3.0)

    def test_head_drift_after_success_fails(self):
        api = FakeApi(runs_seq=[all_runs()], pr_after=pr(head=OTHER_SHA))
        report, _ = poll(api)
        self.assertEqual(report["status"], ac.STATUS_FAILED)
        self.assertTrue(any("漂移" in p for p in report["problems"]), report)

    def test_closed_pr_fails_preflight_without_polling(self):
        api = FakeApi(pr_before=pr(state="closed"))
        report, _ = poll(api)
        self.assertEqual(report["status"], ac.STATUS_FAILED)
        self.assertEqual(report["polls"], 0)
        self.assertTrue(any("非 open" in p for p in report["problems"]), report)

    def test_wrong_base_branch_fails_preflight(self):
        report, _ = poll(FakeApi(pr_before=pr(base="develop")))
        self.assertEqual(report["status"], ac.STATUS_FAILED)

    def test_api_403_after_retries_is_error(self):
        api = FakeApi(runs_seq=[all_runs()], errors={"/check-runs": [403, 403, 403, 403, 403]})
        report, _ = poll(api)
        self.assertEqual(report["status"], ac.STATUS_ERROR)

    def test_api_429_then_success_recovers(self):
        api = FakeApi(runs_seq=[all_runs()], errors={"/check-runs": [429]})
        report, _ = poll(api)
        self.assertEqual(report["status"], ac.STATUS_SUCCESS)
        self.assertGreaterEqual(report["http_requests"], 3)

    def test_api_5xx_is_error(self):
        api = FakeApi(runs_seq=[all_runs()], errors={"/check-runs": [500, 500, 500, 500, 500]})
        report, _ = poll(api)
        self.assertEqual(report["status"], ac.STATUS_ERROR)

    def test_only_read_requests_and_no_writes(self):
        api = FakeApi(runs_seq=[[], all_runs()])
        report, _ = poll(api)
        self.assertEqual(report["writes_performed"], [])
        self.assertTrue(all("/pulls/" in u or "/check-runs" in u or "/git/ref/" in u for u in api.calls),
                        api.calls)


class ManifestTest(unittest.TestCase):
    def test_real_manifest_names_and_sources(self):
        required = ac.load_required(MANIFEST)
        self.assertEqual([r["name"] for r in required], NAMES)
        self.assertTrue(all(r["app_slug"] == "github-actions" and r["app_id"] == 15368 for r in required))

    def test_legacy_required_list_still_loads(self):
        import json
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump({"required": ["a", "b"]}, fh)
            path = fh.name
        self.assertEqual([r["name"] for r in ac.load_required(path)], ["a", "b"])

    def test_unreadable_manifest_fails_closed(self):
        with self.assertRaises(ac.ManifestError):
            ac.load_required("/nonexistent/manifest.json")

    def test_two_loaders_agree_on_real_manifest(self):
        """两个加载器（await_checks / verify_required_checks）不得漂移。"""
        import verify_required_checks as vrc
        self.assertEqual([r["name"] for r in ac.load_required(MANIFEST)],
                         [c["name"] for c in vrc.load_manifest(MANIFEST)])


if __name__ == "__main__":
    unittest.main()
