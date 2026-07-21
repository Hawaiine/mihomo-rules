"""
parse_loyalsoldier.py — 解析 Loyalsoldier/clash-rules 上游数据

双重角色：
1. 基础规则集主数据源（7 个）：Reject, LanCIDR, Private, Direct, CNCIDR, Proxy, Applications
2. 品牌规则集补充源：补充 v2fly 没有的域名 + CIDR

Loyalsoldier 格式（标准 Clash YAML）：
  payload:
    - '+.google.com'              → DOMAIN-SUFFIX,google.com
    - '1.1.8.0/24'                → IP-CIDR,1.1.8.0/24
    - '2001:67c:4e8::/48'         → IP-CIDR6,2001:67c:4e8::/48
    - 'PROCESS-NAME,frpc'         → PROCESS-NAME,frpc
"""

import ipaddress
import os
import re
import sys
from pathlib import Path
from typing import Any, NamedTuple

# 确保 scripts/ 目录在 Python 路径中
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from lib.canonical import (
    CanonicalRule,
    normalize_value,
    sort_rules,
    count_by_type,
)


# ── 基础规则集映射（7 个，全从 Loyalsoldier 获取） ────────────

LOYALSOLDIER_BASE_MAP: dict[str, dict[str, str]] = {
    "reject.txt": {
        "ruleset": "Reject",
        "behavior": "domain",
        "description": "广告/追踪域名",
    },
    "lancidr.txt": {
        "ruleset": "LanCIDR",
        "behavior": "classical",
        "description": "内网 IP 段",
    },
    "private.txt": {
        "ruleset": "Private",
        "behavior": "domain",
        "description": "私有域名",
    },
    "direct.txt": {
        "ruleset": "Direct",
        "behavior": "domain",
        "description": "国内直连域名",
    },
    "cncidr.txt": {
        "ruleset": "CNCIDR",
        "behavior": "classical",
        "description": "国内 IP 段",
    },
    "proxy.txt": {
        "ruleset": "Proxy",
        "behavior": "domain",
        "description": "代理域名兜底",
    },
    "applications.txt": {
        "ruleset": "Applications",
        "behavior": "classical",
        "description": "代理工具进程名",
    },
}


# ── 品牌补充映射 ──────────────────────────────────────────────

# Loyalsoldier 文件 → 品牌名（补充 v2fly 没有的域名/CIDR）
LOYALSOLDIER_BRAND_MAP: dict[str, str] = {
    "icloud.txt": "iCloud",
    "telegramcidr.txt": "Telegram",
}


# ── 跳过列表 ──────────────────────────────────────────────────

LOYALSOLDIER_SKIP: set[str] = {
    "gfw.txt",
    "greatfire.txt",
    "tld-not-cn.txt",
}


# ── 规则类型检测 ──────────────────────────────────────────────

# PROCESS-NAME 规则正则
PROCESS_NAME_RE = re.compile(r"^PROCESS-NAME,", re.IGNORECASE)


def detect_rule_type(value: str) -> str:
    """
    自动判断 Loyalsoldier 规则值的类型。

    Args:
        value: 原始值（去掉引号后）

    Returns:
        str: 规则类型（DOMAIN-SUFFIX / IP-CIDR / IP-CIDR6 / PROCESS-NAME）
    """
    # 已经是标准格式（如 PROCESS-NAME,xxx）
    if PROCESS_NAME_RE.match(value):
        return "PROCESS-NAME"

    # 域名格式（+. 前缀）
    if value.startswith("+."):
        return "DOMAIN-SUFFIX"

    # 尝试解析为 CIDR
    try:
        network = ipaddress.ip_network(value, strict=False)
        if isinstance(network, ipaddress.IPv6Network):
            return "IP-CIDR6"
        return "IP-CIDR"
    except ValueError:
        pass

    # 裸域名（无 +. 前缀，但含 . 且有效）
    if "." in value:
        return "DOMAIN-SUFFIX"

    return "DOMAIN-SUFFIX"


def extract_value(value: str) -> str:
    """
    从 Loyalsoldier 规则值中提取归一化后的值。

    Args:
        value: 原始值（去掉引号后）

    Returns:
        str: 归一化后的值
    """
    # PROCESS-NAME → 保持原样
    if PROCESS_NAME_RE.match(value):
        # 去掉 "PROCESS-NAME," 前缀，取真实进程名
        return value[len("PROCESS-NAME,"):].strip()

    # +.domain.com → domain.com
    if value.startswith("+."):
        return normalize_value(value[2:])

    # 其他（CIDR、裸域名）→ 标准化
    return normalize_value(value)


# ── YAML 解析 ─────────────────────────────────────────────────

def parse_loyalsoldier_yaml(content: str) -> list[CanonicalRule]:
    """
    解析 Loyalsoldier YAML 格式内容。

    Args:
        content: YAML 文件内容

    Returns:
        list[CanonicalRule]: 解析后的规则列表
    """
    rules: list[CanonicalRule] = []
    in_payload = False

    for line in content.split("\n"):
        stripped = line.strip()

        # 跳过空行
        if not stripped:
            continue

        # 检测 payload: 开始
        if stripped == "payload:":
            in_payload = True
            continue

        # payload 内的行
        if in_payload:
            # 匹配 YAML 列表项：  - 'value' 或  - "value"
            if stripped.startswith("- "):
                raw = stripped[2:].strip()
                # 去掉引号
                if (raw.startswith("'") and raw.endswith("'")) or \
                   (raw.startswith('"') and raw.endswith('"')):
                    raw = raw[1:-1]

                # 判断类型并提取值
                rule_type = detect_rule_type(raw)
                value = extract_value(raw)

                if rule_type == "PROCESS-NAME":
                    rules.append(CanonicalRule(
                        rule_type="PROCESS-NAME",
                        value=value,
                        param="",
                        source="loyalsoldier",
                    ))
                else:
                    rules.append(CanonicalRule(
                        rule_type=rule_type,
                        value=value,
                        param="",
                        source="loyalsoldier",
                    ))

    return rules


