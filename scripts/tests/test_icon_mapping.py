"""Business-contract tests for the icon mapping layer.

Tests are named as contracts: each one pins behavior a future consumer
(generator / CI) may rely on — resolution states, conflict rejection,
migration bookkeeping — not an implementation detail.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib import icon_mapping as mapping
from lib.icon_mapping import IconStatus


class TestResolutionContracts(unittest.TestCase):
    """Single-source resolution states, the conflict contract, and no-silent fallback."""

    def setUp(self):
        self.catalog = {
            "CanonicalA": {"id": "CanonicalA", "display_name": "A", "icon_path": "icons/Media/A/A.png"},
            "CanonicalB": {"id": "CanonicalB", "display_name": "B", "icon_path": "icons/Media/B/B.png"},
            "NoAsset": {"id": "NoAsset", "display_name": "No asset"},
        }
        self.tree = {"Media/A/A.png", "Media/B/B.png", "Media/Legacy/Legacy.png"}

    # ---- resolution states ----

    def test_contract_approved_mapping_with_valid_tree_resolves_found(self):
        result = mapping.resolve_icon("TechA", "Renamed display", self.catalog, self.tree,
                                      mapping={"TechA": "CanonicalA"})
        self.assertEqual(
            (result.status, result.technical_id, result.canonical_id, result.icon_path, result.source),
            (IconStatus.FOUND, "TechA", "CanonicalA", "icons/Media/A/A.png", "canonical-map"),
        )

    def test_contract_mapping_without_asset_tree_is_unverified_never_found(self):
        result = mapping.resolve_icon("TechA", "A", self.catalog, None, mapping={"TechA": "CanonicalA"})
        self.assertEqual(result.status, IconStatus.UNVERIFIED)
        self.assertNotEqual(result.status, IconStatus.FOUND)
        self.assertEqual(result.icon_path, "icons/Media/A/A.png")

    def test_contract_mapping_with_missing_asset_is_not_found(self):
        missing_file = mapping.resolve_icon("TechA", "A", self.catalog, set(), mapping={"TechA": "CanonicalA"})
        self.assertEqual(missing_file.status, IconStatus.NOT_FOUND)
        no_path = mapping.resolve_icon("TechA", "A", self.catalog, self.tree, mapping={"TechA": "NoAsset"})
        self.assertEqual(no_path.status, IconStatus.NOT_FOUND)

    def test_contract_mapping_with_unknown_canonical_requires_review(self):
        result = mapping.resolve_icon("TechA", "A", self.catalog, self.tree, mapping={"TechA": "Absent"})
        self.assertEqual((result.status, result.reason), (IconStatus.REQUIRES_REVIEW, "canonical ID is absent"))

    def test_contract_review_entry_requires_review_with_reason(self):
        review = {"TechA": {"reason": "identity pending"}}
        result = mapping.resolve_icon("TechA", "A", self.catalog, self.tree, review=review)
        self.assertEqual((result.status, result.source, result.reason),
                         (IconStatus.REQUIRES_REVIEW, "review-manifest", "identity pending"))

    def test_contract_undecided_brand_requires_review_and_never_guesses(self):
        # No mapping/review/no-icon/override input: the layer must NOT invent an icon.
        result = mapping.resolve_icon("BrandNew", "Brand New", self.catalog, self.tree)
        self.assertEqual((result.status, result.source), (IconStatus.REQUIRES_REVIEW, "unmapped"))

    def test_contract_emoji_policy_skips_and_tolerates_redundant_no_icon(self):
        result = mapping.resolve_icon("Bank", "🏦 Bank", self.catalog, self.tree, emoji_group=True)
        self.assertEqual((result.status, result.source), (IconStatus.SKIPPED, "emoji-policy"))
        redundant = mapping.resolve_icon("Bank", "🏦 Bank", self.catalog, self.tree,
                                         emoji_group=True, no_icon=("Bank",))
        self.assertEqual(redundant.status, IconStatus.SKIPPED)

    def test_contract_no_icon_policy_skips(self):
        result = mapping.resolve_icon("BrandX", "Brand X", self.catalog, self.tree, no_icon=("BrandX",))
        self.assertEqual((result.status, result.source), (IconStatus.SKIPPED, "no-icon-policy"))

    # ---- legacy override states ----

    def test_contract_valid_override_resolves_found_with_legacy_source(self):
        result = mapping.resolve_icon("TechA", "Current Group", self.catalog, self.tree,
                                      overrides={"Current Group": "Media/Legacy/Legacy.png"})
        self.assertEqual((result.status, result.source, result.icon_path),
                         (IconStatus.FOUND, "legacy-override", "icons/Media/Legacy/Legacy.png"))

    def test_contract_stale_override_path_is_detected(self):
        result = mapping.resolve_icon("TechA", "Current Group", self.catalog, set(),
                                      overrides={"Current Group": "Media/Old/Old.png"})
        self.assertEqual(result.status, IconStatus.STALE_OVERRIDE)

    def test_contract_override_without_asset_tree_is_unverified(self):
        result = mapping.resolve_icon("TechA", "Current Group", self.catalog, None,
                                      overrides={"Current Group": "Media/Legacy/Legacy.png"})
        self.assertEqual(result.status, IconStatus.UNVERIFIED)

    # ---- conflict contract: the runtime rejects every ambiguous combination ----

    def test_contract_dual_decisions_are_rejected_not_prioritised(self):
        review = {"TechA": {"reason": "pending"}}
        cases = [
            ({"TechA": "CanonicalA"}, review, (), "Conflicting mapping and review"),
            ({"TechA": "CanonicalA"}, None, ("TechA",), "Conflicting mapping and no-icon"),
            (None, review, ("TechA",), "Conflicting review entry and no-icon"),
        ]
        for mapping_data, review_data, no_icon, expected in cases:
            with self.subTest(expected=expected):
                with self.assertRaisesRegex(ValueError, expected):
                    mapping.resolve_icon("TechA", "A", self.catalog, self.tree,
                                         mapping=mapping_data or {}, review=review_data, no_icon=no_icon)

    def test_contract_override_conflicts_with_every_explicit_decision(self):
        overrides = {"A": "Media/Legacy/Legacy.png"}
        cases = [
            ({"TechA": "CanonicalA"}, None, (), "override conflicts with canonical mapping"),
            (None, {"TechA": {"reason": "pending"}}, (), "override conflicts with review entry"),
            (None, None, ("TechA",), "override conflicts with no-icon policy"),
        ]
        for mapping_data, review_data, no_icon, expected in cases:
            with self.subTest(expected=expected):
                with self.assertRaisesRegex(ValueError, expected):
                    mapping.resolve_icon("TechA", "A", self.catalog, self.tree,
                                         mapping=mapping_data or {}, overrides=overrides,
                                         review=review_data, no_icon=no_icon)

    def test_contract_emoji_policy_rejects_any_explicit_icon_decision(self):
        cases = [
            ({"Bank": "CanonicalA"}, None, None, "cannot carry a canonical mapping"),
            (None, {"Bank": {"reason": "pending"}}, None, "cannot carry a review entry"),
            (None, None, {"🏦 Bank": "Media/Legacy/Legacy.png"}, "cannot carry a legacy override"),
        ]
        for mapping_data, review_data, overrides, expected in cases:
            with self.subTest(expected=expected):
                with self.assertRaisesRegex(ValueError, expected):
                    mapping.resolve_icon("Bank", "🏦 Bank", self.catalog, self.tree,
                                         mapping=mapping_data or {}, overrides=overrides,
                                         review=review_data, emoji_group=True)

    def test_contract_shared_canonical_requires_explicit_approval(self):
        shared = {"TechA": "CanonicalA", "TechB": "CanonicalA"}
        with self.assertRaisesRegex(ValueError, "Unapproved shared canonical mapping"):
            mapping.resolve_icon("TechA", "A", self.catalog, self.tree, mapping=shared)
        approved = mapping.resolve_icon("TechA", "A", self.catalog, self.tree,
                                        mapping=shared, allow_shared=("CanonicalA",))
        self.assertEqual(approved.status, IconStatus.FOUND)

    def test_contract_find_uncovered_counts_only_explicit_decisions(self):
        self.assertEqual(mapping.find_uncovered(["A"], mapping={"A": "CanonicalA"}), [])
        self.assertEqual(mapping.find_uncovered(["A"], review={"A": {"reason": "r"}}), [])
        self.assertEqual(mapping.find_uncovered(["A"], no_icon=("A",)), [])
        # A legacy override is migration debt, not a decision: never a coverage input.
        self.assertEqual(mapping.find_uncovered(["A"]), ["A"])

    def test_contract_pending_brands_stay_in_review_queue(self):
        manifest = json.loads((Path(__file__).parent / "fixtures" / "icon-review.json").read_text(encoding="utf-8"))
        self.assertEqual(set(manifest), {"Podcast"})
        self.assertFalse(set(manifest) & set(mapping.TECHNICAL_TO_CANONICAL))
        for technical_id, entry in manifest.items():
            result = mapping.resolve_icon(technical_id, technical_id, self.catalog, self.tree, review=manifest)
            self.assertEqual((result.status, result.reason), (IconStatus.REQUIRES_REVIEW, entry["reason"]))


class TestValidatorContracts(unittest.TestCase):
    """validate_mapping() mirrors the runtime: same conflicts, reported as errors."""

    def setUp(self):
        self.catalog = {
            "CanonicalA": {"id": "CanonicalA", "display_name": "A", "icon_path": "icons/Media/A/A.png"},
            "CanonicalB": {"id": "CanonicalB", "display_name": "B", "icon_path": "icons/Media/B/B.png"},
            "NoAsset": {"id": "NoAsset", "display_name": "No asset"},
        }
        self.tree = {"Media/A/A.png", "Media/B/B.png", "Media/Legacy/Legacy.png"}

    def test_validator_flags_decision_and_override_conflicts(self):
        errors = mapping.validate_mapping(
            ["TechA", "TechB", "TechC", "TechD"],
            {"TechA": "A", "TechB": "B", "TechC": "C", "TechD": "D"},
            [],
            self.catalog, self.tree,
            mapping={"TechA": "CanonicalA", "TechD": "CanonicalB"},
            overrides={"A": "Media/Legacy/Legacy.png", "B": "Media/Legacy/Legacy.png",
                       "C": "Media/Legacy/Legacy.png"},
            review={"TechB": {"reason": "pending"}},
            no_icon=("TechC", "TechD"),
        )
        self.assertIn("MAPPING_OVERRIDE_CONFLICT: TechA", errors)
        self.assertIn("REVIEW_OVERRIDE_CONFLICT: TechB", errors)
        self.assertIn("NO_ICON_OVERRIDE_CONFLICT: TechC", errors)
        self.assertIn("NO_ICON_POLICY_CONFLICT: TechD", errors)

    def test_validator_flags_emoji_conflicts(self):
        errors = mapping.validate_mapping(
            ["Bank", "Bank2", "Bank3"],
            {"Bank": "🏦 Bank", "Bank2": "🏦 Bank2", "Bank3": "🏦 Bank3"},
            ["Bank", "Bank2", "Bank3"],
            self.catalog, self.tree,
            mapping={"Bank3": "CanonicalA"},
            review={"Bank2": {"reason": "pending"}},
            overrides={"🏦 Bank": "Media/Legacy/Legacy.png", "🏦 Bank2": "Media/Legacy/Legacy.png"},
        )
        self.assertIn("EMOJI_MAPPING_CONFLICT: Bank3", errors)
        self.assertIn("EMOJI_REVIEW_CONFLICT: Bank2", errors)
        self.assertIn("EMOJI_OVERRIDE_CONFLICT: Bank", errors)
        self.assertIn("EMOJI_OVERRIDE_CONFLICT: Bank2", errors)

    def test_validator_rejects_mapping_review_conflict(self):
        errors = mapping.validate_mapping(
            ["TechA"], {"TechA": "A"}, [], self.catalog, self.tree,
            mapping={"TechA": "CanonicalA"}, review={"TechA": {"reason": "pending"}},
        )
        self.assertIn("MAPPING_REVIEW_CONFLICT: TechA", errors)

    def test_validator_requires_full_group_coverage(self):
        errors = mapping.validate_mapping(["TechA", "TechB"], {"TechA": "A"}, [], self.catalog, self.tree)
        self.assertIn("GROUP_OF_MISSING: TechB", errors)

    def test_validator_flags_unknown_ids_missing_assets_and_policy_conflicts(self):
        errors = mapping.validate_mapping(
            ["Known"], {"Known": "Known Group"}, [], self.catalog, set(),
            mapping={"Unknown": "Absent", "Known": "CanonicalA"},
            review={"Known": {"reason": "pending"}}, no_icon=("Known",),
        )
        self.assertIn("TECHNICAL_ID_UNKNOWN: Unknown", errors)
        self.assertIn("CANONICAL_ID_UNKNOWN: Unknown -> Absent", errors)
        self.assertIn("CANONICAL_ICON_FILE_MISSING: Known -> icons/Media/A/A.png", errors)
        self.assertIn("MAPPING_REVIEW_CONFLICT: Known", errors)
        self.assertIn("NO_ICON_POLICY_CONFLICT: Known", errors)

    def test_validator_reports_unverified_without_asset_tree(self):
        errors = mapping.validate_mapping(
            ["TechA", "TechB"], {"TechA": "A", "TechB": "B"}, [], self.catalog, None,
            mapping={"TechA": "CanonicalA"},
            overrides={"B": "Media/Legacy/Legacy.png"},
        )
        self.assertIn("CANONICAL_ICON_UNVERIFIED: TechA -> icons/Media/A/A.png", errors)
        self.assertIn("OVERRIDE_ICON_UNVERIFIED: B -> Media/Legacy/Legacy.png", errors)

    def test_validator_rejects_stale_override_key_and_path(self):
        errors = mapping.validate_mapping(
            ["TechA"], {"TechA": "Current Group"}, [], self.catalog, self.tree,
            overrides={"Old Group": "missing.png"},
        )
        self.assertIn("STALE_OVERRIDE_KEY: Old Group", errors)
        self.assertIn("STALE_OVERRIDE: Old Group -> missing.png", errors)

    def test_validator_rejects_invalid_review_entries(self):
        errors = mapping.validate_mapping(
            ["TechA"], {"TechA": "A"}, [], self.catalog, self.tree,
            review={"Unknown": {}, "TechA": "bad"},  # type: ignore[arg-type]  # malformed on purpose
        )
        self.assertIn("REVIEW_TECHNICAL_ID_UNKNOWN: Unknown", errors)
        self.assertIn("REVIEW_REASON_MISSING: Unknown", errors)
        self.assertIn("REVIEW_ENTRY_INVALID: TechA", errors)

    def test_validator_requires_explicit_approval_for_shared_canonical(self):
        shared = {"TechA": "CanonicalA", "TechB": "CanonicalA"}
        unapproved = mapping.validate_mapping(
            ["TechA", "TechB"], {"TechA": "A", "TechB": "B"}, [], self.catalog, self.tree,
            mapping=shared,
        )
        self.assertIn("SHARED_CANONICAL_NOT_APPROVED: CanonicalA <- TechA, TechB", unapproved)
        approved = mapping.validate_mapping(
            ["TechA", "TechB"], {"TechA": "A", "TechB": "B"}, [], self.catalog, self.tree,
            mapping=shared, allow_shared=("CanonicalA",),
        )
        self.assertNotIn("SHARED_CANONICAL_NOT_APPROVED: CanonicalA <- TechA, TechB", approved)

    def test_validator_flags_unused_shared_approval(self):
        errors = mapping.validate_mapping(
            ["TechA", "TechB"], {"TechA": "A", "TechB": "B"}, [], self.catalog, self.tree,
            mapping={"TechA": "CanonicalA", "TechB": "CanonicalB"},
            allow_shared=("CanonicalA",),
        )
        self.assertIn("SHARED_CANONICAL_NOT_USED: CanonicalA", errors)


class TestCatalogLoaderContracts(unittest.TestCase):
    """load_catalog() is the single Oasisic SSOT adapter; fixtures keep tests portable."""

    def test_catalog_loader_uses_portable_fixture(self):
        fixture = Path(__file__).parent / "fixtures" / "icon-brands.json"
        catalog = mapping.load_catalog(fixture, {"Media/A/A.png"})
        self.assertEqual(set(catalog), {"CanonicalA", "NoAsset"})
        result = mapping.resolve_icon("Netflix", "Netflix", catalog, {"Media/A/A.png"},
                                      mapping={"Netflix": "CanonicalA"})
        self.assertEqual((result.status, result.icon_path), (IconStatus.FOUND, "icons/Media/A/A.png"))

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
