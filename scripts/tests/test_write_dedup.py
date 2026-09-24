"""
test_write_dedup.py - 测试写入路径的完全重复去除（与 verify 口径对齐）

背景：Loyalsoldier 上游同一域名同时写为 'foo' 与 '+.foo' 时，归一化后都变成
DOMAIN-SUFFIX,foo。品牌路径经 merge_and_dedup 去重，但基础集路径此前没有去重，
落盘后触发 verify_rulesets 的「payload 完全重复」导致 CI 失败。

修复：write_ruleset 在生成 YAML 前调用 dedup_exact()。
"""
import sys
import tempfile
import unittest
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from lib.canonical import CanonicalRule, sort_rules
from commit_writer import dedup_exact, generate_yaml
import verify_rulesets


def _rule(value, rtype="DOMAIN-SUFFIX", param=""):
    return CanonicalRule(
        rule_type=rtype,
        value=value,
        param=param,
        source="loyalsoldier",
    )


class TestDedupExact(unittest.TestCase):
    """dedup_exact() 行为测试"""

    def test_duplicates_removed(self):
        """两条完全相同 DOMAIN-SUFFIX → 只剩 1 条"""
        rules = [_rule("foo.com"), _rule("foo.com")]
        out = dedup_exact(rules)
        self.assertEqual(len(out), 1)

    def test_order_preserved_by_sort_rules(self):
        """去重结果仍符合 canonical.sort_rules 顺序"""
        rules = [_rule("bbb.com"), _rule("aaa.com"), _rule("bbb.com")]
        out = dedup_exact(rules)
        self.assertEqual(out, sort_rules(out))

    def test_different_types_kept(self):
        """TYPE 不同不算重复"""
        rules = [_rule("foo.com", "DOMAIN"), _rule("foo.com", "DOMAIN-SUFFIX")]
        self.assertEqual(len(dedup_exact(rules)), 2)

    def test_duplicate_ip_removed(self):
        """IP-CIDR 重复同样去除"""
        rules = [
            CanonicalRule(rule_type="IP-CIDR", value="1.2.3.0/24"),
            CanonicalRule(rule_type="IP-CIDR", value="1.2.3.0/24"),
        ]
        self.assertEqual(len(dedup_exact(rules)), 1)

    def test_applications_keeps_case_variants(self):
        """Applications：tailscale 与 Tailscale 各留一条，完全相同的两行只留一行"""
        rules = [
            CanonicalRule(rule_type="PROCESS-NAME", value="tailscale"),
            CanonicalRule(rule_type="PROCESS-NAME", value="Tailscale"),
            CanonicalRule(rule_type="PROCESS-NAME", value="tailscale"),
        ]
        out = dedup_exact(rules, case_sensitive=True)
        values = [r.value for r in out]
        self.assertEqual(sorted(values), ["Tailscale", "tailscale"])


class TestWriteRulesetDedup(unittest.TestCase):
    """write_ruleset 落盘内容不含完全重复行"""

    def test_generated_yaml_has_no_exact_dup(self):
        """含重复的输入经 dedup 后，生成的 YAML 无重复 payload 行"""
        rules = [_rule("bar.com"), _rule("foo.com"), _rule("foo.com")]
        content = generate_yaml("DupTest", sort_rules(dedup_exact(rules)))
        payload = [ln.strip() for ln in content.split("\n")
                   if ln.strip().startswith("- DOMAIN-SUFFIX,")]
        self.assertEqual(len(payload), len(set(payload)), content)


class TestVerifyStillCatchesDuplicates(unittest.TestCase):
    """证明 verify_rulesets 的完全重复检查未被削弱"""

    def _write_ruleset_dir(self, base):
        brand = Path(base) / "ruleset" / "DupBrand"
        brand.mkdir(parents=True)
        (brand / f"DupBrand.yaml").write_text(
            "# ===========================================\n"
            "# Rule Name: DupBrand\n"
            "# Author: Hawaiine\n"
            "# Updated: 2026-09-24 00:00:00\n"
            "# DOMAIN-SUFFIX: 2\n"
            "# ===========================================\n"
            "payload:\n"
            "  - DOMAIN-SUFFIX,foo.com\n"
            "  - DOMAIN-SUFFIX,foo.com\n",
            encoding="utf-8",
        )
        (brand / "README.md").write_text(
            "# 📦 DupBrand 规则集\n\n## 📊 统计\n\n"
            "- **behavior**: classical\n- **策略组**: DupBrand\n",
            encoding="utf-8",
        )

    def test_verify_fails_on_duplicate(self):
        """构造含完全重复 payload 的规则集，verify 必须判 FAIL"""
        orig_root = verify_rulesets.ROOT
        with tempfile.TemporaryDirectory() as tmp:
            self._write_ruleset_dir(tmp)
            verify_rulesets.ROOT = Path(tmp)
            try:
                ok, errors = verify_rulesets.check_brand("DupBrand")
            finally:
                verify_rulesets.ROOT = orig_root
        self.assertFalse(ok, "verify 不应放行含完全重复 payload 的规则集")
        self.assertTrue(
            any("完全重复" in e for e in errors),
            f"错误信息应包含「完全重复」: {errors}",
        )


if __name__ == "__main__":
    unittest.main()
