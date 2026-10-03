"""Cross-repo integration tests for the icon mapping layer.

These tests pin the CURRENT combined state of mihomo-rules + a local Oasisic-Icons
checkout. They are INTEGRATION tests, not unit tests:

- They run only when MIHOMO_ICON_REPO points at an Oasisic-Icons checkout;
  otherwise they skip with an explicit message (hermetic CI behavior).
- They intentionally fail when the icon repository or the matcher output changes,
  so cross-repo drift is reviewed consciously instead of passing silently.
"""
import hashlib
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib import icon_mapping as mapping


def _icon_repo() -> Path:
    value = os.environ.get("MIHOMO_ICON_REPO")
    if not value:
        raise unittest.SkipTest("MIHOMO_ICON_REPO is not set: cross-repo integration test skipped")
    repo = Path(value)
    if not (repo / "config" / "brands.json").is_file():
        raise unittest.SkipTest(f"MIHOMO_ICON_REPO={value} does not contain config/brands.json: skipped")
    return repo


class TestIconMappingIntegration(unittest.TestCase):
    def test_legacy_overrides_against_current_icon_head(self):
        repo = _icon_repo()
        # Asset paths are relative to the icons/ directory, matching match_icons conventions.
        paths = {str(p.relative_to(repo / "icons")) for p in (repo / "icons").rglob("*.png")}
        catalog = mapping.load_catalog(repo / "config" / "brands.json", paths)
        root = Path(__file__).resolve().parents[2]
        technical_ids = [p.parent.name for p in (root / "ruleset").glob("*/*.yaml")]
        from commit_writer import STRATEGY_GROUP_MAP
        from match_icons import ICON_OVERRIDES
        strategy_groups = sorted({STRATEGY_GROUP_MAP.get(brand, brand) for brand in technical_ids})
        errors = mapping.validate_mapping(
            technical_ids, strategy_groups, catalog, paths, overrides=ICON_OVERRIDES,
        )
        # Only stale-override findings are expected today; new findings must be reviewed here.
        self.assertEqual(
            [error for error in errors if not error.startswith("STALE_OVERRIDE")], [],
            f"unexpected validation findings: {errors}",
        )
        stale_labels = {
            error.split(": ", 1)[1].split(" -> ", 1)[0]
            for error in errors if error.startswith("STALE_OVERRIDE")
        }
        self.assertEqual(stale_labels, {"Disney", "HBO", "网易云音乐", "Cloudflare"})

    def test_matcher_output_snapshot_against_current_icon_head(self):
        repo = _icon_repo()
        import match_icons
        original_repo = match_icons.ICON_REPO
        match_icons.ICON_REPO = repo
        try:
            icon_map, missing = match_icons.build_icon_map()
        finally:
            match_icons.ICON_REPO = original_repo
        self.assertEqual((len(icon_map), len(missing)), (137, 5))
        digest = hashlib.sha256(
            json.dumps(icon_map, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
        self.assertEqual(digest, "4e65190b89dc167f43dd6829f5542d7e87c1c3f40ae13aa7a802e2aaa59fd53b")


if __name__ == "__main__":
    unittest.main()
