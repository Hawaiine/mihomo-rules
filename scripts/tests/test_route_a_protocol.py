#!/usr/bin/env python3
"""协议层测试：GitHubRefStore 的 CAS/权限/缺失语义；publish_status 的 head 绑定。

用进程内 HTTP stub 模拟 GitHub REST（**模拟**，不是真实平台演练）。
"""
from __future__ import annotations

import base64
import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

import gate_state as gs  # noqa: E402
import publish_status as ps  # noqa: E402


class Stub(BaseHTTPRequestHandler):
    routes: dict = {}
    log: list = []

    def log_message(self, *a):  # noqa: D401
        pass

    def _send(self, code: int, body: object) -> None:
        raw = json.dumps(body).encode() if not isinstance(body, bytes) else body
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _handle(self, method: str) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length)) if length else None
        Stub.log.append((method, self.path, body, self.headers.get("Authorization")))
        key = f"{method} {self.path.split('?')[0]}"
        for pattern, (code, payload) in Stub.routes.items():
            if pattern == key or (pattern.endswith("*") and key.startswith(pattern[:-1])):
                self._send(code, payload)
                return
        self._send(404, {"message": "Not Found"})

    def do_GET(self): self._handle("GET")
    def do_POST(self): self._handle("POST")
    def do_PATCH(self): self._handle("PATCH")


class ServerCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.api = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()

    def setUp(self) -> None:
        Stub.routes = {}
        Stub.log = []


