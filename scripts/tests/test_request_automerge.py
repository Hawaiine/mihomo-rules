#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""auto-merge 请求测试：三态区分、fail-closed、**mutation 前的 base/head 对账**（Fix C）。

使用 mock 传输：**不代表真实 GitHub 平台端到端成功**。
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))
sys.path.insert(0, str(ROOT / "scripts" / "tests"))

import prov_fixture as fx  # noqa: E402
import request_automerge as ra  # noqa: E402

MANIFEST = str(ROOT / ".github" / "required-checks.json")
HEAD, OTHER, NAMES = fx.HEAD, fx.OTHER_SHA, fx.NAMES
MAIN = fx.MAIN_SHA


def checks(conclusion="success"):
    return [fx.check_run(n, conclusion) for n in NAMES]


def pr(state="open", head=HEAD, base="main", base_sha=MAIN, auto_merge=None, merged=False,
       merge_commit_sha=None):
    return {"state": state, "head": {"sha": head}, "base": {"ref": base, "sha": base_sha},
            "number": 29, "node_id": "PR_kwNode", "merged": merged, "auto_merge": auto_merge,
            "merge_commit_sha": merge_commit_sha, "user": {"login": "Hawaiine", "type": "User"}}


class Transport:
    """mock 传输：PR 序列按调用顺序消费（耗尽后粘住最后一项）。"""

    def __init__(self, prs, check_runs=None, main_seq=None, gql=None, errors=None,
                 workflow_runs=None):
        self.prs = list(prs)
        self.check_runs = check_runs if check_runs is not None else checks()
        self.main_seq = list(main_seq) if main_seq else None
        self.gql = gql or (200, {"data": {"enablePullRequestAutoMerge": {"clientMutationId": None}}})
        self.errors = {k: list(v) for k, v in (errors or {}).items()}
        self.workflow_runs = workflow_runs or [fx.workflow_run()]
        self.gets: list[str] = []
        self.posts: list[tuple] = []

    def get(self, url, token):
        self.gets.append(url)
        for key, codes in self.errors.items():
            if key in url and codes:
                return codes.pop(0), {"_error": "simulated failure"}
        if "/pulls/" in url:
            return 200, (self.prs[0] if len(self.prs) == 1 else self.prs.pop(0))
        if "/check-runs" in url:
            return 200, {"check_runs": self.check_runs}
        if "/actions/runs/" in url and url.endswith("/jobs?per_page=100"):
            return 200, {"jobs": fx.jobs_for(self.check_runs)}
        if "/actions/runs" in url:
            return 200, {"workflow_runs": self.workflow_runs}
        if "/git/ref/heads/" in url:
            if self.main_seq:
                return 200, {"object": {"sha": self.main_seq[0] if len(self.main_seq) == 1
                                        else self.main_seq.pop(0)}}
            return 200, {"object": {"sha": MAIN}}
        raise AssertionError("unexpected GET " + url)

    def post(self, url, token, payload):
        self.posts.append((url, payload))
        return self.gql


def call(transport, **kw):
    return ra.run(repo="o/r", pr=29, head_sha=HEAD, required_manifest=MANIFEST, token="t",
                  getter=transport.get, poster=transport.post, log=lambda *a, **k: None, **kw)


class ThreeStateTest(unittest.TestCase):
    def test_request_accepted_but_not_yet_merged(self):
        t = Transport([pr(), pr(auto_merge={"enabledAt": "2026-10-13T00:00:00Z"})])
        report = call(t)
        self.assertTrue(report["graphql_request_ok"])
        self.assertTrue(report["auto_merge_enabled"])
        self.assertFalse(report["merged"])
        self.assertTrue(ra.succeeded(report))
        self.assertNotIn("merge_commit_sha", report)
        self.assertEqual(report["writes_performed"], ["POST /graphql enablePullRequestAutoMerge"])
        self.assertEqual(len(t.posts), 1)
        self.assertEqual(report["pre_mutation_reconciliation"]["base_sha"], MAIN)

    def test_already_merged_reports_commit_and_base_head(self):
        # mutation 前共有 4 次 PR 读取（严格检查 → node_id → 对账 → 对账内复核），第 5 次为回读
        t = Transport([pr(), pr(), pr(), pr(),
                       pr(state="closed", merged=True, merge_commit_sha="d" * 40)])
        report = call(t)
        self.assertTrue(report["merged"])
        self.assertEqual(report["merge_commit_sha"], "d" * 40)
        self.assertEqual(report["main_head_sha"], MAIN)
        self.assertTrue(ra.succeeded(report))

    def test_graphql_errors_fail(self):
        t = Transport([pr()], gql=(200, {"errors": [{"message": "nope"}]}))
        report = call(t)
        self.assertFalse(report["graphql_request_ok"])
        self.assertFalse(ra.succeeded(report))
        self.assertIn("GraphQL", report["reason"])

    def test_graphql_missing_expected_data_fails(self):
        t = Transport([pr()], gql=(200, {"data": {}}))
        report = call(t)
        self.assertFalse(report["graphql_request_ok"])
        self.assertTrue(any("enablePullRequestAutoMerge" in p for p in report["problems"]), report)

    def test_graphql_http_error_fails(self):
        t = Transport([pr()], gql=(500, {"_error": "boom"}))
        report = call(t)
        self.assertFalse(report["graphql_request_ok"])
        self.assertFalse(ra.succeeded(report))

    def test_auto_merge_not_enabled_after_request_fails(self):
        t = Transport([pr(), pr(auto_merge=None)])
        report = call(t)
        self.assertTrue(report["graphql_request_ok"])
        self.assertFalse(report["auto_merge_enabled"])
        self.assertFalse(ra.succeeded(report))
        self.assertIn("auto_merge 为空", " ".join(report["problems"]))


