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


if __name__ == "__main__":
    unittest.main()
