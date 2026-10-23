#!/usr/bin/env python3
"""审批闸门的 **PR 身份绑定** 测试：编号、仓库、base 分支。

只校验 head/base 快照是不够的：同一个 head SHA 可以出现在另一个 PR、另一个仓库
（fork）或另一个 base 分支上。审批必须绑定到"哪一个 PR、哪一个仓库、哪一条 base 分支"，
否则把 A 场景的审批证据搬到 B 场景是可能的。

全部负例必须 fail-closed（ok=False + 稳定 reason code），不得抛异常。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

import approval_policy as apol  # noqa: E402
import verify_pr_approval_gate as gate  # noqa: E402

HEAD = "b" * 40
BASE = "4" * 40
REPO = "Hawaiine/mihomo-rules"
TRUSTED_ID = 152380613
BOT_ID = 41898282


def policy():
    doc = {"schema": apol.SCHEMA, "version": 1, "approvers": ["Hawaiine"],
           "approver_ids": [TRUSTED_ID], "required_approvals": 1,
           "allow_bot_approvers": False, "head_binding": "exact",
           "on_missing": "fail-closed"}
    r = apol.validate_doc(doc, source="<test>")
    assert r.ok, r.reason_codes
    return r.policy


def pr_obj(*, number=34, repo=REPO, base_ref="main", head_repo=None):
    return {"number": number, "state": "open", "merged": False, "merged_at": None,
            "user": {"id": BOT_ID, "login": "github-actions[bot]", "type": "Bot"},
            "head": {"sha": HEAD, "ref": "feat/x", "repo": {"full_name": head_repo or repo}},
            "base": {"sha": BASE, "ref": base_ref, "repo": {"full_name": repo}}}


def review():
    return {"id": 1, "state": "APPROVED", "commit_id": HEAD,
            "submitted_at": "2026-10-23T04:00:00Z",
            "user": {"id": TRUSTED_ID, "login": "Hawaiine", "type": "User"}}


def run(pr, **kw):
    kw.setdefault("expected_head_sha", HEAD)
    kw.setdefault("expected_base_sha", BASE)
    kw.setdefault("expected_pr", 34)
    kw.setdefault("expected_repo", REPO)
    return gate.evaluate(pr=pr, reviews=[review()], policy=policy(),
                         review_complete=True, **kw)


class IdentityBindingTest(unittest.TestCase):
    def test_matching_identity_passes(self):
        out = run(pr_obj())
        self.assertTrue(out["ok"], out["problems"])

    def test_other_pr_number_rejected(self):
        out = run(pr_obj(number=99))
        self.assertFalse(out["ok"])
        self.assertIn("PR_NUMBER_MISMATCH", out["problems"])

    def test_other_repository_rejected(self):
        out = run(pr_obj(repo="attacker/mihomo-rules"))
        self.assertFalse(out["ok"])
        self.assertIn("PR_REPOSITORY_MISMATCH", out["problems"])

    def test_repository_compare_is_case_insensitive_but_exact(self):
        self.assertTrue(run(pr_obj(repo="hawaiine/MIHOMO-RULES"))["ok"])
        self.assertFalse(run(pr_obj(repo="Hawaiine/mihomo-rules2"))["ok"])

    def test_other_base_branch_rejected(self):
        out = run(pr_obj(base_ref="release"))
        self.assertFalse(out["ok"])
        self.assertIn("PR_BASE_REF_MISMATCH", out["problems"])

    def test_missing_repository_object_fails_closed(self):
        pr = pr_obj()
        pr["base"].pop("repo")
        out = run(pr)
        self.assertFalse(out["ok"])
        self.assertIn("PR_REPOSITORY_UNREADABLE", out["problems"])

    def test_missing_number_fails_closed(self):
        pr = pr_obj()
        pr.pop("number")
        out = run(pr)
        self.assertFalse(out["ok"])
        self.assertIn("PR_NUMBER_UNREADABLE", out["problems"])

    def test_fork_head_repo_is_recorded_not_trusted(self):
        out = run(pr_obj(head_repo="someone/mihomo-rules"))
        self.assertTrue(out["ok"], out["problems"])
        self.assertEqual(out["evidence"]["snapshot"]["head_repo"], "someone/mihomo-rules")

    def test_expected_pr_optional_for_backward_compat(self):
        out = run(pr_obj(), expected_pr=None, expected_repo=None)
        self.assertTrue(out["ok"], out["problems"])


if __name__ == "__main__":
    unittest.main()
