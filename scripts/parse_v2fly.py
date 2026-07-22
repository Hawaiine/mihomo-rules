"""
parse_v2fly.py — 解析 v2fly/domain-list-community 上游数据

v2fly 语法 → mihomo 规则映射（官方确认）：
┌──────────────────┬──────────────────────┬──────────────────┐
│ v2fly 语法       │ v2fly 语义           │ mihomo 映射      │
├──────────────────┼──────────────────────┼──────────────────┤
│ 裸有效域名       │ 隐式 domain:, 匹配   │ DOMAIN-SUFFIX    │
│ google.com       │ 域名+所有子域名      │                  │
│ 裸无效名         │ 非域名               │ 丢弃             │
│ microsoft        │                      │                  │
│ domain:xxx       │ 匹配+所有子域名      │ DOMAIN-SUFFIX    │
│ full:xxx         │ 精确匹配             │ DOMAIN           │
│ keyword:xxx      │ 关键字搜索           │ DOMAIN-KEYWORD   │
│ regexp:xxx       │ 正则匹配             │ DOMAIN-REGEX     │
│ include:xxx      │ 递归引用             │ 递归解析         │
│ @ads             │ 广告属性             │ ads 规则集       │
│ @cn              │ 国内属性             │ cn 规则集        │
└──────────────────┴──────────────────────┴──────────────────┘
"""

import ipaddress
import os
import re
import sys
from pathlib import Path
from typing import NamedTuple

# 确保 scripts/ 目录在 Python 路径中，使 from lib.xxx 导入正常工作
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from lib.canonical import (
    CanonicalRule,
    KNOWN_PREFIXES,
    normalize_value,
    sort_rules,
    count_by_type,
)
from lib.ownership import OwnershipRegistry


# ── 品牌映射表 ─────────────────────────────────────────────────

