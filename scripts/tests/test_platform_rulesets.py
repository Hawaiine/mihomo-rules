#!/usr/bin/env python3
"""platform_rulesets 的严格判定测试（P0/P1-B）。

核心安全属性：**只有真正覆盖 main、且把 `route-a/verify` 作为必需 status、
且来源可验证（GitHub Actions）的 ruleset** 才能让平台判定通过。
"别的分支有同名 context"、"近似名"、"缺 conditions"、"读不到" 全部必须 BLOCKED。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

import evaluate_candidate_policy as ecp  # noqa: E402
import platform_rulesets as pr  # noqa: E402


def doc(*, include=("~DEFAULT_BRANCH",), exclude=(), enforcement="active",
        target="branch", contexts=(("route-a/verify", 15368),), rules=True):
    d = {"id": 1, "name": "protect-main", "target": target, "enforcement": enforcement,
         "source_type": "Repository", "source": "o/r",
         "conditions": {"ref_name": {"include": list(include), "exclude": list(exclude)}}}
    if rules:
        d["rules"] = [{"type": "required_status_checks",
                       "parameters": {"required_status_checks":
                                      [{"context": c, **({"integration_id": i} if i is not None else {})}
                                       for c, i in contexts]}}]
    else:
        d["rules"] = [{"type": "deletion"}]
    return d


def ev(docs, *, default_branch="main", required=("route-a/verify",)):
    return pr.evaluate(docs, repo="o/r", target_branch="main",
                       required_contexts=list(required), default_branch=default_branch)


class CoverageTest(unittest.TestCase):
    def test_correct_main_ruleset_is_ready(self):
        out = ev([{"id": 1, "http": 200, "body": doc()}])
        self.assertTrue(out["ok"], out["reason_codes"])
        self.assertEqual(out["evidence"]["covering_rulesets"], [1])

    def test_context_only_on_other_branch_is_blocked(self):
        other = doc(include=["refs/heads/dev"])
        out = ev([{"id": 2, "http": 200, "body": other}])
        self.assertFalse(out["ok"])
        self.assertIn("RULESET_NOT_COVERING_TARGET", out["reason_codes"])

    def test_other_branch_plus_correct_main_ruleset_is_ready(self):
        out = ev([{"id": 2, "http": 200, "body": doc(include=["refs/heads/dev"])},
                  {"id": 1, "http": 200, "body": doc()}])
        self.assertTrue(out["ok"], out["reason_codes"])

    def test_inactive_ruleset_is_blocked(self):
        out = ev([{"id": 1, "http": 200, "body": doc(enforcement="evaluate")}])
        self.assertFalse(out["ok"])
        self.assertIn("RULESET_NOT_ACTIVE", out["reason_codes"])

    def test_tag_ruleset_is_ignored(self):
        out = ev([{"id": 3, "http": 200, "body": doc(target="tag", include=["refs/tags/v*"])}])
        self.assertFalse(out["ok"])
        self.assertIn("RULESET_NOT_COVERING_TARGET", out["reason_codes"])

    def test_wildcard_include_covers_main(self):
        out = ev([{"id": 1, "http": 200, "body": doc(include=["refs/heads/*"])}])
        self.assertTrue(out["ok"], out["reason_codes"])

    def test_exclude_main_overrides_include(self):
        out = ev([{"id": 1, "http": 200, "body": doc(exclude=["refs/heads/main"])}])
        self.assertFalse(out["ok"])
        self.assertIn("RULESET_EXCLUDES_TARGET", out["reason_codes"])

    def test_default_branch_token_requires_real_default(self):
        """~DEFAULT_BRANCH 只有在 API 证明默认分支是 main 时才算覆盖。"""
        out = ev([{"id": 1, "http": 200, "body": doc()}], default_branch="trunk")
        self.assertFalse(out["ok"])
        self.assertIn("RULESET_NOT_COVERING_TARGET", out["reason_codes"])

    def test_missing_conditions_is_blocked(self):
        body = doc()
        body.pop("conditions")
        out = ev([{"id": 1, "http": 200, "body": body}])
        self.assertFalse(out["ok"])
        self.assertIn("RULESET_CONDITIONS_MISSING", out["reason_codes"])

    def test_empty_include_is_blocked(self):
        out = ev([{"id": 1, "http": 200, "body": doc(include=[])}])
        self.assertFalse(out["ok"])
        self.assertIn("RULESET_CONDITIONS_MISSING", out["reason_codes"])


class ContextTest(unittest.TestCase):
    def test_missing_context_is_blocked(self):
        out = ev([{"id": 1, "http": 200, "body": doc(contexts=(("general-pr-verify", 15368),))}])
        self.assertFalse(out["ok"])
        self.assertIn("REQUIRED_STATUS_CONTEXT_MISSING:route-a/verify", out["reason_codes"])

    def test_near_miss_context_is_blocked(self):
        for near in ("route-a/verify2", "route-a-verify", "route-a/verify/", "Route-A/verify"):
            out = ev([{"id": 1, "http": 200,
                       "body": doc(contexts=((near, 15368), ("route-a/verify", 15368)))}])
            # 精确 context 同时存在时才算通过；否则必须拒
            self.assertTrue(out["ok"])
            only_near = ev([{"id": 1, "http": 200, "body": doc(contexts=((near, 15368),))}])
            self.assertFalse(only_near["ok"], near)
            self.assertIn("REQUIRED_STATUS_CONTEXT_MISSING:route-a/verify",
                          only_near["reason_codes"])

    def test_near_miss_is_recorded_in_evidence(self):
        out = ev([{"id": 1, "http": 200, "body": doc(contexts=(("route-a/verify2", 15368),))}])
        self.assertIn("route-a/verify2", out["evidence"].get("near_miss_contexts", []))

    def test_wrong_integration_is_blocked(self):
        out = ev([{"id": 1, "http": 200, "body": doc(contexts=(("route-a/verify", 1234),))}])
        self.assertFalse(out["ok"])
        self.assertIn("REQUIRED_STATUS_SOURCE_UNVERIFIED:route-a/verify", out["reason_codes"])

    def test_missing_integration_is_unverified(self):
        out = ev([{"id": 1, "http": 200, "body": doc(contexts=(("route-a/verify", None),))}])
        self.assertFalse(out["ok"])
        self.assertIn("REQUIRED_STATUS_SOURCE_UNVERIFIED:route-a/verify", out["reason_codes"])

    def test_no_required_status_rule_is_blocked(self):
        out = ev([{"id": 1, "http": 200, "body": doc(rules=False)}])
        self.assertFalse(out["ok"])
        self.assertIn("REQUIRED_STATUS_CONTEXT_MISSING:route-a/verify", out["reason_codes"])


class ReadFailureTest(unittest.TestCase):
    def test_detail_403_blocks_everything(self):
        out = ev([{"id": 1, "http": 200, "body": doc()},
                  {"id": 4, "http": 403, "body": "Resource not accessible"}])
        self.assertFalse(out["ok"])
        self.assertIn("RULESET_UNREADABLE", out["reason_codes"])

    def test_empty_list_is_blocked(self):
        out = ev([])
        self.assertFalse(out["ok"])
        self.assertIn("NO_RULESET_FOUND", out["reason_codes"])

    def test_malformed_body_is_blocked(self):
        out = ev([{"id": 1, "http": 200, "body": ["not", "a", "dict"]}])
        self.assertFalse(out["ok"])
        self.assertIn("RULESET_UNREADABLE", out["reason_codes"])


class FetchTest(unittest.TestCase):
    """fetch_and_evaluate：分页、默认分支、错误码传播。"""

    def fake(self, routes):
        return lambda path, token, api=None: routes.get(path, (404, {"message": "nf"}))

    def run_fetch(self, routes):
        with mock.patch.object(pr, "_http_get", self.fake(routes)):
            return pr.fetch_and_evaluate("o/r", "t", target_branch="main",
                                         required_contexts=["route-a/verify"])

    def routes(self, *, list_page1=None, page2=None, repo_status=200,
               detail_status=200, body=None):
        entry = {"id": 1, "name": "protect-main", "target": "branch", "enforcement": "active"}
        return {
            "/repos/o/r": (repo_status, {"default_branch": "main"}),
            "/repos/o/r/rulesets?per_page=100&page=1": (
                200, list_page1 if list_page1 is not None else [entry]),
            "/repos/o/r/rulesets?per_page=100&page=2": (
                200, page2 if page2 is not None else []),
            "/repos/o/r/rulesets/1": (detail_status, body if body is not None else doc()),
        }

    def test_happy_path(self):
        out = self.run_fetch(self.routes())
        self.assertTrue(out["ok"], out["reason_codes"])
        self.assertEqual(out["evidence"]["default_branch"], "main")

    def test_list_403_blocks(self):
        r = self.routes()
        r["/repos/o/r/rulesets?per_page=100&page=1"] = (403, "forbidden")
        out = self.run_fetch(r)
        self.assertFalse(out["ok"])
        self.assertIn("RULESET_LIST_HTTP_403", out["reason_codes"])

    def test_detail_500_blocks(self):
        out = self.run_fetch(self.routes(detail_status=500))
        self.assertFalse(out["ok"])
        self.assertIn("RULESET_UNREADABLE", out["reason_codes"])

    def test_repo_unreadable_makes_default_branch_unknown(self):
        """默认分支读不到 → ~DEFAULT_BRANCH 无法证明 → 不通过（不是"大概对"）。"""
        out = self.run_fetch(self.routes(repo_status=403))
        self.assertFalse(out["ok"])
        self.assertIsNone(out["evidence"]["default_branch"])
        self.assertIn("RULESET_NOT_COVERING_TARGET", out["reason_codes"])

    def test_pagination_is_followed(self):
        first = [{"id": 1, "name": "p", "target": "branch", "enforcement": "active"}] * 100
        out = self.run_fetch(self.routes(list_page1=first, page2=[]))
        self.assertTrue(out["ok"], out["reason_codes"])  # 跟随到 page2 后完整
        self.assertEqual(out["evidence"]["ruleset_count"], 100)

    def test_pagination_error_blocks(self):
        first = [{"id": 1, "name": "p", "target": "branch", "enforcement": "active"}] * 100
        r = self.routes(list_page1=first)
        r["/repos/o/r/rulesets?per_page=100&page=2"] = (500, "boom")
        out = self.run_fetch(r)
        self.assertFalse(out["ok"])
        self.assertIn("RULESET_LIST_HTTP_500", out["reason_codes"])


class PlatformStateWiringTest(unittest.TestCase):
    """ecp._platform_state 必须继承同一套严格语义（防止两处口径漂移）。"""

    def test_ok_propagates(self):
        routes = {
            "/repos/o/r": (200, {"default_branch": "main"}),
            "/repos/o/r/rulesets?per_page=100&page=1": (
                200, [{"id": 1, "name": "p", "target": "branch", "enforcement": "active"}]),
            "/repos/o/r/rulesets?per_page=100&page=2": (200, []),
            "/repos/o/r/rulesets/1": (200, doc()),
        }
        with mock.patch.object(pr, "_http_get",
                               lambda path, token, api=None: routes.get(path, (404, "nf"))):
            ok, evidence = ecp._platform_state("o/r", "t")
        self.assertTrue(ok)
        self.assertTrue(evidence["readable"])
        self.assertEqual(evidence["reason_codes"], [])

    def test_other_branch_only_is_not_ok(self):
        routes = {
            "/repos/o/r": (200, {"default_branch": "main"}),
            "/repos/o/r/rulesets?per_page=100&page=1": (
                200, [{"id": 2, "name": "dev-only", "target": "branch", "enforcement": "active"}]),
            "/repos/o/r/rulesets?per_page=100&page=2": (200, []),
            "/repos/o/r/rulesets/2": (200, doc(include=["refs/heads/dev"])),
        }
        with mock.patch.object(pr, "_http_get",
                               lambda path, token, api=None: routes.get(path, (404, "nf"))):
            ok, evidence = ecp._platform_state("o/r", "t")
        self.assertFalse(ok)
        self.assertIn("RULESET_NOT_COVERING_TARGET", evidence["reason_codes"])


if __name__ == "__main__":
    unittest.main()
