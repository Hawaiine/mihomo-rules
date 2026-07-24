#!/usr/bin/env python3
"""
verify_rulesets.py — 校验 ruleset/ 一致性
检查每个品牌的 header/payload/README/behavior 一致性，失败 exit≠0
"""
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))

from lib.canonical import parse_rule_line, TYPES_ORDER, CanonicalRule
from commit_writer import get_strategy_group, STRATEGY_GROUP_MAP

SG_MAP = STRATEGY_GROUP_MAP

BASE = {'Reject', 'Direct', 'Proxy', 'CNCIDR', 'Private', 'Applications', 'LanCIDR'}

# 规则类型正则（用于 payload 计数）
TYPE_RE = re.compile(r'^\s*[-–]\s*([A-Z][A-Z0-9_-]+)\s*,')


def get_brands():
    brands = []
    for d in sorted(os.listdir(ROOT / 'ruleset')):
        if not (ROOT / 'ruleset' / d).is_dir():
            continue
        if not (ROOT / 'ruleset' / d / f'{d}.yaml').exists():
            continue
        brands.append(d)
    return brands


def parse_header_counts(yaml_path):
    """从 YAML header 提取 8 类计数"""
    counts = {}
    with open(yaml_path) as f:
        for line in f:
            for t in TYPES_ORDER:
                m = re.match(rf'#\s*{re.escape(t)}:\s*(\d+)', line)
                if m:
                    counts[t] = int(m.group(1))
    return counts


def count_payload_types(yaml_path):
    """从 payload 实际统计 8 类规则数"""
    counts = {t: 0 for t in TYPES_ORDER}
    in_payload = False
    with open(yaml_path) as f:
        for line in f:
            s = line.strip()
            if s.startswith('payload'):
                in_payload = True
                continue
            if not in_payload or not s or s.startswith('#'):
                continue
            m = TYPE_RE.match(s)
            if m:
                rtype = m.group(1)
                if rtype in counts:
                    counts[rtype] += 1
    return counts


def get_rule_name(yaml_path):
    """读取 YAML 的 # Rule Name"""
    with open(yaml_path) as f:
        for line in f:
            m = re.match(r'#\s*Rule\s*Name:\s*(.+)', line)
            if m:
                return m.group(1).strip()
    return ''


def get_readme_title(readme_path):
    """读取 README 第一行标题"""
    with open(readme_path) as f:
        first = f.readline().strip()
    # # 📦 <Name> 规则集
    m = re.match(r'#\s*📦\s*(.+?)\s*规则集', first)
    if m:
        return m.group(1).strip()
    return ''


def get_readme_behavior(readme_path):
    """读取 README 的 behavior 字段"""
    with open(readme_path) as f:
        for line in f:
            m = re.match(r'\*\*behavior\*\*:\s*(.+)', line)
            if m:
                return m.group(1).strip()
    return ''


def get_readme_strategy(readme_path):
    """读取 README 的策略组字段"""
    with open(readme_path) as f:
        for line in f:
            m = re.match(r'\*\*策略组\*\*:\s*(.+)', line)
            if m:
                return m.group(1).strip()
    return ''


def check_brand(brand):
    """检查单个品牌，返回 (pass, errors)"""
    yaml_path = ROOT / 'ruleset' / brand / f'{brand}.yaml'
    readme_path = ROOT / 'ruleset' / brand / 'README.md'
    errors = []

    # 文件存在
    if not yaml_path.exists():
        return False, [f'  {brand}: YAML 文件不存在']
    if not readme_path.exists():
        return False, [f'  {brand}: README 不存在']

    # header 计数 vs payload 实际
    header_counts = parse_header_counts(yaml_path)
    payload_counts = count_payload_types(yaml_path)
    for t in TYPES_ORDER:
        hc = header_counts.get(t, 0)
        pc = payload_counts.get(t, 0)
        if hc != pc:
            errors.append(f'  {brand}: header #{t}={hc} ≠ payload={pc}')

    # Rule Name
    rule_name = get_rule_name(yaml_path)
    display = SG_MAP.get(brand, brand)
    if rule_name and rule_name != display:
        errors.append(f'  {brand}: # Rule Name "{rule_name}" ≠ 策略组名 "{display}"')

    # README 标题
    readme_title = get_readme_title(readme_path)
    if readme_title and readme_title != display:
        errors.append(f'  {brand}: README 标题 "{readme_title}" ≠ 策略组名 "{display}"')

    # README 策略组
    readme_sg = get_readme_strategy(readme_path)
    if readme_sg and readme_sg != display:
        errors.append(f'  {brand}: README 策略组 "{readme_sg}" ≠ 策略组名 "{display}"')

    # behavior
    readme_bhv = get_readme_behavior(readme_path)
    # 从 payload 检测 behavior
    classical_types = {'IP-CIDR', 'IP-CIDR6', 'IP-ASN', 'PROCESS-NAME',
                       'PROCESS-NAME-WILDCARD', 'PROCESS-NAME-REGEX',
                       'PROCESS-PATH', 'PROCESS-PATH-WILDCARD', 'PROCESS-PATH-REGEX',
                       'SRC-IP-CIDR', 'SRC-IP-CIDR6', 'DST-PORT', 'SRC-PORT'}
    actual_bhv = 'domain'
    for t, c in payload_counts.items():
        if c > 0 and t in classical_types:
            actual_bhv = 'classical'
            break
    if readme_bhv and readme_bhv != actual_bhv:
        errors.append(f'  {brand}: README behavior="{readme_bhv}" ≠ payload 实际="{actual_bhv}"')

    return len(errors) == 0, errors


def main():
    print('=' * 60)
    print('  verify_rulesets.py — ruleset 一致性校验')
    print('=' * 60)

    brands = get_brands()
    print(f'  品牌总数: {len(brands)}')
    print()

    total_pass = 0
    total_fail = 0
    all_errors = {}

    for brand in brands:
        ok, errors = check_brand(brand)
        if ok:
            total_pass += 1
        else:
            total_fail += 1
            all_errors[brand] = errors

    print(f'--- 结果 ---')
    print(f'  PASS: {total_pass}')
    print(f'  FAIL: {total_fail}')

    if all_errors:
        print()
        print('--- 失败明细 ---')
        for brand, errors in sorted(all_errors.items()):
            for e in errors:
                print(e)

    print(f'{"=" * 60}')
    sys.exit(0 if total_fail == 0 else 1)


if __name__ == '__main__':
    main()