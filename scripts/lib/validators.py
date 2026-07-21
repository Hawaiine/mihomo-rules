"""
lib/validators.py — 唯一公共校验模块

三个上游解析器统一调用此模块，不允许各自实现一套判断标准。
"""

import ipaddress
import re
from typing import NamedTuple


# ── 校验结果类型 ──────────────────────────────────────────────

class ValidationResult(NamedTuple):
    """校验结果"""
    is_valid: bool
    error_msg: str = ""  # 空字符串表示通过


class ValidationBatchResult(NamedTuple):
    """批量校验结果"""
    passed: list[tuple[str, str, str]]       # (type, value, param)
    failed: list[tuple[str, str, str, str]]  # (type, value, param, error_msg)
    total: int
    valid_count: int
    invalid_count: int


# ── 常量 ──────────────────────────────────────────────────────

# 过宽关键词黑名单（拒绝作为 DOMAIN-KEYWORD）
KEYWORD_BLACKLIST = {
    "a", "an", "the", "co", "com", "www", "http", "https",
    "io", "org", "net", "cn", "gov", "edu", "app", "api",
    "cdn", "mail", "news", "blog", "shop", "store", "web",
    "test", "dev", "beta", "help", "support", "login", "sign",
    "api", "v1", "v2", "v3", "v4", "v5",
}

# 拒绝的全网段
REJECTED_CIDR_ALL = {"0.0.0.0/0", "::/0"}

# 内置正常域名样本池（用于 DOMAIN-REGEX 告警检查）
SAMPLE_DOMAINS = [
    "www.google.com", "api.github.com", "mail.google.com",
    "cdn.cloudflare.com", "docs.python.org", "example.com",
    "www.youtube.com", "play.google.com", "support.apple.com",
    "login.microsoft.com", "www.baidu.com", "www.amazon.com",
]


# ── 域名校验 ──────────────────────────────────────────────────

def validate_domain(value: str) -> ValidationResult:
    """校验 DOMAIN / DOMAIN-SUFFIX 值"""
    if not value:
        return ValidationResult(False, "域名为空")

    if len(value) > 253:
        return ValidationResult(False, f"域名长度超过 253: {len(value)}")

    if "." not in value:
        return ValidationResult(False, f"裸词域名，缺少点号: {value}")

    # 拒绝 IP 当域名
    try:
        ipaddress.ip_address(value)
        return ValidationResult(False, f"IP 地址不能作为域名: {value}")
    except ValueError:
        pass

    labels = value.split(".")
    if len(labels) < 2:
        return ValidationResult(False, f"域名 label 数不足: {value}")

    # 检查每个 label
    for label in labels:
        if not label:
            return ValidationResult(False, f"空 label: {value}")
        if len(label) > 63:
            return ValidationResult(False, f"label 长度超过 63: {label}")
        if not re.match(r"^[a-z0-9-]+$", label):
            return ValidationResult(False, f"label 含非法字符: {label}")
        if label.startswith("-") or label.endswith("-"):
            return ValidationResult(False, f"label 以连字符开头/结尾: {label}")

    # TLD 必须纯字母且长度≥2
    tld = labels[-1]
    if not re.match(r"^[a-z]{2,}$", tld):
        return ValidationResult(False, f"TLD 非法: {tld}")

    return ValidationResult(True)


def validate_domain_suffix(value: str) -> ValidationResult:
    """DOMAIN-SUFFIX 校验，复用 domain 校验"""
    return validate_domain(value)


# ── DOMAIN-KEYWORD 校验 ──────────────────────────────────────

def validate_domain_keyword(value: str) -> ValidationResult:
    """校验 DOMAIN-KEYWORD 值"""
    if not value:
        return ValidationResult(False, "关键字为空")

    if len(value) < 3:
        return ValidationResult(False, f"关键字长度不足 3: {value}")

    if value.lower() in KEYWORD_BLACKLIST:
        return ValidationResult(False, f"过宽关键字（黑名单）: {value}")

    if value.isdigit():
        return ValidationResult(False, f"关键字不能纯数字: {value}")

    return ValidationResult(True)


# ── DOMAIN-REGEX 校验 ────────────────────────────────────────

def validate_domain_regex(value: str) -> ValidationResult:
    """校验 DOMAIN-REGEX 值"""
    if not value:
        return ValidationResult(False, "正则表达式为空")

    try:
        compiled = re.compile(value, re.IGNORECASE)
    except re.error as e:
        return ValidationResult(False, f"正则编译失败: {e}")

    # 在内置样本池中测试匹配数
    match_count = sum(1 for d in SAMPLE_DOMAINS if compiled.search(d))
    if match_count == 0:
        return ValidationResult(True, "正则未匹配任何样本域名，建议人工复核")

    return ValidationResult(True)


# ── IP-CIDR / IP-CIDR6 校验 ─────────────────────────────────

