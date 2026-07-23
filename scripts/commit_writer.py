"""
commit_writer.py — 唯一写入 ruleset/ 的模块

确保：
1. 生成临时 YAML 文件（含 header 注释）
2. diff 与当前正式文件对比
3. 校验通过才原子写入覆盖
4. 同时更新 README.md
"""

import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

import sys
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from lib.canonical import CanonicalRule, count_by_type, TYPES_ORDER


# ── 策略组映射 ─────────────────────────────────────────────────

STRATEGY_GROUP_MAP: dict[str, str] = {
    "PTChina": "PT China",
    "PornChina": "Porn China",
    "F1TV": "F1 TV",
    "SiriAI": "Siri AI",
    "YouTubeMusic": "YouTube Music",
    "ZLibrary": "Z-Library",
    "iCloudPrivateRelay": "iCloud Private Relay",
    "GeneralAI": "General AI",
    "DMMTV": "DMM TV",
    "RakutenTV": "Rakuten TV",
    "AppleTV": "Apple TV",
    'CatchPlay': 'CATCHPLAY+',
    'CATCHPLAY': 'CATCHPLAY+',
    'Cloudflare': 'Cloudflare',
    "DAnimeStore": "D Anime Store",
    "FujiTV": "Fuji TV",
    "GameJapan": "Game Japan",
    "GoogleAI": "Google AI",
    "HOYTV": "HOY TV",
    "HamiVideo": "Hami Video",
    "LiTV": "LiTV",
    "LineTV": "LINE TV",
    "LINETV": "LINE TV",
    "MusicJapan": "Music Japan",
    "myTVSuper": "myTV Super",
    "MyVideo": "MyVideo",
    "NowE": "Now E",
    "PrimeVideo": "Prime Video",
    "ReadsJapan": "Reads Japan",
    "UNext": "U-NEXT",
    "VideoMarket": "Video Market",
    "friDayvideo": "friDay video",
    "karaokeDAM": "Karaoke@DAM",
}


def get_strategy_group(brand_name: str) -> str:
    """获取策略组名"""
    return STRATEGY_GROUP_MAP.get(brand_name, brand_name)


def determine_behavior(rules: list[CanonicalRule]) -> str:
    """
    自动判断 behavior 类型。

    - domain: 仅含 DOMAIN / DOMAIN-SUFFIX / DOMAIN-KEYWORD / DOMAIN-REGEX 类规则
    - classical: 含 IP-CIDR / IP-CIDR6 / IP-ASN / PROCESS-NAME 中任一类型
    """
    classical_types = {"IP-CIDR", "IP-CIDR6", "IP-ASN",
                       "PROCESS-NAME", "PROCESS-NAME-WILDCARD", "PROCESS-NAME-REGEX",
                       "PROCESS-PATH", "PROCESS-PATH-WILDCARD", "PROCESS-PATH-REGEX",
                       "SRC-IP-CIDR", "SRC-IP-CIDR6", "DST-PORT", "SRC-PORT"}

    for rule in rules:
        if rule.rule_type in classical_types:
            return "classical"
    return "domain"


# ── YAML 生成 ──────────────────────────────────────────────────

def generate_yaml(
    brand_name: str,
    rules: list[CanonicalRule],
    strategy_group: str = "",
) -> str:
    """
    生成规则集 YAML 内容。

    Args:
        brand_name: 品牌名
        rules: 规则列表（已排序）
        strategy_group: 策略组名（可选，默认从映射表获取）

    Returns:
        str: YAML 文件内容
    """
    if not strategy_group:
        strategy_group = get_strategy_group(brand_name)

    type_counts = count_by_type(rules)
    total = sum(type_counts.values())

    # 当前时间（UTC+8）
    now = datetime.now(timezone.utc).astimezone()
    beijing_time = now.strftime("%Y-%m-%d %H:%M:%S")

    lines: list[str] = []
    lines.append("# ===========================================")
    lines.append(f"# Rule Name: {strategy_group}")
    lines.append("# Author: Hawaiine")
    lines.append(f"# Updated: {beijing_time}")
    for t in TYPES_ORDER:
        lines.append(f"# {t}: {type_counts.get(t, 0)}")
    lines.append("# ===========================================")
    lines.append("payload:")

    for rule in rules:
        if rule.param:
            lines.append(f"  - {rule.rule_type},{rule.value},{rule.param}")
        else:
            lines.append(f"  - {rule.rule_type},{rule.value}")

    return "\n".join(lines) + "\n"


