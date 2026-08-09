"""
test_canonical.py — 测试 lib/canonical.py 核心函数
"""
import sys
import os
import unittest

# 确保能 import scripts 下的模块
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from lib.canonical import (
    normalize_value,
    normalize_rule,
    dedup_key,
    parse_rule_line,
    sort_rules,
    count_by_type,
    CanonicalRule,
    TYPES_ORDER,
)


class TestNormalizeValue(unittest.TestCase):
    """normalize_value() 测试"""

    def test_domain_lowercase(self):
        """DOMAIN 类型应小写"""
        self.assertEqual(normalize_value("Google.com"), "google.com")

    def test_domain_suffix_lowercase(self):
        """DOMAIN-SUFFIX 类型应小写"""
        self.assertEqual(normalize_value("Example.COM"), "example.com")

    def test_process_name_case_preserved(self):
        """PROCESS-NAME 类型应保留大小写"""
        self.assertEqual(normalize_value("Google Chrome", "PROCESS-NAME"), "Google Chrome")

    def test_process_path_case_preserved(self):
        """PROCESS-PATH 类型应保留大小写"""
        self.assertEqual(normalize_value("/usr/bin/Google Chrome", "PROCESS-PATH"), "/usr/bin/Google Chrome")

    def test_trailing_dot_removed(self):
        """尾部点号应去除"""
        self.assertEqual(normalize_value("google.com."), "google.com")

    def test_multiple_trailing_dots_removed(self):
        """多个尾部点号应全部去除"""
        self.assertEqual(normalize_value("google.com.."), "google.com")

    def test_trailing_dot_process_preserved(self):
        """PROCESS 类型也去除尾部点号（但保留大小写）"""
        self.assertEqual(normalize_value("App.exe.", "PROCESS-NAME"), "App.exe")

    def test_empty_string(self):
        """空字符串应返回空"""
        self.assertEqual(normalize_value(""), "")

    def test_whitespace_stripped(self):
        """首尾空格应去除"""
        self.assertEqual(normalize_value("  google.com  "), "google.com")


class TestNormalizeRule(unittest.TestCase):
    """normalize_rule() 测试"""

    def test_basic_rule(self):
        """基本规则创建"""
        r = normalize_rule("domain", "Google.com")
        self.assertEqual(r.rule_type, "DOMAIN")
        self.assertEqual(r.value, "google.com")
        self.assertEqual(r.param, "")
        self.assertEqual(r.source, "")

    def test_rule_with_param(self):
        """带参数的规则"""
        r = normalize_rule("IP-CIDR", "1.1.1.1/32", "no-resolve")
        self.assertEqual(r.rule_type, "IP-CIDR")
        self.assertEqual(r.value, "1.1.1.1/32")
        self.assertEqual(r.param, "no-resolve")

    def test_rule_with_source(self):
        """带来源标识的规则"""
        r = normalize_rule("DOMAIN-SUFFIX", "example.com", source="v2fly")
        self.assertEqual(r.source, "v2fly")

    def test_process_type_case_preserved(self):
        """PROCESS 类型保留大小写"""
        r = normalize_rule("PROCESS-NAME", "Google Chrome")
        self.assertEqual(r.value, "Google Chrome")

    def test_type_auto_uppercase(self):
        """类型自动大写"""
        r = normalize_rule("domain-suffix", "example.com")
        self.assertEqual(r.rule_type, "DOMAIN-SUFFIX")


class TestDedupKey(unittest.TestCase):
    """dedup_key() 测试"""

    def test_basic_key(self):
        """基本去重 key"""
        r = CanonicalRule("DOMAIN", "google.com")
        self.assertEqual(dedup_key(r), "DOMAIN|google.com")

    def test_case_insensitive(self):
        """去重 key 大小写不敏感"""
        r1 = CanonicalRule("DOMAIN", "Google.com")
        r2 = CanonicalRule("DOMAIN", "google.com")
        self.assertEqual(dedup_key(r1), dedup_key(r2))

    def test_different_types_different_keys(self):
        """不同类型不同 key"""
        r1 = CanonicalRule("DOMAIN", "google.com")
        r2 = CanonicalRule("DOMAIN-SUFFIX", "google.com")
        self.assertNotEqual(dedup_key(r1), dedup_key(r2))


class TestParseRuleLine(unittest.TestCase):
    """parse_rule_line() 测试"""

    def test_standard_format(self):
        """标准 TYPE,VALUE 格式"""
        r = parse_rule_line("- DOMAIN-SUFFIX,google.com", "v2fly")
        self.assertIsNotNone(r)
        self.assertEqual(r.rule_type, "DOMAIN-SUFFIX")
        self.assertEqual(r.value, "google.com")
        self.assertEqual(r.source, "v2fly")

    def test_with_param(self):
        """TYPE,VALUE,PARAM 格式"""
        r = parse_rule_line("- IP-CIDR,1.1.1.1/32,no-resolve")
        self.assertIsNotNone(r)
        self.assertEqual(r.param, "no-resolve")

    def test_with_trailing_comment(self):
        """行尾注释"""
        r = parse_rule_line("- DOMAIN-SUFFIX,google.com # 谷歌")
        self.assertIsNotNone(r)
        self.assertEqual(r.value, "google.com")

    def test_domain_regex_with_commas(self):
        """DOMAIN-REGEX 含逗号"""
        r = parse_rule_line(r"- DOMAIN-REGEX,^https?://[^/]+\.example\.com/.*")
        self.assertIsNotNone(r)
        self.assertEqual(r.rule_type, "DOMAIN-REGEX")
        self.assertIn("example", r.value)

    def test_bare_domain(self):
        """裸域名（v2fly 格式，无类型前缀）"""
        r = parse_rule_line("example.com")
        self.assertIsNotNone(r)
        self.assertEqual(r.rule_type, "DOMAIN")
        self.assertEqual(r.value, "example.com")

    def test_comment_line(self):
        """注释行应返回 None"""
        r = parse_rule_line("# 这是注释")
        self.assertIsNone(r)

    def test_empty_line(self):
        """空行应返回 None"""
        r = parse_rule_line("")
        self.assertIsNone(r)

    def test_yaml_list_marker(self):
        """YAML 列表标记 '-' 应被正确处理"""
        r = parse_rule_line("- DOMAIN-SUFFIX,example.com")
        self.assertIsNotNone(r)
        self.assertEqual(r.rule_type, "DOMAIN-SUFFIX")
        self.assertEqual(r.value, "example.com")

    def test_include_prefix(self):
        """include: 指令应返回 None（由上层解析器处理）"""
        r = parse_rule_line("include:category-ads-all")
        self.assertIsNone(r)

    def test_ip_cidr_with_param(self):
        """IP-CIDR 带 no-resolve"""
        r = parse_rule_line("- IP-CIDR,10.0.0.0/8,no-resolve")
        self.assertIsNotNone(r)
        self.assertEqual(r.param, "no-resolve")

    def test_ip_cidr6(self):
        """IP-CIDR6 解析"""
        r = parse_rule_line("- IP-CIDR6,2001:db8::/32")
        self.assertIsNotNone(r)
        self.assertEqual(r.rule_type, "IP-CIDR6")


