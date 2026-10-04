import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MATCHER = ROOT / "scripts" / "match_icons.py"


class IconRevisionAuthorityTest(unittest.TestCase):
    def test_missing_manifest_fails_even_when_environment_revision_is_set(self):
        with tempfile.TemporaryDirectory(prefix="missing-oasisic-manifest-") as temp:
            isolated = Path(temp) / "repo"
            scripts = isolated / "scripts"
            scripts.mkdir(parents=True)
            shutil.copy2(MATCHER, scripts / "match_icons.py")
            env = {
                **os.environ,
                "OASIC_REVISION": "f0f3bc2a44616885682ee5f0e5921540b964e2d8",
                "MIHOMO_ICON_REPO": "/opt/data/Oasisic-Icons",
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
