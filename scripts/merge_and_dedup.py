"""
merge_and_dedup.py — 合并去重模块

将三个上游（v2fly / Loyalsoldier / blackmatrix7）的解析结果合并，
去重后排序输出。

数据流：
  v2fly_rules ∪ Loyalsoldier_rules ∪ blackmatrix7_rules
                    ↓ 去重 (TYPE+VALUE)
                    ↓ 排序
             最终 CanonicalRule 列表
"""

import sys
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from lib.canonical import (
    CanonicalRule,
    dedup_key,
    sort_rules,
    count_by_type,
    TYPES_ORDER,
)


# ── 合并 ──────────────────────────────────────────────────────

def merge_rules(
    *rule_lists: list[CanonicalRule],
) -> list[CanonicalRule]:
    """
    合并多个规则列表，去重后排序。

    去重规则：
    - 按 TYPE+VALUE 去重（大小写不敏感）
    - param 不同不影响去重（同 TYPE+VALUE 不同 param 视为重复）
    - source 不同不影响去重（跨上游的同 TYPE+VALUE 应去重）
    - 保留第一个出现的规则

    Args:
        *rule_lists: 可变数量的规则列表

    Returns:
        合并去重排序后的规则列表
    """
    seen: set[str] = set()
    merged: list[CanonicalRule] = []

    for rules in rule_lists:
        for rule in rules:
            key = dedup_key(rule)
            if key not in seen:
                seen.add(key)
                merged.append(rule)

    return sort_rules(merged)


def merge_with_stats(
    *rule_lists: list[CanonicalRule],
) -> dict[str, Any]:
    """
    合并并返回统计信息。

    Args:
        *rule_lists: 可变数量的规则列表

    Returns:
        {
            "rules": [CanonicalRule, ...],
            "total_before": int,
            "total_after": int,
            "dedup_count": int,
            "type_counts": {"DOMAIN-SUFFIX": 123, ...},
        }
    """
    total_before = sum(len(rules) for rules in rule_lists)

    merged = merge_rules(*rule_lists)
    total_after = len(merged)
    dedup_count = total_before - total_after

    return {
        "rules": merged,
        "total_before": total_before,
        "total_after": total_after,
        "dedup_count": dedup_count,
        "type_counts": count_by_type(merged),
    }


# ── 命令行入口 ─────────────────────────────────────────────────

def main():
    """命令行入口：演示合并效果"""
    import sys

    print("📊 merge_and_dedup.py — 合并去重测试")
    print("=" * 50)

    # 演示：从三个上游分别解析一个品牌，然后合并
    from parse_v2fly import parse_v2fly_brand
    from parse_loyalsoldier import parse_loyalsoldier_brand
    from parse_blackmatrix7 import parse_blackmatrix7_brand

    if len(sys.argv) < 2:
        # 默认测试 Google
        test_brands = ["Google"]
    else:
        test_brands = sys.argv[1:]

    for brand_name in test_brands:
        print(f"\n🔍 测试品牌: {brand_name}")

        v2fly_rules = []
        ls_rules = []
        bm7_rules = []

        # v2fly
        try:
            v2fly_result = parse_v2fly_brand(brand_name, "upstream/v2fly/data")
            v2fly_rules = v2fly_result.get("main", [])
            print(f"  v2fly:         {len(v2fly_rules)} 条")
        except Exception as e:
            print(f"  v2fly:         ❌ {e}")

        # Loyalsoldier
        try:
            ls_rules = parse_loyalsoldier_brand(brand_name, "upstream/loyalsoldier")
            print(f"  Loyalsoldier:  {len(ls_rules)} 条")
        except Exception as e:
            print(f"  Loyalsoldier:  ❌ {e}")

        # blackmatrix7
        try:
            bm7_rules = parse_blackmatrix7_brand(brand_name, "upstream/blackmatrix7/rule/Clash")
            print(f"  blackmatrix7:  {len(bm7_rules)} 条")
        except Exception as e:
            print(f"  blackmatrix7:  ❌ {e}")

        # 合并
        result = merge_with_stats(v2fly_rules, ls_rules, bm7_rules)
        rules = result["rules"]

        print(f"\n  📊 合并结果:")
        print(f"     合并前: {result['total_before']} 条")
        print(f"     去重:   {result['dedup_count']} 条")
        print(f"     合并后: {result['total_after']} 条")

        print(f"\n     类型分布:")
        for t, c in sorted(result["type_counts"].items()):
            print(f"       {t}: {c}")

        if rules:
            print(f"\n     前 5 条:")
            for rule in rules[:5]:
                if rule.param:
                    print(f"       {rule.rule_type},{rule.value},{rule.param}")
                else:
                    print(f"       {rule.rule_type},{rule.value}")


if __name__ == "__main__":
    main()