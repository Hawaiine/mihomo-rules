#!/usr/bin/env python3
"""机器人作者 PR 路径测试矩阵（A：创建/幂等/回读；B：事件状态分类）。

这些用例只覆盖**纯逻辑**与**编排顺序**；真实 GitHub 写入路径由端到端步骤验证，
不得用本地 mock 结果冒充平台成功。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

import robot_pr as rp  # noqa: E402

REPO = "Hawaiine/mihomo-rules"
HEAD = "b" * 40
BASE = "4" * 40


def pr_obj(*, number=34, author_id=rp.BOT_USER_ID, author_type="Bot",
           login=rp.BOT_LOGIN, head=HEAD, head_ref="feat/x", base_ref="main",
           repo=REPO, state="open"):
    return {"number": number, "state": state,
            "user": {"id": author_id, "login": login, "type": author_type},
            "head": {"sha": head, "ref": head_ref},
            "base": {"ref": base_ref, "repo": {"full_name": repo}}}


class BranchValidationTest(unittest.TestCase):
    def test_safe_branch_accepted(self):
        for name in ("feat/x", "fix/f-a-f-b-strict-runid", "robot/pr-33", "v1.2.3"):
            with self.subTest(name=name):
                self.assertEqual(rp.validate_branch_name(name), [])

    def test_unsafe_branches_rejected(self):
        cases = {
            "": "SOURCE_BRANCH_INVALID:empty",
            "main": "SOURCE_BRANCH_INVALID:reserved:main",
            "master": "SOURCE_BRANCH_INVALID:reserved:master",
            "a..b": "SOURCE_BRANCH_INVALID:traversal",
            "a//b": "SOURCE_BRANCH_INVALID:traversal",
            "/lead": "SOURCE_BRANCH_INVALID:charset",
            "trail/": "SOURCE_BRANCH_INVALID:shape",
            "-x": "SOURCE_BRANCH_INVALID:charset",
            "@x": "SOURCE_BRANCH_INVALID:charset",
            "a b": "SOURCE_BRANCH_INVALID:charset",
            "a;rm -rf /": "SOURCE_BRANCH_INVALID:charset",
            "$(id)": "SOURCE_BRANCH_INVALID:charset",
            "x.lock": "SOURCE_BRANCH_INVALID:shape",
            "a" * 120: "SOURCE_BRANCH_INVALID:charset",
        }
        for name, want in cases.items():
            with self.subTest(name=name):
                problems = rp.validate_branch_name(name)
                self.assertTrue(problems, name)
                self.assertIn(want, problems[0])

    def test_non_string_rejected(self):
        for value in (None, 1, ["x"], {"a": 1}):
            with self.subTest(value=value):
                self.assertEqual(rp.validate_branch_name(value), ["SOURCE_BRANCH_INVALID:empty"])

    def test_base_ref_restricted(self):
        self.assertEqual(rp.validate_base_ref("main"), [])
        for bad in ("develop", "", None, "main "):
            with self.subTest(bad=bad):
                self.assertTrue(rp.validate_base_ref(bad))


class AuthorIdentityTest(unittest.TestCase):
    def test_github_actions_bot_recognised(self):
        self.assertEqual(rp.classify_author(pr_obj())[0], "BOT")

    def test_human_author_rejected_as_bot(self):
        cls, problems = rp.classify_author(pr_obj(author_id=152380613, author_type="User",
                                                  login="Hawaiine"))
        self.assertEqual(cls, "HUMAN")
        self.assertTrue(any(p.startswith("PR_AUTHOR_NOT_BOT") for p in problems))

    def test_other_bot_rejected(self):
        cls, problems = rp.classify_author(pr_obj(author_id=999, author_type="Bot",
                                                  login="dependabot[bot]"))
        self.assertEqual(cls, "HUMAN")
        self.assertTrue(any(p.startswith("PR_AUTHOR_UNEXPECTED_BOT") for p in problems))

    def test_spoofed_login_with_bot_type_but_wrong_id_rejected(self):
        cls, _ = rp.classify_author(pr_obj(author_id=42))
        self.assertEqual(cls, "HUMAN")

    def test_missing_author_fails_closed(self):
        for bad in ({"user": {}}, {"user": None}, {}, None, "x"):
            with self.subTest(bad=bad):
                cls, problems = rp.classify_author(bad)
                self.assertEqual(cls, "UNKNOWN")
                self.assertTrue(problems)


class ReadbackTest(unittest.TestCase):
    def verify(self, pr):
        return rp.verify_pr_readback(pr, repo=REPO, base_ref="main", head_sha=HEAD,
                                     source_branch="feat/x")

    def test_clean_readback_passes(self):
        ok, problems, evidence = self.verify(pr_obj())
        self.assertTrue(ok, problems)
        self.assertEqual(evidence["author_id"], rp.BOT_USER_ID)

    def test_human_author_fails(self):
        ok, problems, _ = self.verify(pr_obj(author_id=152380613, author_type="User",
                                             login="Hawaiine"))
        self.assertFalse(ok)
        self.assertTrue(any(p.startswith("PR_AUTHOR_NOT_BOT") for p in problems))

    def test_head_sha_mismatch_fails(self):
        ok, problems, _ = self.verify(pr_obj(head="c" * 40))
        self.assertFalse(ok)
        self.assertIn("PR_HEAD_SHA_MISMATCH", problems)

    def test_head_ref_mismatch_fails(self):
        ok, problems, _ = self.verify(pr_obj(head_ref="other"))
        self.assertFalse(ok)
        self.assertIn("PR_HEAD_REF_MISMATCH", problems)

    def test_base_ref_mismatch_fails(self):
        ok, problems, _ = self.verify(pr_obj(base_ref="release"))
        self.assertFalse(ok)
        self.assertIn("PR_BASE_REF_MISMATCH", problems)

    def test_base_repo_mismatch_fails(self):
        ok, problems, _ = self.verify(pr_obj(repo="attacker/mihomo-rules"))
        self.assertFalse(ok)
        self.assertIn("PR_BASE_REPO_MISMATCH", problems)

    def test_closed_pr_fails(self):
        ok, problems, _ = self.verify(pr_obj(state="closed"))
        self.assertFalse(ok)
        self.assertTrue(any(p.startswith("PR_NOT_OPEN") for p in problems))

    def test_malformed_head_sha_fails(self):
        for bad in ("", "abc", "Z" * 40, None):
            with self.subTest(bad=bad):
                ok, problems, _ = self.verify(pr_obj(head=bad))
                self.assertFalse(ok)
                self.assertTrue(problems)


class IdempotencyTest(unittest.TestCase):
    def decide(self, existing):
        return rp.choose_action(existing, repo=REPO, source_branch="feat/x", base_ref="main")

    def test_none_existing_creates(self):
        self.assertEqual(self.decide([])[0], "create")

    def test_single_existing_updates(self):
        action, pr, problems = self.decide([pr_obj(number=7)])
        self.assertEqual(action, "update")
        self.assertEqual(pr["number"], 7)
        self.assertEqual(problems, [])

    def test_multiple_existing_is_ambiguous(self):
        action, pr, problems = self.decide([pr_obj(number=7), pr_obj(number=8)])
        self.assertEqual(action, "fail")
        self.assertIsNone(pr)
        self.assertTrue(any(p.startswith("AMBIGUOUS_PR_CANDIDATES") for p in problems))

    def test_other_branch_pr_ignored(self):
        self.assertEqual(self.decide([pr_obj(head_ref="other")])[0], "create")

    def test_closed_pr_ignored(self):
        self.assertEqual(self.decide([pr_obj(state="closed")])[0], "create")

    def test_malformed_list_fails(self):
        self.assertEqual(self.decide(None)[0], "fail")


class RunStateClassificationTest(unittest.TestCase):
    def test_no_run(self):
        self.assertEqual(rp.classify_run_state(None)[0], "A_NO_RUN")

    def test_action_required(self):
        for run in ({"status": "queued", "conclusion": None},
                    {"status": "waiting", "conclusion": None},
                    {"status": "completed", "conclusion": "action_required"}):
            with self.subTest(run=run):
                self.assertEqual(rp.classify_run_state(run)[0], "B_ACTION_REQUIRED")

    def test_failed_or_skipped(self):
        for conclusion in ("failure", "cancelled", "skipped", "timed_out"):
            with self.subTest(conclusion=conclusion):
                self.assertEqual(
                    rp.classify_run_state({"status": "completed", "conclusion": conclusion})[0],
                    "C_FAILED_OR_SKIPPED")

    def test_success(self):
        self.assertEqual(
            rp.classify_run_state({"status": "completed", "conclusion": "success"})[0],
            "F_SUCCESS")

    def test_malformed_run(self):
        self.assertEqual(rp.classify_run_state("nope")[0], "UNKNOWN")


class OrchestrationTest(unittest.TestCase):
    """用假 API 验证**调用顺序与失败路径**（不代表 GitHub 真实行为）。"""

    def setUp(self):
        self.calls = []
        self._orig = rp._call

    def tearDown(self):
        rp._call = self._orig

    def fake(self, responses):
        def _call(token, method, path, api, body=None):
            self.calls.append((method, path))
            for (m, prefix), value in responses:
                if m == method and path.startswith(prefix):
                    return value
            return 404, {"message": "not found"}
        rp._call = _call

    def run_orch(self, **kw):
        args = dict(repo=REPO, source_branch="feat/x", base_ref="main", title="t",
                    body="b", token="x", api="https://api.github.com")
        args.update(kw)
        return rp.create_or_update(**args)

    def test_branch_unreadable_fails_closed(self):
        self.fake([])
        out = self.run_orch()
        self.assertFalse(out["ok"])
        self.assertTrue(out["problems"][0].startswith("SOURCE_BRANCH_UNREADABLE"))

    def test_empty_diff_rejected(self):
        self.fake([(("GET", "/repos/Hawaiine/mihomo-rules/git/ref/heads/feat%2Fx"),
                    (200, {"object": {"sha": HEAD}})),
                   (("GET", "/repos/Hawaiine/mihomo-rules/compare/main..."),
                    (200, {"files": []}))])
        out = self.run_orch()
        self.assertFalse(out["ok"])
        self.assertTrue(any(p.startswith("DIFF_EMPTY") for p in out["problems"]))

    def test_create_then_readback_mismatch_fails(self):
        self.fake([(("GET", "/repos/Hawaiine/mihomo-rules/git/ref/heads/feat%2Fx"),
                    (200, {"object": {"sha": HEAD}})),
                   (("GET", "/repos/Hawaiine/mihomo-rules/compare/main..."),
                    (200, {"files": [{"filename": "a"}]})),
                   (("GET", "/repos/Hawaiine/mihomo-rules/pulls?state=open&head="),
                    (200, [])),
                   (("POST", "/repos/Hawaiine/mihomo-rules/pulls"),
                    (201, {"number": 99})),
                   (("GET", "/repos/Hawaiine/mihomo-rules/pulls/99"),
                    (200, pr_obj(number=99, author_id=152380613, author_type="User",
                                 login="Hawaiine")))])
        out = self.run_orch()
        self.assertFalse(out["ok"])
        self.assertTrue(any(p.startswith("PR_AUTHOR_NOT_BOT") for p in out["problems"]))
        self.assertIn(("POST", "/repos/Hawaiine/mihomo-rules/pulls"), self.calls)

    def test_successful_create_reports_bot_author(self):
        self.fake([(("GET", "/repos/Hawaiine/mihomo-rules/git/ref/heads/feat%2Fx"),
                    (200, {"object": {"sha": HEAD}})),
                   (("GET", "/repos/Hawaiine/mihomo-rules/compare/main..."),
                    (200, {"files": [{"filename": "a"}, {"filename": "b"}]})),
                   (("GET", "/repos/Hawaiine/mihomo-rules/pulls?state=open&head="),
                    (200, [])),
                   (("POST", "/repos/Hawaiine/mihomo-rules/pulls"),
                    (201, {"number": 99})),
                   (("GET", "/repos/Hawaiine/mihomo-rules/pulls/99"),
                    (200, pr_obj(number=99)))])
        out = self.run_orch()
        self.assertTrue(out["ok"], out["problems"])
        self.assertEqual(out["action"], "create")
        self.assertEqual(out["changed_files"], 2)
        self.assertEqual(out["evidence"]["author"], rp.BOT_LOGIN)

    def test_write_failure_never_reports_success(self):
        self.fake([(("GET", "/repos/Hawaiine/mihomo-rules/git/ref/heads/feat%2Fx"),
                    (200, {"object": {"sha": HEAD}})),
                   (("GET", "/repos/Hawaiine/mihomo-rules/compare/main..."),
                    (200, {"files": [{"filename": "a"}]})),
                   (("GET", "/repos/Hawaiine/mihomo-rules/pulls?state=open&head="),
                    (200, [])),
                   (("POST", "/repos/Hawaiine/mihomo-rules/pulls"),
                    (403, {"message": "Resource not accessible by integration"}))])
        out = self.run_orch()
        self.assertFalse(out["ok"])
        self.assertTrue(any(p.startswith("PR_WRITE_FAILED:HTTP403") for p in out["problems"]))

    def test_dry_run_makes_no_api_calls(self):
        self.fake([])
        out = self.run_orch(dry_run=True)
        self.assertTrue(out["ok"])
        self.assertEqual(self.calls, [])

    def test_invalid_inputs_short_circuit_before_api(self):
        self.fake([])
        out = self.run_orch(source_branch="a;rm -rf /")
        self.assertFalse(out["ok"])
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
