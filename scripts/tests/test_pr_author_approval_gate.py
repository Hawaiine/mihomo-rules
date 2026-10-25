#!/usr/bin/env python3
"""机器人/人类作者两类 PR 的审批闸门矩阵测试。

覆盖策略的三条硬约束：
* 审批只能来自策略里不可变 GitHub user ID，且必须是人类 ``type == "User"``；
* 审批必须精确绑定当前 PR head，PR 必须仍 open 且 head/base 未漂移；
* PR 作者若本身就是受信审批人，则该 PR 在结构上无法获得有效审批（GitHub 原生禁止自审，
  闸门也必须自行拒绝，不能依赖平台 UI 行为）。

所有负例都必须 fail-closed：返回 ok=False 且带稳定 reason code，不得抛异常。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

import approval_policy as apol  # noqa: E402
import verify_approval as va  # noqa: E402
import verify_pr_approval_gate as gate  # noqa: E402

HEAD = "b" * 40
OLD = "a" * 40
BASE = "4" * 40
TRUSTED_ID = 152380613
TRUSTED_LOGIN = "Hawaiine"
BOT_ID = 41898282


def policy():
    doc = {"schema": apol.SCHEMA, "version": 1, "approvers": [TRUSTED_LOGIN],
           "approver_ids": [TRUSTED_ID], "required_approvals": 1,
           "allow_bot_approvers": False, "head_binding": "exact",
           "on_missing": "fail-closed"}
    result = apol.validate_doc(doc, source="<test>")
    assert result.ok, result.reason_codes
    return result.policy


def pr_obj(*, author_id=TRUSTED_ID, author_login=TRUSTED_LOGIN, author_type="User",
           head=HEAD, base=BASE, state="open", merged=False, number=33):
    return {"number": number, "state": state, "merged": merged, "merged_at": None,
            "user": {"id": author_id, "login": author_login, "type": author_type},
            "head": {"sha": head, "ref": "feat/x"},
            "base": {"sha": base, "ref": "main"}}


def review(state="APPROVED", *, user_id=TRUSTED_ID, login=TRUSTED_LOGIN, utype="User",
           commit=HEAD, when="2026-10-23T04:00:00Z"):
    return {"id": 1, "state": state, "commit_id": commit, "submitted_at": when,
            "user": {"id": user_id, "login": login, "type": utype}}


def bot_pr():
    return pr_obj(author_id=BOT_ID, author_login="github-actions[bot]", author_type="Bot")


def run(pr=None, reviews=None, *, head=HEAD, base=BASE, complete=True):
    return gate.evaluate(pr=pr if pr is not None else bot_pr(),
                         reviews=reviews if reviews is not None else [review()],
                         expected_head_sha=head, expected_base_sha=base,
                         policy=policy(), review_complete=complete)


class AuthorPathTest(unittest.TestCase):
    """两条路径的身份分类：机器人作者可被人类审批，受信作者不可自审。"""

    def test_bot_authored_pr_with_trusted_approval_passes(self):
        out = run(pr_obj(author_id=BOT_ID, author_login="github-actions[bot]",
                         author_type="Bot"))
        self.assertTrue(out["ok"], out["problems"])
        self.assertEqual(out["evidence"]["author_class"], "BOT_AUTHOR")

    def test_trusted_author_cannot_be_approved_by_self(self):
        out = run(pr_obj())
        self.assertFalse(out["ok"])
        self.assertIn("SELF_APPROVAL_BY_TRUSTED_AUTHOR", out["problems"])
        self.assertEqual(out["evidence"]["author_class"], "TRUSTED_AUTHOR")

    def test_other_human_author_requires_trusted_approver(self):
        pr = pr_obj(author_id=999, author_login="someone", author_type="User")
        ok_out = run(pr)
        self.assertTrue(ok_out["ok"], ok_out["problems"])
        self.assertEqual(ok_out["evidence"]["author_class"], "OTHER_HUMAN_AUTHOR")

    def test_missing_author_identity_fails_closed(self):
        pr = pr_obj()
        pr["user"] = {"login": TRUSTED_LOGIN}
        out = run(pr)
        self.assertFalse(out["ok"])
        self.assertIn("PR_AUTHOR_UNREADABLE", out["problems"])

    def test_bot_that_is_also_approver_is_rejected(self):
        pol_doc = {"schema": apol.SCHEMA, "version": 1, "approvers": [TRUSTED_LOGIN],
                   "approver_ids": [TRUSTED_ID], "required_approvals": 1,
                   "allow_bot_approvers": False, "head_binding": "exact",
                   "on_missing": "fail-closed"}
        p = apol.validate_doc(pol_doc).policy
        pr = pr_obj(author_id=TRUSTED_ID, author_type="Bot")
        out = gate.evaluate(pr=pr, reviews=[review(utype="Bot")],
                            expected_head_sha=HEAD, expected_base_sha=BASE,
                            policy=p, review_complete=True)
        self.assertFalse(out["ok"])


class ReviewMatrixTest(unittest.TestCase):
    def test_missing_review_rejects(self):
        out = run(reviews=[])
        self.assertFalse(out["ok"])
        self.assertTrue(any(p.startswith("APPROVAL_MISSING_ID") for p in out["problems"]))

    def test_wrong_reviewer_rejects(self):
        out = run(reviews=[review(user_id=999, login="attacker")])
        self.assertFalse(out["ok"])
        self.assertTrue(any("APPROVAL_MISSING_ID" in p for p in out["problems"]))

    def test_bot_reviewer_rejects(self):
        out = run(reviews=[review(utype="Bot")])
        self.assertFalse(out["ok"])
        self.assertTrue(any("APPROVER_NOT_HUMAN_ID" in p for p in out["problems"]))

    def test_comment_and_changes_requested_reject(self):
        for state in ("COMMENTED", "CHANGES_REQUESTED", "DISMISSED", "PENDING"):
            with self.subTest(state=state):
                out = run(reviews=[review(state=state)])
                self.assertFalse(out["ok"])

    def test_stale_head_approval_rejects(self):
        out = run(reviews=[review(commit=OLD)])
        self.assertFalse(out["ok"])
        self.assertTrue(any("APPROVAL_STALE_ID" in p for p in out["problems"]))

    def test_incomplete_review_pagination_fails_closed(self):
        out = run(complete=False)
        self.assertFalse(out["ok"])
        self.assertIn("REVIEWS_PAGINATION_INCOMPLETE", out["problems"])


class SnapshotDriftTest(unittest.TestCase):
    def test_closed_or_merged_pr_rejects(self):
        for pr in (pr_obj(state="closed"), pr_obj(merged=True)):
            with self.subTest(pr=pr):
                out = run(pr=pr)
                self.assertFalse(out["ok"])

    def test_head_moved_after_review_rejects(self):
        out = run(pr=pr_obj(head=OLD))
        self.assertFalse(out["ok"])
        self.assertIn("PR_HEAD_MOVED", out["problems"])

    def test_base_moved_rejects(self):
        out = run(pr=pr_obj(base="5" * 40))
        self.assertFalse(out["ok"])
        self.assertIn("PR_BASE_MOVED", out["problems"])

    def test_empty_or_malformed_head_fails_closed(self):
        for head in ("", "abc", "Z" * 40, None):
            with self.subTest(head=head):
                out = run(pr=pr_obj(head=head), head=head)
                self.assertFalse(out["ok"])

    def test_unreadable_pr_object_fails_closed(self):
        out = gate.evaluate(pr=None, reviews=[review()], expected_head_sha=HEAD,
                            expected_base_sha=BASE, policy=policy(), review_complete=True)
        self.assertFalse(out["ok"])
        self.assertIn("PR_OBJECT_MALFORMED", out["problems"])


class CoreAuthorBindingTest(unittest.TestCase):
    """核心校验函数自身也要拒绝作者自批（纵深防御，不只靠闸门脚本）。"""

    def test_verify_approvals_rejects_author_as_approver(self):
        ok, problems, evidence = va.verify_approvals(
            [review()], head_sha=HEAD, approver_ids=[TRUSTED_ID],
            required=1, complete=True, pr_author_id=TRUSTED_ID)
        self.assertFalse(ok)
        self.assertTrue(any(p.startswith("APPROVER_IS_PR_AUTHOR") for p in problems))
        self.assertEqual(evidence["valid_approver_ids"], [])

    def test_verify_approvals_accepts_when_author_is_bot(self):
        ok, problems, _ = va.verify_approvals(
            [review()], head_sha=HEAD, approver_ids=[TRUSTED_ID],
            required=1, complete=True, pr_author_id=BOT_ID)
        self.assertTrue(ok, problems)


if __name__ == "__main__":
    unittest.main()
