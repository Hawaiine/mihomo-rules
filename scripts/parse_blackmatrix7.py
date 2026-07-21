"""
parse_blackmatrix7.py — 解析 blackmatrix7/ios_rule_script 上游数据

定位：品牌规则集补充源（补充 v2fly 没有的域名/IP-CIDR/PROCESS-NAME）

格式说明：
- 每个品牌一个目录：rule/Clash/<Brand>/<Brand>.yaml
- 我们只取 <Brand>.yaml（主文件），忽略 _No_Resolve、_Classical、_Domain 等子集
- 格式已经是标准 Clash 格式：TYPE,VALUE
- 使用 clean() 白名单前缀匹配过滤已知类型

目录结构：
  rule/Clash/Google/
  ├── Google.yaml                  # 主文件 ✅ 我们需要的
  ├── Google_No_Resolve.yaml       # 忽略（只是 IP 加了 no-resolve）
  ├── Google_Classical.yaml        # 忽略（子集）
  ├── Google_Domain.yaml           # 忽略（子集，裸域名格式）
  ├── Google_Domain.txt            # 忽略（纯文本）
  ├── Google.list                  # 忽略（Surge 格式）
  └── README.md                    # 忽略
"""

import os
import re
import sys
from pathlib import Path
from typing import Any

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


# ── 白名单：只保留已知类型 ────────────────────────────────────

ALLOWED_TYPES: set[str] = {
    "DOMAIN",
    "DOMAIN-SUFFIX",
    "DOMAIN-KEYWORD",
    "DOMAIN-REGEX",
    "DOMAIN-WILDCARD",
    "IP-CIDR",
    "IP-CIDR6",
    "IP-ASN",
    "PROCESS-NAME",
    "PROCESS-NAME-WILDCARD",
    "PROCESS-NAME-REGEX",
    "PROCESS-PATH",
    "PROCESS-PATH-WILDCARD",
    "PROCESS-PATH-REGEX",
    "SRC-IP-CIDR",
    "SRC-IP-CIDR6",
    "DST-PORT",
    "SRC-PORT",
}


# ── 品牌映射表 ─────────────────────────────────────────────────

# blackmatrix7 目录名 → 我们项目中的品牌名
# 47 个交集品牌（blackmatrix7 有，我们也有）
BLACKMATRIX7_BRAND_MAP: dict[str, str] = {
    "AbemaTV": "AbemaTV",
    "Amazon": "Amazon",
    "Anthropic": "Anthropic",
    "Apple": "Apple",
    "AppleTV": "AppleTV",
    "Bahamut": "Bahamut",
    "Cloudflare": "Cloudflare",
    "DAZN": "DAZN",
    "Deezer": "Deezer",
    "Discord": "Discord",
    "Disney": "Disney",
    "Docker": "Docker",
    "Facebook": "Facebook",
    "GitHub": "GitHub",
    "Google": "Google",
    "HBO": "HBO",
    "HamiVideo": "HamiVideo",
    "Hulu": "Hulu",
    "Instagram": "Instagram",
    "KKTV": "KKTV",
    "LiTV": "LiTV",
    "LineTV": "LineTV",
    "Microsoft": "Microsoft",
    "Netflix": "Netflix",
    "Niconico": "Niconico",
    "Nintendo": "Nintendo",
    "NowE": "NowE",
    "OneDrive": "OneDrive",
    "OpenAI": "OpenAI",
    "PayPal": "PayPal",
    "Pinterest": "Pinterest",
    "Pixiv": "Pixiv",
    "PrimeVideo": "PrimeVideo",
    "Qobuz": "Qobuz",
    "Reddit": "Reddit",
    "Spotify": "Spotify",
    "Steam": "Steam",
    "Synology": "Synology",
    "TVer": "TVer",
    "Telegram": "Telegram",
    "Threads": "Threads",
    "TikTok": "TikTok",
    "Twitch": "Twitch",
    "YouTube": "YouTube",
    "YouTubeMusic": "YouTubeMusic",
    "iCloud": "iCloud",
    "iCloudPrivateRelay": "iCloudPrivateRelay",
    "Twitter": "X",
}

