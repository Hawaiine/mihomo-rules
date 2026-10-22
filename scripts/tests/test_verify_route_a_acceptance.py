#!/usr/bin/env python3
"""Route A 功能验收工具测试（纯分类 + 一次真实 HTTP 错误路径）。"""
from __future__ import annotations

import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import verify_route_a_acceptance as acc  # noqa: E402

HEAD = "a" * 40
MAIN = "b" * 40
REQUIRED = ["general-pr-verify", "workflow-policy-guard", "trusted-baseline-guard"]


def cr(name, app=15368, conclusion="success", head=HEAD, details=None):
    return {"name": name, "head_sha": head, "status": "completed", "conclusion": conclusion,
            "app": {"id": app, "slug": "github-actions"},
            "details_url": details or "https://github.com/o/r/actions/runs/500"}


def ev(**over):
    d = {
        "pull": {"state": "open", "merged": False, "mergeable": True, "mergeable_state": "clean",
                 "head": {"sha": HEAD}, "base": {"ref": "main", "sha": MAIN}},
        "main": {"object": {"sha": MAIN}},
        "check_runs": {"check_runs": [cr("general-pr-verify"), cr("workflow-policy-guard"),
                                      cr("trusted-baseline-guard"), cr("route-a/verify")]},
        "statuses": [{"context": "route-a/verify", "state": "success",
                      "target_url": "https://github.com/o/r/actions/runs/500",
                      "creator": {"login": "github-actions[bot]", "type": "Bot"}}],
        "combined": {"state": "success"},
        "publisher_run": {"id": 500, "name": "General PR verification",
                          "path": ".github/workflows/pr-verify.yml", "event": "pull_request",
                          "head_sha": HEAD, "conclusion": "success"},
        "expected_head_sha": HEAD,
    }
    for k, v in over.items():
        if k == "pull":
            d["pull"].update(v)
        elif k == "check_runs":
            d["check_runs"] = {"check_runs": v}
        elif k == "main_sha":
            d["main"] = {"object": {"sha": v}}
        else:
            d[k] = v
    return d


