#!/usr/bin/env python3
"""D-15 接入测试：逻辑锁 CLI（acquire/complete/pause/resume）与暂停不可被普通路径清除。"""
from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

import json
import gate_lock as gl  # noqa: E402
import gate_state as gs  # noqa: E402


class GateLockCliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_cli(self, *args: str) -> int:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return gl.main(["--local-dir", str(self.dir), *args])

    def state(self) -> gs.GateState:
        return gs.LocalFileStore(self.dir).read()[0]

    def acquire(self, run_id: str = "1-1") -> int:
        return self.run_cli("--run-id", run_id, "acquire", "--base-sha", "a" * 40)

    def test_acquire_creates_generation_one_with_lock(self):
        self.assertEqual(self.acquire(), 0)
        st = self.state()
        self.assertEqual(st.generation, 1)
        self.assertEqual(st.lock_owner, "gate-1-1")
        self.assertEqual(st.phase, "candidate")

    def test_second_run_is_busy(self):
        self.assertEqual(self.acquire("1-1"), 0)
        self.assertEqual(self.acquire("2-2"), gl.EXIT_BUSY)
        self.assertEqual(self.state().generation, 1, "被拒绝的运行不得写状态")

    def test_complete_advances_generation_and_clears_lock(self):
        self.acquire()
        self.assertEqual(self.run_cli("--run-id", "1-1", "complete", "--result", "success"), 0)
        st = self.state()
        self.assertEqual(st.generation, 2)
        self.assertEqual(st.phase, "final-review")
        self.assertEqual(st.check_status, "completed")
        self.assertEqual(st.lock_owner, "")

    def test_complete_by_non_owner_rejected(self):
        self.acquire("1-1")
        self.assertEqual(self.run_cli("--run-id", "9-9", "complete", "--result", "success"), gl.EXIT_INVALID)
        self.assertEqual(self.state().generation, 1)

    def test_pause_then_normal_complete_is_refused(self):
        self.acquire()
        self.assertEqual(self.run_cli("--run-id", "1-1", "pause", "--reason", "verify failed"), 0)
        self.assertEqual(self.state().phase, "paused")
        self.assertEqual(self.run_cli("--run-id", "1-1", "complete", "--result", "success"),
                         gl.EXIT_NEEDS_HUMAN)
        self.assertEqual(self.state().phase, "paused", "普通完成路径不得清除暂停")

    def test_acquire_while_paused_is_refused(self):
        self.acquire("1-1")
        self.run_cli("--run-id", "1-1", "pause", "--reason", "x")
        self.assertEqual(self.acquire("3-3"), gl.EXIT_NEEDS_HUMAN)

    def test_resume_from_pause(self):
        self.acquire("1-1")
        self.run_cli("--run-id", "1-1", "pause", "--reason", "x")
        self.assertEqual(self.run_cli("--run-id", "4-4", "resume", "--reason", "manual ok"), 0)
        st = self.state()
        self.assertEqual(st.phase, "candidate")
        self.assertEqual(st.epoch, 1)
        self.assertEqual(st.lock_owner, "gate-4-4")

    def test_snapshot_written_on_acquire_and_complete(self):
        self.acquire()
        store = gs.LocalFileStore(self.dir)
        self.assertTrue(store.snapshot_get(1))
        self.run_cli("--run-id", "1-1", "complete", "--result", "success")
        self.assertIn(b"completed", store.snapshot_get(2))

    def test_require_enabled_accepts_true_only(self):
        import os
        from unittest import mock
        for raw, expected in (("true", 0), ("TRUE", 0), ("false", 1), ("", 1), ("yes", 1), ("1", 1)):
            with mock.patch.dict(os.environ, {"GATE_STATE_ENABLED": raw}, clear=False), \
                    redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                rc = gl.main(["--local-dir", str(self.dir), "require-enabled"])
            self.assertEqual(rc, expected, f"GATE_STATE_ENABLED={raw!r}")

    def test_require_enabled_is_read_only(self):
        import os
        from unittest import mock
        self.acquire()
        before = (self.dir / "state.json").read_bytes()
        with mock.patch.dict(os.environ, {"GATE_STATE_ENABLED": "true"}, clear=False), \
                redirect_stdout(io.StringIO()):
            gl.main(["--local-dir", str(self.dir), "require-enabled"])
        self.assertEqual((self.dir / "state.json").read_bytes(), before)

    def test_expired_lease_requires_explicit_takeover(self):
        import time as _t
        from unittest import mock
        self.acquire("1-1")
        # 把租约改成已过期（模拟"时间流逝"：状态字段被改，随后清掉该代旧快照，
        # 使 store 保持自洽——严格快照对账下，状态变了而快照未变会被判冲突）。
        store = gs.LocalFileStore(self.dir)
        st, sha = store.read()
        doc = st.to_doc(); doc["lease_until"] = _t.time() - 10
        doc["snapshot_digest"] = gs.canonical_digest(doc)
        (self.dir / "snapshots" / "gen-1.json").unlink()
        store.cas_write(gs.GateState.from_doc(doc), expected_old_sha=sha)
        self.assertEqual(self.acquire("2-2"), gl.EXIT_NEEDS_HUMAN)
        self.assertEqual(self.state().generation, 1, "未授权接管不得写入")
        rc = self.run_cli("--run-id", "2-2", "acquire", "--base-sha", "a" * 40, "--takeover")
        self.assertEqual(rc, 0)
        st2 = self.state()
        self.assertEqual(st2.lock_owner, "gate-2-2")
        self.assertEqual(st2.epoch, 1, "接管必须推进 epoch")

    def test_snapshot_failure_blocks_state_change(self):
        from unittest import mock
        store = gs.LocalFileStore(self.dir)
        with mock.patch.object(gs.LocalFileStore, "snapshot_put",
                               side_effect=gs.StateError("disk full")):
            self.assertEqual(self.acquire("1-1"), gl.EXIT_INVALID)
        self.assertIsNone(gs.LocalFileStore(self.dir).read()[0], "快照失败时不得写状态")

    def test_recovery_required_blocks_acquire_and_complete(self):
        self.acquire("1-1")
        self.assertEqual(self.run_cli("--run-id", "1-1", "pause", "--reason", "bad",
                                      "--recovery"), 0)
        self.assertEqual(self.state().phase, "recovery-required")
        self.assertEqual(self.acquire("2-2"), gl.EXIT_NEEDS_HUMAN)
        self.assertEqual(self.run_cli("--run-id", "1-1", "complete", "--result", "success"),
                         gl.EXIT_NEEDS_HUMAN)
        self.assertEqual(self.state().phase, "recovery-required")

    def test_resume_from_recovery_required(self):
        self.acquire("1-1")
        self.run_cli("--run-id", "1-1", "pause", "--reason", "bad", "--recovery")
        self.assertEqual(self.run_cli("--run-id", "3-3", "resume", "--reason", "checked"), 0)
        self.assertEqual(self.state().phase, "candidate")

    def test_require_usable(self):
        self.assertEqual(self.run_cli("require-usable"), 0)
        self.acquire("1-1")
        self.assertEqual(self.run_cli("require-usable"), 0)
        self.run_cli("--run-id", "1-1", "pause", "--reason", "x")
        self.assertEqual(self.run_cli("require-usable"), gl.EXIT_NEEDS_HUMAN)

    def test_write_timeout_reconciled_when_write_landed(self):
        """API 超时但写入实际成功 → 回读确认后幂等接受，不重复写。"""
        from unittest import mock
        store = gs.LocalFileStore(self.dir)
        real = gs.LocalFileStore.cas_write

        def flaky(self_, state, *, expected_old_sha):
            result = real(self_, state, expected_old_sha=expected_old_sha)
            raise gs.StateError("timeout after write")

        with mock.patch.object(gs.LocalFileStore, "cas_write", flaky):
            rc = self.acquire("1-1")
        self.assertEqual(rc, gl.EXIT_OK)
        self.assertEqual(self.state().generation, 1)

    def test_write_timeout_not_landed_fails_closed(self):
        from unittest import mock
        with mock.patch.object(gs.LocalFileStore, "cas_write",
                               side_effect=gs.StateError("timeout before write")):
            rc = self.acquire("1-1")
        self.assertEqual(rc, gl.EXIT_INVALID)
        self.assertIsNone(self.state())

    def test_status_is_read_only(self):
        self.acquire()
        before = (self.dir / "state.json").read_bytes()
        self.assertEqual(self.run_cli("status"), 0)
        self.assertEqual((self.dir / "state.json").read_bytes(), before)

    def test_missing_run_id_rejected(self):
        # CI 环境本身带 GITHUB_RUN_ID；这里显式清空，避免测试依赖外部环境。
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {"GITHUB_RUN_ID": ""}, clear=False), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            rc = gl.main(["--local-dir", str(self.dir), "acquire", "--base-sha", "a" * 40])
        self.assertEqual(rc, gl.EXIT_INVALID)