# 反向映射：品牌名 → blackmatrix7 目录名
BRAND_TO_BLACKMATRIX7: dict[str, str] = {v: k for k, v in BLACKMATRIX7_BRAND_MAP.items()}


# ── 白名单清洗 ────────────────────────────────────────────────

def clean(line: str) -> tuple[str, str, str] | None:
    """
    白名单清洗单行规则。

    只保留 ALLOWED_TYPES 中定义的规则类型，非白名单类型直接丢弃。

    Args:
        line: YAML payload 行，如 "  - DOMAIN,google.com" 或 "  - IP-CIDR,1.1.8.0/24"

    Returns:
        (rule_type, value, param) 或 None（丢弃）
    """
    # 去掉 YAML 列表前缀
    stripped = line.strip()
    if not stripped.startswith("- "):
        return None

    raw = stripped[2:].strip()

    # 去掉引号
    if (raw.startswith("'") and raw.endswith("'")) or \
       (raw.startswith('"') and raw.endswith('"')):
        raw = raw[1:-1]

    # 按逗号分割 TYPE,VALUE[,PARAM]
    parts = [p.strip() for p in raw.split(",")]

    if len(parts) < 2:
        return None

    rule_type = parts[0].upper()
    value = parts[1]
    param = parts[2] if len(parts) > 2 else ""

    # 白名单检查
    if rule_type not in ALLOWED_TYPES:
        return None

    # 基本校验
    if not value:
        return None

    # 域名校验
    # DOMAIN-SUFFIX/DOMAIN 必须有 .（拒绝裸词，如 amazon）
    if rule_type in ("DOMAIN-SUFFIX", "DOMAIN") and "." not in value:
        return None

    # DOMAIN-KEYWORD 不能是完整域名（如 abematv.akamaized.net）
    if rule_type == "DOMAIN-KEYWORD" and "." in value:
        return None

    return (rule_type, value, param)


# ── YAML 解析 ─────────────────────────────────────────────────

def parse_blackmatrix7_yaml(content: str) -> list[CanonicalRule]:
    """
    解析 blackmatrix7 YAML 文件内容。

    Args:
        content: YAML 文件内容

    Returns:
        list[CanonicalRule]: 清洗后的规则列表
    """
    rules: list[CanonicalRule] = []
    in_payload = False
    clean_stats = {"before": 0, "after": 0, "dropped": 0}

    for line in content.split("\n"):
        stripped = line.strip()

        # 检测 payload: 开始
        if stripped == "payload:":
            in_payload = True
            continue

        if not in_payload:
            continue

        # 处理 payload 内的行
        result = clean(line)
        if result is None:
            clean_stats["dropped"] += 1
            continue

        rule_type, value, param = result
        clean_stats["before"] += 1

        # 归一化值
        if rule_type in ("DOMAIN", "DOMAIN-SUFFIX", "DOMAIN-KEYWORD",
                         "DOMAIN-REGEX", "DOMAIN-WILDCARD",
                         "IP-CIDR", "IP-CIDR6",
                         "SRC-IP-CIDR", "SRC-IP-CIDR6"):
            # 域名/IP 类统一小写
            value = normalize_value(value)
        elif rule_type.startswith("PROCESS-"):
            # 进程名保持原样
            pass
        elif rule_type.startswith("DST-") or rule_type.startswith("SRC-"):
            # 端口保持原样
            pass

        rules.append(CanonicalRule(
            rule_type=rule_type,
            value=value,
            param=param if param else ("no-resolve" if rule_type in ("IP-CIDR", "IP-CIDR6") else ""),
            source="blackmatrix7",
        ))
        clean_stats["after"] += 1

    return rules


