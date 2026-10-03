import json
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

    def test_explicit_mapping_uses_technical_id(self):
        with patch.dict(mapping.TECHNICAL_TO_CANONICAL, {"TechA": "CanonicalA"}, clear=True):
            result = mapping.resolve_icon("TechA", "Changed display", self.catalog, self.tree)
        self.assertEqual((result.status, result.technical_id, result.canonical_id, result.icon_path),
                         (mapping.IconStatus.FOUND, "TechA", "CanonicalA", "icons/Media/A/A.png"))

    def test_unknown_canonical_id_requires_review(self):
        with patch.dict(mapping.TECHNICAL_TO_CANONICAL, {"TechA": "Absent"}, clear=True):
            result = mapping.resolve_icon("TechA", "A", self.catalog, self.tree)
        self.assertEqual(result.status, mapping.IconStatus.REQUIRES_REVIEW)
        self.assertEqual(result.reason, "canonical ID is absent")

    def test_missing_path_or_asset_not_found(self):
        with patch.dict(mapping.TECHNICAL_TO_CANONICAL, {"TechA": "NoAsset"}, clear=True):
            result = mapping.resolve_icon("TechA", "A", self.catalog, self.tree)
        self.assertEqual(result.status, mapping.IconStatus.NOT_FOUND)
        with patch.dict(mapping.TECHNICAL_TO_CANONICAL, {"TechA": "CanonicalA"}, clear=True):
            result = mapping.resolve_icon("TechA", "A", self.catalog, set())
        self.assertEqual(result.status, mapping.IconStatus.NOT_FOUND)

    def test_missing_asset_tree_is_unverified(self):
        with patch.dict(mapping.TECHNICAL_TO_CANONICAL, {"TechA": "CanonicalA"}, clear=True):
            result = mapping.resolve_icon("TechA", "A", self.catalog, None)
        self.assertEqual(result.status, mapping.IconStatus.UNVERIFIED)
        self.assertEqual(result.icon_path, "icons/Media/A/A.png")

    def test_mapping_review_conflict_fails_resolution_and_validation(self):
        review = {"TechA": {"reason": "pending"}}
        with patch.dict(mapping.TECHNICAL_TO_CANONICAL, {"TechA": "CanonicalA"}, clear=True):
            with self.assertRaisesRegex(ValueError, "Conflicting mapping and review"):
                mapping.resolve_icon("TechA", "A", self.catalog, self.tree, review=review)
        errors = mapping.validate_mapping(["TechA"], ["A"], self.catalog, self.tree,
                                          mapping={"TechA": "CanonicalA"}, review=review)
        self.assertIn("MAPPING_REVIEW_CONFLICT: TechA", errors)

    def test_override_path_and_key_are_validated(self):
        errors = mapping.validate_mapping(["TechA"], ["Current Group"], self.catalog, self.tree,
                                          mapping={}, overrides={"Old Group": "missing.png"}, review={})
        self.assertIn("STALE_OVERRIDE_KEY: Old Group", errors)
        self.assertIn("STALE_OVERRIDE: Old Group -> missing.png", errors)
        good = mapping.resolve_icon("TechA", "Current Group", self.catalog, self.tree,
                                    overrides={"Current Group": "Media/Legacy/Legacy.png"})
        self.assertEqual(good.status, mapping.IconStatus.FOUND)
        self.assertEqual(good.source, "legacy-override")
        stale = mapping.resolve_icon("TechA", "Current Group", self.catalog, set(),
                                     overrides={"Current Group": "Media/Old/Old.png"})
        self.assertEqual(stale.status, mapping.IconStatus.STALE_OVERRIDE)
        unavailable = mapping.resolve_icon("TechA", "Current Group", self.catalog, None,
                                           overrides={"Current Group": "Media/Legacy/Legacy.png"})
        self.assertEqual(unavailable.status, mapping.IconStatus.UNVERIFIED)

    def test_emoji_and_explicit_no_icon_are_skipped(self):
        emoji = mapping.resolve_icon("AI", "🤖 AI", self.catalog, self.tree, emoji_group=True)
        no_icon = mapping.resolve_icon("NoIcon", "No Icon", self.catalog, self.tree, no_icon=("NoIcon",))
        self.assertEqual(emoji.status, mapping.IconStatus.SKIPPED)
        self.assertEqual(no_icon.status, mapping.IconStatus.SKIPPED)
        missing = mapping.resolve_icon("NeedsIcon", "Needs Icon", self.catalog, self.tree)
        self.assertEqual(missing.status, mapping.IconStatus.REQUIRES_REVIEW)

    def test_five_migration_candidates_live_in_data_manifest(self):
        manifest = json.loads((Path(__file__).parent / "fixtures" / "icon-review.json").read_text(encoding="utf-8"))
        self.assertEqual(set(manifest), {"AbemaTV", "AppleNews", "Hotstar", "PeacockTV", "Podcast"})
        self.assertFalse(set(manifest) & set(mapping.TECHNICAL_TO_CANONICAL))
        for technical_id, entry in manifest.items():
            result = mapping.resolve_icon(technical_id, technical_id, self.catalog, self.tree, review=manifest)
            self.assertEqual(result.status, mapping.IconStatus.REQUIRES_REVIEW)
            self.assertEqual(result.reason, entry["reason"])

    def test_unapproved_many_to_one_fails_and_review_approval_is_explicit(self):
        shared = {"TechA": "CanonicalA", "TechB": "CanonicalA"}
        unapproved = mapping.validate_mapping(["TechA", "TechB"], ["A", "B"], self.catalog, self.tree,
                                              mapping=shared, review={})
        self.assertIn("SHARED_CANONICAL_NOT_APPROVED: CanonicalA <- TechA, TechB", unapproved)
        with patch.dict(mapping.TECHNICAL_TO_CANONICAL, shared, clear=True):
            with self.assertRaisesRegex(ValueError, "Unapproved shared canonical mapping"):
                mapping.resolve_icon("TechA", "A", self.catalog, self.tree)
        allowed = mapping.validate_mapping(["TechA", "TechB"], ["A", "B"], self.catalog, self.tree,
                                           mapping=shared, review={}, allow_shared=("CanonicalA",))
        self.assertNotIn("SHARED_CANONICAL_NOT_APPROVED: CanonicalA <- TechA, TechB", allowed)
        self.assertFalse(any(error.startswith("MAPPING_REVIEW_CONFLICT") for error in allowed))

    def test_review_manifest_entries_are_validated(self):
        errors = mapping.validate_mapping(["TechA"], ["A"], self.catalog, self.tree,
                                          mapping={}, review={"Unknown": {}, "TechA": "bad"})
        self.assertIn("REVIEW_TECHNICAL_ID_UNKNOWN: Unknown", errors)
        self.assertIn("REVIEW_REASON_MISSING: Unknown", errors)
        self.assertIn("REVIEW_ENTRY_INVALID: TechA", errors)

    def test_validator_covers_unknown_ids_assets_and_policy_conflicts(self):
        errors = mapping.validate_mapping(
            ["Known"], ["Known Group"], self.catalog, set(),
            mapping={"Unknown": "Absent", "Known": "CanonicalA"},
            review={"Known": {"reason": "pending"}}, no_icon=("Known",),
        )
        self.assertIn("TECHNICAL_ID_UNKNOWN: Unknown", errors)
        self.assertIn("CANONICAL_ID_UNKNOWN: Unknown -> Absent", errors)
        self.assertIn("CANONICAL_ICON_FILE_MISSING: Known -> icons/Media/A/A.png", errors)
        self.assertIn("MAPPING_REVIEW_CONFLICT: Known", errors)
        self.assertIn("NO_ICON_POLICY_CONFLICT: Known", errors)

    def test_validator_reports_unverified_without_asset_tree(self):
        errors = mapping.validate_mapping(["TechA"], ["A"], self.catalog, None,
                                          mapping={"TechA": "CanonicalA"},
                                          overrides={"A": "Media/Legacy/Legacy.png"})
        self.assertIn("CANONICAL_ICON_UNVERIFIED: TechA -> icons/Media/A/A.png", errors)
        self.assertIn("OVERRIDE_ICON_UNVERIFIED: A -> Media/Legacy/Legacy.png", errors)

    def test_find_uncovered_reports_brands_without_explicit_decisions(self):
        uncovered = mapping.find_uncovered(
            ["A", "B", "C", "D"],
            mapping={"A": "CanonicalA"},
            review={"B": {"reason": "pending"}},
            no_icon=("C",),
        )
        self.assertEqual(uncovered, ["D"])

    def test_catalog_loader_uses_portable_fixture(self):
        fixture = Path(__file__).parent / "fixtures" / "icon-brands.json"
        catalog = mapping.load_catalog(fixture, self.tree)
        self.assertEqual(set(catalog), {"CanonicalA", "NoAsset"})
        with patch.dict(mapping.TECHNICAL_TO_CANONICAL, {"Netflix": "CanonicalA"}, clear=True):
            result = mapping.resolve_icon("Netflix", "Netflix", catalog, self.tree)
        self.assertEqual(result.status, mapping.IconStatus.FOUND)
        self.assertEqual(result.icon_path, "icons/Media/A/A.png")

    def test_catalog_loader_rejects_bad_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "brands.json"

            def write(payload):
                path.write_text(json.dumps(payload), encoding="utf-8")

            write({"brands": [
                {"id": "A", "icon_path": "icons/a.png"}, {"id": "A", "icon_path": "icons/a.png"},
            ]})
            with self.assertRaisesRegex(ValueError, "Duplicate Oasisic canonical ID"):
                mapping.load_catalog(path)
            write({"brands": [{"id": "A", "icon_path": "Media/a.png"}]})
            with self.assertRaisesRegex(ValueError, "Invalid icon_path"):
                mapping.load_catalog(path)
            write({"brands": [{"id": "A", "icon_path": "icons/Old/A.png"}]})
            with self.assertRaisesRegex(ValueError, "Stale icon_path"):
                mapping.load_catalog(path, set())
            write({"oops": True})
            with self.assertRaisesRegex(ValueError, "brands list"):
                mapping.load_catalog(path)


if __name__ == "__main__":
    unittest.main()