class PreconditionTest(unittest.TestCase):
    def test_required_checks_not_satisfied_blocks_request(self):
        t = Transport([pr()], check_runs=[fx.check_run("general-pr-verify", "skipped")]
                      + [fx.check_run(n) for n in NAMES[1:]])
        report = call(t)
        self.assertFalse(ra.succeeded(report))
        self.assertEqual(t.posts, [])
        self.assertIn("严格必需检查未通过", report["reason"])

    def test_fake_same_name_success_from_other_workflow_blocks_request(self):
        real = [fx.check_run("general-pr-verify", "failure")] + [fx.check_run(n) for n in NAMES[1:]]
        fake = fx.check_run("general-pr-verify", "success", suite=fx.OTHER_SUITE, rid=424242,
                            started="2026-10-13T09:00:00Z")
        t = Transport([pr()], check_runs=real + [fake],
                      workflow_runs=[fx.workflow_run(),
                                     fx.workflow_run(rid=fx.OTHER_RUN_ID, path=fx.OTHER_WORKFLOW_PATH,
                                                     suite=fx.OTHER_SUITE)])
        report = call(t)
        self.assertFalse(ra.succeeded(report))
        self.assertEqual(t.posts, [])

    def test_head_mismatch_blocks_request(self):
        t = Transport([pr(head=OTHER)])
        report = call(t)
        self.assertFalse(ra.succeeded(report))
        self.assertEqual(t.posts, [])

    def test_pr_closed_blocks_request(self):
        t = Transport([pr(state="closed")])
        report = call(t)
        self.assertFalse(ra.succeeded(report))
        self.assertEqual(t.posts, [])

    def test_dry_run_sends_no_mutation_but_runs_reconciliation(self):
        t = Transport([pr()])
        report = call(t, dry_run=True)
        self.assertEqual(t.posts, [])
        self.assertFalse(report["graphql_request_ok"])
        self.assertTrue(report["dry_run"])
        self.assertIn("pre_mutation_reconciliation", report)

    def test_readback_http_failure_fails_closed(self):
        t = Transport([pr(), pr()])
        original = t.get

        def flaky(url, token):
            if "/pulls/" in url and sum(1 for u in t.gets if "/pulls/" in u) >= 4:
                t.gets.append(url)
                return 503, {"_error": "unavailable"}
            return original(url, token)

        t.get = flaky
        report = call(t)
        self.assertTrue(report["graphql_request_ok"])
        self.assertFalse(report["auto_merge_enabled"])
        self.assertFalse(ra.succeeded(report))


class PreMutationReconciliationTest(unittest.TestCase):
    """Fix C：mutation 紧邻之前的只读对账，任何漂移都必须阻止 mutation。"""

    def test_base_sha_drift_before_mutation_blocks_request(self):
        # 前两次 main 读取（前置校验 + 对账）为 MAIN，第三次（对账内必需检查复核）已前进
        t = Transport([pr()], main_seq=[MAIN, MAIN, "9" * 40])
        report = call(t)
        self.assertFalse(ra.succeeded(report))
        self.assertEqual(t.posts, [])
        self.assertIn("mutation 前", report["reason"])

    def test_pr_base_sha_ahead_of_main_before_mutation_blocks_request(self):
        t = Transport([pr(base_sha="9" * 40)])
        report = call(t)
        self.assertFalse(ra.succeeded(report))
        self.assertEqual(t.posts, [])
        self.assertTrue(any("base 漂移" in p for p in report["problems"]), report)

    def test_pr_base_branch_changed_before_mutation_blocks_request(self):
        t = Transport([pr(), pr(base="release/2.x")])
        report = call(t)
        self.assertFalse(ra.succeeded(report))
        self.assertEqual(t.posts, [])
        self.assertTrue(any("目标分支" in p for p in report["problems"]), report)

    def test_head_drift_before_mutation_blocks_request(self):
        t = Transport([pr(), pr(head=OTHER)])
        report = call(t)
        self.assertFalse(ra.succeeded(report))
        self.assertEqual(t.posts, [])

    def test_pr_closed_before_mutation_blocks_request(self):
        t = Transport([pr(), pr(state="closed")])
        report = call(t)
        self.assertFalse(ra.succeeded(report))
        self.assertEqual(t.posts, [])

    def test_main_ref_error_before_mutation_blocks_request(self):
        t = Transport([pr()], errors={"/git/ref/heads/": [500] * 4})
        report = call(t)
        self.assertFalse(ra.succeeded(report))
        self.assertEqual(t.posts, [])

    def test_required_checks_regress_before_mutation_blocks_request(self):
        """对账时必须**重新执行**严格必需检查；若此时已不满足则不得 mutation。"""
        t = Transport([pr()])
        calls = {"n": 0}
        original = t.get

        def flaky(url, token):
            if "/check-runs" in url:
                calls["n"] += 1
                if calls["n"] >= 2:
                    t.gets.append(url)
                    return 200, {"check_runs": [fx.check_run("general-pr-verify", "failure")]
                                 + [fx.check_run(n) for n in NAMES[1:]]}
            return original(url, token)

        t.get = flaky
        report = call(t)
        self.assertFalse(ra.succeeded(report))
        self.assertEqual(t.posts, [])

    def test_no_mutation_on_any_reconciliation_failure(self):
        for t in (Transport([pr()], main_seq=[MAIN, "9" * 40]),
                  Transport([pr(), pr(base="other")]),
                  Transport([pr()], errors={"/git/ref/heads/": [500] * 4})):
            with self.subTest(t=t):
                report = call(t)
                self.assertEqual(t.posts, [])
                self.assertFalse(report["graphql_request_ok"])


if __name__ == "__main__":
    unittest.main()