class ClassifyTest(unittest.TestCase):
    def verdict(self, evidence, required=REQUIRED):
        return acc.classify(evidence, required)["verdict"]

    def codes(self, evidence, required=REQUIRED):
        return [r["code"] for r in acc.classify(evidence, required)["reasons"]]

    def test_pass(self):
        self.assertEqual(self.verdict(ev()), acc.PASS)

    def test_pr_not_open(self):
        self.assertEqual(self.verdict(ev(pull={"state": "closed"})), acc.EVIDENCE_INSUFFICIENT)
        self.assertIn("PR_NOT_OPEN", self.codes(ev(pull={"state": "closed"})))

    def test_head_sha_mismatch(self):
        d = ev()
        d["expected_head_sha"] = "c" * 40
        self.assertEqual(self.verdict(d), acc.SHA_MISMATCH)

    def test_baseline_drift_distinct_from_source_mismatch(self):
        d = ev(pull={"base": {"ref": "main", "sha": "d" * 40}})
        self.assertEqual(self.verdict(d), acc.BASELINE_DRIFT)
        self.assertIn("BASE_SHA_BEHIND_MAIN_TIP", self.codes(d))

    def test_required_check_missing(self):
        runs = [cr("general-pr-verify"), cr("workflow-policy-guard"), cr("route-a/verify")]
        self.assertEqual(self.verdict(ev(check_runs=runs)), acc.REQUIRED_CONTEXT_UNSATISFIED)

    def test_required_check_failed(self):
        runs = [cr("general-pr-verify", conclusion="failure"), cr("workflow-policy-guard"),
                cr("trusted-baseline-guard"), cr("route-a/verify")]
        self.assertEqual(self.verdict(ev(check_runs=runs)), acc.REQUIRED_CONTEXT_UNSATISFIED)

    def test_required_check_wrong_app(self):
        runs = [cr("general-pr-verify", app=1), cr("workflow-policy-guard"),
                cr("trusted-baseline-guard"), cr("route-a/verify")]
        self.assertEqual(self.verdict(ev(check_runs=runs)), acc.REQUIRED_CONTEXT_UNSATISFIED)

    def test_route_a_wrong_app_is_source_mismatch(self):
        runs = [cr("general-pr-verify"), cr("workflow-policy-guard"),
                cr("trusted-baseline-guard"), cr("route-a/verify", app=9999)]
        out = acc.classify(ev(check_runs=runs), REQUIRED)
        self.assertEqual(out["verdict"], acc.SOURCE_MISMATCH)
        self.assertEqual(out["facts"]["route_a_check_run_app_id"], 9999)

    def test_route_a_wrong_sha(self):
        runs = [cr("general-pr-verify"), cr("workflow-policy-guard"),
                cr("trusted-baseline-guard"), cr("route-a/verify", head="e" * 40)]
        self.assertEqual(self.verdict(ev(check_runs=runs)), acc.SHA_MISMATCH)

    def test_route_a_not_success(self):
        runs = [cr("general-pr-verify"), cr("workflow-policy-guard"),
                cr("trusted-baseline-guard"), cr("route-a/verify", conclusion="neutral")]
        self.assertEqual(self.verdict(ev(check_runs=runs)), acc.REQUIRED_CONTEXT_UNSATISFIED)

    def test_route_a_missing_entirely(self):
        runs = [cr("general-pr-verify"), cr("workflow-policy-guard"), cr("trusted-baseline-guard")]
        self.assertEqual(self.verdict(ev(check_runs=runs, statuses=[])), acc.PUBLISHER_NOT_RUN)

    def test_status_only_is_not_accepted_as_source_proof(self):
        runs = [cr("general-pr-verify"), cr("workflow-policy-guard"), cr("trusted-baseline-guard")]
        out = acc.classify(ev(check_runs=runs), REQUIRED)
        self.assertEqual(out["verdict"], acc.EVIDENCE_INSUFFICIENT)
        self.assertIn("STATUS_ONLY_NO_APP_ID", [r["code"] for r in out["reasons"]])

    def test_check_run_without_run_binding(self):
        runs = [cr("general-pr-verify"), cr("workflow-policy-guard"),
                cr("trusted-baseline-guard"),
                cr("route-a/verify", details="https://example.com/not-a-run")]
        out = acc.classify(ev(check_runs=runs), REQUIRED)
        self.assertEqual(out["verdict"], acc.EVIDENCE_INSUFFICIENT)
        self.assertIn("PUBLISHER_RUN_UNBOUND", [r["code"] for r in out["reasons"]])

    def test_status_only_accepted_when_merge_box_clean(self):
        runs = [cr("general-pr-verify"), cr("workflow-policy-guard"), cr("trusted-baseline-guard")]
        out = acc.classify(ev(check_runs=runs, merge_state_status="CLEAN"), REQUIRED)
        self.assertEqual(out["verdict"], acc.PASS)
        self.assertIn("platform_merge_box_clean", out["facts"]["app_identity_proof"])

    def test_status_only_insufficient_when_merge_box_blocked(self):
        runs = [cr("general-pr-verify"), cr("workflow-policy-guard"), cr("trusted-baseline-guard")]
        out = acc.classify(ev(check_runs=runs, merge_state_status="BLOCKED"), REQUIRED)
        self.assertEqual(out["verdict"], acc.EVIDENCE_INSUFFICIENT)

    def test_check_run_path_marks_app_id_proof(self):
        out = acc.classify(ev(), REQUIRED)
        self.assertEqual(out["facts"]["app_identity_proof"], "check_run.app.id")
        self.assertEqual(out["facts"]["evidence_level"], "object_app_id")

    def test_publisher_run_head_mismatch_is_source_mismatch(self):
        d = ev()
        d["publisher_run"] = dict(d["publisher_run"], head_sha="q" * 40)
        out = acc.classify(d, REQUIRED)
        self.assertEqual(out["verdict"], acc.SOURCE_MISMATCH)
        self.assertIn("PUBLISHER_RUN_MISMATCH", [r["code"] for r in out["reasons"]])

    def test_publisher_run_wrong_event_is_source_mismatch(self):
        d = ev()
        d["publisher_run"] = dict(d["publisher_run"], event="push")
        self.assertEqual(self.verdict(d), acc.SOURCE_MISMATCH)

    def test_publisher_run_unreadable_is_evidence_insufficient(self):
        d = ev()
        d["publisher_run"] = None
        out = acc.classify(d, REQUIRED)
        self.assertEqual(out["verdict"], acc.EVIDENCE_INSUFFICIENT)
        self.assertIn("PUBLISHER_RUN_UNREADABLE", [r["code"] for r in out["reasons"]])

    def test_in_progress_check_run_is_not_accepted(self):
        runs = [cr("general-pr-verify"), cr("workflow-policy-guard"),
                cr("trusted-baseline-guard"),
                {"name": "route-a/verify", "head_sha": HEAD, "status": "in_progress",
                 "conclusion": None, "app": {"id": 15368},
                 "details_url": "https://github.com/o/r/actions/runs/500"}]
        out = acc.classify(ev(check_runs=runs), REQUIRED)
        self.assertEqual(out["verdict"], acc.REQUIRED_CONTEXT_UNSATISFIED)
        self.assertIn("CHECK_RUN_NOT_SUCCESS", [r["code"] for r in out["reasons"]])

    def test_status_only_reports_platform_evidence_level(self):
        runs = [cr("general-pr-verify"), cr("workflow-policy-guard"), cr("trusted-baseline-guard")]
        out = acc.classify(ev(check_runs=runs, merge_state_status="CLEAN"), REQUIRED)
        self.assertEqual(out["verdict"], acc.PASS)
        self.assertEqual(out["facts"]["evidence_level"], "platform_merge_box")
        self.assertFalse(out["facts"]["object_level_source_proof"])

    def test_graphql_behind_wins_over_rest(self):
        d = ev(pull={"mergeable_state": "clean"})
        d["merge_state_status"] = "BEHIND"
        self.assertEqual(self.verdict(d), acc.BASELINE_DRIFT)

    def test_merge_state_blocked_with_rule_satisfied(self):
        out = acc.classify(ev(pull={"mergeable_state": "blocked"}), REQUIRED)
        self.assertEqual(out["verdict"], acc.EVIDENCE_INSUFFICIENT)
        self.assertIn("MERGE_STATE_BLOCKED_OTHER_RULES", [r["code"] for r in out["reasons"]])

    def test_merge_state_behind_is_drift(self):
        self.assertEqual(self.verdict(ev(pull={"mergeable_state": "behind"})), acc.BASELINE_DRIFT)

    def test_mergeable_unknown(self):
        self.assertEqual(self.verdict(ev(pull={"mergeable_state": None})),
                         acc.EVIDENCE_INSUFFICIENT)

    def test_main_tip_unreadable(self):
        self.assertEqual(self.verdict(ev(main_sha=None)), acc.EVIDENCE_INSUFFICIENT)

    def test_does_not_confuse_api_success_with_satisfied_context(self):
        # combined status 为 success 但 route-a/verify 无 check run → 不得 PASS
        runs = [cr("general-pr-verify"), cr("workflow-policy-guard"), cr("trusted-baseline-guard")]
        self.assertNotEqual(self.verdict(ev(check_runs=runs, combined={"state": "success"})),
                            acc.PASS)


