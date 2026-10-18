#!/usr/bin/env python3
"""Route A 门禁数据源测试：status 模式（候选 PR）与 check-run 模式的一致性。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

import await_checks as ac  # noqa: E402
import gate_status as gst  # noqa: E402
import verify_required_checks as vrc  # noqa: E402

HEAD = "h" * 40


def status(state: str = "success", *, ctx: str = "route-a/verify", sha: str = HEAD,
           creator: str = "github-actions[bot]") -> dict:
    return {"context": ctx, "state": state, "sha": sha,
            "creator": {"login": creator, "type": "Bot"}, "target_url": ""}


class EvaluateStatusesTest(unittest.TestCase):
    def test_success(self):
        state, per, problems = gst.evaluate_statuses([status()], ["route-a/verify"], HEAD)
        self.assertEqual(state, "success")
        self.assertEqual(problems, [])
        self.assertEqual(per["route-a/verify"]["state"], "success")

    def test_pending_when_absent(self):
        state, per, problems = gst.evaluate_statuses([], ["route-a/verify"], HEAD)
        self.assertEqual(state, "pending")
        self.assertTrue(any("缺少状态" in p for p in problems))

    def test_failure_is_terminal(self):
        state, _, problems = gst.evaluate_statuses([status("failure")], ["route-a/verify"], HEAD)
        self.assertEqual(state, "failed")
        self.assertTrue(any("failure" in p for p in problems))

    def test_wrong_sha_rejected(self):
        state, _, problems = gst.evaluate_statuses([status(sha="x" * 40)], ["route-a/verify"], HEAD)
        self.assertEqual(state, "failed")
        self.assertTrue(any("不是预期 head" in p for p in problems))

    def test_untrusted_creator_rejected(self):
        state, _, problems = gst.evaluate_statuses([status(creator="attacker")], ["route-a/verify"], HEAD)
        self.assertEqual(state, "failed")
        self.assertTrue(any("来源" in p for p in problems))

    def test_missing_sha_rejected(self):
        item = status(); item.pop("sha")
        state, _, problems = gst.evaluate_statuses([item], ["route-a/verify"], HEAD)
        self.assertEqual(state, "failed")
        self.assertTrue(any("缺少合法 sha" in p for p in problems))

    def test_creator_user_type_rejected(self):
        item = status(); item["creator"] = {"login": "github-actions[bot]", "type": "User"}
        state, _, problems = gst.evaluate_statuses([item], ["route-a/verify"], HEAD)
        self.assertEqual(state, "failed")
        self.assertTrue(any("不是 Bot" in p for p in problems))

    def test_latest_status_wins(self):
        older = status("failure")
        newer = status("success")
        state, _, _ = gst.evaluate_statuses([newer, older], ["route-a/verify"], HEAD)
        self.assertEqual(state, "success")

    def test_missing_manifest_contexts(self):
        state, _, problems = gst.evaluate_statuses([status()], [], HEAD)
        self.assertEqual(state, "missing")
        self.assertTrue(problems)

    def test_expected_mode_from_author(self):
        self.assertEqual(gst.expected_mode(
            {"user": {"login": "github-actions[bot]", "type": "Bot"}}), "status")
        self.assertEqual(gst.expected_mode({"user": {"login": "Hawaiine", "type": "User"}}), "check-run")

    def test_expected_mode_rejects_login_only_spoof(self):
        with self.assertRaises(gst.GateModeError):
            gst.expected_mode({"user": {"login": "github-actions[bot]", "type": "User"}})

    def test_expected_mode_unknown_author_fails(self):
        with self.assertRaises(gst.GateModeError):
            gst.expected_mode({})


class StatusModeVerifierTest(unittest.TestCase):
    def _pr(self, head: str = HEAD, state: str = "open", login: str = "github-actions[bot]") -> dict:
        return {"state": state, "merged": False, "head": {"sha": head},
                "base": {"ref": "main", "sha": "m" * 40},
                "user": {"login": login, "type": "Bot" if login.endswith("[bot]") else "User"}}

    def test_status_mode_passes(self):
        ok, problems, report = vrc.evaluate(
            self._pr(), [], [], {}, [], HEAD, "main", "m" * 40,
            statuses=[status()], contexts=["route-a/verify"])
        self.assertTrue(ok, problems)
        self.assertEqual(report["gate_mode"], "status")

    def test_status_mode_fails_without_context(self):
        ok, problems, report = vrc.evaluate(
            self._pr(), [], [], {}, [], HEAD, "main", "m" * 40, statuses=[], contexts=["route-a/verify"])
        self.assertFalse(ok)
        self.assertEqual(report["gate_mode"], "status")

    def test_no_downgrade_when_check_runs_missing_for_human_pr(self):
        """P0-3：人工 PR 缺 check-run 时不得退回 status 模式。"""
        ok, problems, report = vrc.evaluate(
            self._pr(login="Hawaiine"), [], [], {}, [], HEAD, "main", "m" * 40,
            statuses=[status()], contexts=["route-a/verify"])
        self.assertFalse(ok)
        self.assertEqual(report["gate_mode"], "check-run")
        self.assertTrue(any("拒绝降级" in p for p in problems), problems)

    def test_bot_pr_without_contexts_declared_fails(self):
        ok, problems, _ = vrc.evaluate(
            self._pr(), [], [], {}, [], HEAD, "main", "m" * 40, statuses=[], contexts=[])
        self.assertFalse(ok)
        self.assertTrue(any("status_contexts" in p for p in problems), problems)

    def test_head_drift_still_fails_in_status_mode(self):
        ok, problems, _ = vrc.evaluate(
            self._pr(head="x" * 40), [], [], {}, [], HEAD, "main", None,
            statuses=[status()], contexts=["route-a/verify"])
        self.assertFalse(ok)
        self.assertTrue(any("head" in p for p in problems))


class PollerStatusModeTest(unittest.TestCase):
    def test_poller_succeeds_on_status(self):
        poller = ac.Poller(repo="o/r", pr=7, expected_head=HEAD, base_branch="main", required=[],
                           token="t", contexts=["route-a/verify"], sleeper=lambda s: None)
        with mock.patch.object(poller, "preflight", return_value=(True, [], {"pr_head_sha": HEAD, "pr": {"user": {"login": "github-actions[bot]", "type": "Bot"}}})), \
             mock.patch.object(poller, "read_provenance", return_value=([], [], {})), \
             mock.patch.object(poller, "_read_statuses", return_value=[status()]), \
             mock.patch.object(poller, "recheck_pr", return_value=(True, [])):
            report = poller.run()
        self.assertEqual(report["status"], ac.STATUS_SUCCESS, report)
        self.assertEqual(report["gate_mode"], "status")

    def test_poller_times_out_when_status_never_arrives(self):
        poller = ac.Poller(repo="o/r", pr=7, expected_head=HEAD, base_branch="main", required=[],
                           token="t", contexts=["route-a/verify"], timeout=0.0, sleeper=lambda s: None)
        with mock.patch.object(poller, "preflight", return_value=(True, [], {"pr_head_sha": HEAD, "pr": {"user": {"login": "github-actions[bot]", "type": "Bot"}}})), \
             mock.patch.object(poller, "read_provenance", return_value=([], [], {})), \
             mock.patch.object(poller, "_read_statuses", return_value=[]):
            report = poller.run()
        self.assertEqual(report["status"], ac.STATUS_TIMEOUT, report)

    def test_poller_fails_on_failure_status(self):
        poller = ac.Poller(repo="o/r", pr=7, expected_head=HEAD, base_branch="main", required=[],
                           token="t", contexts=["route-a/verify"], sleeper=lambda s: None)
        with mock.patch.object(poller, "preflight", return_value=(True, [], {"pr_head_sha": HEAD, "pr": {"user": {"login": "github-actions[bot]", "type": "Bot"}}})), \
             mock.patch.object(poller, "read_provenance", return_value=([], [], {})), \
             mock.patch.object(poller, "_read_statuses", return_value=[status("failure")]):
            report = poller.run()
        self.assertEqual(report["status"], ac.STATUS_FAILED, report)


if __name__ == "__main__":
    unittest.main()
