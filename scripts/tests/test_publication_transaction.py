import tempfile
import unittest
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "config_contract"))
from publication import FILES, PublicationBlocked, publish, stage


class PublicationTransactionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="publication-transaction-")
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / "repo"
        self.staging = base / "staging"
        self.old = {}
        self.candidates = {}
        for relative in FILES:
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            self.old[relative] = ("old:" + relative).encode()
            self.candidates[relative] = ("new:" + relative).encode()
            target.write_bytes(self.old[relative])
        stage(self.root, self.candidates, self.staging)

    def assert_originals(self):
        for relative in FILES:
            self.assertEqual((self.root / relative).read_bytes(), self.old[relative], relative)

    def test_missing_original_target_aborts_staging(self):
        (self.root / FILES[0]).unlink()
        isolated_staging = Path(self.temp.name) / "missing-target-staging"
        with self.assertRaises((RuntimeError, FileNotFoundError)):
            stage(self.root, self.candidates, isolated_staging)
        self.assertFalse((isolated_staging / "manifest.json").exists())

    def test_missing_backup_aborts_before_any_publish(self):
        (self.staging / "backup" / FILES[0]).unlink()
        with self.assertRaises(PublicationBlocked):
            publish(self.root, self.staging, lambda _: None, fail_at=2)
        self.assert_originals()

    def test_corrupt_backup_aborts_before_any_publish(self):
        (self.staging / "backup" / FILES[0]).write_bytes(b"corrupt")
        with self.assertRaises(PublicationBlocked):
            publish(self.root, self.staging, lambda _: None, fail_at=2)
        self.assert_originals()

    def test_corrupt_candidate_aborts_before_any_publish(self):
        (self.staging / "candidate" / FILES[0]).write_bytes(b"corrupt")
        with self.assertRaises(PublicationBlocked):
            publish(self.root, self.staging, lambda _: None)
        self.assert_originals()

    def test_validation_failure_does_not_mutate_production(self):
        def reject(_):
            raise ValueError("candidate rejected")
        with self.assertRaises(ValueError):
            publish(self.root, self.staging, reject)
        self.assert_originals()

    def test_publish_failures_restore_all_originals(self):
        for index in (1, 2, 3, 4):
            with self.subTest(publish_index=index):
                for relative in FILES:
                    (self.root / relative).write_bytes(self.old[relative])
                stage(self.root, self.candidates, self.staging)
                with self.assertRaisesRegex(RuntimeError, "publication rolled back"):
                    publish(self.root, self.staging, lambda _: None, fail_at=index)
                self.assert_originals()

    def test_readback_failure_restores_all_originals(self):
        with self.assertRaisesRegex(RuntimeError, "publication rolled back"):
            publish(self.root, self.staging, lambda _: None, verify_fail=True)
        self.assert_originals()

    def test_rollback_failure_is_surfaced_and_evidence_preserved(self):
        with self.assertRaisesRegex(PublicationBlocked, "rollback failed"):
            publish(self.root, self.staging, lambda _: None, verify_fail=True, rollback_fail=True)
        self.assertTrue((self.staging / "manifest.json").is_file())
        for relative in FILES:
            self.assertTrue((self.staging / "backup" / relative).is_file())
        for relative in FILES:
            self.assertEqual((self.root / relative).read_bytes(), self.candidates[relative])

    def test_silent_restore_failure_is_caught_by_post_rollback_verification(self):
        import publication
        real_replace = publication.os.replace

        def skip_rollback_replace(source, destination):
            if str(source).endswith(".rollback"):
                return None
            return real_replace(source, destination)

        with patch.object(publication.os, "replace", side_effect=skip_rollback_replace):
            with self.assertRaisesRegex(PublicationBlocked, "rollback incomplete"):
                publish(self.root, self.staging, lambda _: None, verify_fail=True)


if __name__ == "__main__":
    unittest.main()
