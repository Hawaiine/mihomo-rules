#!/usr/bin/env python3
"""审批策略不可变 GitHub user ID 与 login 旁路负向测试。"""
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))
import approval_policy as apol  # noqa: E402
import verify_approval as va  # noqa: E402


class PolicyValidationTest(unittest.TestCase):
    def valid(self, **over):
        doc = {"schema": apol.SCHEMA, "version": 1, "approvers": ["Hawaiine"],
               "approver_ids": [152380613], "required_approvals": 1,
               "allow_bot_approvers": False, "head_binding": "exact",
               "on_missing": "fail-closed"}
        doc.update(over)
        return doc

    def test_valid_policy_with_immutable_user_id(self):
        r = apol.validate_doc(self.valid())
        self.assertTrue(r.ok, r.reason_codes)
        self.assertEqual(r.policy.approver_ids, (152380613,))
        self.assertEqual(r.policy.approvers, ("Hawaiine",))

    def test_missing_ids_fails_closed(self):
        doc = self.valid()
        del doc["approver_ids"]
        r = apol.validate_doc(doc)
        self.assertFalse(r.ok)
        self.assertIn("APPROVAL_POLICY_MISSING_FIELDS:approver_ids", r.reason_codes)

    def test_invalid_ids_fail_closed(self):
        for ids in ([True], [0], ["152380613"], []):
            with self.subTest(ids=ids):
                r = apol.validate_doc(self.valid(approver_ids=ids))
                self.assertFalse(r.ok)

    def test_login_override_cannot_grant_approval(self):
        who, required, result = apol.resolve_approvers(explicit="Hawaiine")
        self.assertEqual(who, ())
        self.assertEqual(required, 0)
        self.assertFalse(result.ok)
        self.assertIn("APPROVAL_POLICY_LOGIN_OVERRIDE_FORBIDDEN", result.reason_codes)


class UserIdApprovalTest(unittest.TestCase):
    HEAD = "a" * 40

    def review(self, user_id, login="Hawaiine", state="APPROVED", commit=None, utype="User"):
        return {"state": state, "commit_id": commit or self.HEAD,
                "submitted_at": "2026-10-19T00:00:00Z",
                "user": {"id": user_id, "login": login, "type": utype}}

    def check(self, reviews, ids=(152380613,)):
        return va.verify_approvals(reviews, head_sha=self.HEAD,
                                   approver_ids=list(ids), required=1)

    def test_bound_user_id_current_head_approved(self):
        ok, problems, evidence = self.check([self.review(152380613)])
        self.assertTrue(ok, problems)
        self.assertEqual(evidence["valid_approver_ids"], [152380613])

    def test_other_user_id_rejected_even_matching_login(self):
        ok, problems, _ = self.check([self.review(999999, login="Hawaiine")])
        self.assertFalse(ok)
        self.assertTrue(any("APPROVAL_MISSING_ID" in p for p in problems))

    def test_bot_id_rejected(self):
        ok, problems, _ = self.check([self.review(152380613, utype="Bot")])
        self.assertFalse(ok)
        self.assertTrue(any("APPROVER_NOT_HUMAN_ID" in p for p in problems))

    def test_old_head_rejected(self):
        ok, problems, _ = self.check([self.review(152380613, commit="b" * 40)])
        self.assertFalse(ok)
        self.assertTrue(any("APPROVAL_STALE_ID" in p for p in problems))

    def test_comment_only_rejected(self):
        ok, problems, _ = self.check([self.review(152380613, state="COMMENTED")])
        self.assertFalse(ok)
        self.assertTrue(any("APPROVAL_NOT_CURRENT_ID" in p for p in problems))

    def test_missing_user_id_rejected(self):
        review = self.review(152380613)
        del review["user"]["id"]
        ok, problems, _ = self.check([review])
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
