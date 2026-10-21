#!/usr/bin/env python3
"""P0-1 受信状态发布器测试（进程内 HTTP stub，模拟平台语义）。"""
from __future__ import annotations

import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

import publish_run_status as prs  # noqa: E402

HEAD = "h" * 40
REQUIRED = {"general-pr-verify", "workflow-policy-guard", "trusted-baseline-guard"}


class Stub(BaseHTTPRequestHandler):
    routes: dict = {}
    log: list = []
    stateful: bool = False
    remote_statuses: list = []          # 索引 0 = 当前有效状态（GitHub 语义：最新在前）
    remote_checks: list = []
    inject: dict = {}
    counters: dict = {}

    def log_message(self, *a):
        pass

    def _send(self, code, body):
        raw = json.dumps(body).encode() if not isinstance(body, bytes) else body
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _handle(self, method):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length)) if length else None
        Stub.log.append((method, self.path.split("?")[0], body))
        key = f"{method} {self.path.split('?')[0]}"
        if Stub.stateful:
            if key.startswith("POST /repos/o/r/statuses/"):
                Stub.counters["status_post"] = Stub.counters.get("status_post", 0) + 1
                is_comp = body.get("state") == "failure"
                if is_comp and Stub.inject.get("comp_post_disconnect_no_write"):
                    self.close_connection = True
                    return
                if is_comp and Stub.inject.get("comp_post_disconnect_after_write"):
                    Stub.counters["status_id"] = Stub.counters.get("status_id", 9000) + 1
                    Stub.remote_statuses.insert(0, {**body, "id": Stub.counters["status_id"],
                        "creator": {"login": "github-actions[bot]", "type": "Bot"}})
                    self.close_connection = True
                    return
                if is_comp and Stub.inject.get("comp_post"):
                    self._send(Stub.inject["comp_post"], {"message": "injected comp failure"}); return
                if not is_comp and Stub.inject.get("status_post"):
                    self._send(Stub.inject["status_post"], {"message": "injected status failure"}); return
                Stub.counters["status_id"] = Stub.counters.get("status_id", 9000) + 1
                status_obj = {**body, "id": Stub.counters["status_id"],
                              "creator": {"login": "github-actions[bot]", "type": "Bot"},
                              "target_url": body.get("target_url")}
                Stub.remote_statuses.insert(0, status_obj)
                if not is_comp and Stub.inject.get("status_post_timeout"):
                    # 远端已写入，客户端收到连接中断（模拟超时/半开连接）
                    self.close_connection = True
                    return
                if not is_comp and Stub.inject.get("drift_after_status_post"):
                    Stub.routes["GET /repos/o/r/pulls/29"] = (200, {
                        "state": "open", "head": {"sha": "z" * 40},
                        "base": {"ref": "main", "sha": "m" * 40}, "user": {"login": "Hawaiine"}})
                self._send(201, status_obj); return
            if key == "POST /repos/o/r/check-runs":
                if Stub.inject.get("check_post"):
                    self._send(Stub.inject["check_post"], {"message": "injected check failure"}); return
                obj = {**body, "id": len(Stub.remote_checks) + 77,
                       "app": {"id": Stub.inject.get("app_id", 15368), "slug": "github-actions"}}
                Stub.remote_checks.insert(0, obj)
                self._send(201, obj); return
            if key.startswith("PATCH /repos/o/r/check-runs/"):
                Stub.counters["check_patch"] = Stub.counters.get("check_patch", 0) + 1
                if (Stub.inject.get("check_patch_commit_then_disconnect")
                        and Stub.counters["check_patch"] == 1):
                    for obj in Stub.remote_checks:
                        if obj["id"] == int(key.rsplit("/", 1)[1]):
                            obj.update(body or {})
                    self.close_connection = True
                    return
                dca = Stub.inject.get("check_patch_disconnect_after")
                if dca is not None and Stub.counters["check_patch"] > dca:
                    for obj in Stub.remote_checks:
                        if obj["id"] == int(key.rsplit("/", 1)[1]):
                            obj.update(body or {})
                    self.close_connection = True
                    return
                if Stub.inject.get("check_patch"):
                    self._send(Stub.inject["check_patch"], {"message": "injected patch failure"}); return
                fa = Stub.inject.get("check_patch_fail_after")
                if fa is not None and Stub.counters["check_patch"] > fa:
                    self._send(403, {"message": "injected patch failure"}); return
                cid = int(key.rsplit("/", 1)[1])
                for obj in Stub.remote_checks:
                    if obj["id"] == cid:
                        obj.update(body or {})
                        if Stub.inject.get("drift_after_patch"):
                            Stub.routes["GET /repos/o/r/pulls/29"] = (200, {
                                "state": "open", "head": {"sha": "z" * 40},
                                "base": {"ref": "main", "sha": "m" * 40},
                                "user": {"login": "Hawaiine"}})
                        self._send(200, obj); return
                self._send(404, {"message": "not found"}); return
            if key.startswith("GET /repos/o/r/commits/") and key.endswith("/statuses"):
                Stub.counters["status_get"] = Stub.counters.get("status_get", 0) + 1
                if Stub.inject.get("status_get"):
                    self._send(Stub.inject["status_get"], {"message": "injected status read failure"}); return
                srd = Stub.inject.get("status_read_disconnect_after")
                if srd is not None and Stub.counters["status_get"] > srd:
                    self.close_connection = True
                    return
                after = Stub.inject.get("status_read_fail_after")
                if after is not None and Stub.counters["status_get"] > after:
                    mode = Stub.inject.get("status_read_fail_mode", "hide")
                    if isinstance(mode, int):
                        self._send(mode, {"message": "injected readback failure"}); return
                    self._send(200, []); return
                if Stub.inject.get("status_read") == "hide":
                    self._send(200, []); return
                if Stub.inject.get("status_read") == "stale":
                    self._send(200, [{"id": 1, "context": "route-a/verify", "state": "failure",
                                      "target_url": "https://github.com/o/r/actions/runs/1",
                                      "creator": {"login": "github-actions[bot]", "type": "Bot"}}])
                    return
                if Stub.inject.get("status_read") == "other_context_first":
                    other = {"id": 9002, "context": "another/check", "state": "success",
                             "target_url": "https://example.invalid/other",
                             "creator": {"login": "github-actions[bot]", "type": "Bot"}}
                    self._send(200, [other] + Stub.remote_statuses); return
                if Stub.inject.get("status_read") == "same_context_failure_wrong_id":
                    wrong = {"id": 12345, "context": "route-a/verify", "state": "failure",
                             "target_url": "https://github.com/o/r/actions/runs/9",
                             "creator": {"login": "github-actions[bot]", "type": "Bot"}}
                    self._send(200, [wrong] + Stub.remote_statuses); return
                if Stub.inject.get("status_read") == "newer_same_context_success":
                    newer = {"id": 9100, "context": "route-a/verify", "state": "success",
                             "target_url": "https://github.com/o/r/actions/runs/9",
                             "creator": {"login": "github-actions[bot]", "type": "Bot"}}
                    self._send(200, [newer] + Stub.remote_statuses); return
                self._send(200, Stub.remote_statuses); return
            if key.startswith("GET /repos/o/r/commits/") and key.endswith("/check-runs"):
                Stub.counters["check_get"] = Stub.counters.get("check_get", 0) + 1
                cgda = Stub.inject.get("check_get_disconnect_after")
                if cgda is not None and Stub.counters["check_get"] > cgda:
                    self.close_connection = True
                    return
                if Stub.inject.get("check_get_disconnect"):
                    self.close_connection = True
                    return
                if Stub.inject.get("check_get"):
                    self._send(Stub.inject["check_get"], {"message": "injected check read failure"}); return
                # optionally return a wrong app/external ID as API readback corruption
                objs = [dict(o, app={"id": Stub.inject.get("read_app_id", o["app"]["id"]), "slug": "github-actions"})
                        for o in Stub.remote_checks]
                if Stub.inject.get("read_external_id"):
                    objs = [dict(o, external_id=Stub.inject["read_external_id"]) for o in objs]
                self._send(200, {"check_runs": objs}); return
        for pattern, (code, payload) in Stub.routes.items():
            if pattern == key or (pattern.endswith("*") and key.startswith(pattern[:-1])):
                self._send(code, payload)
                return
        self._send(404, {"message": "Not Found"})

    def do_GET(self): self._handle("GET")
    def do_POST(self): self._handle("POST")
    def do_PATCH(self): self._handle("PATCH")


