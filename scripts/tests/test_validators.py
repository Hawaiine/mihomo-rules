"""
test_validators.py — 测试 lib/validators.py 校验函数
"""
import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from lib.validators import (
    validate_domain,
    validate_domain_suffix,
    validate_domain_keyword,
    validate_domain_regex,
    validate_ip_cidr,
    validate_ip_cidr6,
    validate_ip_asn,
    validate_process_name,
    validate_user_agent,
    validate_rule,
    validate_payload,
    ValidationResult,
    ValidationBatchResult,
)


class TestValidateDomain(unittest.TestCase):
    """validate_domain() 测试"""

    def test_valid_domain(self):
        """有效域名"""
        result = validate_domain("google.com")
        self.assertTrue(result.is_valid)

    def test_valid_subdomain(self):
        """有效子域名"""
        result = validate_domain("api.google.com")
        self.assertTrue(result.is_valid)

    def test_empty_value(self):
        """空值应拒绝"""
        result = validate_domain("")
        self.assertFalse(result.is_valid)

    def test_ip_as_domain(self):
        """IP 地址不能作为域名"""
        result = validate_domain("1.1.1.1")
        self.assertFalse(result.is_valid)

    def test_no_dot(self):
        """裸词域名应拒绝"""
        result = validate_domain("localhost")
        self.assertFalse(result.is_valid)

    def test_single_label(self):
        """单 label 域名"""
        result = validate_domain("example")
        self.assertFalse(result.is_valid)

    def test_long_label(self):
        """超长 label 应拒绝"""
        result = validate_domain("a" * 64 + ".com")
        self.assertFalse(result.is_valid)

    def test_valid_long_domain(self):
        """合法长域名"""
        result = validate_domain("a" * 63 + ".com")
        self.assertTrue(result.is_valid)

    def test_label_with_hyphens(self):
        """含连字符的 label"""
        result = validate_domain("my-service.example.com")
        self.assertTrue(result.is_valid)

    def test_label_starting_with_hyphen(self):
        """以连字符开头的 label 应拒绝"""
        result = validate_domain("-bad.example.com")
        self.assertFalse(result.is_valid)

    def test_label_ending_with_hyphen(self):
        """以连字符结尾的 label 应拒绝"""
        result = validate_domain("bad-.example.com")
        self.assertFalse(result.is_valid)

    def test_short_tld(self):
        """单字母 TLD 应拒绝"""
        result = validate_domain("example.x")
        self.assertFalse(result.is_valid)

    def test_numeric_tld(self):
        """数字 TLD 应拒绝"""
        result = validate_domain("example.123")
        self.assertFalse(result.is_valid)

    def test_domain_over_253_chars(self):
        """超长域名应拒绝"""
        long_domain = "a" * 126 + ".com"  # 126 + 4 = 130, 不够长
        # 需要超过 253
        long_domain = "a" * 250 + ".com"  # 254
        result = validate_domain(long_domain)
        self.assertFalse(result.is_valid)


class TestValidateDomainSuffix(unittest.TestCase):
    """validate_domain_suffix() 测试"""

    def test_valid_suffix(self):
        """有效后缀"""
        result = validate_domain_suffix("google.com")
        self.assertTrue(result.is_valid)

    def test_invalid_suffix(self):
        """无效后缀"""
        result = validate_domain_suffix("")
        self.assertFalse(result.is_valid)


class TestValidateDomainKeyword(unittest.TestCase):
    """validate_domain_keyword() 测试"""

    def test_valid_keyword(self):
        """有效关键字"""
        result = validate_domain_keyword("google")
        self.assertTrue(result.is_valid)

    def test_too_short(self):
        """长度不足 3 应拒绝"""
        result = validate_domain_keyword("ab")
        self.assertFalse(result.is_valid)

    def test_blacklist_common(self):
        """黑名单过宽关键字应拒绝"""
        blacklisted = ["a", "the", "com", "www", "api", "cdn", "mail"]
        for kw in blacklisted:
            result = validate_domain_keyword(kw)
            self.assertFalse(result.is_valid, f"'{kw}' 应在黑名单中")

    def test_digits_only(self):
        """纯数字应拒绝"""
        result = validate_domain_keyword("12345")
        self.assertFalse(result.is_valid)

    def test_empty(self):
        """空值应拒绝"""
        result = validate_domain_keyword("")
        self.assertFalse(result.is_valid)


