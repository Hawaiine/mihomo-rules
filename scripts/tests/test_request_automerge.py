#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""auto-merge 请求的三态区分测试（请求成功 / 已启用 / 已合并）与 fail-closed 行为。

使用 mock 传输：**不代表真实 GitHub 平台端到端成功**。
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

import request_automerge as ra  # noqa: E402

MANIFEST = str(ROOT / ".github" / "required-checks.json")
HEAD = "a" * 40
OTHER = "b" * 40
NAMES = ["general-pr-verify", "workflow-policy-guard", "trusted-baseline-guard"]


def run(name, conclusion="success"):
    return {"name": name, "conclusion": conclusion, "status": "completed", "head_sha": HEAD,
            "app": {"slug": "github-actions", "id": 15368}, "id": 1,
            "started_at": "2026-10-13T00:00:00Z"}


def pr(state="open", head=HEAD, auto_merge=None, merged=False, merge_commit_sha=None):
    return {"state": state, "head": {"sha": head}, "base": {"ref": "main"}, "number": 7,
            "node_id": "PR_kwNode", "merged": merged, "auto_merge": auto_merge,
            "merge_commit_sha": merge_commit_sha}


class Transport:
    def __init__(self, prs, runs=None, gql=(200, {"data": {"enablePullRequestAutoMerge": {"clientMutationId": None}}}),
                 main_head="c" * 40):
        self.prs = list(prs)
        self.runs = runs if runs is not None else [run(n) for n in NAMES]
        self.gql = gql
        self.main_head = main_head
        self.gets = []
        self.posts = []

    def get(self, url, token):
        self.gets.append(url)
        if "/pulls/" in url:
            body = self.prs[0] if len(self.prs) == 1 else self.prs.pop(0)
            return 200, body
        if "/check-runs" in url:
            return 200, {"check_runs": self.runs}
        if "/git/ref/heads/main" in url:
            return 200, {"object": {"sha": self.main_head}}
        raise AssertionError("unexpected GET " + url)

    def post(self, url, token, payload):
        self.posts.append((url, payload))
        return self.gql


def call(transport, **kw):
    return ra.run(repo="o/r", pr=7, head_sha=HEAD, required_manifest=MANIFEST, token="t",
                  getter=transport.get, poster=transport.post, log=lambda *a, **k: None, **kw)


class ThreeStateTest(unittest.TestCase):
    def test_request_accepted_but_not_yet_merged(self):
        t = Transport([pr(), pr(), pr(auto_merge={"enabledAt": "2026-10-13T00:00:00Z"})])
        report = call(t)
        self.assertTrue(report["graphql_request_ok"])
        self.assertTrue(report["auto_merge_enabled"])
        self.assertFalse(report["merged"])
        self.assertTrue(ra.succeeded(report))
        self.assertNotIn("merge_commit_sha", report)
        self.assertEqual(report["writes_performed"], ["POST /graphql enablePullRequestAutoMerge"])
        self.assertEqual(len(t.posts), 1)

    def test_already_merged_reports_commit_and_base_head(self):
        t = Transport([pr(), pr(), pr(state="closed", merged=True, merge_commit_sha="d" * 40)])
        report = call(t)
        self.assertTrue(report["merged"])
        self.assertEqual(report["merge_commit_sha"], "d" * 40)
        self.assertEqual(report["main_head_sha"], "c" * 40)
        self.assertTrue(ra.succeeded(report))

    def test_graphql_errors_fail(self):
        t = Transport([pr(), pr()], gql=(200, {"errors": [{"message": "nope"}]}))
        report = call(t)
        self.assertFalse(report["graphql_request_ok"])
        self.assertFalse(ra.succeeded(report))
        self.assertIn("GraphQL", report["reason"])

    def test_graphql_missing_expected_data_fails(self):
        """只检查没有 errors 是不够的：必须确认 data.enablePullRequestAutoMerge 存在。"""
        t = Transport([pr(), pr()], gql=(200, {"data": {}}))
        report = call(t)
        self.assertFalse(report["graphql_request_ok"])
        self.assertTrue(any("enablePullRequestAutoMerge" in p for p in report["problems"]), report)

    def test_graphql_http_error_fails(self):
        t = Transport([pr(), pr()], gql=(500, {"_error": "boom"}))
        report = call(t)
        self.assertFalse(report["graphql_request_ok"])
        self.assertFalse(ra.succeeded(report))

    def test_auto_merge_not_enabled_after_request_fails(self):
        t = Transport([pr(), pr(), pr(auto_merge=None)])
        report = call(t)
        self.assertTrue(report["graphql_request_ok"])
        self.assertFalse(report["auto_merge_enabled"])
        self.assertFalse(ra.succeeded(report))
        self.assertIn("auto_merge 为空", " ".join(report["problems"]))


class PreconditionTest(unittest.TestCase):
    def test_required_checks_not_satisfied_blocks_request(self):
        t = Transport([pr()], runs=[run("general-pr-verify", "skipped")] + [run(n) for n in NAMES[1:]])
        report = call(t)
        self.assertFalse(ra.succeeded(report))
        self.assertEqual(t.posts, [])              # 绝不发送 mutation
        self.assertIn("严格必需检查未通过", report["reason"])

    def test_head_mismatch_blocks_request(self):
        t = Transport([pr(head=OTHER), pr(head=OTHER)])
        report = call(t)
        self.assertFalse(ra.succeeded(report))
        self.assertEqual(t.posts, [])

    def test_pr_closed_blocks_request(self):
        t = Transport([pr(state="closed"), pr(state="closed")])
        report = call(t)
        self.assertFalse(ra.succeeded(report))
        self.assertEqual(t.posts, [])

    def test_dry_run_sends_no_mutation(self):
        t = Transport([pr()])
        report = call(t, dry_run=True)
        self.assertEqual(t.posts, [])
        self.assertFalse(report["graphql_request_ok"])
        self.assertTrue(report["dry_run"])

    def test_readback_http_failure_fails_closed(self):
        """mutation 发出但回读失败 ⇒ 无法确认 ⇒ 不得判定成功。"""
        t = Transport([pr(), pr()])
        original = t.get

        def flaky(url, token):
            if "/pulls/" in url and sum(1 for u in t.gets if "/pulls/" in u) >= 2:
                t.gets.append(url)
                return 503, {"_error": "unavailable"}
            return original(url, token)

        t.get = flaky
        report = call(t)
        self.assertTrue(report["graphql_request_ok"])   # mutation 已发出
        self.assertFalse(report["auto_merge_enabled"])
        self.assertFalse(ra.succeeded(report))          # 但无法确认 → 不算成功


if __name__ == "__main__":
    unittest.main()
