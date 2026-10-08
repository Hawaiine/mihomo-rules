import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MATCHER = ROOT / "scripts" / "match_icons.py"
_MANIFEST = ROOT / "scripts" / "config_contract" / "oasisic_revision.json"


def _manifest_revision() -> str:
    """pin 的单一来源：config_contract/oasisic_revision.json（discovery/validation）。"""
    return json.loads(_MANIFEST.read_text(encoding="utf-8"))["revision"]


class IconRevisionAuthorityTest(unittest.TestCase):
    def test_missing_manifest_fails_even_when_environment_revision_is_set(self):
        with tempfile.TemporaryDirectory(prefix="missing-oasisic-manifest-") as temp:
            isolated = Path(temp) / "repo"
            scripts = isolated / "scripts"
            scripts.mkdir(parents=True)
            shutil.copy2(MATCHER, scripts / "match_icons.py")
            env = {
                **os.environ,
                "OASIC_REVISION": _manifest_revision(),
            }
            result = subprocess.run(
                [sys.executable, str(scripts / "match_icons.py")],
                cwd=isolated,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("缺少固定 Oasisic revision", result.stderr)
            self.assertNotIn("No module named 'commit_writer'", result.stderr)


if __name__ == "__main__":
    unittest.main()