class TestValidateDomainRegex(unittest.TestCase):
    """validate_domain_regex() 测试"""

    def test_valid_regex(self):
        """有效正则"""
        result = validate_domain_regex(r"^https?://")
        self.assertTrue(result.is_valid)

    def test_invalid_regex(self):
        """无效正则应拒绝"""
        result = validate_domain_regex(r"[invalid")
        self.assertFalse(result.is_valid)

    def test_empty_regex(self):
        """空正则应拒绝"""
        result = validate_domain_regex("")
        self.assertFalse(result.is_valid)


class TestValidateIpCidr(unittest.TestCase):
    """validate_ip_cidr() 测试"""

    def test_valid_cidr(self):
        """有效 CIDR"""
        result = validate_ip_cidr("1.1.1.1/32")
        self.assertTrue(result.is_valid)

    def test_valid_cidr_v6(self):
        """有效 IPv6 CIDR"""
        result = validate_ip_cidr("2001:db8::/32", version=6)
        self.assertTrue(result.is_valid)

    def test_ipv4_cidr_v6_mismatch(self):
        """IPv4 CIDR 在 IPv6 模式下应拒绝"""
        result = validate_ip_cidr("1.1.1.1/32", version=6)
        self.assertFalse(result.is_valid)

    def test_ipv6_cidr_v4_mismatch(self):
        """IPv6 CIDR 在 IPv4 模式下应拒绝"""
        result = validate_ip_cidr("2001:db8::/32", version=4)
        self.assertFalse(result.is_valid)

    def test_invalid_cidr(self):
        """无效 CIDR"""
        result = validate_ip_cidr("not-a-cidr")
        self.assertFalse(result.is_valid)

    def test_slash_zero_rejected(self):
        """全网段 /0 应拒绝"""
        result = validate_ip_cidr("0.0.0.0/0")
        self.assertFalse(result.is_valid)

    def test_empty_cidr(self):
        """空值应拒绝"""
        result = validate_ip_cidr("")
        self.assertFalse(result.is_valid)

    def test_cidr_without_prefix(self):
        """无前缀长度的 CIDR"""
        result = validate_ip_cidr("1.1.1.1")
        self.assertTrue(result.is_valid)  # strict=False 允许


class TestValidateIpCidr6(unittest.TestCase):
    """validate_ip_cidr6() 测试"""

    def test_valid_ipv6(self):
        """有效 IPv6 CIDR"""
        result = validate_ip_cidr6("::1/128")
        self.assertTrue(result.is_valid)

    def test_ipv4_in_ipv6(self):
        """IPv4 在 IPv6 函数中应拒绝"""
        result = validate_ip_cidr6("1.1.1.1/32")
        self.assertFalse(result.is_valid)


class TestValidateIpAsn(unittest.TestCase):
    """validate_ip_asn() 测试"""

    def test_valid_asn(self):
        """有效 ASN"""
        result = validate_ip_asn("15169")
        self.assertTrue(result.is_valid)

    def test_non_numeric(self):
        """非数字 ASN 应拒绝"""
        result = validate_ip_asn("AS15169")
        self.assertFalse(result.is_valid)

    def test_asn_zero(self):
        """ASN 0 应拒绝"""
        result = validate_ip_asn("0")
        self.assertFalse(result.is_valid)

    def test_asn_too_large(self):
        """ASN 超上限应拒绝"""
        result = validate_ip_asn("4294967296")
        self.assertFalse(result.is_valid)

    def test_empty_asn(self):
        """空值应拒绝"""
        result = validate_ip_asn("")
        self.assertFalse(result.is_valid)