class ParseRunIdTest(unittest.TestCase):
    def test_prefix_collision_rejected(self):
        self.assertEqual(acc.parse_run_id("https://github.com/o/r/actions/runs/10010"), "10010")
        self.assertNotEqual(acc.parse_run_id("https://github.com/o/r/actions/runs/10010"), "1001")

    def test_malformed(self):
        for bad in ["", None, "https://github.com/o/r/actions", "https://github.com/o/r/actions/runs/abc",
                    "https://github.com/o/r/actions/runs/0100", "https://github.com/o/r/actions/runs/0"]:
            self.assertIsNone(acc.parse_run_id(bad))

    def test_query_and_fragment_stripped(self):
        self.assertEqual(
            acc.parse_run_id("https://github.com/o/r/actions/runs/77?check_suite_focus=true#x"), "77")


class Stub(BaseHTTPRequestHandler):
    routes: dict = {}
    pages: dict | None = None        # 分页模式：{page_no: [status,...]}
    page_fail: dict = {}             # {page_no: http_code | "disconnect" | "malformed"}
    calls: list = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        key = f"GET {self.path.split('?')[0]}"
        Stub.calls.append(self.path)
        if Stub.pages is not None and key.endswith("/statuses"):
            page = 1
            for part in self.path.split("?", 1)[-1].split("&"):
                if part.startswith("page="):
                    page = int(part.split("=", 1)[1])
            fail = Stub.page_fail.get(page)
            if fail == "disconnect":
                self.close_connection = True
                return
            if fail == "malformed":
                code, payload = 200, {"not": "a list"}
            elif isinstance(fail, int):
                code, payload = fail, {"message": "injected"}
            elif callable(fail):
                code, payload = 200, fail()
            else:
                code, payload = 200, Stub.pages.get(page, [])
            raw = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        code, payload = Stub.routes.get(key, (404, {"message": "Not Found"}))
        raw = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.api = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        self.req = ROOT / ".github" / "required-checks.json"

    def test_api_error_is_its_own_verdict(self):
        Stub.routes = {}
        out = acc.run(repo="o/r", pr=31, head_sha=HEAD, token="t", api=self.api,
                      required_path=str(self.req), expect_app_id=15368)
        self.assertEqual(out["verdict"], acc.API_OR_PERMISSION_ERROR)
        self.assertTrue(out["read_only"])

    def test_end_to_end_pass_over_http(self):
        Stub.routes = {
            "GET /repos/o/r/pulls/31": (200, ev()["pull"]),
            "GET /repos/o/r/git/ref/heads/main": (200, {"object": {"sha": MAIN}}),
            f"GET /repos/o/r/commits/{HEAD}/check-runs": (200, ev()["check_runs"]),
            f"GET /repos/o/r/commits/{HEAD}/statuses": (200, ev()["statuses"]),
            f"GET /repos/o/r/commits/{HEAD}/status": (200, {"state": "success"}),
            "GET /repos/o/r/actions/runs/500": (200, ev()["publisher_run"]),
        }
        out = acc.run(repo="o/r", pr=31, head_sha=HEAD, token="t", api=self.api,
                      required_path=str(self.req), expect_app_id=15368)
        self.assertEqual(out["verdict"], acc.PASS)
        self.assertEqual(out["facts"]["publisher_run_id"], "500")