# ── README 生成 ────────────────────────────────────────────────

def generate_readme(
    brand_name: str,
    rules: list[CanonicalRule],
    behavior: str = "",
    strategy_group: str = "",
) -> str:
    """
    生成规则集 README 内容。

    Args:
        brand_name: 品牌名
        rules: 规则列表
        behavior: behavior 类型（可选，自动检测）
        strategy_group: 策略组名（可选）

    Returns:
        str: README 文件内容
    """
    if not strategy_group:
        strategy_group = get_strategy_group(brand_name)
    if not behavior:
        behavior = determine_behavior(rules)

    type_counts = count_by_type(rules)

    lines: list[str] = []
    lines.append(f"# 📦 {strategy_group} 规则集")
    lines.append("")
    lines.append("## 📊 统计")
    lines.append("| 类型 | 数量 |")
    lines.append("|------|------|")
    for t in TYPES_ORDER:
        lines.append(f"| {t} | {type_counts.get(t, 0)} |")
    lines.append("")
    lines.append(f"- **behavior**: {behavior}")
    lines.append(f"- **策略组**: {strategy_group}")

    return "\n".join(lines) + "\n"


# ── 写入结果类型 ──────────────────────────────────────────────

class WriteResult(NamedTuple):
    """写入结果"""
    success: bool
    diff: str
    stats: dict
    error: str


# ── 原子写入 ──────────────────────────────────────────────────

def atomic_write(content: str, final_path: str) -> tuple[bool, str]:
    """
    原子写入文件：先写临时文件，再 rename 覆盖。

    Args:
        content: 文件内容
        final_path: 最终文件路径

    Returns:
        (success, error_msg)
    """
    try:
        # 确保目标目录存在
        os.makedirs(os.path.dirname(final_path), exist_ok=True)

        # 写临时文件
        fd, temp_path = tempfile.mkstemp(
            dir=os.path.dirname(final_path),
            prefix=".tmp_",
            suffix=".yaml",
        )
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)

        # 原子 rename
        shutil.move(temp_path, final_path)
        return True, ""
    except Exception as e:
        return False, str(e)


# ── 差异报告 ──────────────────────────────────────────────────

def diff_report(old_path: str, new_content: str) -> str:
    """
    生成差异报告（新内容 vs 现有文件）。

    Args:
        old_path: 现有文件路径
        new_content: 新内容

    Returns:
        str: 差异文本
    """
    import difflib

    try:
        with open(old_path, "r", encoding="utf-8") as f:
            old_lines = f.readlines()
    except FileNotFoundError:
        old_lines = []

    new_lines = new_content.splitlines(keepends=True)

    diff = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=os.path.basename(old_path),
        tofile=os.path.basename(old_path) + " (new)",
        lineterm="",
    )

    return "\n".join(diff)


# ── 写入规则集 ────────────────────────────────────────────────

