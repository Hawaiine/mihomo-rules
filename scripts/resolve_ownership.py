#!/usr/bin/env python3
"""
resolve_ownership.py — 所有权裁决
从父品牌移除子品牌已拥有的规则，避免规则截胡。
使用 commit_writer.write_ruleset 全量再生 YAML+README。

支持链式关系（YouTubeMusic→YouTube→Google）：通过祖先链解析，
从每个祖先（直接父、祖父、...最顶层）移除该子品牌的规则，
消除对 SUB_PARENT dict 顺序的依赖。
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))

from lib.ownership_map import SUB_PARENT
from lib.canonical import parse_rule_line, sort_rules, CanonicalRule
from commit_writer import write_ruleset

BASE = {'Reject', 'Direct', 'Proxy', 'CNCIDR', 'Private', 'Applications', 'LanCIDR', 'DirectDNS', 'ProxyDNS'}


def resolve_ancestor_chain(brand, sub_parent):
    """解析品牌的所有祖先链（从直接父到最顶层），顺序无关。

    例: YouTubeMusic → ['YouTube', 'Google']
    含循环引用保护。
    """
    chain = []
    seen = {brand}
    current = brand
    while current in sub_parent:
        current = sub_parent[current]
        if current in seen:
            break  # 循环引用保护
        seen.add(current)
        chain.append(current)
    return chain


def is_owned_by_child(rule, child_rule_set):
    """父品牌规则是否已被子品牌拥有（应剥离）。

    两种情形：
    1. 完全同 TYPE+VALUE（原有口径）。
    2. 父品牌是精确域名、子品牌持有同值后缀：DOMAIN-SUFFIX,x 已声明 x 及
       其全部子域，父品牌不应再单独持有 DOMAIN,x（否则去重/清理后被重新长回来）。
    """
    if (rule.rule_type, rule.value) in child_rule_set:
        return True
    return rule.rule_type == 'DOMAIN' and ('DOMAIN-SUFFIX', rule.value) in child_rule_set


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


def find_parent_suffix_child_domain_overlaps(brands):
    """父品牌持 DOMAIN-SUFFIX,x 且子品牌持 DOMAIN,x 的形态。

    该形态**不应自动剥离**：父品牌的 DOMAIN-SUFFIX 同时覆盖 x 的全部子域
    （如 Apple 的 DOMAIN-SUFFIX,podcasts.apple.com 覆盖 amp-api.podcasts.apple.com），
    移除父规则会造成子域覆盖丢失。实际归属由 RULE-SET 顺序决定——子品牌规则集
    必须排在父品牌之前（generate_config.sort_brands），由子品牌的精确 DOMAIN 优先命中。

    本函数只做**报告**，让 "0 对重叠" 不会被误读为 "不存在重叠"。
    """
    overlaps = []
    for child in sorted(SUB_PARENT.keys()):
        if child not in brands:
            continue
        child_rules = build_child_rule_set(brands, child)
        if not child_rules:
            continue
        for parent in resolve_ancestor_chain(child, SUB_PARENT):
            if parent not in brands:
                continue
            parent_rules = build_child_rule_set(brands, parent)
            for rtype, value in sorted(child_rules):
                if rtype == 'DOMAIN' and ('DOMAIN-SUFFIX', value) in parent_rules:
                    overlaps.append((child, parent, value))
    return overlaps


def find_child_contains_parent_rules(brands):
    """子品牌规则集中出现祖先品牌规则（**child→parent 方向**）。

    §22–§30 要求 ownership 审计必须**双向**：只检查「父是否含子」会结构性漏掉
    「子是否含父 / 父公司通用域 / 父生态基础设施」。新增或拆分规则集会改变整个
    ownership 图，因此必须触发对既有规则集的反向重审（§41）。

    该形态**不得机械剥离**：命中项可能是
      * child-service —— 子品牌专属子域（如 photos.googleapis.com 属 GooglePhotos）
      * shared —— 父子双方都真实需要
      * parent-infrastructure —— 子品牌实际依赖的父基础设施
    删除前必须逐条确认该域是否承担子品牌 routing 所需的真实功能。
    故本函数只做**报告**，返回 (child, parent, rule_type, value)。
    """
    hits = []
    for child in sorted(SUB_PARENT.keys()):
        if child not in brands:
            continue
        child_rules = build_child_rule_set(brands, child)
        if not child_rules:
            continue
        for parent in resolve_ancestor_chain(child, SUB_PARENT):
            if parent not in brands:
                continue
            shared = child_rules & build_child_rule_set(brands, parent)
            for rtype, value in sorted(shared):
                hits.append((child, parent, rtype, value))
    return hits


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

    # 对每个子品牌，解析其完整祖先链，从每个祖先移除该子品牌规则
    for child in sorted(SUB_PARENT.keys()):
        if child not in brands:
            continue

        # 收集子品牌规则（只解析一次）
        child_rule_set = build_child_rule_set(brands, child)
        if not child_rule_set:
            continue

        # 解析祖先链（顺序无关）
        ancestors = resolve_ancestor_chain(child, SUB_PARENT)
        for parent in ancestors:
            if parent not in brands:
                continue

            parent_yaml = ROOT / 'ruleset' / parent / f'{parent}.yaml'

            # 读取父品牌规则
            parent_rules = parse_rules_to_canonical(parent_yaml)

            # 找出重叠并过滤
            to_remove = []
            kept = []
            for r in parent_rules:
                if is_owned_by_child(r, child_rule_set):
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

    overlaps = find_parent_suffix_child_domain_overlaps(brands)
    if overlaps:
        print(f'\n=== 未覆盖形态（仅报告，不剥离）: 父 DOMAIN-SUFFIX + 子 DOMAIN ===')
        for child, parent, value in overlaps:
            print(f'  [{parent}] DOMAIN-SUFFIX,{value}  ↔  [{child}] DOMAIN,{value}')
        print(f'  共 {len(overlaps)} 条；父后缀仍覆盖其子域，剥离会造成覆盖丢失；'
              '归属由子品牌 RULE-SET 前置（sort_brands）保证。')

    if dry_run:
        print(f'模式: dry-run（未修改文件）')
        print(f'如需实际清理，运行: python3 resolve_ownership.py --apply')
    elif not any_written:
        print(f'所有文件均已为最新（无变化）')

    rev = find_child_contains_parent_rules(brands)
    print(f'\n=== 反向审计（仅报告，不剥离）: 子品牌含祖先品牌规则（child→parent）===')
    if rev:
        for child, parent, rtype, value in rev:
            print(f'  [{child}] {rtype},{value}  ⊂  [{parent}]  '
                  f'(ownership_direction=child→parent)')
        print(f'  共 {len(rev)} 条；可能是 child-service（子品牌专属子域）或 shared / '
              'parent-infrastructure，**不得机械从子品牌删除**——删除前须逐条确认 '
              '该域是否承担子品牌 routing 所需的真实功能（§25/§28/§29）。')
    else:
        print('  0 条 —— 子品牌规则集中未出现祖先品牌的同名规则。')


if __name__ == '__main__':
    dry_run = '--apply' not in sys.argv
    if dry_run:
        print('=== resolve_ownership.py (dry-run) ===')
        print('使用 --apply 参数执行实际清理')
    else:
        print('=== resolve_ownership.py (apply) ===')
    resolve_ownership(dry_run)