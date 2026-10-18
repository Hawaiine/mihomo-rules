#!/usr/bin/env python3
"""审批校验与决策矩阵测试。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

import decide_merge as dm  # noqa: E402
import verify_approval as va  # noqa: E402

HEAD = "c" * 40
OLD = "d" * 40
APPROVER = "Hawaiine"


def review(state="APPROVED", *, login=APPROVER, utype="User", commit=HEAD, when="2026-10-18T04:00:00Z"):
    return {"state": state, "commit_id": commit, "submitted_at": when,
            "user": {"login": login, "type": utype}}


class ApprovalTest(unittest.TestCase):
    def ok(self, reviews, **kw):
        kw.setdefault("head_sha", HEAD)
        kw.setdefault("approver", APPROVER)
        return va.verify_approval(reviews, **kw)

    def test_approved_on_head(self):
        ok, problems, _ = self.ok([review()])
        self.assertTrue(ok, problems)

    def test_missing_review(self):
        ok, problems, _ = self.ok([])
        self.assertFalse(ok)
        self.assertIn("APPROVAL_MISSING", problems)

    def test_stale_commit(self):
        ok, problems, _ = self.ok([review(commit=OLD)])
        self.assertFalse(ok)
        self.assertIn("APPROVAL_STALE", problems)

    def test_missing_commit_field(self):
        item = review()
        item.pop("commit_id")
        ok, problems, _ = self.ok([item])
        self.assertFalse(ok)
        self.assertIn("APPROVAL_MISSING_COMMIT", problems)

    def test_dismissed_latest_wins(self):
        ok, problems, _ = self.ok([review(), review("DISMISSED", when="2026-10-18T05:00:00Z")])
        self.assertFalse(ok)
        self.assertIn("APPROVAL_INVALIDATED", problems)

    def test_changes_requested_latest_wins(self):
        ok, problems, _ = self.ok([review(), review("CHANGES_REQUESTED", when="2026-10-18T06:00:00Z")])
        self.assertFalse(ok)
        self.assertIn("APPROVAL_INVALIDATED", problems)

    def test_bot_approver_rejected(self):
        ok, problems, _ = self.ok([review(utype="Bot")])
        self.assertFalse(ok)
        self.assertIn("APPROVER_NOT_HUMAN", problems)

    def test_other_user_approval_not_counted(self):
        ok, problems, _ = self.ok([review(login="someone-else")])
        self.assertFalse(ok)
        self.assertIn("APPROVAL_MISSING", problems)

    def test_incomplete_pagination_rejected(self):
        ok, problems, _ = self.ok([review()], complete=False)
        self.assertFalse(ok)
        self.assertIn("REVIEWS_PAGINATION_INCOMPLETE", problems)

    def test_no_approver_configured(self):
        ok, problems, _ = va.verify_approval([review()], head_sha=HEAD, approver="")
        self.assertFalse(ok)
        self.assertIn("APPROVER_NOT_CONFIGURED", problems)


class DecideTest(unittest.TestCase):
    base = dict(checks_ok=True, head_sha_ok=True, lock_state="held", approval_ok=True)

    def test_existing_data_eligible_but_production_off(self):
        out = dm.decide(change_class="EXISTING_DATA_ONLY", **self.base)
        self.assertEqual(out["decision"], dm.ELIGIBLE_EXISTING_DATA)
        self.assertFalse(out["production_allowed"])
        self.assertIn(dm.REASON_PRODUCTION_OFF, out["reason_codes"])

    def test_existing_data_production_on(self):
        out = dm.decide(change_class="EXISTING_DATA_ONLY", production_mode=True, **self.base)
        self.assertEqual(out["decision"], dm.ELIGIBLE_EXISTING_DATA)
        self.assertTrue(out["production_allowed"])

    def test_new_ruleset_with_approval(self):
        out = dm.decide(change_class="NEW_RULESET", **self.base)
        self.assertEqual(out["decision"], dm.ELIGIBLE_NEW_RULESET_AFTER_APPROVAL)

    def test_new_ruleset_without_approval_blocked(self):
        kw = dict(self.base); kw["approval_ok"] = False
        out = dm.decide(change_class="NEW_RULESET", **kw)
        self.assertEqual(out["decision"], dm.BLOCKED)
        self.assertIn(dm.REASON_APPROVAL_MISSING, out["reason_codes"])

    def test_governance_blocked(self):
        out = dm.decide(change_class="GOVERNANCE_CHANGE", **self.base)
        self.assertEqual(out["decision"], dm.BLOCKED)
        self.assertIn(dm.REASON_CHANGE_GOVERNANCE, out["reason_codes"])

    def test_unknown_blocked(self):
        out = dm.decide(change_class="MIXED_OR_UNKNOWN", **self.base)
        self.assertEqual(out["decision"], dm.BLOCKED)
        self.assertIn(dm.REASON_CHANGE_UNKNOWN, out["reason_codes"])

    def test_lock_states(self):
        for state, code in (("idle", dm.REASON_LOCK_NOT_HELD), ("paused", dm.REASON_LOCK_PAUSED),
                            ("recovery_required", dm.REASON_RECOVERY_REQUIRED),
                            ("snapshot_failed", dm.REASON_SNAPSHOT_FAILED),
                            ("unknown", dm.REASON_LOCK_NOT_HELD)):
            kw = dict(self.base); kw["lock_state"] = state
            out = dm.decide(change_class="EXISTING_DATA_ONLY", **kw)
            self.assertEqual(out["decision"], dm.BLOCKED, state)
            self.assertIn(code, out["reason_codes"], state)

    def test_head_and_base_drift_blocked(self):
        kw = dict(self.base); kw["head_sha_ok"] = False
        self.assertIn(dm.REASON_HEAD_MISMATCH, dm.decide(change_class="EXISTING_DATA_ONLY", **kw)["reason_codes"])
        kw = dict(self.base); kw["base_sha_ok"] = False
        self.assertIn(dm.REASON_BASE_DRIFT, dm.decide(change_class="EXISTING_DATA_ONLY", **kw)["reason_codes"])

    def test_checks_failed_blocked(self):
        kw = dict(self.base); kw["checks_ok"] = False
        out = dm.decide(change_class="EXISTING_DATA_ONLY", **kw)
        self.assertEqual(out["decision"], dm.BLOCKED)

    def test_closed_pr_blocked(self):
        kw = dict(self.base); kw["pr_open"] = False
        out = dm.decide(change_class="EXISTING_DATA_ONLY", **kw)
        self.assertIn(dm.REASON_PR_NOT_OPEN, out["reason_codes"])

    def test_platform_not_ready_blocked(self):
        kw = dict(self.base); kw["platform_ok"] = False
        out = dm.decide(change_class="EXISTING_DATA_ONLY", **kw)
        self.assertEqual(out["decision"], dm.BLOCKED)

    def test_never_returns_merge_action(self):
        out = dm.decide(change_class="EXISTING_DATA_ONLY", production_mode=True, **self.base)
        self.assertNotIn("merge", {k.lower() for k in out})


if __name__ == "__main__":
    unittest.main()