class TestValidateProcessName(unittest.TestCase):
    """validate_process_name() 测试"""

    def test_valid_name(self):
        """有效进程名"""
        result = validate_process_name("chrome.exe")
        self.assertTrue(result.is_valid)

    def test_too_short(self):
        """长度不足 2 应拒绝"""
        result = validate_process_name("a")
        self.assertFalse(result.is_valid)

    def test_path_separator_slash(self):
        """含路径分隔符 '/' 应拒绝"""
        result = validate_process_name("/usr/bin/chrome")
        self.assertFalse(result.is_valid)

    def test_path_separator_backslash(self):
        """含反斜杠应拒绝"""
        result = validate_process_name("C:\\Program Files\\chrome.exe")
        self.assertFalse(result.is_valid)

    def test_empty_name(self):
        """空值应拒绝"""
        result = validate_process_name("")
        self.assertFalse(result.is_valid)


class TestValidateUserAgent(unittest.TestCase):
    """validate_user_agent() 测试"""

    def test_valid_ua(self):
        """有效 User-Agent"""
        result = validate_user_agent("Mozilla/5.0")
        self.assertTrue(result.is_valid)

    def test_too_short(self):
        """长度不足 2 应拒绝"""
        result = validate_user_agent("a")
        self.assertFalse(result.is_valid)

    def test_empty(self):
        """空值应拒绝"""
        result = validate_user_agent("")
        self.assertFalse(result.is_valid)


class TestValidateRule(unittest.TestCase):
    """validate_rule() 统一入口测试"""

    def test_domain_dispatch(self):
        """DOMAIN 分发到域名校验"""
        result = validate_rule("DOMAIN", "google.com")
        self.assertTrue(result.is_valid)

    def test_ip_cidr_dispatch(self):
        """IP-CIDR 分发到 CIDR 校验"""
        result = validate_rule("IP-CIDR", "1.1.1.1/32")
        self.assertTrue(result.is_valid)

    def test_process_name_dispatch(self):
        """PROCESS-NAME 分发到进程名校验"""
        result = validate_rule("PROCESS-NAME", "chrome.exe")
        self.assertTrue(result.is_valid)

    def test_empty_type(self):
        """空类型应拒绝"""
        result = validate_rule("", "value")
        self.assertFalse(result.is_valid)

    def test_empty_value(self):
        """空值应拒绝"""
        result = validate_rule("DOMAIN", "")
        self.assertFalse(result.is_valid)

    def test_unknown_type_fallback(self):
        """未知类型回退到域名校验"""
        result = validate_rule("UNKNOWN", "example.com")
        self.assertTrue(result.is_valid)

    def test_include_prefix(self):
        """value 以 include: 开头应通过校验（由解析器递归处理）"""
        result = validate_rule("DOMAIN", "include:category-ads-all")
        self.assertTrue(result.is_valid)


class TestValidatePayload(unittest.TestCase):
    """validate_payload() 批量校验测试"""

    def test_all_valid(self):
        """全部有效"""
        rules = [("DOMAIN", "google.com", ""), ("DOMAIN-SUFFIX", "example.com", "")]
        result = validate_payload(rules)
        self.assertEqual(result.valid_count, 2)
        self.assertEqual(result.invalid_count, 0)
        self.assertEqual(result.total, 2)

    def test_mixed_results(self):
        """混合有效/无效"""
        rules = [
            ("DOMAIN", "google.com", ""),
            ("DOMAIN", "", ""),  # 无效：空值
            ("DOMAIN", "1.1.1.1", ""),  # 无效：IP 当域名
        ]
        result = validate_payload(rules)
        self.assertEqual(result.valid_count, 1)
        self.assertEqual(result.invalid_count, 2)
        self.assertEqual(result.total, 3)

    def test_empty_list(self):
        """空列表"""
        result = validate_payload([])
        self.assertEqual(result.total, 0)
        self.assertEqual(result.valid_count, 0)
        self.assertEqual(result.invalid_count, 0)


if __name__ == '__main__':
    unittest.main()