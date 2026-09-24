"""test_type_dedup.py — 同值跨类型去重（DOMAIN ⊂ DOMAIN-SUFFIX）三入口 + 写入兜底 + verify 检查。

背景：同一域名在品牌集里既写 DOMAIN 又写 DOMAIN-SUFFIX（manual 种子撞上游、
v2fly full: 撞 bm7 DOMAIN-SUFFIX），合并路径只按 TYPE+VALUE 去重，跨类型不去重。

修复：
- lib.canonical.drop_domain_covered_by_suffix：同值只留 DOMAIN-SUFFIX（通用实现）。
- parse_loyalsoldier / parse_v2fly / parse_blackmatrix7 三个入口都调用。
- commit_writer.prepare_rules_for_write 写入兜底（manual 保留 / Union 合并路径）。
- verify_rulesets：同值跨类型 → FAIL；被更宽后缀覆盖 → 基础集 FAIL、品牌集默认汇总。
"""
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from lib.canonical import (
    CanonicalRule,
    drop_domain_covered_by_broader_suffix,
    drop_domain_covered_by_suffix,
)
import parse_blackmatrix7 as pb
import parse_loyalsoldier as ls
import parse_v2fly as pv
import verify_rulesets
from commit_writer import prepare_rules_for_write


def _r(rtype, value):
    return CanonicalRule(rule_type=rtype, value=value, param="", source="test")


class TestCanonicalSameValue(unittest.TestCase):
    """同值跨类型：删 DOMAIN、留 DOMAIN-SUFFIX。"""

    def test_same_value_keeps_suffix(self):
        kept, dropped = drop_domain_covered_by_suffix(
            [_r("DOMAIN", "foo.com"), _r("DOMAIN-SUFFIX", "foo.com")])
        self.assertEqual([(r.rule_type, r.value) for r in kept], [("DOMAIN-SUFFIX", "foo.com")])
        self.assertEqual(len(dropped), 1)

    def test_case_insensitive(self):
        kept, dropped = drop_domain_covered_by_suffix(
            [_r("DOMAIN", "Foo.COM"), _r("DOMAIN-SUFFIX", "foo.com")])
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].rule_type, "DOMAIN-SUFFIX")

    def test_parent_child_not_compared(self):
        """itunes.apple.com 与 hls.itunes.apple.com 是不同值，都留。"""
        kept, dropped = drop_domain_covered_by_suffix(
            [_r("DOMAIN", "itunes.apple.com"), _r("DOMAIN-SUFFIX", "hls.itunes.apple.com")])
        self.assertEqual(len(kept), 2)
        self.assertEqual(dropped, [])

    def test_domain_alone_kept(self):
        kept, dropped = drop_domain_covered_by_suffix([_r("DOMAIN", "foo.com")])
        self.assertEqual(len(kept), 1)
        self.assertEqual(dropped, [])

    def test_legacy_alias_still_works(self):
        """parse_loyalsoldier.drop_exact_covered_by_suffix 保留（旧名，返回数量）。"""
        kept, dropped = ls.drop_exact_covered_by_suffix(
            [_r("DOMAIN", "foo.com"), _r("DOMAIN-SUFFIX", "foo.com")])
        self.assertEqual(len(kept), 1)
        self.assertEqual(dropped, 1)


class TestCanonicalBroaderSuffix(unittest.TestCase):
    """被更宽后缀覆盖：多标签父域才删，单标签 TLD 不删。"""

    def test_multi_label_parent_dropped(self):
        kept, dropped = drop_domain_covered_by_broader_suffix(
            [_r("DOMAIN", "3dns.adobe.com"), _r("DOMAIN-SUFFIX", "adobe.com")])
        self.assertEqual(len(kept), 1)
        self.assertEqual([r.value for r in dropped], ["3dns.adobe.com"])

    def test_grandparent_dropped(self):
        kept, dropped = drop_domain_covered_by_broader_suffix(
            [_r("DOMAIN", "a.b.adobe.com"), _r("DOMAIN-SUFFIX", "adobe.com")])
        self.assertEqual([r.value for r in dropped], ["a.b.adobe.com"])

    def test_single_label_tld_exempt(self):
        """DOMAIN-SUFFIX,cn 不得导致 *.cn 的 DOMAIN 被删。"""
        kept, dropped = drop_domain_covered_by_broader_suffix(
            [_r("DOMAIN", "ai.zhaomi.cn"), _r("DOMAIN-SUFFIX", "cn")])
        self.assertEqual(len(kept), 2)
        self.assertEqual(dropped, [])

    def test_uncovered_domain_kept(self):
        kept, dropped = drop_domain_covered_by_broader_suffix(
            [_r("DOMAIN", "keep.example.org"), _r("DOMAIN-SUFFIX", "adobe.com")])
        self.assertEqual(len(kept), 2)