# v2fly 文件名 → 我们项目中的品牌名
# 仅包含我们项目中有对应品牌的条目
V2FLY_BRAND_MAP: dict[str, str] = {
    "google": "Google",
    "microsoft": "Microsoft",
    "github": "GitHub",
    "netflix": "Netflix",
    "telegram": "Telegram",
    "openai": "OpenAI",
    "youtube": "YouTube",
    "android": "Android",
    "apple": "Apple",
    "x": "X",
    "facebook": "Facebook",
    "amazon": "Amazon",
    "cloudflare": "Cloudflare",
    "discord": "Discord",
    "spotify": "Spotify",
    "steam": "Steam",
    "adobe": "Adobe",
    "alibaba": "Alibaba",
    "baidu": "Baidu",
    "tencent": "Tencent",
    "bytedance": "ByteDance",
    "xiaomi": "Xiaomi",
    "huawei": "Huawei",
    "oppo": "OPPO",
    "vivo": "Vivo",
    "meituan": "Meituan",
    "jd": "JD",
    "pinduoduo": "Pinduoduo",
    "sina": "Sina",
    "weibo": "Weibo",
    "iqiyi": "iQiyi",
    "bilibili": "Bilibili",
    "douyu": "Douyu",
    "huya": "Huya",
    "kuaishou": "Kuaishou",
    "didichuxing": "DiDi",
    "ctrip": "Ctrip",
    "eleme": "Eleme",
    "dazhongdianping": "Dianping",
    "netease": "Netease",
    "netease-music": "NeteaseMusic",
    "sankuai": "Sankuai",
    "zhihu": "Zhihu",
    "tieba": "Tieba",
    "csdn": "CSDN",
    "docker": "Docker",
    "gitlab": "GitLab",
    "jetbrains": "JetBrains",
    "npmjs": "NPM",
    "pypi": "PyPI",
    "rubygems": "RubyGems",
    "debian": "Debian",
    "ubuntu": "Ubuntu",
    "centos": "CentOS",
    "archlinux": "ArchLinux",
    "fedora": "Fedora",
    "oracle": "Oracle",
    "ibm": "IBM",
    "salesforce": "Salesforce",
    "intel": "Intel",
    "amd": "AMD",
    "nvidia": "NVIDIA",
    "qualcomm": "Qualcomm",
    "broadcom": "Broadcom",
    "cisco": "Cisco",
    "vmware": "VMware",
    "redhat": "RedHat",
    "sap": "SAP",
    "sony": "Sony",
    "nintendo": "Nintendo",
    "epicgames": "EpicGames",
    "roblox": "Roblox",
    "minecraft": "Minecraft",
    "tiktok": "TikTok",
    "snapchat": "Snapchat",
    "pinterest": "Pinterest",
    "reddit": "Reddit",
    "linkedin": "LinkedIn",
    "whatsapp": "WhatsApp",
    "instagram": "Instagram",
    "zoom": "Zoom",
    "slack": "Slack",
    "notion": "Notion",
    "figma": "Figma",
    "canva": "Canva",
    "vercel": "Vercel",
    "netlify": "Netlify",
    "heroku": "Heroku",
    "digitalocean": "DigitalOcean",
    "linode": "Linode",
    "vultr": "Vultr",
    "ovh": "OVH",
    "namecheap": "Namecheap",
    "googledomains": "GoogleDomains",
    "cloudinary": "Cloudinary",
    "fastly": "Fastly",
    "akamai": "Akamai",
    "incapsula": "Incapsula",
    "imperva": "Imperva",
    "sucuri": "Sucuri",
    "stackpath": "StackPath",
    "bunnycdn": "BunnyCDN",
    "keycdn": "KeyCDN",
    "cdn77": "CDN77",
    "gcore": "G-Core",
    "edgecast": "EdgeCast",
    "section": "Section",
    "aws": "AWS",
    "bangumi": "Bangumi",
    "bluesky": "Bluesky",
    "catchplay": "CatchPlay",
    "cursor": "Cursor",
    "hotstar": "Hotstar",
    "manus": "Manus",
    "messenger": "Messenger",
    "metabrainz": "MetaBrainz",
    "musixmatch": "Musixmatch",
    "mytvsuper": "MyTVSuper",
    "nhk": "NHK",
    "perplexity": "Perplexity",
    "poe": "Poe",
    "category-porn": "Porn",
    "google-deepmind": "GoogleAI",
    "radiko": "Radiko",
    "tmdb": "TMDB",
    "tidal": "Tidal",
    "tubi": "Tubi",
    "unext": "UNext",
    "viu": "Viu",
    "wsj": "WSJ",
}

# 反向映射：品牌名 → v2fly 文件名
BRAND_TO_V2FLY: dict[str, str] = {v: k for k, v in V2FLY_BRAND_MAP.items()}


# ── 分类文件映射（非品牌，对应兜底规则集） ────────────────────

KNOWN_CATEGORY_FILES: dict[str, str] = {
    "category-ads": "Reject",
    "category-ads-all": "Reject",
    "geolocation-!cn": "Proxy",
    "cn": "Direct",
    "private": "Private",
}


# ── TLD 列表 ──────────────────────────────────────────────────

# 常见 TLD 正则（2-24 位字母，部分含国际化）
TLD_RE = re.compile(r"^[a-z]{2,}$")


# ── v2fly 行解析正则 ──────────────────────────────────────────

# v2fly 语法：prefix:value @attr1 @attr2 &affiliation
# prefix 可以是 domain:/full:/keyword:/regexp:/include:
V2FLY_LINE_RE = re.compile(
    r"^\s*"
    r"(?P<prefix>domain|full|keyword|regexp|include):"
    r"(?P<value>\S+?)"
    r"(?:\s+@(?P<attrs>[\w!-]+(?:\s+@[\w!-]+)*))?"
    r"(?:\s+&(?P<affils>[\w!-]+(?:\s+&[\w!-]+)*))?"
    r"\s*"
    r"(?:\s*#.*)?$",
    re.IGNORECASE,
)

