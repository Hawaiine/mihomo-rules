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
        from commit_writer import STRATEGY_GROUP_MAP
        from match_icons import ICON_OVERRIDES, brand_dirs, emoji_skipped_brands

        technical_ids = sorted(brand_dirs())
        group_of = {brand: (STRATEGY_GROUP_MAP.get(brand) or brand) for brand in technical_ids}
        errors = mapping.validate_mapping(
            technical_ids, group_of, emoji_skipped_brands(), catalog, paths,
            overrides=ICON_OVERRIDES,
        )
        self.assertEqual(errors, [], f"unexpected mapping findings: {errors}")

    def test_matcher_output_snapshot_against_current_icon_head(self):
        repo = _icon_repo()
        import match_icons
        original_repo = match_icons.ICON_REPO
        match_icons.ICON_REPO = repo
        try:
            icon_map, missing = match_icons.build_icon_map()
        finally:
            match_icons.ICON_REPO = original_repo
        self.assertEqual((len(icon_map), len(missing)), (143, 0))
        self.assertEqual(missing, [])
        production_ref = "main"
        self.assertEqual(
            icon_map["Podcast"],
            f"https://raw.githubusercontent.com/Hawaiine/Oasisic-Icons/{production_ref}/icons/Media/Xiaoyuzhou/Xiaoyuzhou.png",
        )
        self.assertEqual(
            icon_map["Apple Podcasts"],
            f"https://raw.githubusercontent.com/Hawaiine/Oasisic-Icons/{production_ref}/icons/Apple/ApplePodcasts/ApplePodcasts.png",
        )
        digest = hashlib.sha256(
            json.dumps(icon_map, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
        pinned_revision = "f0f3bc2a44616885682ee5f0e5921540b964e2d8"
        self.assertEqual(match_icons._OASIC_REVISION, pinned_revision)
        scan_source = match_icons.scan_source()
        self.assertIsNotNone(scan_source)
        self.assertIn(pinned_revision, scan_source or "")
        expected_paths = {
            "Cloudflare": "Infrastructure/Cloudflare/Cloudflare.png",
            "Disney": "Disney/DisneyPlus/DisneyPlus.png",
            "HBO": "WarnerBrosDiscovery/HBOMax/HBOMax.png",
            "网易云音乐": "NetEase/NetEaseCloudMusic/NetEaseCloudMusic.png",
            "Podcast": "Media/Xiaoyuzhou/Xiaoyuzhou.png",
            "Apple Podcasts": "Apple/ApplePodcasts/ApplePodcasts.png",
        }
        for strategy_group, relative_path in expected_paths.items():
            with self.subTest(strategy_group=strategy_group):
                self.assertEqual(
                    icon_map[strategy_group],
                    f"https://raw.githubusercontent.com/Hawaiine/Oasisic-Icons/{production_ref}/icons/{relative_path}",
                )
        digest = hashlib.sha256(
            json.dumps(icon_map, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
        self.assertEqual(digest, "1ff07f684f93129310dc55b6da55cb32a86fbdd0722d884585b12d02473bfa4f")



if __name__ == "__main__":
    unittest.main()