class TestV2flyEntry(unittest.TestCase):
    """parse_v2fly_brand：full: 与裸域名同值时只留 DOMAIN-SUFFIX。"""

    def test_full_vs_bare_dedup(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "steam").write_text(
                "full:foo.example.com\n"
                "foo.example.com\n"
                "full:keep.example.com\n",
                encoding="utf-8",
            )
            with redirect_stdout(io.StringIO()):
                result = pv.parse_v2fly_brand("Steam", tmp)
        types = {(r.rule_type, r.value) for r in result["main"]}
        self.assertIn(("DOMAIN-SUFFIX", "foo.example.com"), types)
        self.assertNotIn(("DOMAIN", "foo.example.com"), types)
        # 没有同值后缀的 full: 保持不动
        self.assertIn(("DOMAIN", "keep.example.com"), types)


class TestBlackmatrix7Entry(unittest.TestCase):
    """parse_blackmatrix7_brand：同值 DOMAIN 被同集 DOMAIN-SUFFIX 覆盖时删除。"""

    def test_same_value_dedup(self):
        with tempfile.TemporaryDirectory() as tmp:
            brand_dir = Path(tmp) / "Steam"
            brand_dir.mkdir()
            (brand_dir / "Steam.yaml").write_text(
                "payload:\n"
                "  - DOMAIN,foo.example.com\n"
                "  - DOMAIN-SUFFIX,foo.example.com\n"
                "  - DOMAIN,keep.example.com\n",
                encoding="utf-8",
            )
            with redirect_stdout(io.StringIO()):
                rules = pb.parse_blackmatrix7_brand("Steam", tmp)
        types = {(r.rule_type, r.value) for r in rules}
        self.assertIn(("DOMAIN-SUFFIX", "foo.example.com"), types)
        self.assertNotIn(("DOMAIN", "foo.example.com"), types)
        self.assertIn(("DOMAIN", "keep.example.com"), types)