def parse_loyalsoldier_file(filepath: str) -> list[CanonicalRule]:
    """
    解析单个 Loyalsoldier 文件。

    Args:
        filepath: 文件路径

    Returns:
        list[CanonicalRule]: 解析后的规则列表
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
    except (FileNotFoundError, IOError):
        return []

    return parse_loyalsoldier_yaml(content)


# ── 基础规则集入口 ────────────────────────────────────────────

def parse_loyalsoldier_basic(
    loyalsoldier_dir: str,
) -> dict[str, dict[str, Any]]:
    """
    解析所有 7 个基础规则集。

    Args:
        loyalsoldier_dir: upstream/loyalsoldier/ 目录路径

    Returns:
        {
            "Reject": {
                "rules": [CanonicalRule, ...],
                "behavior": "domain",
                "file_count": int,
                "type_counts": {"DOMAIN-SUFFIX": 123, ...},
            },
            ...
        }
    """
    results: dict[str, dict[str, Any]] = {}

    for filename, config in LOYALSOLDIER_BASE_MAP.items():
        filepath = os.path.join(loyalsoldier_dir, filename)
        ruleset_name = config["ruleset"]
        behavior = config["behavior"]

        if not os.path.isfile(filepath):
            print(f"  ⚠️ 文件不存在: {filename}")
            results[ruleset_name] = {
                "rules": [],
                "behavior": behavior,
                "file_count": 0,
                "type_counts": {},
            }
            continue

        rules = parse_loyalsoldier_file(filepath)
        rules = sort_rules(rules)
        type_counts = count_by_type(rules)

        results[ruleset_name] = {
            "rules": rules,
            "behavior": behavior,
            "file_count": len(rules),
            "type_counts": type_counts,
        }

        print(f"  ✅ {ruleset_name}: {len(rules)} 条规则")

    return results


# ── 品牌补充入口 ──────────────────────────────────────────────

def parse_loyalsoldier_brand(
    brand_name: str,
    loyalsoldier_dir: str,
) -> list[CanonicalRule]:
    """
    按品牌名从 Loyalsoldier 获取补充数据。

    仅返回 v2fly 没有的域名/CIDR（去重由调用方处理）。

    Args:
        brand_name: 品牌名，如 "iCloud", "Telegram"
        loyalsoldier_dir: upstream/loyalsoldier/ 目录路径

    Returns:
        list[CanonicalRule]: 补充规则列表
    """
    # 反向查找：品牌名 → 文件名
    filename = None
    for fn, bn in LOYALSOLDIER_BRAND_MAP.items():
        if bn == brand_name:
            filename = fn
            break

    if filename is None:
        return []

    filepath = os.path.join(loyalsoldier_dir, filename)
    if not os.path.isfile(filepath):
        return []

    rules = parse_loyalsoldier_file(filepath)
    rules = sort_rules(rules)

    return rules


# ── 命令行入口 ─────────────────────────────────────────────────

def main():
    """命令行入口"""
    import sys

    if len(sys.argv) < 2:
        print("用法: python parse_loyalsoldier.py <mode> [args]")
        print("")
        print("模式:")
        print("  basic              解析所有 7 个基础规则集")
        print("  brand <品牌名>     按品牌名获取补充数据")
        print("")
        print("示例:")
        print("  python parse_loyalsoldier.py basic")
        print("  python parse_loyalsoldier.py brand iCloud")
        sys.exit(1)

    mode = sys.argv[1]
    loyalsoldier_dir = "upstream/loyalsoldier"

    if mode == "basic":
        print("📥 解析 Loyalsoldier 基础规则集...")
        results = parse_loyalsoldier_basic(loyalsoldier_dir)

        print(f"\n{'='*50}")
        print("📊 汇总:")
        for ruleset_name, data in results.items():
            rules = data["rules"]
            type_counts = data["type_counts"]
            type_str = " + ".join([f"{t}:{c}" for t, c in sorted(type_counts.items())])
            print(f"  {ruleset_name} ({data['behavior']}): {len(rules)} 条 [{type_str}]")

        total = sum(d["file_count"] for d in results.values())
        print(f"\n  总计: {total} 条规则")

    elif mode == "brand":
        if len(sys.argv) < 3:
            print("请指定品牌名")
            sys.exit(1)

        brand_name = sys.argv[2]
        print(f"📥 获取 {brand_name} 的 Loyalsoldier 补充数据...")

        rules = parse_loyalsoldier_brand(brand_name, loyalsoldier_dir)
        print(f"  共 {len(rules)} 条规则")

        if rules:
            type_counts = count_by_type(rules)
            print(f"\n  类型分布:")
            for t, c in sorted(type_counts.items()):
                print(f"    {t}: {c}")

            print(f"\n  前 10 条:")
            for rule in rules[:10]:
                print(f"    {rule.rule_type},{rule.value}")

    else:
        print(f"未知模式: {mode}")
        sys.exit(1)


if __name__ == "__main__":
    main()