class Case(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.api = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        Stub.routes = {}
        Stub.log = []
        Stub.stateful = False
        Stub.remote_statuses = []
        Stub.remote_checks = []
        Stub.inject = {}
        Stub.counters = {}
        self.client = prs.Client("o/r", "tok", api=self.api)

    def run_doc(self, **over):
        doc = {"repository": {"full_name": "o/r"}, "head_repository": {"full_name": "o/r"},
               "path": ".github/workflows/pr-verify.yml", "pull_requests": [], "event": "pull_request",
               "name": "General PR verification", "head_sha": HEAD,
               "status": "completed", "conclusion": "success"}
        doc.update(over)
        return doc

    def jobs_doc(self, bad=None, drop=None):
        names = sorted(REQUIRED - {drop} if drop else REQUIRED)
        jobs = [{"name": n, "conclusion": (bad or {}).get(n, "success")} for n in names]
        return {"jobs": jobs}

    def pr_doc(self, head=HEAD, state="open", base_sha="m" * 40):
        return {"state": state, "head": {"sha": head}, "base": {"ref": "main", "sha": base_sha},
                "user": {"login": "Hawaiine"}}

    def base_routes(self, **over):
        routes = {
            f"GET /repos/o/r/actions/runs/9": (200, self.run_doc(**over.get("run", {}))),
            "GET /repos/o/r/actions/runs/9/jobs": (200, self.jobs_doc()),
            "GET /repos/o/r/pulls/29": (200, self.pr_doc()),
            "GET /repos/o/r/git/ref/heads/main": (200, {"object": {"sha": "m" * 40}}),
            f"POST /repos/o/r/statuses/{HEAD}": (201, {"state": "success"}),
            f"GET /repos/o/r/commits/{HEAD}/statuses": (200, [
                {"context": "route-a/verify", "state": "success",
                 "target_url": "https://github.com/o/r/actions/runs/9",
                 "creator": {"login": "github-actions[bot]", "type": "Bot"}}]),
        }
        routes.update(over.get("routes", {}))
        Stub.routes = routes

    def publish(self, attempts: int = 1, mode: str = "status"):
        return prs.publish_for_run(repo="o/r", run_id="9", pr=29, head_sha=HEAD, required=REQUIRED,
                                   token="tok", api=self.api, attempts=attempts, publish_mode=mode,
                                   target_url="https://github.com/o/r/actions/runs/9")

    def flow(self, **inject):
        """有状态远端：POST/PATCH 会真实改变 mock 对象，用于验证"失败后远端残留什么"。"""
        self.base_routes()
        Stub.routes = {k: v for k, v in Stub.routes.items()
                       if "/statuses" not in k and "/check-runs" not in k}
        Stub.stateful = True
        Stub.inject = dict(inject)

    def remote_success_status(self) -> bool:
        return any(x.get("context") == "route-a/verify" and x.get("state") == "success"
                   for x in Stub.remote_statuses)

    def remote_check(self, run_id: str = "9"):
        for c in Stub.remote_checks:
            if c.get("external_id") == f"route-a-run:{run_id}":
                return c
        return None

    def seed_foreign_success(self, run_id: str = "8", app_id: int = 15368):
        Stub.remote_checks.insert(0, {"id": 42, "name": "route-a/verify", "head_sha": HEAD,
                                      "status": "completed", "conclusion": "success",
                                      "external_id": f"route-a-run:{run_id}",
                                      "details_url": f"https://github.com/o/r/actions/runs/{run_id}",
                                      "app": {"id": app_id, "slug": "github-actions"}})

    # ── 发布事务：成功证据必须最后写 ──

    def test_dual_publishes_check_run_before_status(self):
        self.flow()
        out = self.publish(mode="dual")
        order = [f"{m} {p}" for m, p, _ in Stub.log if m in ("POST", "PATCH")]
        self.assertLess(order.index("POST /repos/o/r/check-runs"),
                        order.index("PATCH /repos/o/r/check-runs/77"))
        self.assertLess(order.index("PATCH /repos/o/r/check-runs/77"),
                        order.index(f"POST /repos/o/r/statuses/{HEAD}"))
        patch = [b for m, p, b in Stub.log if m == "PATCH"][0]
        self.assertEqual(patch["conclusion"], "success")
        self.assertEqual(self.remote_check()["conclusion"], "success")
        self.assertTrue(self.remote_success_status())
        self.assertEqual(out["check_run"]["verify"]["app_id"], 15368)

    def test_check_run_only_skips_status(self):
        self.flow()
        out = self.publish(mode="check-run")
        self.assertIn("check_run", out)
        self.assertNotIn("status", out)
        self.assertFalse(self.remote_success_status())

    def test_creation_returns_wrong_app_id_refused_without_success(self):
        self.flow(app_id=9999)
        with self.assertRaises(prs.Refused):
            self.publish(mode="dual")
        self.assertNotEqual((self.remote_check() or {}).get("conclusion"), "success")
        self.assertFalse(self.remote_success_status())

    def test_check_run_create_403_publishes_no_status(self):
        """缺陷复现（原为 RED）：status 先发、check run 后失败 → 远端不得留 success。"""
        self.flow(check_post=403)
        with self.assertRaises(prs.Refused):
            self.publish(mode="dual")
        self.assertFalse(self.remote_success_status())

    def test_readback_app_id_mismatch_downgrades_and_publishes_no_status(self):
        self.flow(read_app_id=9999)
        with self.assertRaises(prs.Refused):
            self.publish(mode="dual")
        self.assertNotEqual(self.remote_check()["conclusion"], "success")
        self.assertFalse(self.remote_success_status())
        self.assertIn("PATCH /repos/o/r/check-runs/77", [f"{m} {p}" for m, p, _ in Stub.log])

    def test_readback_external_id_mismatch_downgrades(self):
        self.flow(read_external_id="route-a-run:10")
        with self.assertRaises(prs.Refused):
            self.publish(mode="dual")
        self.assertFalse(self.remote_success_status())

    def test_readback_403_downgrades_and_publishes_no_status(self):
        self.flow(check_get=403)
        with self.assertRaises(prs.Refused):
            self.publish(attempts=1, mode="dual")
        self.assertFalse(self.remote_success_status())

    def test_readback_500_downgrades_and_publishes_no_status(self):
        self.flow(check_get=500)
        with self.assertRaises(prs.Refused):
            self.publish(attempts=1, mode="dual")
        self.assertFalse(self.remote_success_status())

    def test_invisible_readback_records_compensation(self):
        self.flow(check_get=404)
        with self.assertRaises(prs.Refused):
            self.publish(attempts=1, mode="dual")
        self.assertFalse(self.remote_success_status())

    def test_post_finalize_drift_downgrades_and_publishes_no_status(self):
        self.flow(drift_after_patch=True)
        with self.assertRaises(prs.Refused):
            self.publish(mode="dual")
        self.assertFalse(self.remote_success_status())

    def test_compensation_failure_is_reported_not_swallowed(self):
        """补偿写入本身失败时，必须如实暴露（不假定撤销成功）。"""
        self.flow(read_app_id=9999, check_patch=403)
        with self.assertRaises(prs.Refused) as ctx:
            self.publish(mode="dual")
        report = getattr(ctx.exception, "report", {})
        self.assertFalse(report.get("compensation", {}).get("ok", True),
                         "补偿失败必须如实报告，不得假定已撤销")
        self.assertFalse(self.remote_success_status())

    def test_compensation_success_is_reported(self):
        self.flow(read_app_id=9999)
        with self.assertRaises(prs.Refused) as ctx:
            self.publish(mode="dual")
        report = getattr(ctx.exception, "report", {})
        self.assertTrue(report.get("compensation", {}).get("ok"), "已撤销应报告 ok=True")
        self.assertEqual(report["compensation"]["conclusion"], "failure")

    def test_rerun_same_run_binds_each_check_run(self):
        self.flow()
        first = self.publish(mode="dual")
        second = self.publish(mode="dual")
        self.assertEqual(first["check_run"]["external_id"], "route-a-run:9")
        self.assertEqual(second["check_run"]["external_id"], "route-a-run:9")
        self.assertEqual(len(Stub.remote_checks), 2)
        self.assertTrue(self.remote_success_status())

    def test_other_run_success_is_not_adopted(self):
        self.flow()
        self.seed_foreign_success(run_id="8")
        out = self.publish(mode="dual")
        self.assertEqual(out["check_run"]["external_id"], "route-a-run:9")
        self.assertNotEqual(out["check_run"]["id"], 42)

    def test_other_run_success_preserved_when_this_run_fails(self):
        """旧 run 的既有成功证据不得被本次失败删除；本次也不得发布成功。"""
        self.flow(check_post=403)
        self.seed_foreign_success(run_id="8")
        with self.assertRaises(prs.Refused):
            self.publish(mode="dual")
        self.assertEqual(self.remote_check("8")["conclusion"], "success")
        self.assertIsNone(self.remote_check("9"))
        self.assertFalse(self.remote_success_status())

    # ── success status 发布后的失败补偿（本轮新增） ──

    def _publish_expect_refused(self, **inject):
        self.flow(**inject)
        with self.assertRaises(prs.Refused) as ctx:
            self.publish(mode="dual")
        return getattr(ctx.exception, "report", {})

    def test_status_readback_403_after_success_marks_possible_remains(self):
        rep = self._publish_expect_refused(status_read_fail_after=0, status_read_fail_mode=403)
        self.assertEqual(Stub.remote_statuses[0]["state"], "failure")   # 尽力撤销已写入
        self.assertTrue(rep["possible_success_evidence_remains"], "回读失败不得算补偿成功")
        self.assertFalse(rep["status_compensation"]["ok"])
        self.assertIn("需要人工只读核验", rep["operator_action"])

    def test_status_readback_500_after_success_marks_possible_remains(self):
        rep = self._publish_expect_refused(status_read_fail_after=0, status_read_fail_mode=500)
        self.assertTrue(rep["possible_success_evidence_remains"])
        self.assertIn("HTTP 500", rep["status_compensation"]["error"])

    def test_status_invisible_after_success_marks_possible_remains(self):
        rep = self._publish_expect_refused(status_read="hide")
        self.assertTrue(rep["possible_success_evidence_remains"])
        self.assertIn("没有找到 route-a/verify", rep["status_compensation"]["error"])

    def test_final_verify_pr_failure_triggers_full_compensation(self):
        rep = self._publish_expect_refused(drift_after_status_post=True)
        self.assertEqual(Stub.remote_statuses[0]["state"], "failure")
        self.assertEqual(Stub.remote_checks[0]["conclusion"], "failure")
        self.assertFalse(rep["possible_success_evidence_remains"])
        self.assertTrue(rep["status_compensation"]["ok"])
        self.assertTrue(rep["compensation"]["ok"])
        self.assertEqual(rep["status_compensation"]["http"], 201)
        self.assertEqual(rep["status_compensation"]["readback"]["state"], "failure")

    def test_status_post_timeout_assumes_write_happened_and_compensates(self):
        rep = self._publish_expect_refused(status_post_timeout=True)
        self.assertTrue(rep["status_publish_attempted"])
        self.assertEqual(Stub.remote_statuses[0]["state"], "failure")
        self.assertFalse(rep["possible_success_evidence_remains"])

    def test_compensation_write_failure_marks_remains(self):
        rep = self._publish_expect_refused(drift_after_status_post=True, comp_post=403)
        self.assertEqual(Stub.remote_statuses[0]["state"], "success")   # 未能撤销
        self.assertTrue(rep["possible_success_evidence_remains"])
        self.assertIn("HTTP 403", rep["status_compensation"]["error"])

    def test_compensation_write_ok_but_readback_fails_marks_remains(self):
        rep = self._publish_expect_refused(drift_after_status_post=True, status_read_fail_after=1,
                                           status_read_fail_mode=500)
        self.assertEqual(Stub.remote_statuses[0]["state"], "failure")
        self.assertFalse(rep["status_compensation"]["ok"])
        self.assertTrue(rep["possible_success_evidence_remains"])

    def test_status_compensation_ok_but_check_run_compensation_fails(self):
        rep = self._publish_expect_refused(drift_after_status_post=True, check_patch_fail_after=1)
        self.assertTrue(rep["status_compensation"]["ok"])
        self.assertFalse(rep["compensation"]["ok"])
        self.assertEqual(Stub.remote_checks[0]["conclusion"], "success")
        self.assertTrue(rep["possible_success_evidence_remains"])

    def test_both_compensations_succeed(self):
        rep = self._publish_expect_refused(drift_after_status_post=True)
        self.assertTrue(rep["compensations"]["status"]["ok"])
        self.assertTrue(rep["compensations"]["check_run"]["ok"])
        self.assertFalse(rep["possible_success_evidence_remains"])
        self.assertTrue(rep["compensation_is_best_effort_not_atomic"])

    def test_stale_foreign_failure_is_not_counted_as_compensation(self):
        """补偿回读只认"当前有效状态"，不得拿历史 failure 冒充撤销成功。"""
        rep = self._publish_expect_refused(status_read="stale")
        self.assertFalse(rep["status_compensation"]["ok"])
        self.assertTrue(rep["possible_success_evidence_remains"])
        self.assertEqual(Stub.remote_statuses[0]["state"], "failure")

    # ── 缺陷 A：传输异常不得绕过补偿 ──

    def test_patch_success_commit_then_disconnect_triggers_compensation(self):
        """远端已写入 completed/success 后客户端断连 → 仍必须进入补偿。"""
        rep = self._publish_expect_refused(check_patch_commit_then_disconnect=True)
        self.assertFalse(rep["ok"])
        self.assertIn("failure", rep)
        self.assertEqual(self.remote_check()["conclusion"], "failure")
        self.assertTrue(rep["compensation"]["ok"])
        self.assertFalse(rep["possible_success_evidence_remains"])
        self.assertTrue(rep["compensation_log"])

    def test_check_run_readback_disconnect_after_remote_success_compensates(self):
        rep = self._publish_expect_refused(check_get_disconnect_after=1)
        self.assertEqual(self.remote_check()["conclusion"], "failure")
        self.assertFalse(rep["possible_success_evidence_remains"])
        self.assertEqual(rep["failure"]["type"], "Refused")

    def test_status_publish_disconnect_is_treated_as_maybe_written(self):
        rep = self._publish_expect_refused(status_post_timeout=True)
        self.assertTrue(rep["status_publish_attempted"])
        self.assertEqual(Stub.remote_statuses[0]["state"], "failure")
        self.assertFalse(rep["possible_success_evidence_remains"])

    def test_compensation_post_disconnect_without_write_marks_remains(self):
        rep = self._publish_expect_refused(drift_after_status_post=True,
                                           comp_post_disconnect_no_write=True)
        self.assertEqual(Stub.remote_statuses[0]["state"], "success")   # 补偿未写入
        self.assertFalse(rep["status_compensation"]["ok"])
        self.assertTrue(rep["possible_success_evidence_remains"])
        self.assertIn("人工只读核验", rep["operator_action"])

    def test_compensation_post_disconnect_after_write_is_unconfirmed(self):
        rep = self._publish_expect_refused(drift_after_status_post=True,
                                           comp_post_disconnect_after_write=True)
        self.assertEqual(Stub.remote_statuses[0]["state"], "failure")
        self.assertFalse(rep["status_compensation"]["ok"], "断连未确认不得算补偿成功")
        self.assertTrue(rep["possible_success_evidence_remains"])

    def test_check_run_compensation_disconnect_does_not_block_status_compensation(self):
        """Check Run 补偿断连不能阻止 status 补偿；两路结果都要记录。"""
        rep = self._publish_expect_refused(drift_after_status_post=True,
                                           check_patch_disconnect_after=1)
        self.assertTrue(rep["status_compensation"]["ok"])
        self.assertFalse(rep["compensation"]["ok"])
        self.assertTrue(rep["possible_success_evidence_remains"])
        self.assertEqual({e["channel"] for e in rep["compensation_log"]},
                         {"check_run", "status"})

    def test_status_compensation_readback_disconnect_is_unconfirmed(self):
        rep = self._publish_expect_refused(drift_after_status_post=True,
                                           status_read_disconnect_after=1)
        self.assertFalse(rep["status_compensation"]["ok"])
        self.assertTrue(rep["possible_success_evidence_remains"])

    # ── 缺陷 B：回读必须按 context 过滤并绑定本次 POST 对象 ──

    def test_other_context_newer_does_not_break_compensation(self):
        """其它 context 更靠前时，补偿仍应被正确确认为成功。"""
        rep = self._publish_expect_refused(drift_after_status_post=True,
                                           status_read="other_context_first")
        self.assertTrue(rep["status_compensation"]["ok"], rep["status_compensation"])
        self.assertFalse(rep["possible_success_evidence_remains"])
        self.assertEqual(rep["status_compensation"]["readback"]["context"], "route-a/verify")

    def test_newer_same_context_success_is_not_confirmed(self):
        rep = self._publish_expect_refused(drift_after_status_post=True,
                                           status_read="newer_same_context_success")
        self.assertFalse(rep["status_compensation"]["ok"])
        self.assertIn("非 failure", rep["status_compensation"]["error"])
        self.assertTrue(rep["possible_success_evidence_remains"])

    def test_same_context_failure_with_wrong_id_is_not_accepted(self):
        """同 context、同为 failure、run 绑定正确，但 id 不是本次补偿对象 → 不得确认。"""
        rep = self._publish_expect_refused(drift_after_status_post=True,
                                           status_read="same_context_failure_wrong_id")
        self.assertFalse(rep["status_compensation"]["ok"])
        self.assertIn("并非本次补偿 POST 对象", rep["status_compensation"]["error"])
        self.assertTrue(rep["possible_success_evidence_remains"])

    def test_historical_failure_id_mismatch_not_accepted(self):
        rep = self._publish_expect_refused(status_read="stale")
        self.assertFalse(rep["status_compensation"]["ok"])
        self.assertTrue(rep["possible_success_evidence_remains"])

    def test_unknown_publish_mode_refused(self):
        self.base_routes()
        with self.assertRaises(prs.Refused):
            self.publish(mode="bogus")

    def test_happy_path(self):
        self.base_routes()
        out = self.publish()
        self.assertTrue(out["ok"])
        self.assertEqual(out["verify"]["creator"], "github-actions[bot]")
        post = [r for r in Stub.log if r[0] == "POST"][0]
        self.assertEqual(post[2]["state"], "success")
        self.assertEqual(post[2]["context"], "route-a/verify")

    def test_wrong_event_refused(self):
        self.base_routes(run={"event": "push"})
        with self.assertRaises(prs.Refused):
            self.publish()

    def test_workflow_path_spoof_refused(self):
        self.base_routes(run={"path": ".github/workflows/evil.yml"})
        with self.assertRaises(prs.Refused):
            self.publish()

    def test_fork_head_repository_refused(self):
        self.base_routes(run={"head_repository": {"full_name": "evil/mihomo-rules"}})
        with self.assertRaises(prs.Refused):
            self.publish()

    def test_run_linked_to_other_pr_refused(self):
        self.base_routes(run={"pull_requests": [{"number": 7}]})
        with self.assertRaises(prs.Refused):
            self.publish()

    def test_non_success_run_refused(self):
        self.base_routes(run={"conclusion": "failure"})
        with self.assertRaises(prs.Refused):
            self.publish()

    def test_head_mismatch_refused(self):
        self.base_routes(run={"head_sha": "x" * 40})
        with self.assertRaises(prs.Refused):
            self.publish()

    def test_missing_job_refused(self):
        self.base_routes(routes={"GET /repos/o/r/actions/runs/9/jobs": (200, self.jobs_doc(drop="general-pr-verify"))})
        with self.assertRaises(prs.Refused):
            self.publish()

    def test_skipped_job_refused(self):
        self.base_routes(routes={"GET /repos/o/r/actions/runs/9/jobs": (200, self.jobs_doc(bad={"trusted-baseline-guard": "skipped"}))})
        with self.assertRaises(prs.Refused):
            self.publish()

    def test_pr_head_drift_refused(self):
        self.base_routes(routes={"GET /repos/o/r/pulls/29": (200, self.pr_doc(head="y" * 40))})
        with self.assertRaises(prs.Refused):
            self.publish()

    def test_base_drift_refused(self):
        self.base_routes(routes={"GET /repos/o/r/git/ref/heads/main": (200, {"object": {"sha": "n" * 40}})})
        with self.assertRaises(prs.Refused):
            self.publish()

    def test_post_403_refused(self):
        self.base_routes(routes={f"POST /repos/o/r/statuses/{HEAD}": (403, {"message": "forbidden"})})
        with self.assertRaises(prs.Refused):
            self.publish()

    def test_head_changes_after_publish_refused(self):
        self.base_routes()
        prs.verify_run(self.client, "9", HEAD, REQUIRED)
        prs.verify_pr(self.client, 29, HEAD)
        prs.publish(self.client, HEAD, "9", "ok", "")
        prs.verify_published(self.client, HEAD, attempts=1)
        # 发布后 PR head 被更新 → 复核必须失败
        Stub.routes["GET /repos/o/r/pulls/29"] = (200, self.pr_doc(head="z" * 40))
        with self.assertRaises(prs.Refused):
            prs.verify_pr(self.client, 29, HEAD)

    def test_creator_type_user_is_rejected(self):
        """login 对得上但 type=User → 必须拒绝（防同名冒充）。"""
        self.base_routes(routes={f"GET /repos/o/r/commits/{HEAD}/statuses": (200, [
            {"context": "route-a/verify", "state": "success",
             "target_url": "https://github.com/o/r/actions/runs/9",
             "creator": {"login": "github-actions[bot]", "type": "User"}}])})
        with self.assertRaises(prs.Refused):
            self.publish()

    def test_missing_creator_type_is_rejected(self):
        self.base_routes(routes={f"GET /repos/o/r/commits/{HEAD}/statuses": (200, [
            {"context": "route-a/verify", "state": "success",
             "target_url": "https://github.com/o/r/actions/runs/9",
             "creator": {"login": "github-actions[bot]"}}])})
        with self.assertRaises(prs.Refused):
            self.publish()

    def test_status_from_other_run_is_rejected(self):
        """同一 context 但 target_url 指向别的 run → 不能当成本次验证成功。"""
        self.base_routes(routes={f"GET /repos/o/r/commits/{HEAD}/statuses": (200, [
            {"context": "route-a/verify", "state": "success",
             "target_url": "https://github.com/o/r/actions/runs/1",
             "creator": {"login": "github-actions[bot]", "type": "Bot"}}])})
        with self.assertRaises(prs.Refused):
            self.publish()

    def test_duplicate_status_creator_rejected(self):
        self.base_routes(routes={f"GET /repos/o/r/commits/{HEAD}/statuses": (200, [
            {"context": "route-a/verify", "state": "success", "creator": {"login": "attacker"}}])})
        with self.assertRaises(prs.Refused):
            self.publish()

    def test_resolve_pr_requires_single_open(self):
        Stub.routes = {"GET /repos/o/r/commits/" + HEAD + "/pulls": (200, [
            {"number": 1, "state": "open", "base": {"ref": "main"}},
            {"number": 2, "state": "open", "base": {"ref": "main"}}])}
        with self.assertRaises(prs.Refused):
            prs.resolve_pr(self.client, HEAD)

    def test_missing_token_refused(self):
        with self.assertRaises(prs.Refused):
            prs.Client("o/r", "")


if __name__ == "__main__":
    unittest.main()
