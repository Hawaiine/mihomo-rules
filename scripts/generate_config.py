#!/usr/bin/env python3
"""
generate_config.py — 自动生成 mihomo 配置文件
扫描 ruleset/ 目录，生成 proxy-groups / rule-providers / rules 三个区块。
输出 4 个变体：Android full/min, Nikki full/min
幂等：无变化不写入，不更新时间戳
"""

import os
import sys
import re
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 排除的基础规则集
BASE = {'Reject', 'Direct', 'Proxy', 'CNCIDR', 'Private', 'Applications', 'LanCIDR'}

from lib.ownership_map import SUB_PARENT

# 基础 provider 配置
BASE_PROVIDERS = {
    'Reject':       'domain',
    'Direct':       'domain',
    'Proxy':        'domain',
    'Applications': 'classical',
    'Private':      'classical',
    'LanCIDR':      'classical',
    'CNCIDR':       'classical',
}

# 系统组名称列表
SYSTEM_GROUPS = [
    '♻️ 自动选择', '🇭🇰 香港节点', '🇯🇵 日本节点', '🇺🇸 美国节点',
    '🇸🇬 新加坡节点', '🇹🇼 台湾节点', '🛑 全球拦截', '🔧 手动切换',
    '🔯 故障转移', '🔀 负载均衡', '🐟 漏网之鱼',
]

GITHUB_BASE = 'https://raw.githubusercontent.com/Hawaiine/mihomo-rules/main'