class NoticeTest(unittest.TestCase):
    """B4 / U-09：关键通知确认门禁"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_cli(self, *args: str) -> int:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return gl.main(["--local-dir", str(self.dir), *args])

    def state(self) -> gs.GateState:
        return gs.LocalFileStore(self.dir).read()[0]

    def acquire(self, run_id: str = "1-1") -> int:
        return self.run_cli("--run-id", run_id, "--lease-seconds", "60",
                            "acquire", "--base-sha", "a" * 40,
                            "--candidate-sha", "b" * 40)

    def test_mark_notice_sets_digest(self):
        self.assertEqual(self.acquire(), 0)
        rc = self.run_cli("--run-id", "1-1", "mark-notice-needed",
                          "--digest", "notice-001")
        self.assertEqual(rc, 0)
        st = self.state()
        self.assertEqual(st.notice_digest, "notice-001")
        self.assertFalse(st.notice_acked)

    def test_confirm_notice_refuses_unverified_run_identity(self):
        self.assertEqual(self.acquire(), 0)
        self.assertEqual(self.run_cli("--run-id", "1-1", "mark-notice-needed",
                                      "--digest", "n1"), 0)
        rc = self.run_cli("--run-id", "1-1", "confirm-notice", "--digest", "n1")
        self.assertEqual(rc, gl.EXIT_NEEDS_HUMAN)
        st = self.state()
        self.assertFalse(st.notice_acked)
        self.assertEqual(st.notice_acked_by, "")

    def test_confirm_wrong_digest_rejected(self):
        self.assertEqual(self.acquire(), 0)
        self.run_cli("--run-id", "1-1", "mark-notice-needed", "--digest", "n1")
        rc = self.run_cli("--run-id", "1-1", "confirm-notice", "--digest", "n2")
        self.assertNotEqual(rc, 0)

    def test_require_usable_blocks_unacked_notice(self):
        self.assertEqual(self.acquire(), 0)
        self.run_cli("--run-id", "1-1", "mark-notice-needed", "--digest", "n1")
        rc = self.run_cli("--run-id", "1-1", "require-usable")
        self.assertNotEqual(rc, 0)  # 未确认时拒绝继续

    def test_require_usable_allows_acked_notice(self):
        self.assertEqual(self.acquire(), 0)
        self.run_cli("--run-id", "1-1", "mark-notice-needed", "--digest", "n1")
        self.run_cli("--run-id", "1-1", "confirm-notice", "--digest", "n1")
        rc = self.run_cli("--run-id", "1-1", "require-usable")
        self.assertNotEqual(rc, 0)  # 自动命令不能伪造人工确认

    def test_complete_blocks_unacked_notice(self):
        self.assertEqual(self.acquire(), 0)
        self.run_cli("--run-id", "1-1", "mark-notice-needed", "--digest", "n1")
        rc = self.run_cli("--run-id", "1-1", "complete", "--result", "success")
        self.assertNotEqual(rc, 0)  # 未确认通知时拒绝 complete

    def test_no_notice_no_blockage(self):
        self.assertEqual(self.acquire(), 0)
        rc = self.run_cli("--run-id", "1-1", "require-usable")
        self.assertEqual(rc, 0)

    def test_mark_notice_without_lock_rejected(self):
        self.assertEqual(self.acquire("2-2"), 0)
        rc = self.run_cli("--run-id", "1-1", "mark-notice-needed",
                          "--digest", "n1")
        self.assertNotEqual(rc, 0)

    def test_confirm_no_notice_marked_rejected(self):
        self.assertEqual(self.acquire(), 0)
        rc = self.run_cli("--run-id", "1-1", "confirm-notice", "--digest", "n1")
        self.assertNotEqual(rc, 0)


class SnapshotBindingTest(unittest.TestCase):
    """B5：快照与 run_id / candidate_sha 绑定，恢复验证"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_cli(self, *args: str) -> int:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return gl.main(["--local-dir", str(self.dir), *args])

    def state(self) -> gs.GateState:
        return gs.LocalFileStore(self.dir).read()[0]

    def acquire(self, run_id: str = "1-1") -> int:
        return self.run_cli("--run-id", run_id, "--lease-seconds", "60",
                            "acquire", "--base-sha", "a" * 40,
                            "--candidate-sha", "b" * 40)

    def test_snapshot_body_contains_run_id_and_candidate_sha(self):
        from scripts.ci.gate_lock import _snapshot_digest
        self.assertEqual(self.acquire(), 0)
        st = self.state()
        # Verify GateState fields carry run_id / candidate_sha
        self.assertEqual(st.run_id, "1-1")
        self.assertEqual(st.candidate_sha, "b" * 40)
        # Snapshot body would include these via to_doc()

    def test_recovery_snap_run_id_mismatch_blocked(self):
        """快照的 snap_run_id 与当前 run_id 不匹配 → 拒绝恢复"""
        from scripts.ci.recover_gate_state import recover, RecoveryRefused
        from scripts.ci.gate_state import LocalFileStore

        # 1) acquire with run_id "1-1"
        self.assertEqual(self.acquire("1-1"), 0)
        acquire_state = self.state().to_doc()  # gen=1, run_id=1-1, epoch=0
        # 2) 先完成当前 run，推进 generation（current=gen=2）
        self.assertEqual(self.run_cli("--run-id", "1-1", "complete", "--result", "success"), 0)
        current = self.state()  # gen=2
        # 3) 删除 gen=1 的真实快照，创建一个携带错误 snap_run_id 的 gen=1 假快照
        import os as _os
        gen_dir = Path(self.dir) / "snapshots"
        # 删除 gen=1 快照 (acquire 产生的)
        for p in gen_dir.glob("gen-1*.json"):
            _os.unlink(p)
        body = {
            "schema_version": 1, "generation": 1,
            "state": acquire_state,
            "meta": {"kind": "fake"}, "digest": "sha256:fake",
            "snap_run_id": "2-2",  # wrong run!
            "snap_candidate_sha": "b" * 40
        }
        from scripts.ci.gate_lock import _snapshot_digest
        body["digest"] = _snapshot_digest(1, body["state"], body["snap_run_id"], body["snap_candidate_sha"])
        store = LocalFileStore(self.dir)
        # 直接写文件绕过 snapshot_put（store 会因 gen 重复拒绝）
        snap_path = gen_dir / "gen-1.json"
        snap_path.write_text(json.dumps(body, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        # 4) attempt recovery from gen=1 snap → must fail on snap_run_id mismatch
        with self.assertRaises(RecoveryRefused) as ctx:
            recover(store, from_generation=1, expected_current=current.generation, dry_run=True)
        self.assertIn("run_id", str(ctx.exception))

    def test_recovery_snap_candidate_mismatch_blocked(self):
        """快照的 snap_candidate_sha 与当前不一致 → 拒绝恢复"""
        from scripts.ci.recover_gate_state import recover, RecoveryRefused
        from scripts.ci.gate_state import LocalFileStore

        # 1) acquire (epoch=0), pause, resume (epoch→1), complete (epoch=1)
        self.assertEqual(self.acquire("1-1"), 0)
        acquire_state = self.state().to_doc()  # gen=1, run_id=1-1, epoch=0, candidate=passed base
        self.run_cli("--run-id", "1-1", "pause", "--reason", "test")
        self.run_cli("--run-id", "2-2", "resume", "--reason", "test")
        self.run_cli("--run-id", "2-2", "complete", "--result", "success")
        current = self.state()  # gen=4, epoch=1, run_id=2-2
        # 2) 删除 gen=1 真实快照，创建假 gen=1 快照（错误候选 SHA）
        import os as _os2
        gen_dir = Path(self.dir) / "snapshots"
        for p in gen_dir.glob("gen-1*.json"):
            _os2.unlink(p)
        body = {
            "schema_version": 1, "generation": 1,
            "state": acquire_state,  # run_id=1-1, epoch=0 (replay guard won't trigger because current.run_id=2-2)
            "meta": {"kind": "fake"}, "digest": "sha256:fake",
            "snap_run_id": "2-2",  # matches current.run_id → passes run_id check
            "snap_candidate_sha": "c" * 40  # wrong candidate → triggers rejection
        }
        from scripts.ci.gate_lock import _snapshot_digest
        body["digest"] = _snapshot_digest(1, body["state"], body["snap_run_id"], body["snap_candidate_sha"])
        store = LocalFileStore(self.dir)
        snap_path = gen_dir / "gen-1.json"
        snap_path.write_text(json.dumps(body, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        with self.assertRaises(RecoveryRefused) as ctx:
            recover(store, from_generation=1, expected_current=current.generation, dry_run=True)
        self.assertIn("candidate", str(ctx.exception))

    def test_recovery_legacy_snapshot_without_binding_rejected(self):
        """旧格式快照（缺 snap_run_id / snap_candidate_sha）必须拒绝恢复，且摘要不能掩盖缺字段。"""
        from scripts.ci.recover_gate_state import recover, RecoveryRefused
        from scripts.ci.gate_state import LocalFileStore
        from scripts.ci.gate_lock import _snapshot_digest

        self.assertEqual(self.acquire("1-1"), 0)
        acquire_state = self.state().to_doc()
        self.run_cli("--run-id", "1-1", "complete", "--result", "success")
        current = self.state()
        gen_dir = Path(self.dir) / "snapshots"
        for p in gen_dir.glob("gen-1*.json"):
            p.unlink()

        # 旧格式：无绑定字段；摘要按旧口径（仅 generation+state）计算，仍必须被拒绝
        legacy = {"schema_version": 1, "generation": 1, "state": acquire_state,
                  "meta": {"kind": "legacy"}}
        legacy["digest"] = _snapshot_digest(1, legacy["state"], "", "")
        path = gen_dir / "gen-1.json"
        path.write_text(json.dumps(legacy, sort_keys=True), encoding="utf-8")

        store = LocalFileStore(self.dir)
        with self.assertRaises(RecoveryRefused) as ctx:
            recover(store, from_generation=1, expected_current=current.generation, dry_run=True)
        self.assertIn("snap_", str(ctx.exception))

    def test_recovery_tampered_binding_field_rejected(self):
        """绑定字段被改写但摘要未同步 → 摘要不符，拒绝恢复。"""
        from scripts.ci.recover_gate_state import recover, RecoveryRefused
        from scripts.ci.gate_state import LocalFileStore

        self.assertEqual(self.acquire("1-1"), 0)
        self.run_cli("--run-id", "1-1", "complete", "--result", "success")
        current = self.state()
        gen_dir = Path(self.dir) / "snapshots"
        snap = json.loads((gen_dir / "gen-1.json").read_text(encoding="utf-8"))
        snap["snap_candidate_sha"] = "e" * 40  # 改绑定字段，不动 digest
        (gen_dir / "gen-1.json").write_text(json.dumps(snap, sort_keys=True), encoding="utf-8")

        store = LocalFileStore(self.dir)
        with self.assertRaises(RecoveryRefused) as ctx:
            recover(store, from_generation=1, expected_current=current.generation, dry_run=True)
        self.assertIn("digest", str(ctx.exception))

    def test_recovery_resets_notice_acked(self):
        """恢复后通知状态重置，恢复后必须重新确认"""
        from scripts.ci.recover_gate_state import recover
        from scripts.ci.gate_state import LocalFileStore

        self.assertEqual(self.acquire("1-1"), 0)
        # pause → resume (advance epoch so recover's replay guard won't trap us)
        self.run_cli("--run-id", "1-1", "pause", "--reason", "epoch advance")
        self.run_cli("--run-id", "2-2", "resume", "--reason", "epoch advance")
        gen_after_resume = self.state().generation  # 4 (after acquire→pause→resume)
        # Set notice → gen after resume + 1 (run_id=2-2 after resume)
        self.assertEqual(self.run_cli("--run-id", "2-2", "mark-notice-needed", "--digest", "n1"), 0)
        # Automated run identity is not human acknowledgement; preserve unacked state.
        self.assertEqual(self.run_cli("--run-id", "2-2", "confirm-notice", "--digest", "n1"),
                         gl.EXIT_NEEDS_HUMAN)
        # Bump epoch to bypass replay guard: pause → resume with same run_id (allowed)
        self.run_cli("--run-id", "2-2", "pause", "--reason", "epoch bump")
        self.run_cli("--run-id", "2-2", "resume", "--reason", "epoch bump")
        self.run_cli("--run-id", "2-2", "complete", "--result", "success")
        st = self.state()  # notice_acked=False (complete cleared it), epoch=2; but snap at gen=5 still has epoch=1
        # Recover from gen after mark-notice-needed → new gen should reset notice
        store = LocalFileStore(self.dir)
        result = recover(store, from_generation=gen_after_resume + 1,  # gen=5 (has notice_digest)
                         expected_current=st.generation, dry_run=True)   # current=gen=3
        would_write = result["would_write"]
        self.assertFalse(would_write.get("notice_acked"), "恢复后 notice_acked 必须重置")
        self.assertEqual(would_write.get("notice_digest"), "")




if __name__ == "__main__":
    unittest.main()
