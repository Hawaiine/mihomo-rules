#!/usr/bin/env python3
"""
resolve_ownership.py — 所有权裁决
从父品牌移除子品牌已拥有的规则，避免规则截胡。
使用 commit_writer.write_ruleset 全量再生 YAML+README。
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))

from lib.ownership_map import SUB_PARENT
from lib.canonical import parse_rule_line, sort_rules, CanonicalRule
from commit_writer import write_ruleset

BASE = {'Reject', 'Direct', 'Proxy', 'CNCIDR', 'Private', 'Applications', 'LanCIDR'}


def parse_rules_to_canonical(yaml_path):
    """解析 YAML 文件的 payload 段，返回 CanonicalRule 列表"""
    rules = []
    in_payload = False
    with open(yaml_path) as f:
        for line in f:
            stripped = line.strip()
            if stripped.startswith('payload'):
                in_payload = True
                continue
            if not in_payload:
                continue
            if not stripped or stripped == '[]':
                continue
            cr = parse_rule_line(stripped, 'ownership')
            if cr:
                rules.append(cr)
    return rules


def build_child_rule_set(brands, child_name):
    """构建子品牌规则集合 {(type, value)}"""
    yaml_path = ROOT / 'ruleset' / child_name / f'{child_name}.yaml'
    if not yaml_path.exists():
        return set()
    rules = parse_rules_to_canonical(yaml_path)
    return {(r.rule_type, r.value) for r in rules}


def resolve_ownership(dry_run=True):
    """执行所有权裁决"""
    brands = []
    for d in sorted(os.listdir(ROOT / 'ruleset')):
        if d in BASE:
            continue
        dir_path = ROOT / 'ruleset' / d
        if not dir_path.is_dir():
            continue
        if not (dir_path / f'{d}.yaml').exists():
            continue
        brands.append(d)

    total_removed = 0
    total_pairs = 0
    any_written = False

    for child, parent in SUB_PARENT.items():
        if child not in brands or parent not in brands:
            continue

        parent_yaml = ROOT / 'ruleset' / parent / f'{parent}.yaml'

        # 收集子品牌规则
        child_rule_set = build_child_rule_set(brands, child)
        if not child_rule_set:
            continue

        # 读取父品牌规则
        parent_rules = parse_rules_to_canonical(parent_yaml)

        # 找出重叠并过滤
        to_remove = []
        kept = []
        for r in parent_rules:
            if (r.rule_type, r.value) in child_rule_set:
                to_remove.append(r)
            else:
                kept.append(r)

        if not to_remove:
            continue

        total_pairs += 1
        total_removed += len(to_remove)

        print(f'\n[{parent}] ← [{child}]')
        print(f'  父品牌规则: {len(parent_rules)}')
        print(f'  子品牌规则: {len(child_rule_set)}')
        print(f'  重叠移除: {len(to_remove)}')
        print(f'  剩余: {len(kept)}')

        if dry_run:
            for r in to_remove[:5]:
                print(f'    {r.rule_type},{r.value}')
        else:
            # 排序后写入（全量再生 YAML+README）
            sorted_kept = sort_rules(kept)
            result = write_ruleset(parent, sorted_kept, dry_run=False)
            if result.stats.get('has_changes', True):
                any_written = True
                print(f'  ✅ 已更新: {parent_yaml.name} + README.md')
            else:
                print(f'  ↪ 无变化跳过: {parent_yaml.name}')

    print(f'\n=== 总结 ===')
    print(f'处理父子关系: {total_pairs} 对')
    print(f'移除重叠规则: {total_removed} 条')
    if dry_run:
        print(f'模式: dry-run（未修改文件）')
        print(f'如需实际清理，运行: python3 resolve_ownership.py --apply')
    elif not any_written:
        print(f'所有文件均已为最新（无变化）')


if __name__ == '__main__':
    dry_run = '--apply' not in sys.argv
    if dry_run:
        print('=== resolve_ownership.py (dry-run) ===')
        print('使用 --apply 参数执行实际清理')
    else:
        print('=== resolve_ownership.py (apply) ===')
    resolve_ownership(dry_run)