class TestLoyalsoldierBasicPolicy(unittest.TestCase):
    """Private 回退后缀语义 + Direct/Proxy 冗余 DOMAIN 清理（严格口径）。"""

    def _basic(self, files):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            for name in (
                "reject.txt", "lancidr.txt", "private.txt", "direct.txt",
                "cncidr.txt", "proxy.txt", "applications.txt",
            ):
                (base / name).write_text(files.get(name, "payload:\n"), encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                return ls.parse_loyalsoldier_basic(str(base))

    def test_private_bare_dotted_is_suffix(self):
        payload = "payload:\n  - 'miwifi.com'\n  - 'routerlogin.com'\n  - '+.local'\n"
        data = self._basic({"private.txt": payload})
        types = {(r.rule_type, r.value) for r in data["Private"]["rules"]}
        self.assertIn(("DOMAIN-SUFFIX", "miwifi.com"), types)
        self.assertIn(("DOMAIN-SUFFIX", "routerlogin.com"), types)
        self.assertNotIn(("DOMAIN", "miwifi.com"), types)

    def test_direct_shadowed_domain_removed(self):
        payload = "payload:\n  - '3dns.adobe.com'\n  - '+.adobe.com'\n"
        data = self._basic({"direct.txt": payload})
        types = {(r.rule_type, r.value) for r in data["Direct"]["rules"]}
        self.assertIn(("DOMAIN-SUFFIX", "adobe.com"), types)
        self.assertNotIn(("DOMAIN", "3dns.adobe.com"), types)

    def test_direct_single_label_tld_kept(self):
        payload = "payload:\n  - 'ai.zhaomi.cn'\n  - '+.cn'\n"
        data = self._basic({"direct.txt": payload})
        types = {(r.rule_type, r.value) for r in data["Direct"]["rules"]}
        self.assertIn(("DOMAIN", "ai.zhaomi.cn"), types)


class TestWritePathDedup(unittest.TestCase):
    """写入兜底：prepare_rules_for_write 同时做完全重复 + 跨类型去重。"""

    def test_cross_type_dropped_on_write(self):
        rules = [
            _r("DOMAIN", "play.google.com"),
            _r("DOMAIN-SUFFIX", "play.google.com"),
            _r("DOMAIN-SUFFIX", "google.com"),
        ]
        out, dropped = prepare_rules_for_write("GooglePlay", rules)
        self.assertEqual(dropped, 1)
        types = {(r.rule_type, r.value) for r in out}
        self.assertNotIn(("DOMAIN", "play.google.com"), types)
        self.assertEqual(len(out), 2)

    def test_exact_dup_still_handled(self):
        rules = [_r("DOMAIN-SUFFIX", "foo.com"), _r("DOMAIN-SUFFIX", "foo.com")]
        out, dropped = prepare_rules_for_write("Netflix", rules)
        self.assertEqual(len(out), 1)
        self.assertEqual(dropped, 0)


def _ruleset(root, brand, payload_lines, header_counts):
    folder = Path(root) / "ruleset" / brand
    folder.mkdir(parents=True)
    header = [
        "# ===========================================",
        f"# Rule Name: {brand}",
        "# Author: Hawaiine",
        "# Updated: 2026-09-24 00:00:00",
    ]
    for kind, count in header_counts.items():
        header.append(f"# {kind}: {count}")
    header += ["# ===========================================", "payload:"]
    body = "\n".join(f"  - {line}" for line in payload_lines)
    (folder / f"{brand}.yaml").write_text("\n".join(header) + "\n" + body + "\n", encoding="utf-8")
    (folder / "README.md").write_text(
        f"# 📦 {brand} 规则集\n\n- **behavior**: classical\n- **策略组**: {brand}\n",
        encoding="utf-8",
    )


class TestVerifyRulesetsDomainChecks(unittest.TestCase):
    """verify_rulesets 新增两类 DOMAIN 检查。"""

    def _check(self, brand, lines, counts):
        original_root = verify_rulesets.ROOT
        with tempfile.TemporaryDirectory() as tmp:
            _ruleset(tmp, brand, lines, counts)
            verify_rulesets.ROOT = Path(tmp)
            try:
                return verify_rulesets.check_brand(brand)
            finally:
                verify_rulesets.ROOT = original_root

    def test_same_value_cross_type_fails(self):
        ok, errors = self._check(
            "Netflix",
            ["DOMAIN,netflix.com", "DOMAIN-SUFFIX,netflix.com"],
            {"DOMAIN": 1, "DOMAIN-SUFFIX": 1},
        )
        self.assertFalse(ok)
        self.assertTrue(any("同值跨类型重复" in e for e in errors), errors)

    def test_broader_suffix_shadow_fails_on_base_set(self):
        ok, errors = self._check(
            "Proxy",
            ["DOMAIN,3dns.adobe.com", "DOMAIN-SUFFIX,adobe.com"],
            {"DOMAIN": 1, "DOMAIN-SUFFIX": 1},
        )
        self.assertFalse(ok)
        self.assertTrue(any("更宽后缀覆盖" in e for e in errors), errors)

    def test_brand_shadow_default_non_fatal_and_strict_fails(self):
        original = verify_rulesets.STRICT_DOMAIN
        verify_rulesets.SHADOW_REPORT.clear()
        try:
            verify_rulesets.STRICT_DOMAIN = False
            ok, errors = self._check(
                "Netflix",
                ["DOMAIN,alt.netflix.com", "DOMAIN-SUFFIX,netflix.com"],
                {"DOMAIN": 1, "DOMAIN-SUFFIX": 1},
            )
            self.assertTrue(ok, errors)
            self.assertEqual(len(verify_rulesets.SHADOW_REPORT), 1)

            verify_rulesets.STRICT_DOMAIN = True
            ok, errors = self._check(
                "Netflix",
                ["DOMAIN,alt.netflix.com", "DOMAIN-SUFFIX,netflix.com"],
                {"DOMAIN": 1, "DOMAIN-SUFFIX": 1},
            )
            self.assertFalse(ok)
            self.assertTrue(any("更宽后缀覆盖" in e for e in errors), errors)
        finally:
            verify_rulesets.STRICT_DOMAIN = original
            verify_rulesets.SHADOW_REPORT.clear()

    def test_private_exempt_from_shadow_check(self):
        ok, errors = self._check(
            "Private",
            ["DOMAIN,foo.local", "DOMAIN-SUFFIX,local"],
            {"DOMAIN": 1, "DOMAIN-SUFFIX": 1},
        )
        self.assertTrue(ok, errors)

    def test_single_label_tld_not_shadowed(self):
        ok, errors = self._check(
            "Direct",
            ["DOMAIN,ai.zhaomi.cn", "DOMAIN-SUFFIX,cn"],
            {"DOMAIN": 1, "DOMAIN-SUFFIX": 1},
        )
        self.assertTrue(ok, errors)


class TestResolveSubsumption(unittest.TestCase):
    """resolve_ownership：父品牌 DOMAIN,x vs 子品牌同值 DOMAIN-SUFFIX 的剥离口径。

    E 清理把子品牌的同值 DOMAIN 去掉后，父品牌从上游重新长回来的 DOMAIN,x
    不再能被「完全同 TYPE+VALUE」剥掉 → 由同值后缀覆盖规则兜住（日更定点）。
    """

    def test_parent_exact_removed_when_child_has_same_suffix(self):
        from resolve_ownership import is_owned_by_child
        r = _r("DOMAIN", "hulu.playback.edge.bamgrid.com")
        self.assertTrue(is_owned_by_child(
            r, {("DOMAIN-SUFFIX", "hulu.playback.edge.bamgrid.com")}))

    def test_parent_suffix_not_removed_by_child_exact(self):
        from resolve_ownership import is_owned_by_child
        r = _r("DOMAIN-SUFFIX", "bamgrid.com")
        self.assertFalse(is_owned_by_child(
            r, {("DOMAIN", "hulu.playback.edge.bamgrid.com")}))

    def test_exact_pair_still_removed(self):
        from resolve_ownership import is_owned_by_child
        r = _r("DOMAIN-SUFFIX", "hulu.com")
        self.assertTrue(is_owned_by_child(r, {("DOMAIN-SUFFIX", "hulu.com")}))

    def test_unrelated_rule_kept(self):
        from resolve_ownership import is_owned_by_child
        r = _r("DOMAIN", "espn.com")
        self.assertFalse(is_owned_by_child(r, {("DOMAIN-SUFFIX", "hulu.com")}))


if __name__ == "__main__":
    unittest.main()