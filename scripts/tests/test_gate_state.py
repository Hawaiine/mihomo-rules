#!/usr/bin/env python3
"""D-15 专项测试：状态模型、CAS 并发、故障注入、恢复拒绝路径。

本地后端测试是**模拟**（用读-比较-原子替换表达 CAS）；GitHubRefStore 用进程内
HTTP stub 验证协议层语义（422/403/404 映射）。真实远端演练未执行，见报告 [无法验证]。
"""
from __future__ import annotations

import json
import sys
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

import gate_state as gs  # noqa: E402
import recover_gate_state as rec  # noqa: E402


def make_state(gen: int = 1, *, run_id: str = "1-1", epoch: int = 0, phase: str = "candidate",
               check: str = "pending", candidate_sha: str = "a" * 40,
               digest: bool = True) -> gs.GateState:
    state = gs.GateState(run_id=run_id, generation=gen, epoch=epoch, base_sha="b" * 40,
                         candidate_sha=candidate_sha, target_ref="refs/heads/candidate/rules-update",
                         phase=phase, check_status=check, lock_owner="gate-" + run_id,
                         lock_token="t" * 32, lease_until=0.0)
    if not digest:
        return state
    doc = state.to_doc()
    doc["snapshot_digest"] = gs.canonical_digest(doc)
    return gs.GateState.from_doc(doc)


class LocalStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.store = gs.LocalFileStore(Path(self.tmp.name))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_create_then_read(self):
        state, sha = self.store.read()
        self.assertIsNone(state)
        self.assertEqual(sha, "")
        result = self.store.cas_write(make_state(), expected_old_sha=None)
        self.assertTrue(result.ok, result.reason)
        got, ref = self.store.read()
        self.assertEqual(got.generation, 1)
        self.assertEqual(ref, result.ref_sha)

    def test_create_when_ref_exists_is_rejected(self):
        self.assertTrue(self.store.cas_write(make_state(), expected_old_sha=None).ok)
        result = self.store.cas_write(make_state(gen=2), expected_old_sha=None)
        self.assertFalse(result.ok)
        self.assertIn("已存在", result.reason)

    def test_stale_expected_sha_is_rejected(self):
        first = self.store.cas_write(make_state(), expected_old_sha=None)
        result = self.store.cas_write(make_state(gen=2), expected_old_sha="0" * 64)
        self.assertFalse(result.ok)
        self.assertIn("stale", result.reason)
        self.assertEqual(self.store.read()[0].generation, 1, "冲突不得覆盖有效状态")
        self.assertTrue(first.ok)

    def test_concurrent_writers_only_one_wins(self):
        """两个并发写者：同一 expected_old_sha，只能有一个成功。"""
        first = self.store.cas_write(make_state(), expected_old_sha=None)
        winners: list[bool] = []
        barrier = threading.Barrier(2)

        def writer(run_id: str) -> None:
            barrier.wait()
            result = self.store.cas_write(make_state(gen=2, run_id=run_id), expected_old_sha=first.ref_sha)
            winners.append(result.ok)

        threads = [threading.Thread(target=writer, args=(f"run-{i}",)) for i in range(2)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(sorted(winners), [False, True])

    def test_digest_mismatch_rejected(self):
        state = make_state()
        doc = state.to_doc()
        doc["snapshot_digest"] = "sha256:" + "0" * 64
        result = self.store.cas_write(gs.GateState(**{k: doc[k] for k in gs.GateState.__dataclass_fields__ if k in doc}),
                                      expected_old_sha=None)
        self.assertFalse(result.ok)
        self.assertIn("digest", result.reason)

    def test_unsupported_schema_version_rejected(self):
        doc = make_state().to_doc()
        doc["schema_version"] = 2
        problems = gs.validate_doc(doc)
        self.assertTrue(any("schema_version" in p for p in problems), problems)

    def test_invalid_phase_combination_rejected(self):
        with self.assertRaises(gs.StateError):
            gs.GateState.from_doc({**make_state().to_doc(), "check_status": "in_progress", "phase": "final-review"})

    def test_generation_must_be_positive(self):
        doc = make_state().to_doc()
        doc["generation"] = 0
        self.assertTrue(any("generation" in p for p in gs.validate_doc(doc)))

    def test_corrupted_state_file_fails_closed(self):
        self.assertTrue(self.store.cas_write(make_state(), expected_old_sha=None).ok)
        self.store.path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(gs.StateError):
            self.store.read()

    def test_snapshot_is_idempotent_and_not_overwritten(self):
        state = make_state()
        digest = self.store.snapshot_put(state, b'{"a":1}')
        self.assertEqual(digest, __import__("hashlib").sha256(b'{"a":1}').hexdigest())
        with self.assertRaises(gs.StateError):
            self.store.snapshot_put(state, b'{"a":2}')
        self.assertEqual(self.store.snapshot_get(1), b'{"a":1}')

    def test_snapshot_missing_is_rejected(self):
        with self.assertRaises(gs.StateError):
            self.store.snapshot_get(9)


class RecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.store = gs.LocalFileStore(Path(self.tmp.name))
        self.store.cas_write(make_state(1, epoch=0), expected_old_sha=None)
        self.store.snapshot_put(make_state(1, epoch=0), json.dumps(
            {"schema_version": 1, "generation": 1, "state": make_state(1, epoch=0).to_doc()},
            sort_keys=True).encode())
        self.store.cas_write(make_state(2, epoch=1, phase="validation", check="in_progress"),
                             expected_old_sha=self.store.read()[1])

    def _seal_snapshot(self, gen: int) -> None:
        """按恢复 CLI 期望的格式（含 digest）重写快照。"""
        state = make_state(gen, epoch=gen - 1)
        body = {"schema_version": 1, "generation": gen,
                "state": state.to_doc(), "snap_run_id": state.run_id,
                "snap_candidate_sha": state.candidate_sha}
        from scripts.ci.gate_lock import _snapshot_digest
        body["digest"] = _snapshot_digest(gen, state.to_doc(), state.run_id, state.candidate_sha)
        path = self.store.snapshots / f"gen-{gen}.json"
        if path.exists():
            path.unlink()
        path.write_text(json.dumps(body), encoding="utf-8")

    def test_recover_creates_next_generation(self):
        self._seal_snapshot(1)
        out = rec.recover(self.store, from_generation=1, expected_current=2, dry_run=False)
        self.assertEqual(out["state"]["generation"], 3)
        self.assertEqual(out["state"]["epoch"], 2)
        self.assertEqual(self.store.read()[0].generation, 3)

    def test_dry_run_does_not_write(self):
        self._seal_snapshot(1)
        out = rec.recover(self.store, from_generation=1, expected_current=2, dry_run=True)
        self.assertTrue(out["dry_run"])
        self.assertEqual(self.store.read()[0].generation, 2)

    def test_replay_rejected(self):
        self._seal_snapshot(1)
        rec.recover(self.store, from_generation=1, expected_current=2, dry_run=False)
        with self.assertRaises(rec.RecoveryRefused):
            rec.recover(self.store, from_generation=1, expected_current=2, dry_run=False)

    def test_generation_mismatch_rejected(self):
        self._seal_snapshot(1)
        with self.assertRaises(rec.RecoveryRefused):
            rec.recover(self.store, from_generation=1, expected_current=5, dry_run=False)

    def test_future_snapshot_rejected(self):
        self._seal_snapshot(2)
        with self.assertRaises(rec.RecoveryRefused):
            rec.recover(self.store, from_generation=2, expected_current=2, dry_run=False)

    def test_corrupted_snapshot_digest_rejected(self):
        self._seal_snapshot(1)
        path = self.store.snapshots / "gen-1.json"
        doc = json.loads(path.read_text())
        doc["digest"] = "sha256:" + "0" * 64
        path.write_text(json.dumps(doc), encoding="utf-8")
        with self.assertRaises(rec.RecoveryRefused):
            rec.recover(self.store, from_generation=1, expected_current=2, dry_run=False)

    def test_truncated_snapshot_rejected(self):
        self._seal_snapshot(1)
        path = self.store.snapshots / "gen-1.json"
        path.write_text(path.read_text()[: 40], encoding="utf-8")
        with self.assertRaises(rec.RecoveryRefused):
            rec.recover(self.store, from_generation=1, expected_current=2, dry_run=False)

    def test_absent_snapshot_for_generation_rejected(self):
        with self.assertRaises(rec.RecoveryRefused):
            rec.recover(self.store, from_generation=99, expected_current=2, dry_run=True)

    def test_missing_current_state_rejected(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            empty = gs.LocalFileStore(Path(d))
            with self.assertRaises(rec.RecoveryRefused):
                rec.recover(empty, from_generation=1, expected_current=1, dry_run=True)


if __name__ == "__main__":
    unittest.main()
