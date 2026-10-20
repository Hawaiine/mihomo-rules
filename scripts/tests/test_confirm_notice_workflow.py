"""可信请求来源核验 + 通知确认正向集成测试。

覆盖：请求制品 → 来源 run 核验 → 默认分支 SHA → workflow 路径/内容 → 授权用户身份
→ 制品 schema → 状态绑定 → ``confirm_notice()`` → 快照 + CAS 状态转换。
另含 ``notice_run_id`` 规范形态契约（工作流入口 / 处理器 / 状态转换必须一致）。
"""
import base64
import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))
import confirm_notice_workflow as nw  # noqa: E402
import gate_lock as gl  # noqa: E402
import gate_state as gs  # noqa: E402

MAIN_SHA = "a" * 40
DIGEST = "sha256:" + "b" * 64
REQUEST_RUN_ID = 9001
NOTICE_RUN = "4242-1"


def make_archive(request) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("notice-request.json", json.dumps(request))
    return buf.getvalue()


def request_of(**over):
    payload = {"notice_run_id": NOTICE_RUN, "notice_digest": DIGEST, "expected_generation": "2"}
    payload.update(over)
    return payload


class MockApi:
    """模拟 GitHub REST：只返回被显式配置的对象，其余一律 404 语义错误。"""

    def __init__(self, *, run=None, workflow=None, profile=None, artifacts=None,
                 archive=b"", main_sha=MAIN_SHA, source=None):
        self.run = run if run is not None else self.valid_run()
        self.workflow = workflow or {"path": nw.WORKFLOW_PATH, "state": "active"}
        self.profile = profile or {"id": nw.ALLOWED_ID, "login": nw.ALLOWED_LOGIN, "type": "User"}
        self.artifacts = artifacts if artifacts is not None else {
            "artifacts": [{"name": f"manual-notice-request-{REQUEST_RUN_ID}", "expired": False,
                           "archive_download_url":
                               f"https://api.github.com/repos/{nw.REPO}/actions/artifacts/1/zip"}]}
        self.archive = archive
        self.main_sha = main_sha
        self.source = source if source is not None else (
            b"name: Manual notice confirmation request\non:\n  workflow_dispatch:\n")
        self.calls = []

    @staticmethod
    def valid_run(actor=None):
        return {"id": REQUEST_RUN_ID, "repository": {"full_name": nw.REPO}, "event": "workflow_dispatch",
                "head_branch": "main", "run_attempt": 1, "run_number": 1,
                "head_sha": MAIN_SHA, "workflow_id": 9,
                "actor": actor or {"id": nw.ALLOWED_ID, "login": nw.ALLOWED_LOGIN}}

    def __call__(self, _token, path, binary=False):
        self.calls.append(path)
        if path.endswith(f"/actions/runs/{REQUEST_RUN_ID}"):
            return self.run
        if path.endswith("/git/ref/heads/main"):
            return {"object": {"sha": self.main_sha}}
        if "/actions/workflows/9" in path:
            return self.workflow
        if f"/users/{nw.ALLOWED_ID}" in path:
            return self.profile
        if "/contents/" in path:
            return {"content": base64.b64encode(self.source).decode()}
        if path.endswith(f"/actions/runs/{REQUEST_RUN_ID}/artifacts"):
            return self.artifacts
        return self.archive


def last_json(text: str) -> dict:
    """取输出中最后一个 JSON 对象（_commit 会先打印一行写入结果）。"""
    for line in reversed([ln for ln in text.splitlines() if ln.strip().startswith("{")]):
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    return {}