def validate_ip_cidr(value: str, version: int = 4) -> ValidationResult:
    """校验 IP-CIDR 或 IP-CIDR6 值"""
    if not value:
        return ValidationResult(False, "CIDR 为空")

    if value in REJECTED_CIDR_ALL:
        return ValidationResult(False, f"拒绝全网段: {value}")

    try:
        network = ipaddress.ip_network(value, strict=False)
    except ValueError as e:
        return ValidationResult(False, f"CIDR 解析失败: {e}")

    if version == 4 and not isinstance(network, ipaddress.IPv4Network):
        return ValidationResult(False, f"期望 IPv4 但得到 IPv6: {value}")

    if version == 6 and not isinstance(network, ipaddress.IPv6Network):
        return ValidationResult(False, f"期望 IPv6 但得到 IPv4: {value}")

    # 拒绝 /0 全网段
    if network.prefixlen == 0:
        return ValidationResult(False, f"拒绝 /0 全网段: {value}")

    return ValidationResult(True)


def validate_ip_cidr6(value: str) -> ValidationResult:
    """校验 IP-CIDR6"""
    return validate_ip_cidr(value, version=6)


# ── IP-ASN 校验 ──────────────────────────────────────────────

def validate_ip_asn(value: str) -> ValidationResult:
    """校验 IP-ASN 值"""
    if not value:
        return ValidationResult(False, "ASN 为空")

    if not value.isdigit():
        return ValidationResult(False, f"ASN 不是纯数字: {value}")

    asn = int(value)
    if asn < 1 or asn > 4294967295:
        return ValidationResult(False, f"ASN 超出范围 1~4294967295: {value}")

    return ValidationResult(True)


# ── PROCESS-NAME 校验 ────────────────────────────────────────

def validate_process_name(value: str) -> ValidationResult:
    """校验 PROCESS-NAME 值"""
    if not value:
        return ValidationResult(False, "进程名为空")

    if len(value) < 2 or len(value) > 255:
        return ValidationResult(False, f"进程名长度 2~255: {len(value)}")

    # 拒绝异常路径分隔符
    abnormal = {"/", "\\", "..", "~"}
    for sep in abnormal:
        if sep in value:
            return ValidationResult(False, f"进程名含异常路径分隔符 '{sep}': {value}")

    return ValidationResult(True)


# ── USER-AGENT 校验 ──────────────────────────────────────────

def validate_user_agent(value: str) -> ValidationResult:
    """校验 USER-AGENT 值"""
    if not value:
        return ValidationResult(False, "User-Agent 为空")

    if len(value) < 2:
        return ValidationResult(False, f"User-Agent 长度不足 2: {value}")

    return ValidationResult(True)


# ── 统一入口 ──────────────────────────────────────────────────

def validate_rule(rule_type: str, value: str, param: str = "") -> ValidationResult:
    """
    统一校验入口，根据 rule_type 分发到具体校验器。

    解析顺序：
    1. 先检查是否匹配已知语法前缀（include:/regexp:/keyword:/full:/domain:）
    2. 都不命中 → 当裸域名走 DOMAIN 校验
    """
    if not rule_type:
        return ValidationResult(False, "规则类型为空")

    if not value:
        return ValidationResult(False, "规则值为空")

    type_upper = rule_type.upper()
    value = value.strip()

    # 已知语法前缀检查
    if value.startswith("include:"):
        return ValidationResult(True, "include 指令，需递归解析")  # 不校验，由解析器处理

    # 按类型分发
    validators = {
        "DOMAIN": validate_domain,
        "DOMAIN-SUFFIX": validate_domain_suffix,
        "DOMAIN-KEYWORD": validate_domain_keyword,
        "DOMAIN-REGEX": validate_domain_regex,
        "IP-CIDR": lambda v: validate_ip_cidr(v, version=4),
        "IP-CIDR6": validate_ip_cidr6,
        "IP-ASN": validate_ip_asn,
        "PROCESS-NAME": validate_process_name,
        "USER-AGENT": validate_user_agent,
    }

    if type_upper in validators:
        return validators[type_upper](value)

    # 未知类型 → 尝试裸域名校验
    return validate_domain(value)


def validate_payload(rules: list[tuple[str, str, str]]) -> ValidationBatchResult:
    """
    批量校验规则列表。

    Args:
        rules: [(type, value, param), ...] 三元组列表

    Returns:
        ValidationBatchResult: 批量校验结果
    """
    passed: list[tuple[str, str, str]] = []
    failed: list[tuple[str, str, str, str]] = []

    for rule_type, value, param in rules:
        result = validate_rule(rule_type, value, param)
        if result.is_valid:
            passed.append((rule_type, value, param))
        else:
            failed.append((rule_type, value, param, result.error_msg))

    total = len(rules)
    return ValidationBatchResult(
        passed=passed,
        failed=failed,
        total=total,
        valid_count=len(passed),
        invalid_count=len(failed),
    )