#!/usr/bin/env python3
"""
resolve_ownership.py — 所有权裁决
从父品牌移除子品牌已拥有的规则，避免规则截胡。
支持 dry-run 和自动清理两种模式。
"""
import os
import sys
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 父子品牌映射（与 generate_config.py 保持一致）
SUB_PARENT = {
    'AppleTV': 'Apple',
    'SiriAI': 'Apple',
    'iCloud': 'Apple',
    'GoogleAI': 'Google',
    'YouTube': 'Google',
    'YouTubeMusic': 'YouTube',
    'AWS': 'Amazon',
    'PrimeVideo': 'Amazon',
    'OneDrive': 'Microsoft',
    'GitHub': 'Microsoft',
    'Instagram': 'Facebook',
    'Messenger': 'Facebook',
    'WhatsApp': 'Facebook',
    'Threads': 'Facebook',
    'iCloudPrivateRelay': 'iCloud',
}

BASE = {'Reject', 'Direct', 'Proxy', 'CNCIDR', 'Private', 'Applications', 'LanCIDR'}


def parse_rules(yaml_path):
    """解析 YAML 规则集，返回规则列表和统计信息"""
    rules = []
    with open(yaml_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or line.startswith('payload') or line == '[]':
                continue
            rules.append(line)
    return rules


def extract_rule_value(rule):
    """提取规则的值（域名 / IP段 / 进程名）"""
    parts = rule.split(',')
    if len(parts) >= 2:
        # 去掉 - 前缀
        val = parts[0].strip()
        if val.startswith('- '):
            val = val[2:]
        # 返回类型和值
        return val, parts[1].strip()
    return None, None


def collect_child_rules(brands, child_name):
    """收集子品牌的所有规则，按 (类型, 值) 建索引"""
    child_rules = {}
    yaml_path = ROOT / 'ruleset' / child_name / f'{child_name}.yaml'
    if not yaml_path.exists():
        return child_rules
    rules = parse_rules(yaml_path)
    for r in rules:
        rtype, rval = extract_rule_value(r)
        if rtype and rval:
            child_rules[(rtype, rval)] = r
    return child_rules


def resolve_ownership(dry_run=True):
    """执行所有权裁决"""
    # 扫描所有品牌
    brands = []
    for d in sorted(os.listdir(ROOT / 'ruleset')):
        if d in BASE:
            continue
        if not os.path.isdir(ROOT / 'ruleset' / d):
            continue
        if not os.path.isfile(ROOT / 'ruleset' / d / f'{d}.yaml'):
            continue
        brands.append(d)
    
    total_removed = 0
    total_pairs = 0
    
    # 对每个父子关系
    for child, parent in SUB_PARENT.items():
        if child not in brands or parent not in brands:
            continue
        
        child_yaml = ROOT / 'ruleset' / child / f'{child}.yaml'
        parent_yaml = ROOT / 'ruleset' / parent / f'{parent}.yaml'
        
        # 收集子品牌规则索引
        child_rules = collect_child_rules(brands, child)
        if not child_rules:
            continue
        
        # 读取父品牌规则
        parent_rules = parse_rules(parent_yaml)
        
        # 找出重叠
        to_remove = []
        kept = []
        for r in parent_rules:
            rtype, rval = extract_rule_value(r)
            if rtype and rval and (rtype, rval) in child_rules:
                to_remove.append(r)
            else:
                kept.append(r)
        
        if not to_remove:
            continue
        
        total_pairs += 1
        total_removed += len(to_remove)
        
        print(f'\n[{parent}] ← [{child}]')
        print(f'  父品牌规则: {len(parent_rules)}')
        print(f'  子品牌规则: {len(child_rules)}')
        print(f'  重叠移除: {len(to_remove)}')
        print(f'  剩余: {len(kept)}')
        
        if dry_run:
            print(f'  重叠示例（前5条）:')
            for r in to_remove[:5]:
                print(f'    {r}')
        else:
            # 更新父品牌 YAML
            update_yaml(parent_yaml, kept, to_remove, child)
    
    print(f'\n=== 总结 ===')
    print(f'处理父子关系: {total_pairs} 对')
    print(f'移除重叠规则: {total_removed} 条')
    if dry_run:
        print(f'模式: dry-run（未修改文件）')
        print(f'如需实际清理，运行: python3 resolve_ownership.py --apply')


def update_yaml(yaml_path, kept_rules, removed_rules, child_name):
    """更新 YAML 文件，移除重叠规则并更新统计注释"""
    with open(yaml_path) as f:
        lines = f.readlines()
    
    # 统计被移除的规则类型
    removed_counts = {}
    for r in removed_rules:
        rtype, rval = extract_rule_value(r)
        if rtype:
            t = rtype.lstrip('- ').strip()
            removed_counts[t] = removed_counts.get(t, 0) + 1
    
    new_lines = []
    in_payload = False
    for line in lines:
        stripped = line.strip()
        
        if stripped.startswith('payload'):
            in_payload = True
            new_lines.append(line)
            continue
        
        if not in_payload:
            # 更新统计注释：减少对应类型的计数
            updated = False
            for t, count in removed_counts.items():
                pattern = f'# {t}:'
                if pattern in stripped:
                    m = re.search(r'(\d+)', stripped)
                    if m:
                        old_count = int(m.group(1))
                        new_count = max(0, old_count - count)
                        new_lines.append(f'# {t}: {new_count}\n')
                        updated = True
                        break
            if not updated:
                new_lines.append(line)
        else:
            # payload 区域：跳过被移除的规则
            if stripped not in removed_rules:
                new_lines.append(line)
    
    with open(yaml_path, 'w') as f:
        f.writelines(new_lines)
    
    print(f'  已更新: {yaml_path.name}')


if __name__ == '__main__':
    dry_run = '--apply' not in sys.argv
    if dry_run:
        print('=== resolve_ownership.py (dry-run) ===')
        print('使用 --apply 参数执行实际清理')
    else:
        print('=== resolve_ownership.py (apply) ===')
    resolve_ownership(dry_run)