def write_ruleset(
    brand_name: str,
    rules: list[CanonicalRule],
    strategy_group: str = "",
    dry_run: bool = False,
) -> WriteResult:
    """
    写入品牌规则集到 ruleset/ 目录。

    流程：
    1. 生成 YAML 内容
    2. diff 对比现有文件
    3. 原子写入 YAML
    4. 生成并写入 README
    5. 返回结果

    Args:
        brand_name: 品牌名
        rules: 规则列表（已排序）
        strategy_group: 策略组名（可选）
        dry_run: 仅预览，不实际写入

    Returns:
        WriteResult
    """
    if not strategy_group:
        strategy_group = get_strategy_group(brand_name)

    behavior = determine_behavior(rules)
    type_counts = count_by_type(rules)

    # 生成内容
    yaml_content = generate_yaml(brand_name, rules, strategy_group)
    readme_content = generate_readme(brand_name, rules, behavior, strategy_group)

    # 文件路径
    ruleset_dir = os.path.join("ruleset", brand_name)
    yaml_path = os.path.join(ruleset_dir, f"{brand_name}.yaml")
    readme_path = os.path.join(ruleset_dir, "README.md")

    # 差异报告
    diff = diff_report(yaml_path, yaml_content)

    stats = {
        "brand": brand_name,
        "strategy_group": strategy_group,
        "behavior": behavior,
        "total": sum(type_counts.values()),
        "type_counts": type_counts,
        "yaml_path": yaml_path,
        "readme_path": readme_path,
        "has_changes": bool(diff.strip()),
    }

    if dry_run:
        return WriteResult(
            success=True,
            diff=diff,
            stats=stats,
            error="",
        )

    # 检查是否有实质性变化（跳过仅时间戳的噪音变更）
    if diff.strip():
        meaningful_lines = [l for l in diff.split('\n')
                          if l and not l.startswith('@@') and not l.startswith('---')
                          and not l.startswith('+++') and not l.startswith('diff')
                          and not l.startswith('index') and not l.startswith('new file')
                          and not l.startswith('deleted')
                          and 'Updated:' not in l]
        if not meaningful_lines:
            stats['has_changes'] = False
            return WriteResult(
                success=True,
                diff="",
                stats=stats,
                error="",
            )

    # 写入 YAML
    yaml_ok, yaml_err = atomic_write(yaml_content, yaml_path)
    if not yaml_ok:
        return WriteResult(
            success=False,
            diff=diff,
            stats=stats,
            error=f"写入 YAML 失败: {yaml_err}",
        )

    # 写入 README
    readme_ok, readme_err = atomic_write(readme_content, readme_path)
    if not readme_ok:
        return WriteResult(
            success=False,
            diff=diff,
            stats=stats,
            error=f"写入 README 失败: {readme_err}",
        )

    return WriteResult(
        success=True,
        diff=diff,
        stats=stats,
        error="",
    )


# ── 命令行入口 ─────────────────────────────────────────────────

def main():
    """命令行入口"""
    import sys
    from merge_and_dedup import merge_with_stats
    from parse_v2fly import parse_v2fly_brand
    from parse_loyalsoldier import parse_loyalsoldier_brand
    from parse_blackmatrix7 import parse_blackmatrix7_brand

    if len(sys.argv) < 2:
        print("用法: python commit_writer.py <brand_name> [--dry-run]")
        print("示例: python commit_writer.py Google")
        print("      python commit_writer.py Google --dry-run")
        sys.exit(1)

    brand_name = sys.argv[1]
    dry_run = "--dry-run" in sys.argv

    # 从三个上游解析并合并
    print(f"📥 解析 {brand_name}...")

    v2fly_result = parse_v2fly_brand(brand_name, "upstream/v2fly/data")
    v2fly_rules = v2fly_result.get("main", [])
    ls_rules = parse_loyalsoldier_brand(brand_name, "upstream/loyalsoldier")
    bm7_rules = parse_blackmatrix7_brand(brand_name, "upstream/blackmatrix7/rule/Clash")

    merged = merge_with_stats(v2fly_rules, ls_rules, bm7_rules)
    rules = merged["rules"]

    print(f"  v2fly: {len(v2fly_rules)} + Loyalsoldier: {len(ls_rules)} + blackmatrix7: {len(bm7_rules)}")
    print(f"  → 合并后: {len(rules)} 条 (去重 {merged['dedup_count']} 条)")

    # 写入
    result = write_ruleset(brand_name, rules, dry_run=dry_run)

    if dry_run:
        print(f"\n📋 预览 (dry-run):")
        print(f"  behavior: {result.stats['behavior']}")
        print(f"  策略组: {result.stats['strategy_group']}")
        print(f"  路径: {result.stats['yaml_path']}")
        print(f"  变化: {'有' if result.stats['has_changes'] else '无'}")
        if result.diff:
            print(f"\n  Diff:")
            for line in result.diff.split("\n")[:20]:
                print(f"    {line}")
            lines = result.diff.split("\n")
            if len(lines) > 20:
                remaining = len(lines) - 20
                print(f"    ... 还有 {remaining} 行")
        print(f"\n  YAML 内容预览:")
        yaml_content = generate_yaml(brand_name, rules)
        for line in yaml_content.split("\n")[:15]:
            print(f"    {line}")
        print(f"    ... 共 {len(rules)} 条规则")
    else:
        if result.success:
            print(f"\n✅ 写入成功!")
            print(f"  {result.stats['yaml_path']}")
            print(f"  {result.stats['readme_path']}")
            if result.diff:
                print(f"  变更行数: {len(result.diff.split(chr(10)))}")
        else:
            print(f"\n❌ 写入失败: {result.error}")


if __name__ == "__main__":
    main()