class NoticeRunIdContractTest(unittest.TestCase):
    """`notice_run_id` 唯一规范：``<run_id>-<attempt>``（生产者：candidate-pr-sync.yml）。"""

    def test_canonical_pattern_matches_real_producer(self):
        workflow = (ROOT / ".github/workflows/candidate-pr-sync.yml").read_text(encoding="utf-8")
        self.assertIn('--run-id "${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"', workflow)
        for value in ("4242-1", "4242-3"):
            self.assertTrue(gs.valid_notice_run_id(value), value)
        for value in ("4242", "0-1", "4242-0", "4242-", "-1", "04242-1", "4242-1-1", "", None, 4242):
            self.assertFalse(gs.valid_notice_run_id(value), value)

    def test_request_workflow_regex_equals_python_contract(self):
        doc = yaml.safe_load((ROOT / ".github/workflows/notice-confirm-request.yml")
                             .read_text(encoding="utf-8"))
        run_step = next(s for s in doc["jobs"]["validate-request"]["steps"]
                        if "NOTICE_RUN_ID" in str(s.get("run", "")))
        script = run_step["run"]
        line = next(ln for ln in script.splitlines() if "NOTICE_RUN_ID\" =~ " in ln)
        regex = line.split("=~ ", 1)[1].split()[0].strip()
        self.assertEqual(regex, f"^{gs.NOTICE_RUN_ID_PATTERN}$")

    def test_state_transition_rejects_bare_run_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = gs.LocalFileStore(Path(tmp))
            self.assertEqual(gl.main(["--local-dir", tmp, "--run-id", NOTICE_RUN,
                                      "acquire", "--base-sha", "a" * 40]), gl.EXIT_OK)
            self.assertEqual(gl.main(["--local-dir", tmp, "--run-id", NOTICE_RUN,
                                      "mark-notice-needed", "--digest", DIGEST]), gl.EXIT_OK)
            before = store.read()[0]
            with patch("sys.stdout", new=io.StringIO()) as out:
                rc = gl.confirm_notice(store, notice_run_id="4242", notice_digest=DIGEST,
                                       expected_generation=before.generation,
                                       actor_id=nw.ALLOWED_ID, actor_login=nw.ALLOWED_LOGIN,
                                       actor_type="User", strict_write=True)
            self.assertEqual(rc, gl.EXIT_INVALID)
            self.assertEqual(last_json(out.getvalue())["reason"], "REQUEST_INVALID")
            after = store.read()[0]
            self.assertEqual(after.generation, before.generation)
            self.assertFalse(after.notice_acked)

    def test_processor_rejects_bare_run_id_in_artifact(self):
        api = MockApi(archive=make_archive(request_of(notice_run_id="4242")))
        with patch.object(nw, "api", api):
            with self.assertRaisesRegex(ValueError, "notice_run_id"):
                nw.obtain_request("fake", str(REQUEST_RUN_ID))


