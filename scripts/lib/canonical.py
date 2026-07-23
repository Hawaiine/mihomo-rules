"""
lib/canonical.py — 五元组数据结构 + 归一化

提供规则数据的标准化表示和归一化操作。
所有三个上游解析器输入的数据都转换为 CanonicalRule 后参与后续处理。
"""

import re
from typing import NamedTuple


# ── 8 种规则类型顺序（固定） ──────────────────────────────────

TYPES_ORDER = [
    "DOMAIN-KEYWORD",
    "DOMAIN-REGEX",
    "DOMAIN",
    "DOMAIN-SUFFIX",
    "IP-CIDR",
    "IP-CIDR6",
    "IP-ASN",
    "PROCESS-NAME",
]

# 识别已知语法前缀
KNOWN_PREFIXES = {
    "include:", "regexp:", "keyword:", "full:", "domain:",
}


# ── 五元组数据结构 ────────────────────────────────────────────

class CanonicalRule(NamedTuple):
    """
    归一化后的规则五元组。

    Attributes:
        rule_type: 规则类型，如 DOMAIN / DOMAIN-SUFFIX / IP-CIDR 等
        value:     归一化后的值（小写、去空格、去尾点）
        param:     参数，如 no-resolve，无参时空字符串
        source:    上游标识: v2fly / loyalsoldier / blackmatrix7
    """
    rule_type: str
    value: str
    param: str = ""
    source: str = ""


# ── 归一化 ────────────────────────────────────────────────────

def normalize_value(value: str) -> str:
    """
    归一化值：小写、去首尾空格、去尾部点号。

    Args:
        value: 原始值

    Returns:
        归一化后的值
    """
    value = value.strip().lower()
    # 去除尾部 . 号（如 .google.com → google.com）
    while value.endswith("."):
        value = value[:-1]
    return value


def normalize_rule(
    rule_type: str,
    value: str,
    param: str = "",
    source: str = "",
) -> CanonicalRule:
    """
    创建归一化的 CanonicalRule。

    Args:
        rule_type: 规则类型
        value:     规则值
        param:     参数（可选）
        source:    上游来源（可选）

    Returns:
        CanonicalRule
    """
    return CanonicalRule(
        rule_type=rule_type.upper(),
        value=normalize_value(value),
        param=param.strip(),
        source=source,
    )


def dedup_key(rule: CanonicalRule) -> str:
    """
    全局去重 key：TYPE + VALUE，大小写不敏感。

    不包含 param，因为同 TYPE+VALUE 不同 param 视为重复。
    不包含 source，因为跨上游的同 TYPE+VALUE 应去重。

    Args:
        rule: CanonicalRule

    Returns:
        str: 去重 key
    """
    return f"{rule.rule_type}|{rule.value.lower()}"


# ── 行解析 ────────────────────────────────────────────────────

# 规则行正则：TYPE,VALUE[,PARAM]
RULE_LINE_RE = re.compile(
    r"^\s*[-–]\s*"          # YAML 列表标记
    r"(?P<type>[A-Z][A-Z0-9_-]+)"  # 规则类型
    r"\s*,\s*"              # 逗号分隔
    r"(?P<value>[^,#]+)"    # 值（不含逗号和 #）
    r"(?:\s*,\s*(?P<param>[^#\s]+))?"  # 可选参数
    r"(?:\s*#.*)?$",        # 可选行尾注释
    re.IGNORECASE,
)


def parse_rule_line(line: str, source: str = "") -> CanonicalRule | None:
    """
    解析 "TYPE,VALUE,PARAM" 格式行 → CanonicalRule。

    不校验有效性（只解析），返回 None 表示无法解析。

    Args:
        line:   原始行
        source: 上游标识

    Returns:
        CanonicalRule 或 None
    """
    line = line.strip()
    if not line or line.startswith("#"):
        return None

    # 检查是否是已知语法前缀（如 include: 等）
    for prefix in KNOWN_PREFIXES:
        if line.lower().startswith(prefix):
            # 提取 type 和 value
            parts = line.split(",", 1)
            if len(parts) == 2:
                rule_type = parts[0].strip()
                value = parts[1].strip()
                return CanonicalRule(
                    rule_type=rule_type,
                    value=value,
                    param="",
                    source=source,
                )
            return None

    # 尝试匹配标准格式
    m = RULE_LINE_RE.match(line)
    if m:
        rule_type = m.group("type").upper()
        value = m.group("value")
        param = (m.group("param") or "").strip()
        
        # DOMAIN-REGEX 的值可能包含逗号，需要重新解析
        if rule_type == "DOMAIN-REGEX":
            # 从原始行提取完整值
            line_stripped = line.lstrip("-– ").strip()
            first_comma = line_stripped.find(",")
            if first_comma >= 0:
                rest = line_stripped[first_comma + 1:].strip()
                # 分割 value 和 param
                if "#" in rest:
                    value = rest[:rest.index("#")].strip()
                else:
                    value = rest
                    param = ""
        
        return CanonicalRule(
            rule_type=rule_type,
            value=normalize_value(value),
            param=param,
            source=source,
        )

    # 尝试作为裸域名（v2fly 常见格式）
    if "." in line and not line.startswith("#"):
        return CanonicalRule(
            rule_type="DOMAIN",
            value=normalize_value(line),
            param="",
            source=source,
        )

    return None


def sort_rules(rules: list[CanonicalRule]) -> list[CanonicalRule]:
    """
    按规则类型顺序 + VALUE 字母序排序。

    排序规则：
    1. 先按 TYPES_ORDER 中定义的 TYPE 顺序分组
    2. 同一 TYPE 内按 VALUE 字母序排列（大小写不敏感）

    Args:
        rules: 待排序的规则列表

    Returns:
        排序后的规则列表
    """
    type_order = {t: i for i, t in enumerate(TYPES_ORDER)}

    def sort_key(rule: CanonicalRule) -> tuple:
        type_idx = type_order.get(rule.rule_type, 99)
        return (type_idx, rule.value.lower())

    return sorted(rules, key=sort_key)


def count_by_type(rules: list[CanonicalRule]) -> dict[str, int]:
    """
    按规则类型统计数量。

    Args:
        rules: 规则列表

    Returns:
        dict: 类型 → 数量
    """
    counts: dict[str, int] = {}
    for rule in rules:
        counts[rule.rule_type] = counts.get(rule.rule_type, 0) + 1
    return counts