# 裸域名正则（不含前缀，不含属性）
BARE_DOMAIN_RE = re.compile(
    r"^\s*"
    r"(?P<value>\S+?)"
    r"(?:\s+@(?P<attrs>[\w!-]+(?:\s+@[\w!-]+)*))?"
    r"(?:\s+&(?P<affils>[\w!-]+(?:\s+&[\w!-]+)*))?"
    r"\s*"
    r"(?:\s*#.*)?$",
    re.IGNORECASE,
)


# ── 行解析结果类型 ────────────────────────────────────────────

class V2FlyLineResult(NamedTuple):
    """v2fly 行解析结果"""
    kind: str  # "rule" / "include" / "comment" / "empty" / "invalid"
    prefix: str  # domain / full / keyword / regexp / include
    value: str  # 原始值
    attrs: list[str]  # 属性列表，如 ["ads", "cn"]
    raw_line: str  # 原始行


# ── 域名有效性判断 ────────────────────────────────────────────

def is_valid_domain(value: str) -> bool:
    """
    判断裸值是否为有效域名。

    条件：
    1. 含至少一个 '.'
    2. 每个 label 合法
    3. TLD 纯字母 ≥2

    Args:
        value: 待判断的值

    Returns:
        bool: 是否有效域名
    """
    value = normalize_value(value)
    if not value:
        return False

    if "." not in value:
        return False

    labels = value.split(".")
    if len(labels) < 2:
        return False

    # 检查每个 label
    for label in labels:
        if not label:
            return False
        if len(label) > 63:
            return False
        if not re.match(r"^[a-z0-9-]+$", label):
            return False
        if label.startswith("-") or label.endswith("-"):
            return False

    # 检查 TLD
    tld = labels[-1]
    if not TLD_RE.match(tld):
        return False

    # 拒绝 IP 地址
    try:
        ipaddress.ip_address(value)
        return False
    except ValueError:
        pass

    return True


# ── v2fly 行解析 ──────────────────────────────────────────────

def parse_v2fly_line(line: str) -> V2FlyLineResult:
    """
    解析 v2fly 数据文件的单行。

    Args:
        line: 原始行

    Returns:
        V2FlyLineResult
    """
    line = line.strip()
    if not line:
        return V2FlyLineResult("empty", "", "", [], line)

    # 注释行
    if line.startswith("#"):
        return V2FlyLineResult("comment", "", "", [], line)

    # 尝试匹配 v2fly 语法行（prefix:value @attr）
    m = V2FLY_LINE_RE.match(line)
    if m:
        prefix = m.group("prefix").lower()
        value = m.group("value").strip()
        attrs = []
        if m.group("attrs"):
            attrs = [a.strip() for a in m.group("attrs").split("@") if a.strip()]

        if prefix == "include":
            return V2FlyLineResult("include", prefix, value, attrs, line)

        return V2FlyLineResult("rule", prefix, value, attrs, line)

    # 尝试匹配裸域名（无前缀）
    m = BARE_DOMAIN_RE.match(line)
    if m:
        value = m.group("value").strip()
        attrs = []
        if m.group("attrs"):
            attrs = [a.strip() for a in m.group("attrs").split("@") if a.strip()]

        # 判断是否为有效域名
        if is_valid_domain(value):
            return V2FlyLineResult("rule", "domain", value, attrs, line)
        else:
            return V2FlyLineResult("invalid", "", value, attrs, line)

    return V2FlyLineResult("invalid", "", "", [], line)


# ── v2fly 规则行 → CanonicalRule ─────────────────────────────

