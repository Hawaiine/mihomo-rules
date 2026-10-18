#!/usr/bin/env python3
"""变更分类器测试：证明"缺失证据绝不降级为 EXISTING_DATA_ONLY"。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

from classify_change import (EXISTING_DATA_ONLY, GOVERNANCE_CHANGE, MIXED_OR_UNKNOWN,
                             NEW_RULESET, ChangedFile, classify)

BASE = "a" * 40
HEAD = "b" * 40
BASE_PATHS = {"ruleset/Netflix/Netflix.yaml", "ruleset/Google/Google.yaml",
              "configs/Android/config.yaml", "README.md"}
HEAD_PATHS = set(BASE_PATHS)


def run(files, **kw):
    kw.setdefault("base_sha", BASE)
    kw.setdefault("head_sha", HEAD)
    kw.setdefault("base_paths", BASE_PATHS)
    kw.setdefault("head_paths", HEAD_PATHS)
    return classify(files, **kw)


class ClassifyTest(unittest.TestCase):
    def test_existing_data_only(self):
        out = run([ChangedFile("ruleset/Netflix/Netflix.yaml", "modified")])
        self.assertEqual(out.change_class, EXISTING_DATA_ONLY)
        self.assertEqual(out.reasons, ["ALL_CHANGES_ARE_EXISTING_DATA"])

    def test_new_ruleset_proven_by_tree(self):
        out = run([ChangedFile("ruleset/NewBrand/NewBrand.yaml", "added")],
                  base_paths=BASE_PATHS, head_paths=BASE_PATHS | {"ruleset/NewBrand/NewBrand.yaml"})
        self.assertEqual(out.change_class, NEW_RULESET)
        self.assertIn("NEW_RULESET_OBJECT_PROVEN", out.reasons)

    def test_added_file_without_tree_evidence_is_not_data(self):
        out = run([ChangedFile("ruleset/NewBrand/NewBrand.yaml", "added")],
                  base_paths=None, head_paths=None)
        self.assertNotEqual(out.change_class, EXISTING_DATA_ONLY)
        self.assertEqual(out.change_class, MIXED_OR_UNKNOWN)

    def test_modified_object_absent_in_base_is_new(self):
        out = run([ChangedFile("ruleset/Ghost/Ghost.yaml", "modified")])
        self.assertEqual(out.change_class, NEW_RULESET)

    def test_governance_wins_and_keeps_new_ruleset_label(self):
        out = run([ChangedFile("ruleset/Netflix/Netflix.yaml", "modified"),
                   ChangedFile(".github/workflows/daily-sync.yml", "modified"),
                   ChangedFile("ruleset/BrandX/BrandX.yaml", "added")])
        self.assertEqual(out.change_class, GOVERNANCE_CHANGE)
        self.assertIn("ALSO_NEW_RULESET", out.reasons)
        self.assertEqual(out.evidence["new_ruleset"], ["ruleset/BrandX/BrandX.yaml"])

    def test_mixed_data_and_new_ruleset(self):
        out = run([ChangedFile("ruleset/Netflix/Netflix.yaml", "modified"),
                   ChangedFile("ruleset/BrandY/BrandY.yaml", "added")])
        self.assertEqual(out.change_class, NEW_RULESET)

    def test_removed_file_is_unknown(self):
        out = run([ChangedFile("ruleset/Netflix/Netflix.yaml", "removed")])
        self.assertEqual(out.change_class, MIXED_OR_UNKNOWN)

    def test_renamed_file_is_unknown(self):
        out = run([ChangedFile("ruleset/Netflix/Netflix2.yaml", "renamed",
                               previous_filename="ruleset/Netflix/Netflix.yaml")])
        self.assertEqual(out.change_class, MIXED_OR_UNKNOWN)

    def test_out_of_scope_path_is_unknown(self):
        out = run([ChangedFile("scripts/evil.py", "modified")])
        # scripts/ 属于治理面
        self.assertEqual(out.change_class, GOVERNANCE_CHANGE)

    def test_unlisted_extension_is_unknown(self):
        out = run([ChangedFile("ruleset/Netflix/notes.txt", "modified")])
        self.assertEqual(out.change_class, MIXED_OR_UNKNOWN)

    def test_truncated_diff_is_unknown(self):
        out = run([ChangedFile("ruleset/Netflix/Netflix.yaml", "modified")], truncated=True)
        self.assertEqual(out.change_class, MIXED_OR_UNKNOWN)
        self.assertIn("DIFF_TRUNCATED", out.reasons)

    def test_invalid_sha_is_unknown(self):
        out = run([ChangedFile("ruleset/Netflix/Netflix.yaml", "modified")], base_sha="main")
        self.assertEqual(out.change_class, MIXED_OR_UNKNOWN)

    def test_empty_diff_with_different_shas_is_unknown(self):
        out = run([])
        self.assertEqual(out.change_class, MIXED_OR_UNKNOWN)
        self.assertIn("EMPTY_DIFF_BUT_SHA_CHANGED", out.reasons)

    def test_path_traversal_is_unknown(self):
        out = run([ChangedFile("ruleset/../evil.yaml", "modified")])
        self.assertEqual(out.change_class, MIXED_OR_UNKNOWN)

    def test_absolute_path_is_unknown(self):
        out = run([ChangedFile("/etc/passwd", "modified")])
        self.assertEqual(out.change_class, MIXED_OR_UNKNOWN)

    def test_case_collision_is_unknown(self):
        out = run([ChangedFile("ruleset/Netflix/Netflix.yaml", "modified"),
                   ChangedFile("ruleset/Netflix/netflix.yaml", "modified")])
        self.assertEqual(out.change_class, MIXED_OR_UNKNOWN)

    def test_duplicate_path_is_unknown(self):
        out = run([ChangedFile("ruleset/Netflix/Netflix.yaml", "modified"),
                   ChangedFile("ruleset/Netflix/Netflix.yaml", "modified")])
        self.assertEqual(out.change_class, MIXED_OR_UNKNOWN)

    def test_config_change_is_existing_data(self):
        out = run([ChangedFile("configs/Nikki/config.min.yaml", "modified")])
        self.assertEqual(out.change_class, EXISTING_DATA_ONLY)

    def test_readme_change_is_existing_data(self):
        out = run([ChangedFile("README.md", "modified")])
        self.assertEqual(out.change_class, EXISTING_DATA_ONLY)

    def test_governance_doc_is_governance(self):
        out = run([ChangedFile("docs/d-15-state-plane.md", "modified")])
        self.assertEqual(out.change_class, GOVERNANCE_CHANGE)

    def test_governance_single_file(self):
        out = run([ChangedFile(".github/required-checks.json", "modified")])
        self.assertEqual(out.change_class, GOVERNANCE_CHANGE)
        self.assertIn("GOVERNANCE_PATH_CHANGED", out.reasons)


if __name__ == "__main__":
    unittest.main()