class TestSortRules(unittest.TestCase):
    """sort_rules() 测试"""

    def setUp(self):
        """创建测试用规则列表"""
        self.rules = [
            CanonicalRule("IP-CIDR", "10.0.0.0/8"),
            CanonicalRule("DOMAIN-SUFFIX", "example.com"),
            CanonicalRule("DOMAIN", "test.com"),
            CanonicalRule("DOMAIN-SUFFIX", "google.com"),
        ]

    def test_type_order(self):
        """排序应按 TYPES_ORDER 分组"""
        sorted_rules = sort_rules(self.rules)
        sorted_types = [r.rule_type for r in sorted_rules]
        # DOMAIN 应在前，DOMAIN-SUFFIX 其次，IP-CIDR 在后
        domain_idx = sorted_types.index("DOMAIN")
        suffix_idx = sorted_types.index("DOMAIN-SUFFIX")
        cidr_idx = sorted_types.index("IP-CIDR")
        self.assertLess(domain_idx, suffix_idx)
        self.assertLess(suffix_idx, cidr_idx)

    def test_alphabetical_within_type(self):
        """同一类型内按字母序排列"""
        sorted_rules = sort_rules(self.rules)
        suffixes = [r.value for r in sorted_rules if r.rule_type == "DOMAIN-SUFFIX"]
        self.assertEqual(suffixes, ["example.com", "google.com"])

    def test_unknown_type_at_end(self):
        """未知类型应排在最后"""
        rules = [CanonicalRule("UNKNOWN_TYPE", "value")]
        sorted_rules = sort_rules(rules)
        self.assertEqual(len(sorted_rules), 1)

    def test_empty_list(self):
        """空列表应返回空列表"""
        self.assertEqual(sort_rules([]), [])


class TestCountByType(unittest.TestCase):
    """count_by_type() 测试"""

    def test_basic_count(self):
        """基本计数"""
        rules = [
            CanonicalRule("DOMAIN", "a.com"),
            CanonicalRule("DOMAIN", "b.com"),
            CanonicalRule("DOMAIN-SUFFIX", "c.com"),
        ]
        counts = count_by_type(rules)
        self.assertEqual(counts.get("DOMAIN"), 2)
        self.assertEqual(counts.get("DOMAIN-SUFFIX"), 1)

    def test_empty_list(self):
        """空列表返回空字典"""
        self.assertEqual(count_by_type([]), {})

    def test_multiple_types(self):
        """多种类型混合"""
        rules = [
            CanonicalRule("DOMAIN-KEYWORD", "test"),
            CanonicalRule("DOMAIN-REGEX", "test.*"),
            CanonicalRule("DOMAIN", "a.com"),
            CanonicalRule("DOMAIN-SUFFIX", "b.com"),
            CanonicalRule("IP-CIDR", "1.1.1.1/32"),
            CanonicalRule("IP-CIDR6", "::1/128"),
            CanonicalRule("IP-ASN", "12345"),
            CanonicalRule("PROCESS-NAME", "chrome"),
        ]
        counts = count_by_type(rules)
        for t in TYPES_ORDER:
            self.assertEqual(counts.get(t, 0), 1, f"类型 {t} 计数应为 1")


class TestCanonicalRule(unittest.TestCase):
    """CanonicalRule 数据结构测试"""

    def test_namedtuple_fields(self):
        """五元组字段"""
        r = CanonicalRule("DOMAIN", "google.com", "no-resolve", "v2fly")
        self.assertEqual(r.rule_type, "DOMAIN")
        self.assertEqual(r.value, "google.com")
        self.assertEqual(r.param, "no-resolve")
        self.assertEqual(r.source, "v2fly")

    def test_default_empty_param(self):
        """param 默认空字符串"""
        r = CanonicalRule("DOMAIN", "google.com")
        self.assertEqual(r.param, "")

    def test_immutable(self):
        """NamedTuple 不可变"""
        r = CanonicalRule("DOMAIN", "google.com")
        with self.assertRaises(AttributeError):
            r.value = "changed.com"

    def test_equality(self):
        """相同内容的规则应相等"""
        r1 = CanonicalRule("DOMAIN", "google.com")
        r2 = CanonicalRule("DOMAIN", "google.com")
        self.assertEqual(r1, r2)


if __name__ == '__main__':
    unittest.main()