def convert_v2fly_rule(
    result: V2FlyLineResult,
    source: str = "v2fly",
) -> CanonicalRule | None:
    """
    将解析后的 v2fly 规则行转换为 CanonicalRule。

    v2fly 语法 → mihomo 规则映射：
    - domain:xxx / 裸有效域名 → DOMAIN-SUFFIX,xxx
    - full:xxx → DOMAIN,xxx
    - keyword:xxx → DOMAIN-KEYWORD,xxx
    - regexp:xxx → DOMAIN-REGEX,xxx

    Args:
        result: v2fly 行解析结果
        source: 上游标识

    Returns:
        CanonicalRule 或 None（无法转换时）
    """
    if result.kind != "rule":
        return None

    prefix = result.prefix
    value = normalize_value(result.value)

    if prefix == "domain":
        # domain:xxx / 裸有效域名 → DOMAIN-SUFFIX,xxx
        return CanonicalRule(
            rule_type="DOMAIN-SUFFIX",
            value=value,
            param="",
            source=source,
        )
    elif prefix == "full":
        # full:xxx → DOMAIN,xxx
        return CanonicalRule(
            rule_type="DOMAIN",
            value=value,
            param="",
            source=source,
        )
    elif prefix == "keyword":
        # keyword:xxx → DOMAIN-KEYWORD,xxx
        return CanonicalRule(
            rule_type="DOMAIN-KEYWORD",
            value=value,
            param="",
            source=source,
        )
    elif prefix == "regexp":
        # regexp:xxx → DOMAIN-REGEX,xxx
        return CanonicalRule(
            rule_type="DOMAIN-REGEX",
            value=value,
            param="",
            source=source,
        )

    return None


# ── 属性过滤 ──────────────────────────────────────────────────

def filter_attributes(
    rules: list[CanonicalRule],
    attrs_list: list[list[str]],
) -> tuple[list[CanonicalRule], list[CanonicalRule], list[CanonicalRule]]:
    """
    按 @ads / @cn 属性分类规则。

    Args:
        rules: 规则列表
        attrs_list: 每条规则对应的属性列表，与 rules 等长

    Returns:
        (main_rules, ads_rules, cn_rules)
    """
    main_rules: list[CanonicalRule] = []
    ads_rules: list[CanonicalRule] = []
    cn_rules: list[CanonicalRule] = []

    for i, rule in enumerate(rules):
        attrs = attrs_list[i] if i < len(attrs_list) else []
        has_ads = "ads" in attrs
        has_cn = "cn" in attrs

        if has_ads:
            ads_rules.append(rule)
        elif has_cn:
            cn_rules.append(rule)
        else:
            main_rules.append(rule)

    return main_rules, ads_rules, cn_rules


# ── 归属过滤 ──────────────────────────────────────────────────

def filter_by_ownership(
    rules: list[CanonicalRule],
    brand_name: str,
    ownership: OwnershipRegistry,
) -> list[CanonicalRule]:
    """
    归属过滤：排除已被其他品牌认领的域名。

    只对 DOMAIN / DOMAIN-SUFFIX 类型做归属检查。
    其他类型（IP-CIDR / IP-ASN / PROCESS-NAME 等）不做归属过滤。

    Args:
        rules: 候选规则列表
        brand_name: 当前品牌名
        ownership: 归属注册表

    Returns:
        过滤后的规则列表
    """
    filtered: list[CanonicalRule] = []

    for rule in rules:
        if rule.rule_type not in ("DOMAIN", "DOMAIN-SUFFIX"):
            # 非域名类型不做归属过滤
            filtered.append(rule)
            continue

        # 查域名归属
        owner = ownership.query_owner(rule.value)
        if owner is None or owner == brand_name:
            # 未认领或属于当前品牌 → 保留
            filtered.append(rule)
        # 已被其他品牌认领 → 排除

    return filtered


# ── 递归解析 include ──────────────────────────────────────────

MAX_INCLUDE_DEPTH = 10


