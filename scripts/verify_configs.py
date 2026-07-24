#!/usr/bin/env python3
"""
verify_configs.py — 校验 4 个 config 一致性
检查命名、集合、顺序、变体语义，失败 exit≠0
"""
import os
import sys
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIRS = {
    'Android': str(ROOT / 'configs' / 'Android'),
    'Nikki': str(ROOT / 'configs' / 'Nikki'),
}
VARIANTS = ['android_full', 'android_min', 'nikki_full', 'nikki_min']

# 基础 provider (7)
BASE_PROVIDERS = {'Reject', 'Direct', 'Proxy', 'Applications', 'Private', 'LanCIDR', 'CNCIDR'}

# 系统组 (11)
SYSTEM_GROUPS = [
    '♻️ 自动选择', '🇭🇰 香港节点', '🇯🇵 日本节点', '🇺🇸 美国节点',
    '🇸🇬 新加坡节点', '🇹🇼 台湾节点', '🛑 全球拦截', '🔧 手动切换',
    '🔯 故障转移', '🔀 负载均衡', '🐟 漏网之鱼',
]

from lib.ownership_map import SUB_PARENT

# 从 commit_writer.py 加载
def load_sg_map():
    import importlib.util
    spec = importlib.util.spec_from_file_location('cw', ROOT / 'scripts' / 'commit_writer.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return getattr(mod, 'STRATEGY_GROUP_MAP', {})

SG_MAP = load_sg_map()

def get_display(key):
    return SG_MAP.get(key, key)

def get_brands():
    """扫描 ruleset/ 获取所有品牌 (排除 7 基础)"""
    brands = []
    ruleset_dir = ROOT / 'ruleset'
    for d in sorted(os.listdir(ruleset_dir)):
        if d in BASE_PROVIDERS:
            continue
        if (ruleset_dir / d / f'{d}.yaml').exists():
            brands.append(d)
    return brands

ALL_BRANDS = get_brands()
BRAND_IDS = set(ALL_BRANDS)
BRAND_DISPLAYS = {get_display(b) for b in ALL_BRANDS}

def read_file_lines(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return f.readlines()

def extract_proxy_group_names(lines):
    names = []
    for line in lines:
        m = re.match(r'\s*-\s*name:\s*"([^"]+)"', line)
        if m:
            names.append(m.group(1))
    return names

def extract_rule_provider_keys(lines):
    keys = []
    in_providers = False
    for line in lines:
        if line.strip().startswith('rule-providers:'):
            in_providers = True
            continue
        if in_providers:
            if line.strip() == '':
                continue
            if not line.startswith(' ') and not line.startswith('#'):
                break
            m = re.match(r'^\s{2}(\w+)', line)
            if m:
                keys.append(m.group(1))
    return keys

def extract_rules_lines(lines):
    """提取 rules 段中未注释的 RULE-SET 行"""
    rules = []
    in_rules = False
    for line in lines:
        if line.strip().startswith('rules:'):
            in_rules = True
            continue
        if in_rules:
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                continue
            if stripped.startswith('- RULE-SET,') or stripped.startswith('- MATCH,') or stripped.startswith('- GEOIP,'):
                rules.append(stripped)
    return rules

def check_has_rules_key(lines, variant):
    """验证 rules: 键存在"""
    for line in lines:
        if line.strip().startswith('rules:'):
            return True
    print(f'  FAIL: {variant} — missing "rules:" key')
    return False


def check_rules_blank_line_before(lines, variant):
    """full: rules: 上一行为空行; min: 上一行非空"""
    is_full = 'full' in variant
    prev_line = ''
    for i, line in enumerate(lines):
        if line.strip().startswith('rules:'):
            if i > 0:
                prev_line = lines[i - 1].rstrip('\n')
            if is_full:
                if prev_line.strip() != '':
                    print(f'  FAIL: {variant} — full: line before "rules:" should be empty, got: {repr(prev_line)}')
                    return False
            else:
                if prev_line.strip() == '':
                    print(f'  FAIL: {variant} — min: line before "rules:" should NOT be empty')
                    return False
            return True
    print(f'  FAIL: {variant} — "rules:" not found')
    return False

def check_proxy_groups_count(lines, variant):
    """proxy-groups 总数 = 110 (11 系统 + 99 品牌)"""
    names = extract_proxy_group_names(lines)
    expected = 11 + len(ALL_BRANDS)
    if len(names) != expected:
        print(f'  FAIL: {variant} — proxy-groups={len(names)}, expected {expected}')
        return False
    return True

def check_rule_providers_count(lines, variant):
    """rule-providers = 7 基础 + 99 品牌 = 106"""
    keys = extract_rule_provider_keys(lines)
    expected = 7 + len(ALL_BRANDS)
    if len(keys) != expected:
        print(f'  FAIL: {variant} — rule-providers={len(keys)}, expected {expected}')
        return False
    return True

def check_system_groups_first(names, variant):
    """前 11 组必须是系统组"""
    for i, sg in enumerate(SYSTEM_GROUPS):
        if i >= len(names) or names[i] != sg:
            print(f'  FAIL: {variant} — system group #{i} expected "{sg}", got "{names[i] if i < len(names) else "N/A"}"')
            return False
    return True

def check_brand_set_equality(names, variant):
    """品牌组集合必须与 BRAND_DISPLAYS 全等"""
    brand_names = set(names[11:])  # skip 11 system groups
    only_old = brand_names - BRAND_DISPLAYS
    only_new = BRAND_DISPLAYS - brand_names
    if only_old or only_new:
        print(f'  FAIL: {variant} — brand set mismatch')
        if only_old:
            print(f'    only_old (in config but not in ruleset/): {sorted(only_old)}')
        if only_new:
            print(f'    only_new (in ruleset/ but not in config): {sorted(only_new)}')
        return False
    return True

def check_sub_parent_order(names, variant):
    """子品牌必须在父品牌前"""
    brand_names = names[11:]
    for child, parent in SUB_PARENT.items():
        child_display = get_display(child)
        parent_display = get_display(parent)
        if child_display not in brand_names or parent_display not in brand_names:
            continue
        ci = brand_names.index(child_display)
        pi = brand_names.index(parent_display)
        if ci >= pi:
            print(f'  FAIL: {variant} — "{child_display}" (child) after "{parent_display}" (parent)')
            return False
    return True

def check_naming_consistency(lines, variant):
    """RULE-SET 第一段=provider key, 第二段=proxy-group name"""
    providers = extract_rule_provider_keys(lines)
    provider_set = set(providers)
    names = extract_proxy_group_names(lines)
    brand_names = names[11:]  # skip system groups

    errors = []
    for line in lines:
        m = re.match(r'\s*#?\s*-\s*RULE-SET,(\w+),(.+)', line)
        if m:
            pkey = m.group(1)
            sg = m.group(2).strip().strip('"')
            if pkey not in provider_set:
                errors.append(f'  RULE-SET,{pkey},{sg} — provider key "{pkey}" not in rule-providers')
            if sg not in brand_names and sg not in SYSTEM_GROUPS:
                # 可能是 DIRECT/REJECT
                if sg not in ('DIRECT', 'REJECT', '🛑 全球拦截', '🔧 手动切换', '🐟 漏网之鱼'):
                    errors.append(f'  RULE-SET,{pkey},{sg} — strategy group "{sg}" not in proxy-groups')

    if errors:
        for e in errors:
            print(f'  FAIL: {variant} — {e}')
        return False
    return True

def check_applications_semantics(variant, lines):
    """Android: Applications 激活; Nikki: Applications 注释或省略"""
    is_nikki = 'nikki' in variant
    is_full = 'full' in variant
    found_active = False
    found_commented = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('- RULE-SET,Applications,'):
            found_active = True
            if is_nikki and not stripped.startswith('#'):
                # 在 Nikki 中 Applications 应该被注释（min 版不出现）
                pass
        if '# - RULE-SET,Applications,' in stripped or '#- RULE-SET,Applications,' in stripped:
            found_commented = True

    if is_nikki:
        if is_full:
            # Nikki full: Applications 必须注释
            if found_active:
                print(f'  FAIL: {variant} — Nikki full should have Applications commented out')
                return False
        else:
            # Nikki min: Applications 不应出现
            if found_active:
                print(f'  FAIL: {variant} — Nikki min should not have Applications rule')
                return False
    else:
        # Android: Applications 必须激活
        if not found_active:
            print(f'  FAIL: {variant} — Android should have Applications active')
            return False
    return True

def check_full_min_rules_equivalence(variant, lines):
    """full vs min: 未注释规则行列表必须全等"""
    is_full = 'full' in variant
    is_nikki = 'nikki' in variant
    rules = extract_rules_lines(lines)
    # 检查基本结构
    expected_lines = 9 if not is_nikki else 8  # Android 9条, Nikki 8条(无Applications)
    if len(rules) != expected_lines:
        print(f'  FAIL: {variant} — active rules={len(rules)}, expected {expected_lines}')
        return False
    return True

def check_rule_providers_base_order(lines, variant):
    """前 7 个 rule-provider 必须是固定顺序: Reject,Direct,Proxy,Applications,Private,LanCIDR,CNCIDR"""
    keys = extract_rule_provider_keys(lines)
    base_order = ['Reject', 'Direct', 'Proxy', 'Applications', 'Private', 'LanCIDR', 'CNCIDR']
    for i, expected in enumerate(base_order):
        if i >= len(keys) or keys[i] != expected:
            print(f'  FAIL: {variant} — base provider #{i} expected "{expected}", got "{keys[i] if i < len(keys) else "N/A"}"')
            return False
    return True


def check_full_comment_order(lines, variant):
    """full 版注释 RULE-SET 顺序必须与 proxy-groups 品牌段顺序一致"""
    if 'full' not in variant:
        return True  # min 版无注释，跳过
    names = extract_proxy_group_names(lines)
    brand_names = names[11:]  # skip system groups

    # 提取 # - RULE-SET 注释行中的策略组名
    commented_sgs = []
    in_rules = False
    for line in lines:
        if line.strip().startswith('rules:'):
            in_rules = True
            continue
        if in_rules:
            m = re.match(r'\s*#\s*-\s*RULE-SET,\w+,(.+)', line)
            if m:
                sg = m.group(1).strip().strip('"')
                commented_sgs.append(sg)

    if not commented_sgs:
        print(f'  FAIL: {variant} — no commented RULE-SET lines found in full version')
        return False

    # 比较
    mismatches = []
    for i, (expected, actual) in enumerate(zip(brand_names, commented_sgs)):
        if expected != actual:
            mismatches.append(f'  #{i}: proxy-groups "{expected}" ≠ commented RULE-SET "{actual}"')

    if mismatches:
        for m in mismatches:
            print(f'  FAIL: {variant} — {m}')
        print(f'  proxy-groups brands ({len(brand_names)}): {brand_names[:5]}...{brand_names[-3:]}')
        print(f'  commented RULE-SET ({len(commented_sgs)}): {commented_sgs[:5]}...{commented_sgs[-3:]}')
        return False
    return True


def check_min_rules_no_blank_lines(lines, variant):
    """min 版 rules: 段内不得出现空行"""
    if 'full' in variant:
        return True
    in_rules = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('rules:'):
            in_rules = True
            continue
        if in_rules:
            if stripped == '':
                print(f'  FAIL: {variant} — blank line found in rules section')
                return False
            if stripped.startswith('- RULE-SET,') or stripped.startswith('- MATCH,') or stripped.startswith('- GEOIP,'):
                continue
            # 非规则行 = 退出 rules 段
            break
    return True


def check_cross_variant_rules(configs, all_lines):
    """同平台 full vs min 激活规则列表必须全等"""
    platforms = {
        'android': ('android_full', 'android_min'),
        'nikki': ('nikki_full', 'nikki_min'),
    }
    all_pass = True
    for platform, (full_var, min_var) in platforms.items():
        full_rules = extract_rules_lines(all_lines[full_var])
        min_rules = extract_rules_lines(all_lines[min_var])
        # 过滤掉 Applications（min Nikki 不应有）
        if platform == 'nikki':
            full_rules = [r for r in full_rules if 'Applications' not in r]
            min_rules = [r for r in min_rules if 'Applications' not in r]
        if full_rules != min_rules:
            print(f'  FAIL: cross-variant — {platform} full vs min active rules mismatch')
            print(f'    full only: {[r for r in full_rules if r not in min_rules]}')
            print(f'    min only:  {[r for r in min_rules if r not in full_rules]}')
            all_pass = False
    return all_pass


def print_order_summary(names, variant):
    """打印品牌组顺序摘要"""
    brand_names = names[11:]
    print(f'  ORDER: {variant} — {len(brand_names)} brands')
    # 显示前 5 和后 5
    print(f'    first 5: {brand_names[:5]}')
    print(f'    last 5: {brand_names[-5:]}')
    # 显示子品牌位置
    children_shown = []
    for child, parent in SUB_PARENT.items():
        cd = get_display(child)
        pd = get_display(parent)
        if cd in brand_names and pd in brand_names:
            ci = brand_names.index(cd)
            pi = brand_names.index(pd)
            children_shown.append(f'    {cd}@{ci} < {pd}@{pi}')
    if children_shown:
        print(f'    SUB_PARENT positions:')
        for s in children_shown:
            print(s)


def main():
    print('=' * 60)
    print('  verify_configs.py — 4 个 config 全量校验')
    print('=' * 60)
    print(f'  品牌总数: {len(ALL_BRANDS)}')
    # 按平台分组
    configs = {}
    for platform, dir_path in CONFIG_DIRS.items():
        configs[f'{platform.lower()}_full'] = os.path.join(dir_path, 'config.yaml')
        configs[f'{platform.lower()}_min'] = os.path.join(dir_path, 'config.min.yaml')

    all_pass = True
    results = {}
    all_lines = {}
    for variant in VARIANTS:
        path = configs[variant]
        print(f'\n--- {variant} ({path}) ---')
        if not os.path.exists(path):
            print(f'  FAIL: file not found')
            all_pass = False
            results[variant] = False
            continue

        lines = read_file_lines(path)
        all_lines[variant] = lines
        checks = [
            ('rules: key', check_has_rules_key(lines, variant)),
            ('rules: blank line before', check_rules_blank_line_before(lines, variant)),
            ('proxy-groups count', check_proxy_groups_count(lines, variant)),
            ('rule-providers count', check_rule_providers_count(lines, variant)),
        ]

        names = extract_proxy_group_names(lines)
        checks += [
            ('system groups first', check_system_groups_first(names, variant)),
            ('brand set equality', check_brand_set_equality(names, variant)),
            ('SUB_PARENT order', check_sub_parent_order(names, variant)),
            ('naming consistency', check_naming_consistency(lines, variant)),
            ('Applications semantics', check_applications_semantics(variant, lines)),
            ('active rules count', check_full_min_rules_equivalence(variant, lines)),
            ('rule-providers base order', check_rule_providers_base_order(lines, variant)),
            ('full comment RULE-SET order', check_full_comment_order(lines, variant)),
            ('min rules no blank lines', check_min_rules_no_blank_lines(lines, variant)),
        ]

        variant_pass = all(r for _, r in checks)
        for name, ok in checks:
            status = 'PASS' if ok else 'FAIL'
            print(f'  [{status}] {name}')

        if variant_pass:
            print_order_summary(names, variant)

        all_pass = all_pass and variant_pass
        results[variant] = variant_pass

    # 跨变体校验
    print(f'\n--- cross-variant ---')
    cv_pass = check_cross_variant_rules(configs, all_lines)
    if cv_pass:
        print('  [PASS] full vs min active rules match')
    all_pass = all_pass and cv_pass

    # 汇总
    print(f'\n{"=" * 60}')
    passed = sum(1 for v in results.values() if v)
    failed = sum(1 for v in results.values() if not v)
    print(f'  结果: {passed}/{len(results)} PASS, {failed} FAIL')
    for v, ok in results.items():
        print(f'    [{ "PASS" if ok else "FAIL" }] {v}')
    print(f'{"=" * 60}')

    sys.exit(0 if all_pass else 1)


if __name__ == '__main__':
    main()