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
        for pattern, (code, payload) in Stub.routes.items():
            if pattern == key or (pattern.endswith("*") and key.startswith(pattern[:-1])):
                self._send(code, payload)
                return
        self._send(404, {"message": "Not Found"})

    def do_GET(self): self._handle("GET")
    def do_POST(self): self._handle("POST")


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
                                   token="tok", api=self.api, attempts=attempts, publish_mode=mode)

    def check_routes(self, app_id: int = 15368, external_id: str = "route-a-run:9",
                     conclusion: str = "success", present: bool = True):
        cr = {"id": 77, "name": "route-a/verify", "head_sha": HEAD, "status": "completed",
              "conclusion": conclusion, "external_id": external_id,
              "details_url": "https://github.com/o/r/actions/runs/9",
              "app": {"id": app_id, "slug": "github-actions"}}
        return {
            "POST /repos/o/r/check-runs": (201, dict(cr)),
            f"GET /repos/o/r/commits/{HEAD}/check-runs":
                (200, {"check_runs": [cr] if present else []}),
        }

    def full_routes(self, **cr):
        self.base_routes()
        Stub.routes.update(self.check_routes(**cr))

    # ── Check Run（可证明 app.id）──

    def test_dual_publishes_status_and_check_run(self):
        self.full_routes()
        out = self.publish(mode="dual")
        self.assertEqual(out["check_run"]["verify"]["app_id"], 15368)
        self.assertEqual(out["check_run"]["external_id"], "route-a-run:9")
        posts = [path for method, path, _ in Stub.log if method == "POST"]
        self.assertIn("/repos/o/r/check-runs", posts)
        self.assertIn(f"/repos/o/r/statuses/{HEAD}", posts)
        body = [b for m, p, b in Stub.log if m == "POST" and p == "/repos/o/r/check-runs"][0]
        self.assertEqual(body["name"], "route-a/verify")
        self.assertEqual(body["head_sha"], HEAD)
        self.assertEqual(body["status"], "completed")
        self.assertEqual(body["conclusion"], "success")

    def test_check_run_only_skips_status(self):
        self.full_routes()
        out = self.publish(mode="check-run")
        self.assertIn("check_run", out)
        self.assertNotIn("status", out)
        self.assertNotIn(f"/repos/o/r/statuses/{HEAD}",
                         [p for m, p, _ in Stub.log if m == "POST"])

    def test_check_run_wrong_app_id_refused(self):
        self.full_routes(app_id=9999)
        with self.assertRaises(prs.Refused) as ctx:
            self.publish(mode="check-run")
        self.assertIn("9999", str(ctx.exception))

    def test_check_run_missing_refused(self):
        self.full_routes(present=False)
        with self.assertRaises(prs.Refused):
            self.publish(attempts=1, mode="check-run")

    def test_check_run_unbound_external_id_refused(self):
        self.full_routes(external_id="route-a-run:10")
        with self.assertRaises(prs.Refused) as ctx:
            self.publish(mode="check-run")
        self.assertIn("external_id", str(ctx.exception))

    def test_check_run_failed_conclusion_refused(self):
        self.full_routes(conclusion="failure")
        with self.assertRaises(prs.Refused):
            self.publish(mode="check-run")

    def test_check_run_creation_http_error_refused(self):
        self.base_routes()
        Stub.routes["POST /repos/o/r/check-runs"] = (403, {"message": "Resource not accessible"})
        with self.assertRaises(prs.Refused) as ctx:
            self.publish(mode="check-run")
        self.assertIn("403", str(ctx.exception))

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