def resolve_include(
    file_name: str,
    base_dir: str,
    visited: set[str],
    depth: int,
    attrs: list[str] | None = None,
    ownership: OwnershipRegistry | None = None,
    current_brand: str = "",
) -> list[tuple[CanonicalRule, list[str]]]:
    """
    解析 include:xxx 指令，递归读取目标文件。

    Args:
        file_name: 要引入的文件名（不含路径）
        base_dir: data/ 目录路径
        visited: 已访问文件集合（防循环）
        depth: 当前递归深度
        attrs: include 上的属性过滤（如 @ads @-cn）
        ownership: 归属注册表（可选）
        current_brand: 当前品牌名

    Returns:
        [(CanonicalRule, attrs), ...] 规则+属性列表
    """
    if depth >= MAX_INCLUDE_DEPTH:
        return []

    # 规范化文件名
    file_name = file_name.strip()
    if file_name in visited:
        return []

    visited.add(file_name)

    filepath = os.path.join(base_dir, file_name)
    if not os.path.isfile(filepath):
        return []

    return _parse_v2fly_file_internal(filepath, base_dir, visited, depth + 1, ownership, current_brand)


# ── 内部文件解析 ──────────────────────────────────────────────

def _parse_v2fly_file_internal(
    filepath: str,
    base_dir: str,
    visited: set[str],
    depth: int = 0,
    ownership: OwnershipRegistry | None = None,
    current_brand: str = "",
) -> list[tuple[CanonicalRule, list[str]]]:
    """
    解析单个 v2fly 数据文件。

    Args:
        filepath: 文件路径
        base_dir: data/ 目录
        visited: 已访问文件集合
        depth: 当前递归深度
        ownership: 归属注册表
        current_brand: 当前品牌名

    Returns:
        [(CanonicalRule, attrs), ...] 规则+属性列表
    """
    results: list[tuple[CanonicalRule, list[str]]] = []

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except (FileNotFoundError, IOError):
        return results

    for line in lines:
        parsed = parse_v2fly_line(line)

        if parsed.kind == "include":
            # include:xxx @attr1 @-attr2
            include_attrs = parsed.attrs
            # 判断是否要选择性包含（带 @attr 过滤）
            # 这里简单处理：传递 include 上的属性，由调用方决定
            sub_rules = resolve_include(
                parsed.value,
                base_dir,
                visited,
                depth,
                include_attrs,
                ownership,
                current_brand,
            )
            results.extend(sub_rules)

        elif parsed.kind == "rule":
            rule = convert_v2fly_rule(parsed, source="v2fly")
            if rule is not None:
                results.append((rule, parsed.attrs))

        # comment / empty / invalid → 跳过

    return results


# ── 公开入口：解析 v2fly 数据文件 ──────────────────────────────

def parse_v2fly_file(
    filepath: str,
    base_dir: str,
    visited: set[str] | None = None,
    depth: int = 0,
) -> list[tuple[CanonicalRule, list[str]]]:
    """
    解析单个 v2fly 数据文件（公开入口）。

    Args:
        filepath: 文件路径
        base_dir: data/ 目录
        visited: 已访问文件集合
        depth: 当前递归深度

    Returns:
        [(CanonicalRule, attrs), ...] 规则+属性列表
    """
    if visited is None:
        visited = set()
    return _parse_v2fly_file_internal(filepath, base_dir, visited, depth)


# ── 公开入口：按品牌名解析 ─────────────────────────────────────