def state_doc(gen: int = 1) -> dict:
    import hashlib
    doc = {"schema_version": 1, "generation": gen, "epoch": gen - 1, "run_id": "run-x",
           "base_sha": "b" * 40, "candidate_sha": "c" * 40,
           "target_ref": "refs/heads/candidate/rules-update", "phase": "candidate",
           "check_status": "pending", "conclusion": "none", "lock_owner": "", "lock_token": "",
           "lease_until": 0, "snapshot_digest": "", "updated_at": 0}
    doc["snapshot_digest"] = "sha256:" + hashlib.sha256(
        json.dumps({k: v for k, v in doc.items() if k not in ("snapshot_digest", "updated_at")},
                   sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    return doc


class GitHubRefStoreTests(ServerCase):
    def store(self) -> gs.GitHubRefStore:
        return gs.GitHubRefStore("o/r", "tok", api=self.api)

    def test_missing_ref_returns_none(self):
        Stub.routes["GET /repos/o/r/git/ref/heads/state/gate"] = (404, {"message": "Not Found"})
        state, sha = self.store().read()
        self.assertIsNone(state)
        self.assertEqual(sha, "")

    def test_create_ref_success(self):
        Stub.routes.update({
            "GET /repos/o/r/git/ref/heads/state/gate": (404, {}),
            "GET /repos/o/r/git/ref/heads/main": (200, {"object": {"sha": "m" * 40}}),
            "GET /repos/o/r/git/commits/*": (200, {"tree": {"sha": "t" * 40}}),
            "POST /repos/o/r/git/blobs": (201, {"sha": "blob1"}),
            "POST /repos/o/r/git/trees": (201, {"sha": "tree1"}),
            "POST /repos/o/r/git/commits": (201, {"sha": "commit1"}),
            "POST /repos/o/r/git/refs": (201, {"ref": "refs/heads/state/gate"}),
        })
        result = self.store().cas_write(gs.GateState.from_doc(state_doc()), expected_old_sha=None)
        self.assertTrue(result.ok, result.reason)
        self.assertEqual(result.ref_sha, "commit1")
        self.assertEqual(Stub.log[-1][2]["ref"], "refs/heads/state/gate")

    def test_cas_conflict_422_raises(self):
        Stub.routes.update({
            "GET /repos/o/r/git/ref/heads/state/gate": (200, {"object": {"sha": "old"}}),
            "GET /repos/o/r/git/commits/*": (200, {"tree": {"sha": "t" * 40}}),
            "POST /repos/o/r/git/blobs": (201, {"sha": "b"}),
            "POST /repos/o/r/git/trees": (201, {"sha": "t"}),
            "POST /repos/o/r/git/commits": (201, {"sha": "c"}),
            "PATCH /repos/o/r/git/refs/heads/state/gate": (422, {"message": "Reference update failed"}),
        })
        with self.assertRaises(gs.CasConflict):
            self.store().cas_write(gs.GateState.from_doc(state_doc(2)), expected_old_sha="old")
        patch = [r for r in Stub.log if r[0] == "PATCH"][0]
        self.assertIs(patch[2]["force"], False, "CAS 必须 force=false")

    def test_stale_expected_sha_rejected_before_write(self):
        Stub.routes["GET /repos/o/r/git/ref/heads/state/gate"] = (200, {"object": {"sha": "new"}})
        result = self.store().cas_write(gs.GateState.from_doc(state_doc(2)), expected_old_sha="old")
        self.assertFalse(result.ok)
        self.assertIn("stale", result.reason)
        self.assertEqual([r for r in Stub.log if r[0] == "POST"], [], "冲突时不得产生任何写入")

    def test_permission_denied_fails_closed(self):
        Stub.routes["GET /repos/o/r/git/ref/heads/state/gate"] = (403, {"message": "Resource not accessible"})
        with self.assertRaises(gs.StateError):
            self.store().read()

    def test_missing_token_refused(self):
        with self.assertRaises(gs.StateError):
            gs.GitHubRefStore("o/r", "")

    def test_read_decodes_content(self):
        payload = base64.b64encode(json.dumps(state_doc()).encode()).decode()
        Stub.routes.update({
            "GET /repos/o/r/git/ref/heads/state/gate": (200, {"object": {"sha": "s1"}}),
            "GET /repos/o/r/contents/state.json": (200, {"content": payload}),
        })
        state, sha = self.store().read()
        self.assertEqual(sha, "s1")
        self.assertEqual(state.generation, 1)


class PublishStatusTests(ServerCase):
    def client(self) -> ps.Client:
        return ps.Client("o/r", "tok", api=self.api)

    def _pr(self, head: str = "h" * 40, base_sha: str = "m" * 40, state: str = "open") -> dict:
        return {"state": state, "base": {"ref": "main", "sha": base_sha}, "head": {"sha": head}}

    def test_preflight_ok_then_publish_and_verify(self):
        Stub.routes.update({
            "GET /repos/o/r/pulls/7": (200, self._pr()),
            "GET /repos/o/r/git/ref/heads/main": (200, {"object": {"sha": "m" * 40}}),
            "POST /repos/o/r/statuses/" + "h" * 40: (201, {"state": "success"}),
            "GET /repos/o/r/commits/" + "h" * 40 + "/statuses": (200, [
                {"context": "route-a/verify", "state": "success", "target_url": ""}]),
        })
        client = self.client()
        ps.preflight(client, 7, "h" * 40, "main")
        ps.publish(client, "h" * 40, "success", "ok")
        out = ps.verify_published(client, "h" * 40, "success", attempts=1)
        self.assertTrue(out["verified"])

    def test_head_drift_refused(self):
        Stub.routes["GET /repos/o/r/pulls/7"] = (200, self._pr(head="x" * 40))
        with self.assertRaises(ps.PublishRefused):
            ps.preflight(self.client(), 7, "h" * 40, "main")
        self.assertFalse([r for r in Stub.log if r[0] == "POST"])

    def test_closed_pr_refused(self):
        Stub.routes["GET /repos/o/r/pulls/7"] = (200, self._pr(state="closed"))
        with self.assertRaises(ps.PublishRefused):
            ps.preflight(self.client(), 7, "h" * 40, "main")

    def test_base_sha_drift_refused(self):
        Stub.routes.update({
            "GET /repos/o/r/pulls/7": (200, self._pr()),
            "GET /repos/o/r/git/ref/heads/main": (200, {"object": {"sha": "n" * 40}}),
        })
        with self.assertRaises(ps.PublishRefused):
            ps.preflight(self.client(), 7, "h" * 40, "main")

    def test_status_post_403_fails(self):
        Stub.routes.update({
            "GET /repos/o/r/pulls/7": (200, self._pr()),
            "GET /repos/o/r/git/ref/heads/main": (200, {"object": {"sha": "m" * 40}}),
            "POST /repos/o/r/statuses/" + "h" * 40: (403, {"message": "Resource not accessible"}),
        })
        client = self.client()
        ps.preflight(client, 7, "h" * 40, "main")
        with self.assertRaises(ps.PublishRefused):
            ps.publish(client, "h" * 40, "success", "ok")

    def test_verify_detects_missing_context(self):
        Stub.routes["GET /repos/o/r/commits/" + "h" * 40 + "/statuses"] = (200, [])
        with self.assertRaises(ps.PublishRefused):
            ps.verify_published(self.client(), "h" * 40, "success", attempts=1)

    def test_missing_token_refused(self):
        with self.assertRaises(ps.PublishRefused):
            ps.Client("o/r", "")


if __name__ == "__main__":
    unittest.main()
