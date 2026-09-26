"""
test_verify_hardening.py — 校验强化后的不变量测试

覆盖三处历史盲区，确保它们不会退化：
1. canonical.dedup_rules：同 TYPE+VALUE 不同 param 不静默丢弃（保留带参、顺序无关、
   多 param 歧义上报）。
2. verify_rulesets：解析不了的行 / 不支持的规则类型 / 同值多 param 必须 FAIL，
   不允许「不计数 → 恰好数字对上 → PASS」。
3. verify_configs：check_full_comment_order 条数不一致必须 FAIL（zip 截断不得掩盖）、
   emoji 前缀组不得带 icon、出站目标引号检查不依赖具体组名白名单。
"""
import sys
import tempfile
import unittest
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from lib.canonical import CanonicalRule, dedup_rules, sort_rules
from commit_writer import dedup_exact
import verify_rulesets
import verify_configs


def _r(value, rtype="IP-CIDR", param=""):
    return CanonicalRule(rule_type=rtype, value=value, param=param)


class TestDedupRulesParam(unittest.TestCase):
    """canonical.dedup_rules 的 param 语义"""

    def test_param_variant_wins_over_plain(self):
        """同 TYPE+VALUE：带 param 的保留，无参的丢弃"""
        kept, dropped, conflicts = dedup_rules([
            _r("1.2.3.0/24"),
            _r("1.2.3.0/24", param="no-resolve"),
        ])
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].param, "no-resolve")
        self.assertEqual(len(dropped), 1)
        self.assertEqual(conflicts, [])

    def test_order_independent(self):
        """输入顺序颠倒，结果一致（旧实现是「保留首次出现」，顺序相关）"""
        a = dedup_rules([_r("1.2.3.0/24"), _r("1.2.3.0/24", param="no-resolve")])[0]
        b = dedup_rules([_r("1.2.3.0/24", param="no-resolve"), _r("1.2.3.0/24")])[0]
        self.assertEqual([(r.rule_type, r.value, r.param) for r in a],
                         [(r.rule_type, r.value, r.param) for r in b])
        self.assertEqual(a[0].param, "no-resolve")

    def test_identical_param_deduped(self):
        """同 TYPE+VALUE+PARAM 完全重复 → 只留一条"""
        kept, dropped, _ = dedup_rules([
            _r("1.2.3.0/24", param="no-resolve"),
            _r("1.2.3.0/24", param="no-resolve"),
        ])
        self.assertEqual(len(kept), 1)
        self.assertEqual(len(dropped), 1)

    def test_multiple_distinct_params_reported_not_dropped(self):
        """多个不同 param 属歧义：全部保留并上报冲突，不自行裁决"""
        kept, _dropped, conflicts = dedup_rules([
            _r("1.2.3.0/24", param="no-resolve"),
            _r("1.2.3.0/24", param="src"),
        ])
        self.assertEqual(len(kept), 2)
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0][1], ["no-resolve", "src"])

    def test_dedup_exact_still_returns_sorted(self):
        """dedup_exact 仍返回排序结果且不含重复"""
        out = dedup_exact([_r("9.9.9.0/24", "IP-CIDR", "no-resolve"), _r("9.9.9.0/24")])
        self.assertEqual(len(out), 1)
        self.assertEqual(out, sort_rules(out))
        self.assertEqual(out[0].param, "no-resolve")


