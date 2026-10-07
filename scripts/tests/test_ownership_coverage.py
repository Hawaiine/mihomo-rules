"""
test_ownership_coverage.py — resolve_ownership 的覆盖形态与「未覆盖形态」报告

resolve_ownership 只剥离两种形态：
  1. (type, value) 完全相同
  2. 父 DOMAIN,x + 子 DOMAIN-SUFFIX,x

父 DOMAIN-SUFFIX,x + 子 DOMAIN,x **不得**剥离：父后缀仍覆盖 x 的子域，
剥离会造成覆盖丢失（例如 Apple 的 DOMAIN-SUFFIX,podcasts.apple.com 覆盖
amp-api.podcasts.apple.com）。该形态必须被
find_parent_suffix_child_domain_overlaps 报告出来，否则「0 对重叠」
会被误读为「不存在重叠」。
"""
import sys
import unittest
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import resolve_ownership as ro  # noqa: E402
from lib.canonical import CanonicalRule  # noqa: E402


def _rule(rtype, value):
    return CanonicalRule(rtype, value, "", "test")


class IsOwnedByChildTest(unittest.TestCase):
    def test_exact_same_type_and_value_is_owned(self):
        self.assertTrue(ro.is_owned_by_child(_rule("DOMAIN", "x.com"), {("DOMAIN", "x.com")}))

    def test_parent_domain_owned_when_child_holds_suffix(self):
        self.assertTrue(ro.is_owned_by_child(_rule("DOMAIN", "x.com"), {("DOMAIN-SUFFIX", "x.com")}))

    def test_parent_suffix_is_not_stripped_when_child_holds_exact_domain(self):
        """父 DOMAIN-SUFFIX 覆盖子域，剥离会丢覆盖 → 必须不剥离。"""
        self.assertFalse(ro.is_owned_by_child(_rule("DOMAIN-SUFFIX", "x.com"), {("DOMAIN", "x.com")}))

    def test_unrelated_rule_is_not_owned(self):
        self.assertFalse(ro.is_owned_by_child(_rule("DOMAIN", "x.com"), {("DOMAIN", "y.com")}))


class UncoveredShapeReportTest(unittest.TestCase):
    """真实仓库：父 SUFFIX + 子 DOMAIN 形态必须被报告（集合相等，非仅计数）。"""

    @classmethod
    def setUpClass(cls):
        cls.brands = sorted(
            d.name for d in (ro.ROOT / "ruleset").iterdir()
            if d.is_dir() and (d / f"{d.name}.yaml").exists()
        )

    def test_real_repo_overlaps_are_reported_as_a_set(self):
        overlaps = ro.find_parent_suffix_child_domain_overlaps(self.brands)
        self.assertEqual(
            set(overlaps),
            {
                ("AppStore", "Apple", "itunes.apple.com"),
                ("ApplePodcasts", "Apple", "podcasts.apple.com"),
                ("PrimeVideo", "Amazon", "avodmp4s3ww-a.akamaihd.net"),
                ("Xbox", "Microsoft", "img-prod-cms-rt-microsoft-com.akamaized.net"),
            },
        )

    def test_apple_podcasts_overlap_keeps_parent_subdomain_coverage(self):
        """ApplePodcasts 案例：父 Apple 的 DOMAIN-SUFFIX 必须保留（否则丢子域覆盖）。"""
        apple = (ro.ROOT / "ruleset" / "Apple" / "Apple.yaml").read_text(encoding="utf-8")
        self.assertIn("DOMAIN-SUFFIX,podcasts.apple.com", apple)

    def test_reported_overlap_child_ruleset_precedes_parent(self):
        """报告出来的重叠由「子品牌 RULE-SET 前置」兜底：子必须先于父。"""
        import generate_config as gc  # noqa: PLC0415

        brands = [b for b in self.brands if b not in ro.BASE]
        ordered = gc.sort_brands(brands, {})
        pos = {b: i for i, b in enumerate(ordered)}
        for child, parent, _value in ro.find_parent_suffix_child_domain_overlaps(self.brands):
            with self.subTest(child=child, parent=parent):
                self.assertLess(pos[child], pos[parent])


if __name__ == "__main__":
    unittest.main()