def parse_blackmatrix7_file(filepath: str) -> list[CanonicalRule]:
    """
    解析单个 blackmatrix7 品牌 YAML 文件。

    只读取主文件 <Brand>.yaml，忽略 _No_Resolve 等子集文件。

    Args:
        filepath: YAML 文件路径

    Returns:
        list[CanonicalRule]: 清洗后的规则列表
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
    except (FileNotFoundError, IOError):
        return []

    return parse_blackmatrix7_yaml(content)


# ── 品牌入口 ──────────────────────────────────────────────────

def parse_blackmatrix7_brand(
    brand_name: str,
    blackmatrix7_dir: str,
) -> list[CanonicalRule]:
    """
    按品牌名从 blackmatrix7 获取补充数据。

    只读取主文件 <Brand>.yaml，忽略 _No_Resolve 等子集文件。

    Args:
        brand_name: 品牌名，如 "Google", "Apple"
        blackmatrix7_dir: upstream/blackmatrix7/rule/Clash/ 目录路径

    Returns:
        list[CanonicalRule]: 补充规则列表
    """
    # 查品牌映射
    bm7_name = BRAND_TO_BLACKMATRIX7.get(brand_name)
    if bm7_name is None:
        return []

    # 只读主文件 <Brand>.yaml
    filepath = os.path.join(blackmatrix7_dir, bm7_name, f"{bm7_name}.yaml")
    if not os.path.isfile(filepath):
        return []

    rules = parse_blackmatrix7_file(filepath)
    rules = sort_rules(rules)

    return rules


# ── 批量解析 ──────────────────────────────────────────────────

def parse_blackmatrix7_all(
    blackmatrix7_dir: str,
) -> dict[str, list[CanonicalRule]]:
    """
    解析所有 47 个交集品牌。

    Args:
        blackmatrix7_dir: upstream/blackmatrix7/rule/Clash/ 目录路径

    Returns:
        dict: 品牌名 → [CanonicalRule, ...]
    """
    results: dict[str, list[CanonicalRule]] = {}

    for bm7_name, brand_name in sorted(BLACKMATRIX7_BRAND_MAP.items()):
        filepath = os.path.join(blackmatrix7_dir, bm7_name, f"{bm7_name}.yaml")

        if not os.path.isfile(filepath):
            print(f"  ⚠️ 文件不存在: {bm7_name}/{bm7_name}.yaml")
            results[brand_name] = []
            continue

        rules = parse_blackmatrix7_file(filepath)
        rules = sort_rules(rules)
        results[brand_name] = rules

        type_counts = count_by_type(rules)
        type_str = " + ".join([f"{t}:{c}" for t, c in sorted(type_counts.items())])
        print(f"  ✅ {brand_name}: {len(rules)} 条 [{type_str}]")

    return results


# ── 命令行入口 ─────────────────────────────────────────────────

def main():
    """命令行入口"""
    import sys

    if len(sys.argv) < 2:
        print("用法: python parse_blackmatrix7.py <mode> [args]")
        print("")
        print("模式:")
        print("  brand <品牌名>   按品牌名获取补充数据")
        print("  all              解析所有 47 个交集品牌")
        print("")
        print("示例:")
        print("  python parse_blackmatrix7.py brand Google")
        print("  python parse_blackmatrix7.py all")
        sys.exit(1)

    mode = sys.argv[1]
    bm7_dir = "upstream/blackmatrix7/rule/Clash"

    if mode == "brand":
        if len(sys.argv) < 3:
            print("请指定品牌名")
            sys.exit(1)

        brand_name = sys.argv[2]
        print(f"📥 获取 {brand_name} 的 blackmatrix7 补充数据...")

        rules = parse_blackmatrix7_brand(brand_name, bm7_dir)
        print(f"  共 {len(rules)} 条规则")

        if rules:
            type_counts = count_by_type(rules)
            print(f"\n  类型分布:")
            for t, c in sorted(type_counts.items()):
                print(f"    {t}: {c}")

            print(f"\n  前 10 条:")
            for rule in rules[:10]:
                print(f"    {rule.rule_type},{rule.value}")

    elif mode == "all":
        print("📥 解析所有 blackmatrix7 交集品牌...")
        print(f"  共 {len(BLACKMATRIX7_BRAND_MAP)} 个品牌\n")
        results = parse_blackmatrix7_all(bm7_dir)

        total = sum(len(rules) for rules in results.values())
        print(f"\n📊 总计: {total} 条规则，{len(results)} 个品牌")

    else:
        print(f"未知模式: {mode}")
        sys.exit(1)


if __name__ == "__main__":
    main()