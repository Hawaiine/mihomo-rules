import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib import icon_mapping as mapping


class TestIconMapping(unittest.TestCase):
    def setUp(self):
        self.catalog = {
            "CanonicalA": {"id": "CanonicalA", "display_name": "A", "icon_path": "icons/Media/A/A.png"},
            "NoAsset": {"id": "NoAsset", "display_name": "No asset"},
        }
        self.tree = {"Media/A/A.png", "Media/Legacy/Legacy.png"}
        self.overrides = {"Legacy Group": "Media/Legacy/Legacy.png"}

    def test_technical_id_resolves_to_current_canonical_icon(self):
        with patch.dict(mapping.TECHNICAL_TO_CANONICAL, {"TechA": "CanonicalA"}, clear=True):
            result = mapping.resolve_icon("TechA", "Renamed display", self.catalog, self.tree, {})
        self.assertEqual(result.status, mapping.IconStatus.FOUND)
        self.assertEqual(result.canonical_id, "CanonicalA")
        self.assertEqual(result.icon_path, "icons/Media/A/A.png")
        self.assertEqual(result.source, "canonical-map")

    def test_missing_canonical_id_requires_review(self):
        with patch.dict(mapping.TECHNICAL_TO_CANONICAL, {"TechA": "Absent"}, clear=True):
            result = mapping.resolve_icon("TechA", "A", self.catalog, self.tree, {})
        self.assertEqual(result.status, mapping.IconStatus.REQUIRES_REVIEW)

    def test_missing_canonical_path_is_not_found(self):
        with patch.dict(mapping.TECHNICAL_TO_CANONICAL, {"TechA": "NoAsset"}, clear=True):
            result = mapping.resolve_icon("TechA", "A", self.catalog, self.tree, {})
        self.assertEqual(result.status, mapping.IconStatus.NOT_FOUND)

    def test_valid_legacy_override_is_identified(self):
        result = mapping.resolve_icon("TechLegacy", "Legacy Group", self.catalog, self.tree, self.overrides)
        self.assertEqual(result.status, mapping.IconStatus.FOUND)
        self.assertEqual(result.source, "legacy-override")
        self.assertEqual(result.icon_path, "icons/Media/Legacy/Legacy.png")

    def test_stale_legacy_override_is_not_found(self):
        result = mapping.resolve_icon("TechLegacy", "Legacy Group", self.catalog, set(), self.overrides)
        self.assertEqual(result.status, mapping.IconStatus.STALE_OVERRIDE)

    def test_emoji_policy_is_explicitly_skipped(self):
        result = mapping.resolve_icon("GeneralAI", "🤖 General AI", self.catalog, self.tree, {}, emoji_group=True)
        self.assertEqual(result.status, mapping.IconStatus.SKIPPED)

    def test_five_unresolved_brands_require_review(self):
        for technical_id in ("AbemaTV", "AppleNews", "Hotstar", "PeacockTV", "Podcast"):
            with self.subTest(technical_id=technical_id):
                result = mapping.resolve_icon(technical_id, technical_id, self.catalog, self.tree, {})
                self.assertEqual(result.status, mapping.IconStatus.REQUIRES_REVIEW)

    def test_catalog_rejects_duplicate_ids_and_stale_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "brands.json"
            path.write_text(json.dumps({"brands": [
                {"id": "A", "icon_path": "icons/Media/A/A.png"},
                {"id": "A", "icon_path": "icons/Media/A/A.png"},
            ]}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Duplicate Oasisic canonical ID"):
                mapping.load_catalog(path)
            path.write_text(json.dumps({"brands": [{"id": "A", "icon_path": "icons/Old/A.png"}]}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Stale icon_path"):
                mapping.load_catalog(path, set())

    def test_validator_reports_bad_ids_missing_assets_duplicates_and_stale_overrides(self):
        errors = mapping.validate_mapping(
            ["Known"],
            {"CanonicalA": self.catalog["CanonicalA"], "NoAsset": self.catalog["NoAsset"]},
            set(),
            mapping={"Unknown": "Absent", "Known": "CanonicalA", "Alias": "CanonicalA", "Missing": "NoAsset"},
            overrides={"Legacy Group": "old/path.png"},
            review={},
        )
        self.assertTrue(any(item.startswith("TECHNICAL_ID_UNKNOWN") for item in errors))
        self.assertTrue(any(item.startswith("CANONICAL_ID_UNKNOWN") for item in errors))
        self.assertTrue(any(item.startswith("DUPLICATE_MAPPING") for item in errors))
        self.assertTrue(any(item.startswith("CANONICAL_ICON_FILE_MISSING") for item in errors))
        self.assertTrue(any(item.startswith("CANONICAL_ICON_PATH_MISSING") for item in errors))
        self.assertTrue(any(item.startswith("STALE_OVERRIDE") for item in errors))

    def test_current_registry_validates_known_seed_mappings_and_finds_three_stale_overrides(self):
        root = Path(__file__).resolve().parents[2]
        icons_repo = Path(os.environ.get("MIHOMO_ICON_REPO", "/opt/data/cache/scratch/Oasisic-Icons-readonly"))
        registry = mapping.load_catalog(icons_repo / "config" / "brands.json")
        paths = set()
        for dirpath, _, files in os.walk(icons_repo / "icons"):
            for filename in files:
                if filename.endswith(".png"):
                    paths.add(os.path.relpath(os.path.join(dirpath, filename), icons_repo / "icons"))
        technical_ids = [p.parent.name for p in (root / "ruleset").glob("*/*.yaml")]
        actual_mapping = {"Netflix": "Netflix"}
        with patch.dict(mapping.TECHNICAL_TO_CANONICAL, actual_mapping, clear=True):
            errors = mapping.validate_mapping(
                technical_ids, registry, paths, mapping=actual_mapping, review={},
                overrides={
                    "Disney": "Media/DisneyPlus/DisneyPlus.png",
                    "HBO": "Media/HBOMAX/HBOMAX.png",
                    "网易云音乐": "Music/NetEaseCloudMusic/NetEaseCloudMusic.png",
                },
            )
        self.assertEqual(errors, [
            "STALE_OVERRIDE: Disney -> Media/DisneyPlus/DisneyPlus.png",
            "STALE_OVERRIDE: HBO -> Media/HBOMAX/HBOMAX.png",
            "STALE_OVERRIDE: 网易云音乐 -> Music/NetEaseCloudMusic/NetEaseCloudMusic.png",
        ])

    def test_current_stable_output_snapshot_is_known(self):
        import hashlib
        import match_icons
        match_icons.ICON_REPO = Path("/opt/data/cache/scratch/Oasisic-Icons-readonly")
        icon_map, missing = match_icons.build_icon_map()
        self.assertEqual((len(icon_map), len(missing)), (137, 5))
        digest = hashlib.sha256(json.dumps(icon_map, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        self.assertEqual(digest, "4e65190b89dc167f43dd6829f5542d7e87c1c3f40ae13aa7a802e2aaa59fd53b")
