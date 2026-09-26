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

from lib.ownership_map import SUB_PARENT


def _load_generate_config():
    """读取 generate_config.py 的单一来源常量。

    REGION_GROUPS / SYSTEM_GROUPS / BASE_PROVIDERS 都只在这里定义一次，
    verify 侧一律 import 而非复制，避免两处硬编码漂移。
    """
    import importlib.util
    if str(ROOT / 'scripts') not in sys.path:
        sys.path.insert(0, str(ROOT / 'scripts'))
    spec = importlib.util.spec_from_file_location(
        'generate_config', ROOT / 'scripts' / 'generate_config.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_GEN = _load_generate_config()

# emoji 前缀组判定：与 match_icons / generate_config 同一实现（唯一来源）
import match_icons
is_emoji_group = match_icons.is_emoji_group

# 基础 provider 集合与固定顺序（9）——单一来源 generate_config.BASE_PROVIDERS
BASE_PROVIDER_ORDER = list(getattr(_GEN, 'BASE_PROVIDERS', {}).keys())
BASE_PROVIDERS = set(BASE_PROVIDER_ORDER)

# 系统组（自动选择 + 21 地区组 + 8 个功能组）——单一来源 generate_config.SYSTEM_GROUPS
SYSTEM_GROUPS = list(getattr(_GEN, 'SYSTEM_GROUPS', []))

# 21 个地区组——单一来源 generate_config.REGION_GROUPS
REGION_GROUPS = list(getattr(_GEN, 'REGION_GROUPS', []))

# Oasisic-Icons 仓库位置（用于校验 icon 引用是否真实存在）
ICON_REPO_CANDIDATES = [
    os.environ.get('MIHOMO_ICON_REPO'),
    str(ROOT / 'Oasisic-Icons'),
    '/opt/data/Oasisic-Icons',
]

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
    """获取所有品牌 (排除 9 基础)"""
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
    """proxy-groups 总数 = 系统组数（generate_config.SYSTEM_GROUPS）+ 当前品牌数"""
    names = extract_proxy_group_names(lines)
    expected = len(SYSTEM_GROUPS) + len(ALL_BRANDS)
    if len(names) != expected:
        print(f'  FAIL: {variant} — proxy-groups={len(names)}, expected {expected}')
        return False
    return True

def check_rule_providers_count(lines, variant):
    """rule-providers = 9 个基础集 + 当前品牌数"""
    keys = extract_rule_provider_keys(lines)
    expected = len(BASE_PROVIDERS) + len(ALL_BRANDS)
    if len(keys) != expected:
        print(f'  FAIL: {variant} — rule-providers={len(keys)}, expected {expected}')
        return False
    return True

def check_system_groups_first(names, variant):
    """前 N 组必须是系统组（N = len(SYSTEM_GROUPS)）"""
    for i, sg in enumerate(SYSTEM_GROUPS):
        if i >= len(names) or names[i] != sg:
            print(f'  FAIL: {variant} — system group #{i} expected "{sg}", got "{names[i] if i < len(names) else "N/A"}"')
            return False
    return True

def check_brand_set_equality(names, variant):
    """品牌组集合必须与 BRAND_DISPLAYS 全等"""
    brand_names = set(names[len(SYSTEM_GROUPS):])  # 跳过系统组，只比品牌组
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
    brand_names = names[len(SYSTEM_GROUPS):]
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
    brand_names = names[len(SYSTEM_GROUPS):]  # 跳过系统组

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
                if sg not in ('DIRECT', 'REJECT', '🛑 全球拦截', '🎯 全球直连', '🔧 手动切换', '🐟 漏网之鱼'):
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

def check_active_rules_count(variant, lines):
    """激活规则**条数**检查（不是等价检查）。

    只断言未注释规则行数等于 generate_config.gen_rules 的固定结构条数；
    full 与 min 的规则**列表全等**由 check_cross_variant_rules 负责。
    两者分工明确，避免「名义等价、实际只数条数」。
    """
    is_nikki = 'nikki' in variant
    rules = extract_rules_lines(lines)
    # Android 11 条 / Nikki 10 条（无 Applications）：见 generate_config.gen_rules
    expected_lines = 11 if not is_nikki else 10
    if len(rules) != expected_lines:
        print(f'  FAIL: {variant} — active rules={len(rules)}, expected {expected_lines}')
        return False
    return True

def check_rule_providers_base_order(lines, variant):
    """前 9 个 rule-provider 必须是固定顺序: DirectDNS,ProxyDNS,Reject,Direct,Proxy,Applications,Private,LanCIDR,CNCIDR"""
    keys = extract_rule_provider_keys(lines)
    for i, expected in enumerate(BASE_PROVIDER_ORDER):
        if i >= len(keys) or keys[i] != expected:
            print(f'  FAIL: {variant} — base provider #{i} expected "{expected}", got "{keys[i] if i < len(keys) else "N/A"}"')
            return False
    return True


def _comment_rule_set_lines(lines):
    """配置 rules 段中所有注释 RULE-SET 行（已 strip，保留原始顺序）"""
    out = []
    in_rules = False
    for line in lines:
        if line.strip().startswith('rules:'):
            in_rules = True
            continue
        if in_rules and re.match(r'\s*#\s*-\s*RULE-SET,\w+,', line):
            out.append(line.strip())
    return out


def check_full_comment_order(lines, variant):
    """full 版注释 RULE-SET 序列必须与 generate_config 的输出完全一致

    三层不变量（比旧版「只比品牌段」严格）：
    1. 品牌组数量 == 品牌 provider 注释数量 —— 显式断言，禁止 zip() 静默截断；
    2. 品牌 provider 注释顺序 == proxy-groups 品牌段顺序；
    3. **整段注释 RULE-SET 序列（含基础集注释）== 生成器输出** —— 基础集注释
       （如 Nikki full 的 Applications）不再「跳过即忽略」：生成器不产出、
       却出现在配置里的任何注释行都会 FAIL。
    """
    if 'full' not in variant:
        return True  # min 版无注释，跳过
    names = extract_proxy_group_names(lines)
    brand_names = names[len(SYSTEM_GROUPS):]  # 跳过系统组

    actual = _comment_rule_set_lines(lines)
    if not actual:
        print(f'  FAIL: {variant} — no commented RULE-SET lines found in full version')
        return False

    # 品牌 provider 注释（基础集注释单列，不参与品牌顺序比较）
    brand_comments = [l for l in actual if l.split(',')[1] not in BASE_PROVIDERS]
    brand_sgs = [l.split(',', 2)[2].strip().strip('"') for l in brand_comments]

    # 1) 条数显式断言（zip() 截断不得掩盖「注释行缺失」）
    if len(brand_names) != len(brand_sgs):
        print(f'  FAIL: {variant} — 品牌组 {len(brand_names)} 个 ≠ 品牌注释 RULE-SET {len(brand_sgs)} 个')
        print(f'  proxy-groups brands ({len(brand_names)}): {brand_names[:5]}...{brand_names[-3:]}')
        print(f'  commented brands ({len(brand_sgs)}): {brand_sgs[:5]}...{brand_sgs[-3:]}')
        return False

    # 2) 品牌注释顺序
    mismatches = [f'  #{i}: proxy-groups "{e}" ≠ commented RULE-SET "{a}"'
                  for i, (e, a) in enumerate(zip(brand_names, brand_sgs)) if e != a]
    if mismatches:
        for m in mismatches:
            print(f'  FAIL: {variant} — {m}')
        print(f'  proxy-groups brands ({len(brand_names)}): {brand_names[:5]}...{brand_names[-3:]}')
        print(f'  commented brands ({len(brand_sgs)}): {brand_sgs[:5]}...{brand_sgs[-3:]}')
        return False

    # 3) 整段与生成器输出全等（含基础集注释，单一来源）
    # gen_rules() 返回拼接后的字符串，需 splitlines()
    sg_map = _GEN.load_strategy_group_map()
    brand_info = _GEN.build_brand_info(_GEN.sort_brands(_GEN.scan_brands(), sg_map), sg_map)
    expected = [l.strip() for l in _GEN.gen_rules(brand_info, variant).splitlines()
                if re.match(r'\s*#\s*-\s*RULE-SET,\w+,', l)]
    if len(expected) != len(actual):
        print(f'  FAIL: {variant} — 注释 RULE-SET 行数 {len(actual)} ≠ 生成器输出 {len(expected)}')
        print(f'    config 独有: {sorted(set(actual) - set(expected))[:3]}')
        print(f'    生成器独有: {sorted(set(expected) - set(actual))[:3]}')
        return False
    for i, (e, a) in enumerate(zip(expected, actual)):
        if e != a:
            print(f'  FAIL: {variant} — 注释 RULE-SET 第 {i} 行与生成器不一致')
            print(f'    生成器: {e}')
            print(f'    配置:   {a}')
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


def check_proxy_groups_key_once(lines, variant):
    """全文 proxy-groups: 出现次数 = 1"""
    count = sum(1 for line in lines if line.strip().startswith('proxy-groups:'))
    if count != 1:
        print(f'  FAIL: {variant} — proxy-groups: appears {count} times, expected 1')
        return False
    return True


def check_min_proxy_groups_no_blank_lines(lines, variant):
    """min 版 proxy-groups 中相邻 - name: 块之间无空行"""
    if 'full' in variant:
        return True
    in_groups = False
    prev_blank = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('proxy-groups:'):
            in_groups = True
            continue
        if in_groups:
            if stripped == '':
                prev_blank = True
                continue
            if not line.startswith(' ') and not line.startswith('#'):
                break  # 退出 proxy-groups 段
            if stripped.startswith('- name:'):
                if prev_blank:
                    print(f'  FAIL: {variant} — blank line before "- name: {stripped}" in proxy-groups')
                    return False
            prev_blank = False
    return True


def check_min_proxy_providers_no_blank_lines(lines, variant):
    """min 版 proxy-providers 中相邻顶级 provider key 之间无空行"""
    if 'full' in variant:
        return True
    in_providers = False
    prev_blank = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('proxy-providers:'):
            in_providers = True
            continue
        if in_providers:
            if stripped == '':
                # 跳过注释行后的空行
                prev_blank = True
                continue
            if not line.startswith(' '):
                break  # 退出 proxy-providers 段
            m = re.match(r'^  (\w+)', line)
            if m:
                if prev_blank:
                    print(f'  FAIL: {variant} — blank line before provider "{m.group(1)}" in proxy-providers')
                    return False
            prev_blank = False
    return True


def check_rules_no_quoted_strategy(lines, variant):
    """rules 激活行的出站目标不得加引号。

    例：`RULE-SET,Direct,🎯 全球直连` 合法；`RULE-SET,Direct,"🎯 全球直连"` 非法。
    不针对具体策略组名做白名单，任何被引号包住的第三段都判失败。
    """
    in_rules = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('rules:'):
            in_rules = True
            continue
        if in_rules:
            if not stripped or stripped.startswith('#'):
                continue
            if not stripped.startswith('- '):
                break
            if not (stripped.startswith('- RULE-SET,')
                    or stripped.startswith('- GEOIP,')
                    or stripped.startswith('- MATCH,')):
                continue
            target = stripped.rsplit(',', 1)[-1].strip()
            if target.startswith('"') or target.startswith("'"):
                print(f'  FAIL: {variant} — quoted strategy name in rule: {stripped}')
                return False
    return True


def check_emoji_groups_have_no_icon(lines, variant):
    """emoji 前缀策略组不得出现 icon 行（项目约定）。

    规则与 match_icons.build_icon_map() / generate_config.gen_proxy_groups 一致：
    显示名以 emoji 开头的组一律不配 icon，上游补图也不得回流。
    """
    emoji_groups = {sg for sg in (SYSTEM_GROUPS + list(BRAND_DISPLAYS)) if is_emoji_group(sg)}
    if not emoji_groups:
        return True
    current = None
    offenders = []
    for line in lines:
        m = re.match(r'\s*-\s*name:\s*"([^"]+)"', line)
        if m:
            current = m.group(1)
            continue
        if current in emoji_groups and re.match(r'\s+icon:\s*"', line):
            offenders.append((current, line.strip()))
    if offenders:
        for name, icon in offenders[:8]:
            print(f'  FAIL: {variant} — emoji 组「{name}」不应有 icon: {icon}')
        return False
    return True



def check_no_literal_direct_in_rules(lines, variant):
    """rules 段中出站规则不得出现字面 DIRECT"""
    in_rules = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('rules:'):
            in_rules = True
            continue
        if in_rules:
            if stripped and stripped != '' and not line.startswith(' ') and not line.startswith('#') and not stripped.startswith('-'):
                break
            if stripped.startswith('#'):
                continue
            if stripped.startswith('- ') and ',DIRECT' in stripped:
                # Check if it's a RULE-SET or GEOIP rule (not a proxy-groups entry)
                parts = stripped.split(',', 2)
                if len(parts) >= 3 and parts[2].strip() == 'DIRECT':
                    print(f'  FAIL: {variant} — literal DIRECT in rules: {stripped}')
                    return False
    return True


def check_use_provider_exists(lines, variant):
    """每个 use 引用的 provider 必须在 proxy-providers 段有定义"""
    # Collect all provider keys from proxy-providers
    providers = set()
    in_providers = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('proxy-providers:'):
            in_providers = True
            continue
        if in_providers:
            if stripped == '':
                continue
            if not line.startswith(' ') and not line.startswith('#'):
                break
            m = re.match(r'^\s{2}(\w+)', line)
            if m:
                providers.add(m.group(1))

    # Collect all use references from proxy-groups
    # 兼容三种写法：块式裸键（生成器输出）、块式带引号、行内 flow（use: [a, b]）
    # 判据：引用既不是 provider 也不是 proxy-group 名 → FAIL（幽灵引用）
    group_names = set(extract_proxy_group_names(lines))
    use_refs = set()
    in_groups = False
    in_use = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('proxy-groups:'):
            in_groups = True
            continue
        if in_groups:
            if stripped == '':
                in_use = False
                continue
            if not line.startswith(' ') and not line.startswith('#'):
                break
            if stripped.startswith('- name:'):
                in_use = False  # 新组定义，重置 use 标志
                continue
            if stripped == 'use:':
                in_use = True
                continue
            m_inline = re.match(r'^use:\s*\[(.*)\]\s*$', stripped)
            if m_inline:
                in_use = False
                for item in m_inline.group(1).split(','):
                    val = item.strip().strip('"').strip("'").strip()
                    if val:
                        use_refs.add(val)
                continue
            if in_use and stripped.startswith('- '):
                val = stripped[2:].strip().strip('"').strip("'").strip()
                if val:
                    use_refs.add(val)

    # Check every use reference exists in providers（或本身是组名，非 provider 引用）
    missing = []
    for ref in sorted(use_refs):
        if ref not in providers and ref not in group_names:
            missing.append(ref)
            print(f'  FAIL: {variant} — use reference "{ref}" not defined in proxy-providers')

    if missing:
        return False
    return True

def parse_proxy_groups(path):
    """解析 config 的 proxy-groups（地区组结构检查用）"""
    import yaml
    with open(path, encoding='utf-8') as f:
        data = yaml.safe_load(f)
    return data.get('proxy-groups', [])


def load_region_groups():
    """兼容旧调用：地区组现在与 SYSTEM_GROUPS 一起在模块顶部单一来源加载。"""
    return list(REGION_GROUPS)

# 品牌组 proxies 固定头（generate_config.gen_proxy_groups 约定）
BRAND_GROUP_HEAD = ['🎯 全球直连', '♻️ 自动选择', '🔧 手动切换', '🔯 故障转移', '🔀 负载均衡']

# 需注入 21 个地区组的基础功能组
BASIC_REGION_GROUPS = ['🔧 手动切换', '🔯 故障转移', '🔀 负载均衡', '🐟 漏网之鱼', '🌍 代理DNS']


def check_brand_region_injection(groups, variant):
    """品牌组 proxies = 5 基础 + 21 地区组，顺序固定"""
    bad = []
    for g in groups:
        if g.get('name') in SYSTEM_GROUPS:
            continue
        proxies = g.get('proxies', [])
        expected = BRAND_GROUP_HEAD + REGION_GROUPS
        if proxies != expected:
            bad.append((g.get('name'), len(proxies), proxies[:5]))
    if bad:
        for name, n, head in bad[:8]:
            print(f'  FAIL: {variant} — 品牌组「{name}」proxies {n} 项/内容不符（前5: {head}）')
        if len(bad) > 8:
            print(f'  FAIL: {variant} — 另有 {len(bad) - 8} 个品牌组同样不符')
        return False
    return True


def check_basic_group_region_injection(groups, variant):
    """5 个基础功能组必须含 21 个地区组，顺序固定"""
    index = {g.get('name'): g for g in groups}
    bad = []
    for name in BASIC_REGION_GROUPS:
        g = index.get(name)
        if g is None:
            bad.append((name, '组不存在'))
            continue
        regions = [x for x in g.get('proxies', []) if x in REGION_GROUPS]
        if regions != REGION_GROUPS:
            bad.append((name, f'地区组 {len(regions)}/{len(REGION_GROUPS)} 项或顺序不符'))
    if bad:
        for name, why in bad:
            print(f'  FAIL: {variant} — 基础功能组「{name}」{why}')
        return False
    return True


def check_regions_not_in_use(groups, variant):
    """21 个地区组只能出现在 proxies，不得泄漏到 use 块"""
    bad = []
    region_set = set(REGION_GROUPS)
    for g in groups:
        leaked = [x for x in (g.get('use') or []) if x in region_set]
        if leaked:
            bad.append((g.get('name'), leaked))
    if bad:
        for name, leaked in bad[:8]:
            print(f'  FAIL: {variant} — 「{name}」use 块含地区组: {leaked}')
        return False
    return True


def check_icons_exist(lines, variant, icon_ref):
    """所有 icon 引用必须指向 Oasisic-Icons 上真实存在的文件

    基准优先取图标仓库的 git tree（origin/main → main → HEAD），
    避免工作区残留已被上游删除的文件造成误判。
    """
    if icon_ref is None:
        return True
    paths, _ = icon_ref
    broken = []
    for line in lines:
        m = re.match(r'\s+icon:\s*"([^"]+)"', line)
        if not m:
            continue
        url = m.group(1)
        if '/icons/' not in url:
            broken.append(url)
            continue
        rel = url.split('/icons/', 1)[1]
        if rel not in paths:
            broken.append(rel)
    if broken:
        for rel in sorted(set(broken)):
            print(f'  FAIL: {variant} — icon 文件不存在于 Oasisic-Icons: {rel}')
        return False
    return True


def load_icon_reference():
    """返回 ((icon 相对路径集合), 来源描述) 或 (None, 原因)"""
    import subprocess
    for cand in ICON_REPO_CANDIDATES:
        if not cand or not os.path.isdir(os.path.join(cand, 'icons')):
            continue
        for ref in ('origin/main', 'main', 'HEAD'):
            try:
                r = subprocess.run(
                    ['git', '-C', cand, 'ls-tree', '-r', '--name-only', ref, '--', 'icons/'],
                    capture_output=True, text=True, timeout=30,
                )
            except (OSError, subprocess.SubprocessError):
                break
            if r.returncode == 0 and r.stdout.strip():
                paths = {p[len('icons/'):] for p in r.stdout.splitlines() if p.endswith('.png')}
                if paths:
                    return paths, f'{cand}@{ref}'
        root = os.path.join(cand, 'icons')
        paths = set()
        for dirpath, _, files in os.walk(root):
            for f in files:
                if f.endswith('.png'):
                    paths.add(os.path.relpath(os.path.join(dirpath, f), root))
        if paths:
            return paths, f'{cand} (工作区扫描)'
    return None, '未找到 Oasisic-Icons'


def check_cross_variant_rules(all_lines):
    """同平台 full vs min 激活规则列表必须全等（真等价检查，含条数）"""
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
        if len(full_rules) != len(min_rules) or full_rules != min_rules:
            print(f'  FAIL: cross-variant — {platform} full vs min active rules mismatch')
            print(f'    full ({len(full_rules)}): {full_rules}')
            print(f'    min  ({len(min_rules)}): {min_rules}')
            all_pass = False
    return all_pass


def print_order_summary(names, variant):
    """打印品牌组顺序摘要"""
    brand_names = names[len(SYSTEM_GROUPS):]  # 跳过系统组
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
    icon_paths, icon_src = load_icon_reference()
    if icon_paths:
        print(f'  icon 基准: {icon_src}（{len(icon_paths)} 个 png）')
    else:
        print(f'  icon 基准: 无（{icon_src}），跳过 icon 存在性检查')
    icon_ref = (icon_paths, icon_src) if icon_paths else None
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
        try:
            groups_cfg = parse_proxy_groups(path)
        except Exception as exc:  # YAML 结构损坏时给出明确失败而不是抛栈
            print(f'  FAIL: {variant} — proxy-groups 解析失败: {exc}')
            groups_cfg = []
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
            ('active rules count', check_active_rules_count(variant, lines)),
            ('rule-providers base order', check_rule_providers_base_order(lines, variant)),
            ('full comment RULE-SET order', check_full_comment_order(lines, variant)),
            ('min rules no blank lines', check_min_rules_no_blank_lines(lines, variant)),
            ('no literal DIRECT in rules', check_no_literal_direct_in_rules(lines, variant)),
            ('proxy-groups: key once', check_proxy_groups_key_once(lines, variant)),
            ('min proxy-groups no blank lines', check_min_proxy_groups_no_blank_lines(lines, variant)),
            ('min proxy-providers no blank lines', check_min_proxy_providers_no_blank_lines(lines, variant)),
            ('rules no quoted strategy', check_rules_no_quoted_strategy(lines, variant)),
            ('use provider exists', check_use_provider_exists(lines, variant)),
            ('brand group region injection', check_brand_region_injection(groups_cfg, variant)),
            ('basic group region injection', check_basic_group_region_injection(groups_cfg, variant)),
            ('regions not in use blocks', check_regions_not_in_use(groups_cfg, variant)),
            ('icon files exist', check_icons_exist(lines, variant, icon_ref)),
            ('emoji groups have no icon', check_emoji_groups_have_no_icon(lines, variant)),
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
    cv_pass = check_cross_variant_rules(all_lines)
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