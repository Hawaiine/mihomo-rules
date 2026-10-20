#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""候选 head SHA 判定与一致性校验的回归测试（修复轮）。

核心回归：**同一 tree、不同 commit SHA** 时，跳过推送的分支必须输出**真实远端 ref SHA**，
而不是本地未推送的新 commit SHA，并且不得进入「陈旧 SHA」检查路径。
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

import candidate_sha as cs  # noqa: E402

LOCAL = "a" * 40
REMOTE = "b" * 40
OTHER = "c" * 40


class EffectiveHeadShaTest(unittest.TestCase):
    def test_same_tree_uses_remote_sha_not_local(self):
        """回归：树一致（跳过推送）时必须输出远端 ref SHA，而不是本地新建 commit SHA。"""
        self.assertEqual(cs.effective_head_sha(LOCAL, REMOTE, trees_equal=True), REMOTE)
        self.assertNotEqual(cs.effective_head_sha(LOCAL, REMOTE, trees_equal=True), LOCAL)

    def test_different_tree_uses_local_sha(self):
        self.assertEqual(cs.effective_head_sha(LOCAL, REMOTE, trees_equal=False), LOCAL)

    def test_same_tree_without_valid_remote_sha_fails_closed(self):
        for bad in (None, "", "   ", "not-a-sha", "b" * 39):
            with self.subTest(bad=bad):
                with self.assertRaises(cs.CandidateError):
                    cs.effective_head_sha(LOCAL, bad, trees_equal=True)


class DecidePublishTest(unittest.TestCase):
    def test_skip_push_returns_remote_sha_and_avoids_stale_path(self):
        """树一致 + PR head 已等于远端 ref ⇒ skip-push，expected=远端 SHA（不报陈旧）。"""
        out = cs.decide_publish(remote_ref_exists=True, remote_ref_sha=REMOTE, local_sha=LOCAL,
                                trees_equal=True, pr_state="open", pr_head_sha=REMOTE)
        self.assertEqual(out["decision"], cs.DECIDE_SKIP_PUSH)
        self.assertEqual(out["expected_head_sha"], REMOTE)
        self.assertEqual(out["problems"], [])

    def test_skip_push_with_stale_pr_head_aborts(self):
        out = cs.decide_publish(remote_ref_exists=True, remote_ref_sha=REMOTE, local_sha=LOCAL,
                                trees_equal=True, pr_state="open", pr_head_sha=OTHER)
        self.assertEqual(out["decision"], cs.DECIDE_ABORT)
        self.assertTrue(any("陈旧" in p for p in out["problems"]), out)

    def test_push_when_trees_differ(self):
        out = cs.decide_publish(remote_ref_exists=True, remote_ref_sha=REMOTE, local_sha=LOCAL,
                                trees_equal=False, pr_state="open", pr_head_sha=REMOTE)
        self.assertEqual(out["decision"], cs.DECIDE_PUSH)
        self.assertEqual(out["expected_head_sha"], LOCAL)

    def test_push_when_branch_absent(self):
        out = cs.decide_publish(remote_ref_exists=False, remote_ref_sha=None, local_sha=LOCAL,
                                trees_equal=False, pr_state="none", pr_head_sha=None)
        self.assertEqual(out["decision"], cs.DECIDE_PUSH)
        self.assertEqual(out["expected_head_sha"], LOCAL)

    def test_closed_pr_aborts_idempotently(self):
        """幂等：已有 PR 被关闭/合并时不得自动复活或覆盖。"""
        for state in ("closed", "merged"):
            with self.subTest(state=state):
                out = cs.decide_publish(remote_ref_exists=True, remote_ref_sha=REMOTE, local_sha=LOCAL,
                                        trees_equal=True, pr_state=state, pr_head_sha=REMOTE)
                self.assertEqual(out["decision"], cs.DECIDE_ABORT)
                self.assertIsNone(out["expected_head_sha"])

    def test_repeated_runs_with_no_change_are_idempotent(self):
        """重复运行 + 无变化：每次都判定 skip-push 且 expected 恒为远端 SHA。"""
        results = [cs.decide_publish(remote_ref_exists=True, remote_ref_sha=REMOTE, local_sha=LOCAL,
                                     trees_equal=True, pr_state="open", pr_head_sha=REMOTE)
                   for _ in range(3)]
        self.assertTrue(all(r["decision"] == cs.DECIDE_SKIP_PUSH and r["expected_head_sha"] == REMOTE
                            for r in results))

    def test_malformed_remote_sha_aborts(self):
        out = cs.decide_publish(remote_ref_exists=True, remote_ref_sha="   ", local_sha=LOCAL,
                                trees_equal=True, pr_state="open", pr_head_sha=REMOTE)
        self.assertEqual(out["decision"], cs.DECIDE_ABORT)

    def test_malformed_local_sha_aborts(self):
        out = cs.decide_publish(remote_ref_exists=False, remote_ref_sha=None, local_sha="zz",
                                trees_equal=False, pr_state="none", pr_head_sha=None)
        self.assertEqual(out["decision"], cs.DECIDE_ABORT)


class ConsistencyTest(unittest.TestCase):
    def test_all_equal_is_consistent(self):
        self.assertEqual(cs.check_consistency(REMOTE, REMOTE, REMOTE), [])

    def test_pr_head_mismatch(self):
        problems = cs.check_consistency(REMOTE, OTHER, REMOTE)
        self.assertTrue(any("PR head SHA" in p for p in problems), problems)

    def test_branch_ref_mismatch(self):
        problems = cs.check_consistency(REMOTE, REMOTE, OTHER)
        self.assertTrue(any("候选分支 ref" in p for p in problems), problems)

    def test_malformed_inputs_fail_closed(self):
        problems = cs.check_consistency("", "x", REMOTE)
        self.assertTrue(len(problems) >= 2, problems)


class CliTest(unittest.TestCase):
    def test_decide_cli_exit_codes(self):
        ok = cs.main(["decide", "--remote-ref-exists", "true", "--remote-ref-sha", REMOTE,
                      "--local-sha", LOCAL, "--trees-equal", "true", "--pr-state", "open",
                      "--pr-head-sha", REMOTE])
        self.assertEqual(ok, 0)
        bad = cs.main(["decide", "--remote-ref-exists", "true", "--remote-ref-sha", REMOTE,
                       "--local-sha", LOCAL, "--trees-equal", "true", "--pr-state", "closed",
                       "--pr-head-sha", REMOTE])
        self.assertEqual(bad, 1)

    def test_verify_cli_exit_codes(self):
        self.assertEqual(cs.main(["verify", "--expected", REMOTE, "--pr-head", REMOTE,
                                  "--branch-ref", REMOTE]), 0)
        self.assertEqual(cs.main(["verify", "--expected", REMOTE, "--pr-head", OTHER,
                                  "--branch-ref", REMOTE]), 1)


if __name__ == "__main__":
    unittest.main()