def st(i, context="other/check", state="success"):
    return {"id": i, "context": context, "state": state,
            "target_url": "https://github.com/o/r/actions/runs/500",
            "creator": {"login": "github-actions[bot]", "type": "Bot"}}


class PaginationTest(unittest.TestCase):
    """第七轮：验收工具 statuses 分页、完整性与 context 语义。"""

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.api = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        Stub.pages, Stub.page_fail = None, {}

    def setUp(self):
        Stub.pages, Stub.page_fail, Stub.calls, Stub.routes = {}, {}, [], {}
        self.client = acc.Client("t", self.api)

    def fetch(self):
        return acc.fetch_statuses(self.client, "o/r", HEAD)

    def hundred(self, base):
        return [st(base + i) for i in range(100)]

    def test_P1_target_on_page_two(self):
        Stub.pages = {1: self.hundred(1000), 2: [st(2000, "route-a/verify")]}
        out = self.fetch()
        self.assertTrue(out["complete"])
        self.assertEqual(out["latest_target"]["id"], 2000)
        self.assertEqual(out["pages_read"], 2)

    def test_P2_page_two_403_5xx_disconnect_incomplete(self):
        for fail in (403, 502, "disconnect"):
            Stub.pages, Stub.page_fail = {1: self.hundred(1000)}, {2: fail}
            out = self.fetch()
            self.assertFalse(out["complete"], fail)
            self.assertIsNone(out["latest_target"])

    def test_P3_empty_malformed_and_exact_hundred(self):
        Stub.pages = {}
        self.assertTrue(self.fetch()["complete"])
        Stub.pages, Stub.page_fail = {1: [st(1)]}, {1: "malformed"}
        self.assertFalse(self.fetch()["complete"])
        Stub.pages, Stub.page_fail = {1: self.hundred(1000)}, {}
        out = self.fetch()
        self.assertTrue(out["complete"])
        self.assertEqual(out["pages_read"], 2, "恰好 100 条必须继续读下一页")

    def test_P4_concurrent_change_during_pagination(self):
        Stub.pages = {1: self.hundred(1000), 2: [st(2000, "route-a/verify", "failure")]}
        seen = {"n": 0}

        def page1():
            seen["n"] += 1
            return self.hundred(1000) if seen["n"] == 1 else [st(3000, "route-a/verify")]
        Stub.page_fail = {1: page1}
        out = self.fetch()
        self.assertFalse(out["complete"])
        self.assertIn("并发", out["error"])

    def test_P5_ten_page_cap_incomplete(self):
        Stub.pages = {p: self.hundred(p * 1000) for p in range(1, 12)}
        out = self.fetch()
        self.assertFalse(out["complete"])
        self.assertEqual(out["pages_read"], 10)
        self.assertIn("上限", out["error"])

    def test_P6_incomplete_never_pass_or_publisher_not_run(self):
        runs = [cr("general-pr-verify"), cr("workflow-policy-guard"), cr("trusted-baseline-guard")]
        for statuses in ([], ev()["statuses"]):
            d = ev(check_runs=runs, statuses=statuses, merge_state_status="CLEAN")
            d.update(statuses_complete=False, statuses_pages_read=1, statuses_error="第 2 页 403")
            out = acc.classify(d, REQUIRED)
            self.assertEqual(out["verdict"], acc.EVIDENCE_INSUFFICIENT)
            self.assertEqual([r["code"] for r in out["reasons"]], ["STATUS_PAGINATION_INCOMPLETE"])

    def test_P7_end_to_end_incomplete_pagination_over_http(self):
        Stub.routes = {
            "GET /repos/o/r/pulls/31": (200, ev()["pull"]),
            "GET /repos/o/r/git/ref/heads/main": (200, {"object": {"sha": MAIN}}),
            f"GET /repos/o/r/commits/{HEAD}/check-runs": (200, {"check_runs": [
                cr("general-pr-verify"), cr("workflow-policy-guard"), cr("trusted-baseline-guard")]}),
            f"GET /repos/o/r/commits/{HEAD}/status": (200, {"state": "success"}),
        }
        Stub.pages, Stub.page_fail = {1: self.hundred(1000)}, {2: 403}
        out = acc.run(repo="o/r", pr=31, head_sha=HEAD, token="t", api=self.api,
                      required_path=str(ROOT / ".github" / "required-checks.json"),
                      expect_app_id=15368)
        self.assertNotIn(out["verdict"], (acc.PASS, acc.PUBLISHER_NOT_RUN))
        self.assertEqual(out["verdict"], acc.EVIDENCE_INSUFFICIENT)
        self.assertIn("STATUS_PAGINATION_INCOMPLETE", [r["code"] for r in out["reasons"]])

    def test_P8_context_case_insensitive_selection(self):
        self.assertTrue(acc.is_route_a_context("Route-A/Verify"))
        for v in ("route-a/verify-x", "route-a", " route-a/verify", None, 3):
            self.assertFalse(acc.is_route_a_context(v), v)
        items = [st(1, "route-a/verify-legacy"), st(2, "ROUTE-A/VERIFY", "failure"),
                 st(3, "route-a/verify")]
        self.assertEqual(acc._pick_status(items)["id"], 2)
        Stub.pages = {1: [st(5, "Route-A/Verify")]}
        self.assertEqual(self.fetch()["latest_target"]["id"], 5)

    def test_P9_case_variant_status_is_seen_by_classify(self):
        runs = [cr("general-pr-verify"), cr("workflow-policy-guard"), cr("trusted-baseline-guard")]
        variant = [dict(ev()["statuses"][0], context="Route-A/Verify")]
        out = acc.classify(ev(check_runs=runs, statuses=variant, merge_state_status="CLEAN"), REQUIRED)
        self.assertNotEqual(out["verdict"], acc.PUBLISHER_NOT_RUN)
        self.assertEqual(out["verdict"], acc.PASS)


if __name__ == "__main__":
    unittest.main()
