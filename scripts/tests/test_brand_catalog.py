import json
import os
import subprocess
import sys
import tempfile
import unittest

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "brand_catalog.py"

class TestBrandCatalog(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=ROOT, text=True, capture_output=True)

    def icon_repo(self):
        value = os.environ.get("MIHOMO_ICON_REPO")
        if not value:
            self.skipTest("MIHOMO_ICON_REPO not set; pinned catalog integration requires the workflow checkout")
        return value

    def test_check_accepts_checked_in_pin_and_projection(self):
        result = self.run_cli("--check", "--repo", self.icon_repo())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_check_rejects_digest_drift(self):
        lock = ROOT / "config" / "brand-catalog.lock.json"
        original = lock.read_bytes()
        try:
            data = json.loads(original)
            data["files"]["config/brands.json"]["sha256"] = "0" * 64
            lock.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            result = self.run_cli("--check", "--repo", self.icon_repo())
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("digest", result.stdout + result.stderr)
        finally:
            lock.write_bytes(original)

    def test_check_rejects_projection_drift(self):
        projection = ROOT / "config" / "brand-catalog.projection.json"
        original = projection.read_bytes()
        try:
            projection.write_bytes(original + b" ")
            result = self.run_cli("--check", "--repo", self.icon_repo())
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("projection drift", result.stdout + result.stderr)
        finally:
            projection.write_bytes(original)

    def test_invalid_sha_is_rejected_before_git(self):
        result = self.run_cli("--refresh", "--repo", "/missing", "--commit", "main")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("40", result.stderr + result.stdout)

    def test_refresh_is_explicit_and_deterministic_candidate_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "first"
            second = Path(tmp) / "second"
            for destination in (first, second):
                result = self.run_cli("--refresh", "--repo", self.icon_repo(),
                                      "--commit", "f0f3bc2a44616885682ee5f0e5921540b964e2d8",
                                      "--candidate-dir", str(destination))
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            files = ("brand-catalog.lock.candidate.json", "brand-catalog.projection.candidate.json", "brand-catalog-diff.md")
            self.assertEqual([[(first / n).read_bytes() for n in files]], [[(second / n).read_bytes() for n in files]])

    def make_mutated_commit(self, mutation):
        source = Path(self.icon_repo())
        with tempfile.TemporaryDirectory() as tmp:
            clone = Path(tmp) / "icons"
            subprocess.run(["git", "clone", "--shared", "--no-hardlinks", str(source), str(clone)],
                           check=True, capture_output=True, text=True)
            subprocess.run(["git", "-C", str(clone), "config", "user.name", "Catalog Test"], check=True)
            subprocess.run(["git", "-C", str(clone), "config", "user.email", "catalog-test@example.invalid"], check=True)
            brands_path = clone / "config/brands.json"
            data = json.loads(brands_path.read_text(encoding="utf-8"))
            mutation(data)
            brands_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(clone), "add", "config/brands.json"], check=True)
            subprocess.run(["git", "-C", str(clone), "commit", "-m", "test invalid catalog"],
                           check=True, capture_output=True, text=True)
            revision = subprocess.check_output(["git", "-C", str(clone), "rev-parse", "HEAD"], text=True).strip()
            result = self.run_cli("--refresh", "--repo", str(clone), "--commit", revision,
                                  "--candidate-dir", str(Path(tmp) / "candidate"))
            return result.returncode, result.stdout + result.stderr

    def test_duplicate_brand_id_fails_closed(self):
        def duplicate(data):
            data["brands"].append(dict(data["brands"][0]))
        code, output = self.make_mutated_commit(duplicate)
        self.assertNotEqual(code, 0)
        self.assertIn("duplicate canonical brand id", output)

    def test_missing_icon_resource_fails_closed(self):
        def remove_resource(data):
            target = next(x for x in data["brands"] if x.get("icon_path"))
            target["icon_path"] = "icons/invalid/missing.png"
        code, output = self.make_mutated_commit(remove_resource)
        self.assertNotEqual(code, 0)
        self.assertTrue("icon" in output.lower() or "relationship" in output.lower(), output)

    def test_unknown_commit_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "not-written"
            result = self.run_cli("--refresh", "--repo", self.icon_repo(),
                                  "--commit", "0" * 40, "--candidate-dir", str(destination))
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(destination.exists())

    def test_refresh_failure_does_not_change_last_good_files(self):
        lock = ROOT / "config" / "brand-catalog.lock.json"
        projection = ROOT / "config" / "brand-catalog.projection.json"
        before = [(p.read_bytes() if p.exists() else None) for p in (lock, projection)]
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_cli("--refresh", "--repo", "/missing", "--commit", "f" * 40,
                                  "--candidate-dir", str(Path(tmp) / "candidate"))
        self.assertNotEqual(result.returncode, 0)
        after = [(p.read_bytes() if p.exists() else None) for p in (lock, projection)]
        self.assertEqual(before, after)

if __name__ == "__main__":
    unittest.main()
