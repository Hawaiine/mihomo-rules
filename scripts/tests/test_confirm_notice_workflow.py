"""Behavioral tests for trusted workflow-run request provenance."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))
import confirm_notice_workflow as nw  # noqa: E402


class NoticeWorkflowProvenanceTest(unittest.TestCase):
    def mock_api(self, run, workflow=None, profile=None, artifacts=None, archive=b""):
        def fake(_token, path, binary=False):
            if "/actions/runs/12" in path and "/artifacts" not in path:
                return run
            if "/git/ref/heads/main" in path:
                return {"object": {"sha": "a" * 40}}
            if "/actions/workflows/9" in path:
                return workflow or {"path": nw.WORKFLOW_PATH, "state": "active"}
            if "/users/152380613" in path:
                return profile or {"id": nw.ALLOWED_ID, "login": nw.ALLOWED_LOGIN, "type": "User"}
            if "/contents/" in path:
                import base64
                return {"content": base64.b64encode(b"name: Manual notice confirmation request\non:\n  workflow_dispatch:\n").decode()}
            if "/actions/runs/12/artifacts" in path:
                return artifacts or {"artifacts": []}
            return archive
        return fake

    def valid_run(self, actor=None):
        return {"repository": {"full_name": nw.REPO}, "event": "workflow_dispatch",
                "head_branch": "main", "run_attempt": 1, "run_number": 1,
                "head_sha": "a" * 40, "workflow_id": 9,
                "actor": actor or {"id": nw.ALLOWED_ID, "login": nw.ALLOWED_LOGIN}}

    def test_rejects_wrong_actor_id_even_matching_login(self):
        with patch.object(nw, "api", self.mock_api(self.valid_run({"id": 99, "login": "Hawaiine"}))):
            with self.assertRaisesRegex(ValueError, "unauthorized"):
                nw.obtain_request("fake", "12")

    def test_rejects_bot_profile(self):
        with patch.object(nw, "api", self.mock_api(self.valid_run(), profile={
                "id": nw.ALLOWED_ID, "login": nw.ALLOWED_LOGIN, "type": "Bot"})):
            with self.assertRaisesRegex(ValueError, "authorized human"):
                nw.obtain_request("fake", "12")

    def test_rejects_non_default_branch_run(self):
        run = self.valid_run()
        run["head_branch"] = "attacker"
        with patch.object(nw, "api", self.mock_api(run)):
            with self.assertRaisesRegex(ValueError, "workflow_dispatch on main"):
                nw.obtain_request("fake", "12")

    def test_rejects_wrong_workflow_path(self):
        with patch.object(nw, "api", self.mock_api(self.valid_run(), workflow={"path": ".github/workflows/pr.yml", "state": "active"})):
            with self.assertRaisesRegex(ValueError, "unexpected request workflow"):
                nw.obtain_request("fake", "12")

    def test_rejects_missing_or_ambiguous_artifact(self):
        with patch.object(nw, "api", self.mock_api(self.valid_run())):
            with self.assertRaisesRegex(ValueError, "missing, expired, or ambiguous"):
                nw.obtain_request("fake", "12")


if __name__ == "__main__":
    unittest.main()