def load_strategy_group_map():
    """从 commit_writer.py 读取 STRATEGY_GROUP_MAP"""
    import importlib.util
    spec = importlib.util.spec_from_file_location('commit_writer', ROOT / 'scripts' / 'commit_writer.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return getattr(mod, 'STRATEGY_GROUP_MAP', {})


def scan_brands():
    """扫描 ruleset/ 目录，返回品牌信息列表"""
    brands = []
    ruleset_dir = ROOT / 'ruleset'
    for d in sorted(os.listdir(ruleset_dir)):
        if d in BASE:
            continue
        dir_path = ruleset_dir / d
        yaml_path = dir_path / f'{d}.yaml'
        if not dir_path.is_dir() or not yaml_path.exists():
            continue
        brands.append(d)
    return brands


def detect_behavior(yaml_path):
    """检测规则集的 behavior"""
    classical_types = {
        'IP-CIDR', 'IP-CIDR6', 'IP-ASN', 'PROCESS-NAME',
        'PROCESS-NAME-WILDCARD', 'PROCESS-NAME-REGEX',
        'PROCESS-PATH', 'PROCESS-PATH-WILDCARD', 'PROCESS-PATH-REGEX',
        'SRC-IP-CIDR', 'SRC-IP-CIDR6', 'DST-PORT', 'SRC-PORT',
        'IN-TYPE', 'IN-USER', 'IN-NAME', 'IN-PORT',
        'UID', 'NETWORK', 'PROTOCOL', 'RULE-SET',
    }
    try:
        with open(yaml_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or line.startswith('payload'):
                    continue
                rule_type = line.split(',')[0].strip()
                if rule_type in classical_types:
                    return 'classical'
                if rule_type.startswith('- '):
                    rule_type = rule_type[2:].strip()
                    if rule_type in classical_types:
                        return 'classical'
        return 'domain'
    except Exception:
        return 'domain'


def sort_brands(brands, sg_map):
    """排序品牌：子品牌→父品牌→字母序"""
    sub_brands_set = set(SUB_PARENT.keys())
    parent_brands_set = set(SUB_PARENT.values()) - sub_brands_set

    # 递归收集所有子品牌（含嵌套），返回 (品牌名, 深度) 列表
    def collect_children(parent, brands_set, depth=1):
        kids = []
        for b in brands_set:
            if b in SUB_PARENT and SUB_PARENT[b] == parent:
                kids.append((b, depth))
                kids.extend(collect_children(b, brands_set, depth+1))
        # 排序：深度大的先（嵌套深的先），同深度按字母序
        def sort_key(item):
            return (-item[1], sg_map.get(item[0], item[0]))
        return sorted(kids, key=sort_key)

    # 按父品牌分组：父品牌内子品牌(含嵌套)按字母序排在父品牌前
    result = []
    done = set()
    for parent in sorted(parent_brands_set, key=lambda p: sg_map.get(p, p)):
        children = collect_children(parent, set(brands))
        for c, _ in children:
            if c not in done:
                result.append(c)
                done.add(c)
        result.append(parent)
        done.add(parent)

    # 其他：按策略组名字母序
    other = sorted(
        [b for b in brands if b not in done],
        key=lambda b: sg_map.get(b, b)
    )
    return result + other


def build_brand_info(brands, sg_map):
    """构建品牌信息列表"""
    info = []
    for b in brands:
        yaml_path = ROOT / 'ruleset' / b / f'{b}.yaml'
        bhv = detect_behavior(yaml_path)
        sg = sg_map.get(b, b)
        info.append({
            'key': b,
            'sg': sg,
            'behavior': bhv,
        })
    return info


def extract_icons(config_path, sg_map):
    """从现有 config 提取品牌图标映射，缺失的从 Oasisic-Icons 匹配"""
    icons = {}
    if os.path.exists(config_path):
        try:
            with open(config_path) as f:
                content = f.read()
            current_name = None
            for line in content.split('\n'):
                m = re.match(r'\s+-\s+name:\s*"([^"]+)"', line)
                if m:
                    current_name = m.group(1)
                    if current_name in SYSTEM_GROUPS:
                        current_name = None
                    continue
                if current_name:
                    m2 = re.match(r'\s+icon:\s*"([^"]+)"', line)
                    if m2:
                        icons[current_name] = m2.group(1)
                        current_name = None
        except:
            pass
    
    # 从 Oasisic-Icons 匹配缺失的图标
    GITHUB_ICON = 'https://raw.githubusercontent.com/Hawaiine/Oasisic-Icons/main/icons'
    icon_repo = Path('/opt/data/Oasisic-Icons')
    if icon_repo.exists():
        for root, dirs, files in os.walk(icon_repo / 'icons'):
            for f in files:
                if not f.endswith('.png'):
                    continue
                cat = os.path.relpath(root, icon_repo / 'icons')
                name = f.rsplit('.', 1)[0]
                # 要匹配的策略组名集合
                all_sgs = list(sg_map.values()) + list(sg_map.keys())
                for sg in all_sgs:
                    if sg in icons:
                        continue
                    s = sg.replace(' ', '').replace('@', '-').lower()
                    n = name.replace(' ', '').replace('@', '-').lower()
                    base_n = re.sub(r'-\d+$', '', n)
                    if s == base_n or s == n:
                        icons[sg] = f'{GITHUB_ICON}/{cat}/{f}'
    
    return icons


def extract_system_groups(config_path):
    """从现有 config 提取系统组 YAML 文本（含 proxy-groups: 标题）"""
    if not os.path.exists(config_path):
        return ''
    try:
        with open(config_path) as f:
            content = f.read()
        m = re.search(r'proxy-groups:\n', content)
        if not m:
            return ''
        start = m.start()
        rest = content[start:]
        lines = rest.split('\n')
        result = []
        system_count = 0
        for line in lines:
            result.append(line)
            if '- name:' in line:
                system_count += 1
                if system_count > len(SYSTEM_GROUPS):
                    result.pop()
                    break
        result_str = '\n'.join(result)
        # 确保系统组完整（🐟 漏网之鱼 可能缺少属性）
        if '  - name: "🐟 漏网之鱼"\n\n' in result_str:
            result_str = result_str.replace(
                '  - name: "🐟 漏网之鱼"\n\n',
                '  - name: "🐟 漏网之鱼"\n    type: select\n    proxies:\n      - DIRECT\n      - "♻️ 自动选择"\n      - "🔧 手动切换"\n    use:\n      - provider1\n'
            )
        return result_str.rstrip('\n') + '\n'
    except Exception:
        return ''


def extract_system_config(config_path):
    """提取系统配置（从文件开头到 proxy-providers 结束）"""
    if not os.path.exists(config_path):
        return ''
    try:
        with open(config_path) as f:
            content = f.read()
        idx = content.find('proxy-groups:')
        if idx >= 0:
            return content[:idx]
        return content
    except Exception:
        return ''


# ============ 生成函数 ============

def gen_proxy_groups(brand_info, icons):
    """生成品牌组 YAML（不含 proxy-groups: 标题，已在系统组中）"""
    lines = []
    for bi in brand_info:
        lines.append(f'  - name: "{bi["sg"]}"')
        lines.append('    type: select')
        lines.append('    proxies:')
        lines.append('      - DIRECT')
        lines.append('      - "♻️ 自动选择"')
        lines.append('      - "🔧 手动切换"')
        lines.append('    use:')
        lines.append('      - provider1')
        if bi['sg'] in icons:
            lines.append(f'    icon: "{icons[bi["sg"]]}"')
    return '\n'.join(lines)


def gen_rule_providers(brand_info):
    """生成 rule-providers 区块"""
    lines = ['rule-providers:']
    # 基础 provider
    for name, bhv in BASE_PROVIDERS.items():
        lines.append(f'  {name}:')
        lines.append('    type: http')
        lines.append(f'    behavior: {bhv}')
        lines.append(f'    url: "{GITHUB_BASE}/ruleset/{name}/{name}.yaml"')
        lines.append('    interval: 86400')
        lines.append(f'    path: ./ruleset/{name}.yaml')
    # 品牌 provider（按 provider key 字母序）
    for bi in sorted(brand_info, key=lambda x: x['key']):
        lines.append(f'  {bi["key"]}:')
        lines.append('    type: http')
        lines.append(f'    behavior: {bi["behavior"]}')
        lines.append(f'    url: "{GITHUB_BASE}/ruleset/{bi["key"]}/{bi["key"]}.yaml"')
        lines.append('    interval: 86400')
        lines.append(f'    path: ./ruleset/{bi["key"]}.yaml')
    return '\n'.join(lines)


def gen_rules(brand_info, variant):
    """
    生成 rules 区块
    variant: 'android_full', 'android_min', 'nikki_full', 'nikki_min'
    """
    is_full = 'full' in variant
    is_nikki = 'nikki' in variant
    
    lines = []
    lines.append('rules:')
    
    # 段 1: 拦截
    if is_full:
        lines.append('                                                    # ----- 1. 拦截 (最高优先级) -----')
    lines.append('  - RULE-SET,Reject,🛑 全球拦截')

    # 段 2: 品牌分流
    if is_full:
        lines.append('')
        lines.append('                                                    # ----- 2. 品牌分流 (按需取消注释, 放在国内规则前, 避免被GEOIP截胡) -----')
        lines.append('                                                    # 顺序说明: 子品牌/重叠品牌排父品牌前, 避免被宽泛规则截胡')
        lines.append('                                                    # 例: AppleTV/SiriAI/iCloud 在 Apple 前')
        lines.append('                                                    # 全量品牌, 按需取消注释即可')
        for bi in brand_info:
            lines.append(f'                                                    # - RULE-SET,{bi["key"]},{bi["sg"]}')
    # min 版：无品牌 RULE-SET（provider 已配置，按需自行添加）
    # 段 3: 应用程序直连
    if is_full:
        lines.append('')
    if is_full:
        if is_nikki:
            lines.append('                                                    # ----- 3. 应用程序直连 (Nikki 跳过: find-process-mode: off, 进程规则无效) -----')
            lines.append('                                                    # - RULE-SET,Applications,DIRECT')
        else:
            lines.append('                                                    # ----- 3. 应用程序直连 (进程匹配, 如迅雷/QQ/微信等不走代理) -----')
            lines.append('  - RULE-SET,Applications,DIRECT')
    else:
        # min 版
        if not is_nikki:
            lines.append('  - RULE-SET,Applications,DIRECT')
        # Nikki min 跳过 Applications
    
    # 段 4: 局域网 & 直连
    if is_full:
        lines.append('')
    if is_full:
        lines.append('                                                    # ----- 4. 局域网 & 直连 -----')
    lines.append('  - RULE-SET,LanCIDR,DIRECT')
    lines.append('  - RULE-SET,Private,DIRECT')
    lines.append('  - RULE-SET,Direct,DIRECT')
    
    # 段 5: 国内 IP
    if is_full:
        lines.append('')
    if is_full:
        lines.append('                                                    # ----- 5. 国内 IP (GeoIP 放最后, 兜底国内流量) -----')
    lines.append('  - RULE-SET,CNCIDR,DIRECT')
    lines.append('  - GEOIP,CN,DIRECT')
    
    # 段 6: 代理
    if is_full:
        lines.append('')
    if is_full:
        lines.append('                                                    # ----- 6. 代理 (国际流量) -----')
    lines.append('  - RULE-SET,Proxy,🔧 手动切换')
    
    # 段 7: 兜底
    if is_full:
        lines.append('')
    if is_full:
        lines.append('                                                    # ----- 7. 兜底 (必须最后) -----')
    lines.append('  - MATCH,🐟 漏网之鱼')
    if is_full:
        lines.append('')
    
    return '\n'.join(lines)


def assemble_config(system_config, system_groups, proxy_groups, rule_providers, rules, variant='full'):
    """拼接完整 config"""
    if 'min' in variant:
        # min 版：sections 之间单换行（system_groups 本身已带尾部换行）
        return system_config + system_groups + proxy_groups + '\n' + rule_providers + '\n' + rules
    return system_config + system_groups + '\n' + proxy_groups + '\n' + rule_providers + '\n\n' + rules


def write_if_changed(path, content):
    """幂等写入：无变化不写入（跳过 Updated: 时间戳噪音）"""
    path = Path(path)
    if path.exists():
        with open(path) as f:
            existing = f.read()
        # 比较时跳过 # Updated: 行
        def strip_updated(t):
            return '\n'.join(l for l in t.split('\n') if 'Updated:' not in l)
        if strip_updated(existing) == strip_updated(content):
            return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w') as f:
        f.write(content)
    return True


def main():
    print('=== generate_config.py ===')
    
    # 加载映射
    sg_map = load_strategy_group_map()
    print(f'[+] 加载 STRATEGY_GROUP_MAP: {len(sg_map)} 条')
    
    # 扫描品牌
    brands = scan_brands()
    print(f'[+] 扫描 ruleset/: {len(brands)} 个品牌')
    
    # 排序
    sorted_brands = sort_brands(brands, sg_map)
    brand_info = build_brand_info(sorted_brands, sg_map)
    
    # 统计 behavior
    domain_count = sum(1 for b in brand_info if b['behavior'] == 'domain')
    classical_count = sum(1 for b in brand_info if b['behavior'] == 'classical')
    print(f'[+] behavior: domain={domain_count}, classical={classical_count}')
    
    # 提取图标
    icons = extract_icons(ROOT / 'configs' / 'Android' / 'config.yaml', sg_map)
    print(f'[+] 图标映射: {len(icons)} 个')
    
    # 生成区块
    proxy_groups = gen_proxy_groups(brand_info, icons)
    rule_providers = gen_rule_providers(brand_info)
    
    # 定义 4 个变体
    variants = {
        'android_full': (ROOT / 'configs' / 'Android' / 'config.yaml', 'Android'),
        'android_min': (ROOT / 'configs' / 'Android' / 'config.min.yaml', 'Android'),
        'nikki_full': (ROOT / 'configs' / 'Nikki' / 'config.yaml', 'Nikki'),
        'nikki_min': (ROOT / 'configs' / 'Nikki' / 'config.min.yaml', 'Nikki'),
    }
    
    for variant, (config_path, platform) in variants.items():
        system_config = extract_system_config(str(config_path))
        system_groups = extract_system_groups(str(config_path))
        rules = gen_rules(brand_info, variant)
        output = assemble_config(system_config, system_groups, proxy_groups, rule_providers, rules, variant)
        
        # 写入 /tmp 用于 diff
        tmp_path = Path(f'/tmp/{variant}.yaml')
        changed = write_if_changed(tmp_path, output)
        if changed:
            # 有实质变化才复制到 configs/
            import shutil
            shutil.copy2(str(tmp_path), str(config_path))
            print(f'[+] 生成 /tmp/{variant}.yaml')
            print(f'    → 已同步到 {config_path}')
        else:
            print(f'[=] 无变化，跳过: {variant}')
    
    print('\n=== 完成 ===')
    print('请对比 diff 后确认覆盖')
    print('  diff configs/Android/config.yaml /tmp/android_full.yaml')
    print('  diff configs/Android/config.min.yaml /tmp/android_min.yaml')
    print('  diff configs/Nikki/config.yaml /tmp/nikki_full.yaml')
    print('  diff configs/Nikki/config.min.yaml /tmp/nikki_min.yaml')


if __name__ == '__main__':
    main()