class TestVerifyRulesetsHardening(unittest.TestCase):
    """verify_rulesets 必须对无法解析/不支持/多 param 的行 FAIL"""

    def _write(self, base, payload_lines, header_counts=None):
        brand = Path(base) / "ruleset" / "HardBrand"
        brand.mkdir(parents=True)
        counts = header_counts or {}
        header = "".join(f"# {t}: {counts.get(t, 0)}\n" for t in (
            "DOMAIN-KEYWORD", "DOMAIN-REGEX", "DOMAIN", "DOMAIN-SUFFIX",
            "IP-CIDR", "IP-CIDR6", "IP-ASN", "PROCESS-NAME"))
        (brand / "HardBrand.yaml").write_text(
            "# ===========================================\n"
            "# Rule Name: HardBrand\n"
            "# Author: Hawaiine\n"
            "# Updated: 2026-09-26 00:00:00\n"
            + header +
            "# ===========================================\n"
            "payload:\n" + payload_lines,
            encoding="utf-8",
        )
        (brand / "README.md").write_text(
            "# 📦 HardBrand 规则集\n\n## 📊 统计\n\n"
            "- **behavior**: classical\n- **策略组**: HardBrand\n",
            encoding="utf-8",
        )

    def _check(self, payload_lines, header_counts=None):
        orig = verify_rulesets.ROOT
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, payload_lines, header_counts)
            verify_rulesets.ROOT = Path(tmp)
            try:
                return verify_rulesets.check_brand("HardBrand")
            finally:
                verify_rulesets.ROOT = orig

    def test_invalid_payload_line_fails(self):
        """无法解析的 payload 行 → INVALID_PAYLOAD_LINE"""
        ok, errors = self._check("  - 这不是一条规则\n")
        self.assertFalse(ok, f"应 FAIL: {errors}")
        self.assertTrue(any("INVALID_PAYLOAD_LINE" in e for e in errors), errors)

    def test_unsupported_rule_type_fails(self):
        """8 种类型之外的规则类型 → UNSUPPORTED_RULE_TYPE"""
        ok, errors = self._check("  - GEOIP,CN\n")
        self.assertFalse(ok, f"应 FAIL: {errors}")
        self.assertTrue(any("UNSUPPORTED_RULE_TYPE" in e for e in errors), errors)

    def test_param_ambiguity_fails(self):
        """同 TYPE+VALUE 两个不同 param → 歧义 FAIL"""
        ok, errors = self._check(
            "  - IP-CIDR,1.2.3.0/24,no-resolve\n"
            "  - IP-CIDR,1.2.3.0/24,src\n",
            {"IP-CIDR": 2},
        )
        self.assertFalse(ok, f"应 FAIL: {errors}")
        self.assertTrue(any("多 param 歧义" in e for e in errors), errors)

    def test_plain_plus_param_variant_fails_as_redundant(self):
        """一条带 param + 一条不带 → 冗余重复，必须 FAIL（写入路径会归一为带参版本）"""
        ok, errors = self._check(
            "  - IP-CIDR,1.2.3.0/24\n"
            "  - IP-CIDR,1.2.3.0/24,no-resolve\n",
            {"IP-CIDR": 2},
        )
        self.assertFalse(ok, f"应 FAIL: {errors}")
        self.assertTrue(any("带参/无参重复" in e for e in errors), errors)


class TestVerifyConfigsHardening(unittest.TestCase):
    """verify_configs 的条数断言 / emoji icon / 引号检查"""

    def test_comment_order_length_mismatch_fails(self):
        """注释 RULE-SET 少于品牌组 → 必须 FAIL（旧实现 zip 截断后 PASS）"""
        lines = [
            'proxy-groups:\n',
            '  - name: "Netflix"\n',
            '  - name: "Spotify"\n',
            'rules:\n',
            '                                                    # - RULE-SET,Netflix,Netflix\n',
        ]
        orig = verify_configs.SYSTEM_GROUPS
        verify_configs.SYSTEM_GROUPS = []
        try:
            ok = verify_configs.check_full_comment_order(lines, 'x_full')
        finally:
            verify_configs.SYSTEM_GROUPS = orig
        self.assertFalse(ok, "注释行数少于品牌组数时必须 FAIL")

    def test_comment_order_base_comment_ignored(self):
        """基础集注释（Applications）不计入品牌注释段"""
        lines = [
            'rules:\n',
            '                                                    # - RULE-SET,Applications,🎯 全球直连\n',
            '                                                    # - RULE-SET,Netflix,Netflix\n',
        ]
        orig = verify_configs.SYSTEM_GROUPS
        verify_configs.SYSTEM_GROUPS = []
        try:
            ok = verify_configs.check_full_comment_order(lines, 'x_full')
        finally:
            verify_configs.SYSTEM_GROUPS = orig
        self.assertFalse(ok, "只有基础集注释时不应被当作品牌注释段通过")

    def test_emoji_group_with_icon_fails(self):
        """emoji 前缀组带 icon → FAIL"""
        lines = [
            '  - name: "🤖 General AI"\n',
            '    icon: "https://raw.githubusercontent.com/Hawaiine/Oasisic-Icons/main/icons/AI/GeneralAI/GeneralAI.png"\n',
        ]
        self.assertFalse(verify_configs.check_emoji_groups_have_no_icon(lines, 'x'))

    def test_non_emoji_group_with_icon_passes(self):
        lines = [
            '  - name: "Netflix"\n',
            '    icon: "https://raw.githubusercontent.com/Hawaiine/Oasisic-Icons/main/icons/Media/Netflix/Netflix.png"\n',
        ]
        self.assertTrue(verify_configs.check_emoji_groups_have_no_icon(lines, 'x'))

    def test_quoted_strategy_any_group_detected(self):
        """任意被引号包住的出站目标都算违规，不依赖具体组名"""
        bad = ['rules:\n', '  - RULE-SET,Direct,"♻️ 自动选择"\n']
        good = ['rules:\n', '  - RULE-SET,Direct,♻️ 自动选择\n']
        self.assertFalse(verify_configs.check_rules_no_quoted_strategy(bad, 'x'))
        self.assertTrue(verify_configs.check_rules_no_quoted_strategy(good, 'x'))


if __name__ == '__main__':
    unittest.main()
