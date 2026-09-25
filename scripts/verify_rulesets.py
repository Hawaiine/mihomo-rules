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

from lib.canonical import parse_rule_line, TYPES_ORDER, CanonicalRule, sort_rules
from lib.canonical import drop_domain_covered_by_broader_suffix
from lib.policy import is_allowed_bare_suffix, is_bare, is_single_char
from commit_writer import get_strategy_group, STRATEGY_GROUP_MAP

SG_MAP = STRATEGY_GROUP_MAP

BASE = {'Reject', 'Direct', 'Proxy', 'CNCIDR', 'Private', 'Applications', 'LanCIDR', 'DirectDNS', 'ProxyDNS'}

# 冗余 DOMAIN 检查口径（恒为 FAIL，清理后应保持 0，防回退）：
# - 同值跨类型：同一 value 同时出现 DOMAIN 与 DOMAIN-SUFFIX；
# - 阴影覆盖：DOMAIN 的多标签父域已在同集 DOMAIN-SUFFIX 中（单标签 TLD 不参与）；
# - 品牌集与基础集统一 FAIL（历史存量已随 fix/domain-shadow-cleanup 清零）。

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


def check_extra_files(brand):
    """检查品牌目录内是否有多余文件（仅允许 <Brand>.yaml 和 README.md）"""
    extra = []
    dir_path = ROOT / 'ruleset' / brand
    allowed = {f'{brand}.yaml', 'README.md'}
    for f in sorted(os.listdir(dir_path)):
        if f not in allowed:
            extra.append(f)
    return extra


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

    # 多余文件检查
    extra = check_extra_files(brand)
    if extra:
        errors.append(f'  {brand}: 多余文件 {extra}（仅允许 {brand}.yaml + README.md）')

    # 行为校验（按 mihomo 官方格式：payload 为 TYPE,value 行→classical）
    readme_bhv = get_readme_behavior(readme_path)
    # 从 payload 检测 behavior：本仓库所有 ruleset 使用 TYPE,value 格式→classical
    expected_bhv = 'classical'
    if readme_bhv and readme_bhv != expected_bhv:
        errors.append(f'  {brand}: README behavior="{readme_bhv}" ≠ 官方格式预期="{expected_bhv}"')

    # 检查 payload 段内是否有空行或行尾空白
    lines = open(yaml_path).read().split('\n')
    in_payload = False
    payload_rules = []
    seen_exact = set()
    updated_ok = False
    for i, ln in enumerate(lines, 1):
        if re.fullmatch(r'# Updated: \d{4}-\d{2}-\d{2}(?: \d{2}:\d{2}:\d{2})?', ln.strip()):
            updated_ok = True
        s = ln.strip()
        if s.startswith('payload'):
            in_payload = True
            continue
        if not in_payload:
            continue
        if s == '' and i < len(lines):
            errors.append(f'  {brand}: payload 段内空行 (第 {i} 行)')
        if ln != ln.rstrip():
            errors.append(f'  {brand}: 行尾空白 (第 {i} 行)')
        if not s or s.startswith('#'):
            continue
        if s in seen_exact:
            errors.append(f'  {brand}: payload 完全重复 (第 {i} 行)')
        seen_exact.add(s)
        rule = parse_rule_line(ln)
        if rule is not None:
            payload_rules.append(rule)
            if rule.rule_type in ('DOMAIN', 'DOMAIN-SUFFIX') and is_single_char(rule.value):
                errors.append(f'  {brand}: 单字符 (第 {i} 行) {rule.value}')
            elif (
                rule.rule_type == 'DOMAIN-SUFFIX'
                and is_bare(rule.value)
                and brand != 'Private'
                and not is_allowed_bare_suffix(brand, rule.value)
            ):
                errors.append(f'  {brand}: 无点品牌词不在白名单 (第 {i} 行) {rule.value}')
    if not updated_ok:
        errors.append(f'  {brand}: # Updated 格式应为 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS')
    if payload_rules != sort_rules(payload_rules):
        errors.append(f'  {brand}: payload 排序不符合 canonical.sort_rules')

    # 同值跨类型重复：同一 value 同时出现 DOMAIN 与 DOMAIN-SUFFIX
    # （写入路径已统一去重，这里防回退）
    dom_values = {r.value.lower() for r in payload_rules if r.rule_type == 'DOMAIN'}
    suf_values = {r.value.lower() for r in payload_rules if r.rule_type == 'DOMAIN-SUFFIX'}
    cross = sorted(dom_values & suf_values)
    if cross:
        sample = ', '.join(cross[:3]) + (' …' if len(cross) > 3 else '')
        errors.append(f'  {brand}: 同值跨类型重复 {len(cross)} 条（DOMAIN 与 DOMAIN-SUFFIX 同名）: {sample}')

    # DOMAIN 被同集更宽后缀覆盖：仅多标签父域判定，单标签 TLD 不参与（恒为 FAIL）
    if brand != 'Private':
        _, shadowed = drop_domain_covered_by_broader_suffix(payload_rules)
        if shadowed:
            sample = ', '.join(r.value for r in shadowed[:3]) + (' …' if len(shadowed) > 3 else '')
            errors.append(f'  {brand}: DOMAIN 被同集更宽后缀覆盖 {len(shadowed)} 条: {sample}')

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

    # 额外: 校验 STRATEGY_GROUP_MAP 无漂移
    sg_orphans = [k for k in SG_MAP if k not in brands]
    if sg_orphans:
        total_fail += 1
        all_errors.setdefault('__STRATEGY_GROUP_MAP__', []).append(
            f'  STRATEGY_GROUP_MAP 中存在已不存在的品牌: {sorted(sg_orphans)}'
        )

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