class NoticeWorkflowProvenanceTest(unittest.TestCase):
    def test_rejects_event_and_api_run_id_mismatch(self):
        run = MockApi.valid_run()
        run["id"] = REQUEST_RUN_ID + 1
        with patch.object(nw, "api", MockApi(run=run)):
            with self.assertRaisesRegex(ValueError, "source run id mismatch"):
                nw.obtain_request("fake", str(REQUEST_RUN_ID))

    def test_rejects_wrong_actor_id_even_matching_login(self):
        api = MockApi(run=MockApi.valid_run({"id": 99, "login": nw.ALLOWED_LOGIN}))
        with patch.object(nw, "api", api):
            with self.assertRaisesRegex(ValueError, "unauthorized"):
                nw.obtain_request("fake", str(REQUEST_RUN_ID))

    def test_rejects_bot_profile(self):
        api = MockApi(profile={"id": nw.ALLOWED_ID, "login": nw.ALLOWED_LOGIN, "type": "Bot"})
        with patch.object(nw, "api", api):
            with self.assertRaisesRegex(ValueError, "authorized human"):
                nw.obtain_request("fake", str(REQUEST_RUN_ID))

    def test_rejects_non_default_branch_run(self):
        run = MockApi.valid_run()
        run["head_branch"] = "attacker"
        with patch.object(nw, "api", MockApi(run=run)):
            with self.assertRaisesRegex(ValueError, "workflow_dispatch on main"):
                nw.obtain_request("fake", str(REQUEST_RUN_ID))

    def test_rejects_rerun_attempt(self):
        run = MockApi.valid_run()
        run["run_attempt"] = 2
        with patch.object(nw, "api", MockApi(run=run)):
            with self.assertRaisesRegex(ValueError, "reruns"):
                nw.obtain_request("fake", str(REQUEST_RUN_ID))

    def test_rejects_run_not_on_current_default_tip(self):
        with patch.object(nw, "api", MockApi(main_sha="c" * 40)):
            with self.assertRaisesRegex(ValueError, "default-branch tip"):
                nw.obtain_request("fake", str(REQUEST_RUN_ID))

    def test_rejects_wrong_workflow_path(self):
        api = MockApi(workflow={"path": ".github/workflows/pr.yml", "state": "active"})
        with patch.object(nw, "api", api):
            with self.assertRaisesRegex(ValueError, "unexpected request workflow"):
                nw.obtain_request("fake", str(REQUEST_RUN_ID))

    def test_rejects_inactive_workflow(self):
        api = MockApi(workflow={"path": nw.WORKFLOW_PATH, "state": "disabled_manually"})
        with patch.object(nw, "api", api):
            with self.assertRaisesRegex(ValueError, "unexpected request workflow"):
                nw.obtain_request("fake", str(REQUEST_RUN_ID))

    def test_rejects_untrusted_source_content(self):
        api = MockApi(source=b"name: something else\non: push\n")
        with patch.object(nw, "api", api):
            with self.assertRaisesRegex(ValueError, "not the trusted request workflow"):
                nw.obtain_request("fake", str(REQUEST_RUN_ID))

    def test_rejects_missing_or_ambiguous_artifact(self):
        with patch.object(nw, "api", MockApi(artifacts={"artifacts": []})):
            with self.assertRaisesRegex(ValueError, "missing, expired, or ambiguous"):
                nw.obtain_request("fake", str(REQUEST_RUN_ID))

    def test_rejects_duplicate_artifact(self):
        entry = {"name": f"manual-notice-request-{REQUEST_RUN_ID}", "expired": False}
        with patch.object(nw, "api", MockApi(artifacts={"artifacts": [entry, dict(entry)]})):
            with self.assertRaisesRegex(ValueError, "missing, expired, or ambiguous"):
                nw.obtain_request("fake", str(REQUEST_RUN_ID))

    def test_rejects_expired_artifact(self):
        entry = {"name": f"manual-notice-request-{REQUEST_RUN_ID}", "expired": True}
        with patch.object(nw, "api", MockApi(artifacts={"artifacts": [entry]})):
            with self.assertRaisesRegex(ValueError, "missing, expired, or ambiguous"):
                nw.obtain_request("fake", str(REQUEST_RUN_ID))

    def test_rejects_extra_fields_and_bad_digest(self):
        for payload, pattern in ((request_of(extra="x"), "request fields"),
                                 (request_of(notice_digest="sha256:zz"), "notice_digest"),
                                 (request_of(expected_generation="0"), "expected_generation")):
            with self.subTest(payload=payload):
                with patch.object(nw, "api", MockApi(archive=make_archive(payload))):
                    with self.assertRaisesRegex(ValueError, pattern):
                        nw.obtain_request("fake", str(REQUEST_RUN_ID))

    def test_rejects_extra_archive_members(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("notice-request.json", json.dumps(request_of()))
            zf.writestr("evil.sh", "rm -rf /")
        with patch.object(nw, "api", MockApi(archive=buf.getvalue())):
            with self.assertRaisesRegex(ValueError, "archive contents"):
                nw.obtain_request("fake", str(REQUEST_RUN_ID))


class NoticeConfirmationIntegrationTest(unittest.TestCase):
    """端到端：制品请求 → 来源核验 → 状态绑定 → confirm_notice → 快照 + CAS。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.store = gs.LocalFileStore(self.dir)
        # 候选流程形态：run_id = <run_id>-<attempt>
        self.assertEqual(gl.main(["--local-dir", str(self.dir), "--run-id", NOTICE_RUN,
                                  "acquire", "--base-sha", "a" * 40]), gl.EXIT_OK)
        self.assertEqual(gl.main(["--local-dir", str(self.dir), "--run-id", NOTICE_RUN,
                                  "mark-notice-needed", "--digest", DIGEST]), gl.EXIT_OK)
        self.generation = self.store.read()[0].generation
        self.assertTrue(self.store.read()[0].notice_digest)
        self.event = self.dir / "event.json"
        self.event.write_text(json.dumps({"workflow_run": {
            "id": REQUEST_RUN_ID, "conclusion": "success",
            "repository": {"full_name": nw.REPO}}}), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def run_handler(self, payload, api=None):
        api = api or MockApi(archive=make_archive(payload))
        env = {"GITHUB_TOKEN": "fake", "GITHUB_EVENT_PATH": str(self.event)}
        with patch.object(nw, "api", api), \
                patch.object(gs, "GitHubRefStore", lambda repo, token: self.store), \
                patch.dict(os.environ, env, clear=False), \
                patch("sys.stdout", new=io.StringIO()) as out:
            rc = nw.main()
        return rc, out.getvalue()

    def test_authorized_request_performs_exactly_one_transition(self):
        before = self.store.read()[0]
        request = request_of(expected_generation=str(self.generation))
        rc, out = self.run_handler(request)
        self.assertEqual(rc, gl.EXIT_OK, out)
        evidence = last_json(out)
        self.assertTrue(evidence["ok"])
        self.assertTrue(evidence["state_written"])

        after = self.store.read()[0]
        self.assertEqual(after.generation, before.generation + 1)
        self.assertTrue(after.notice_acked)
        self.assertEqual(after.notice_acked_by_id, nw.ALLOWED_ID)
        self.assertEqual(after.notice_acked_by_login, nw.ALLOWED_LOGIN)
        self.assertEqual(after.notice_acked_run_id, NOTICE_RUN)
        self.assertEqual(after.notice_acked_digest, DIGEST)
        self.assertEqual(after.notice_acked_generation_from, before.generation)
        self.assertEqual(after.notice_acked_generation_to, after.generation)
        # 绑定的是**通知 run**，不是请求工作流自身的 run ID
        self.assertNotIn(str(REQUEST_RUN_ID), after.notice_acked_run_id)
        self.assertEqual(after.run_id, before.run_id)
        # 快照已落盘且可回读
        self.assertTrue(self.store.snapshot_get(after.generation))

    def test_confirm_is_usable_for_next_gate_step(self):
        rc, out = self.run_handler(request_of(expected_generation=str(self.generation)))
        self.assertEqual(rc, gl.EXIT_OK, out)
        self.assertEqual(gl.main(["--local-dir", str(self.dir), "--run-id", NOTICE_RUN,
                                  "require-usable"]), gl.EXIT_OK)

    def test_replay_same_request_is_refused(self):
        request = request_of(expected_generation=str(self.generation))
        self.assertEqual(self.run_handler(request)[0], gl.EXIT_OK)
        gen_after_first = self.store.read()[0].generation
        rc, out = self.run_handler(request)
        self.assertNotEqual(rc, gl.EXIT_OK)
        evidence = last_json(out)
        self.assertIn(evidence.get("reason", "NON_ZERO_EXIT"), {"GENERATION_MISMATCH", "NOTICE_NOT_PENDING"})
        self.assertEqual(self.store.read()[0].generation, gen_after_first)

    def test_wrong_generation_writes_nothing(self):
        before = self.store.read()[0]
        rc, out = self.run_handler(request_of(expected_generation=str(before.generation + 5)))
        self.assertNotEqual(rc, gl.EXIT_OK)
        self.assertEqual(self.store.read()[0].generation, before.generation)
        self.assertFalse(self.store.read()[0].notice_acked)

    def test_wrong_digest_writes_nothing(self):
        before = self.store.read()[0]
        rc, _ = self.run_handler(request_of(expected_generation=str(before.generation),
                                            notice_digest="sha256:" + "c" * 64))
        self.assertNotEqual(rc, gl.EXIT_OK)
        self.assertEqual(self.store.read()[0].generation, before.generation)
        self.assertFalse(self.store.read()[0].notice_acked)

    def test_unknown_notice_run_writes_nothing(self):
        before = self.store.read()[0]
        rc, _ = self.run_handler(request_of(expected_generation=str(before.generation),
                                            notice_run_id="9999-1"))
        self.assertNotEqual(rc, gl.EXIT_OK)
        self.assertEqual(self.store.read()[0].generation, before.generation)

    def test_untrusted_actor_writes_nothing(self):
        before = self.store.read()[0]
        api = MockApi(run=MockApi.valid_run({"id": 1, "login": nw.ALLOWED_LOGIN}),
                      archive=make_archive(request_of(expected_generation=str(before.generation))))
        env = {"GITHUB_TOKEN": "fake", "GITHUB_EVENT_PATH": str(self.event)}
        with patch.object(nw, "api", api), \
                patch.object(gs, "GitHubRefStore", lambda repo, token: self.store), \
                patch.dict(os.environ, env, clear=False), \
                self.assertRaisesRegex(ValueError, "unauthorized"):
            nw.main()
        self.assertEqual(self.store.read()[0].generation, before.generation)
        self.assertFalse(self.store.read()[0].notice_acked)

    def test_cas_conflict_is_not_reported_as_success(self):
        before = self.store.read()[0]
        payload = request_of(expected_generation=str(before.generation))
        api = MockApi(archive=make_archive(payload))
        real_cas = self.store.cas_write            # bound，用于模拟并发写者
        real_unbound = gs.LocalFileStore.__dict__["cas_write"]

        def racing_write(store_self, state, *, expected_old_sha):
            # 模拟并发写者抢先推进 ref：本次确认的 CAS 必然失配
            import dataclasses
            doc = dataclasses.replace(before, generation=before.generation + 1).to_doc()
            doc["snapshot_digest"] = gs.canonical_digest(doc)
            real_cas(gs.GateState.from_doc(doc), expected_old_sha=expected_old_sha)
            return real_unbound(store_self, state, expected_old_sha=expected_old_sha)

        with patch.object(gs.LocalFileStore, "cas_write", racing_write):
            rc, out = self.run_handler(payload, api=api)
        self.assertNotEqual(rc, gl.EXIT_OK)
        evidence = last_json(out)
        self.assertNotEqual(evidence.get("decision"), "confirm")
        self.assertFalse(self.store.read()[0].notice_acked)

    def test_snapshot_failure_writes_nothing(self):
        before = self.store.read()[0]
        payload = request_of(expected_generation=str(before.generation))
        with patch.object(gs.LocalFileStore, "snapshot_put",
                          side_effect=gs.StateError("disk full")):
            rc, _ = self.run_handler(payload)
        self.assertNotEqual(rc, gl.EXIT_OK)
        self.assertEqual(self.store.read()[0].generation, before.generation)
        self.assertFalse(self.store.read()[0].notice_acked)


if __name__ == "__main__":
    unittest.main()