def parse_v2fly_brand(
    brand_name: str,
    v2fly_data_dir: str,
    ownership: OwnershipRegistry | None = None,
) -> dict[str, list[CanonicalRule]]:
    """
    按品牌名从 v2fly 上游解析数据。

    流程：
    1. 查品牌映射表，获取 v2fly 文件名
    2. 读取并解析文件（含 include 递归）
    3. 属性过滤（分离 main/ads/cn）
    4. 归属过滤（排除已被其他品牌认领的域名）
    5. 排序

    Args:
        brand_name: 我们项目中的品牌名，如 "Google"
        v2fly_data_dir: upstream/v2fly/data/ 目录路径
        ownership: 归属注册表（可选）

    Returns:
        {
            "main": [CanonicalRule, ...],   # 主规则
            "ads":  [CanonicalRule, ...],   # 广告规则
            "cn":   [CanonicalRule, ...],   # 国内规则
        }
    """
    # 查品牌映射
    v2fly_name = BRAND_TO_V2FLY.get(brand_name)
    if v2fly_name is None:
        return {"main": [], "ads": [], "cn": []}

    filepath = os.path.join(v2fly_data_dir, v2fly_name)
    if not os.path.isfile(filepath):
        return {"main": [], "ads": [], "cn": []}

    # 解析文件
    raw_results = parse_v2fly_file(filepath, v2fly_data_dir)

    # 分离规则和属性
    rules: list[CanonicalRule] = []
    attrs_list: list[list[str]] = []
    for rule, attrs in raw_results:
        rules.append(rule)
        attrs_list.append(attrs)

    # 归属过滤
    if ownership is not None:
        rules = filter_by_ownership(rules, brand_name, ownership)
        # 同步过滤 attrs_list
        # 注意：归属过滤后 rules 可能减少，需要同步截断 attrs_list
        # 这里简化为：如果 ownership 启用，重新收集 attrs
        # 实际应该回传过滤后的索引，但为了简单，重新解析一遍
        # 更好的做法：filter_by_ownership 返回 (filtered_rules, filtered_indices)
        # 但当前简化：ownership 过滤后直接从原始数据重新匹配
        # 重新构建 rule → attrs 映射
        rule_to_attrs: dict[str, list[str]] = {}
        for r, a in zip(rules, attrs_list[:len(rules)]):
            key = f"{r.rule_type}|{r.value}"
            if key not in rule_to_attrs:
                rule_to_attrs[key] = a

        # 归属过滤后重新匹配属性
        filtered_attrs: list[list[str]] = []
        for r in rules:
            key = f"{r.rule_type}|{r.value}"
            filtered_attrs.append(rule_to_attrs.get(key, []))
        attrs_list = filtered_attrs

    # 属性过滤
    main_rules, ads_rules, cn_rules = filter_attributes(rules, attrs_list)

    # 排序
    main_rules = sort_rules(main_rules)
    ads_rules = sort_rules(ads_rules)
    cn_rules = sort_rules(cn_rules)

    return {
        "main": main_rules,
        "ads": ads_rules,
        "cn": cn_rules,
    }


# ── 命令行入口 ─────────────────────────────────────────────────

def main():
    """命令行入口：测试解析指定品牌"""
    import sys

    if len(sys.argv) < 2:
        print("用法: python parse_v2fly.py <品牌名> [v2fly_data_dir]")
        print("示例: python parse_v2fly.py Google")
        sys.exit(1)

    brand_name = sys.argv[1]
    v2fly_data_dir = sys.argv[2] if len(sys.argv) > 2 else "upstream/v2fly/data"

    # 加载归属表
    ownership = OwnershipRegistry.load("ownership.json")

    result = parse_v2fly_brand(brand_name, v2fly_data_dir, ownership)

    print(f"📊 {brand_name} 解析结果:")
    print(f"  主规则: {len(result['main'])} 条")
    print(f"  ads 规则: {len(result['ads'])} 条")
    print(f"  cn 规则: {len(result['cn'])} 条")

    # 按类型统计
    main_counts = count_by_type(result["main"])
    print(f"\n  主规则类型分布:")
    for t, c in sorted(main_counts.items()):
        print(f"    {t}: {c}")

    # 打印前 10 条
    print(f"\n  前 10 条主规则:")
    for rule in result["main"][:10]:
        print(f"    {rule.rule_type},{rule.value}")


if __name__ == "__main__":
    main()