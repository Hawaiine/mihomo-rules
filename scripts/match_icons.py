#!/usr/bin/env python3
"""
match_icons.py — 品牌图标映射的唯一入口

`build_icon_map()` 返回 {策略组名: icon URL}，供 generate_config.py 使用；
`python3 scripts/match_icons.py` 直接打印映射与缺失清单。

扫描基准只读取 oasisic_revision.json 固定的 SHA。
找不到该 tree 时失败，不回退 origin/main、main、HEAD 或工作区文件。
"""
import os
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ICON_REPO = Path(os.environ.get('MIHOMO_ICON_REPO', str(ROOT / 'Oasisic-Icons')))
if not ICON_REPO.exists():
    ICON_REPO = Path('/opt/data/Oasisic-Icons')
_REVISION_MANIFEST = ROOT / 'scripts' / 'config_contract' / 'oasisic_revision.json'
try:
    with _REVISION_MANIFEST.open(encoding='utf-8') as f:
        _OASIC_REVISION = json.load(f)['revision']
except (OSError, KeyError, json.JSONDecodeError) as exc:
    raise RuntimeError(f'缺少固定 Oasisic revision: {_REVISION_MANIFEST}') from exc
if not _OASIC_REVISION:
    raise RuntimeError(f'缺少固定 Oasisic revision: {_REVISION_MANIFEST}')
GITHUB_BASE = f'https://raw.githubusercontent.com/Hawaiine/Oasisic-Icons/{_OASIC_REVISION}/icons'

sys.path.insert(0, str(ROOT / 'scripts'))
from commit_writer import STRATEGY_GROUP_MAP

BASE = {'Reject', 'Direct', 'Proxy', 'CNCIDR', 'Private', 'Applications', 'LanCIDR', 'DirectDNS', 'ProxyDNS'}

# 显式覆盖（优先级高于自动匹配）
# 临时兼容层：key 为策略组显示名（非 Technical ID），待 canonical mapping 迁移完成后逐条移除。
# key 失效（STALE_OVERRIDE_KEY）与路径失效（STALE_OVERRIDE）都会被 icon_mapping 校验器检出。
ICON_OVERRIDES = {
    # 图标仓库改过名 / 改过分类，自动匹配不到或需固定指向（对齐 Oasisic-Icons 2026-09 最新结构）
    'friDay影音': 'Media/friDayVideo/friDayVideo.png',
    'Disney': 'Media/DisneyPlus/DisneyPlus.png',
    'HBO': 'Media/HBOMAX/HBOMAX.png',
    '网易云音乐': 'Music/NetEaseCloudMusic/NetEaseCloudMusic.png',
    # 下列品牌在图标仓库中存在多个分类目录，固定指向避免扫描顺序变化导致路径漂移
    'Cloudflare': 'DevOps/Cloudflare/Cloudflare.png',
    'OneDrive': 'Microsoft/OneDrive/OneDrive.png',
}

_SCAN_SOURCE = '未扫描'

# 显示名以 emoji 开头的策略组一律不配 icon（项目约定：emoji 组不加 icon）
EMOJI_PREFIX = re.compile(
    '[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u2190-\u21FF]'
)


def is_emoji_group(sg):
    """策略组显示名是否以 emoji 开头（如 🤖 General AI / 🎮 Game Japan / 💊 PT）"""
    return bool(EMOJI_PREFIX.match(str(sg).strip()))


def _tree_paths():
    """读取图标仓库 git tree 中的 png 路径，返回 (相对路径列表, 来源) 或 (None, None)"""
    for ref in (_OASIC_REVISION,):
        try:
            r = subprocess.run(
                ['git', '-C', str(ICON_REPO), 'ls-tree', '-r', '--name-only', ref, '--', 'icons/'],
                capture_output=True, text=True, timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            return None, None
        if r.returncode == 0 and r.stdout.strip():
            paths = [p for p in r.stdout.splitlines() if p.endswith('.png')]
            if paths:
                return paths, f'{ICON_REPO}@{ref}'
    return None, None


def scan_icons():
    """扫描 Oasisic-Icons，返回 {小写文件名键: (分类, 相对路径)}"""
    global _SCAN_SOURCE
    icons = {}

    paths, src = _tree_paths()
    if paths is not None:
        for p in paths:
            rel = p[len('icons/'):] if p.startswith('icons/') else p
            parts = rel.split('/')
            if len(parts) < 3:
                continue
            icons[parts[-1][:-4].lower()] = (parts[0], rel)
        _SCAN_SOURCE = src
        return icons

    raise RuntimeError(f'固定 Oasisic revision 不存在或无法读取: {ICON_REPO}@{_OASIC_REVISION}')


def scan_source():
    """返回最近一次扫描的数据来源描述"""
    return _SCAN_SOURCE


def match_icon(brand, sg, icons):
    """为品牌匹配图标，返回完整 URL 或 None"""
    candidates = []

    # 1. 用目录名匹配
    candidates.append(brand.replace(' ', ''))
    # 2. 用策略组名匹配
    candidates.append(sg.replace(' ', ''))
    candidates.append(sg.replace(' ', '').replace('@', '-'))

    # 3. 特殊映射：仅 Podcast → Podcasts
    special = {'Podcast': 'Podcasts'}
    key = brand.replace(' ', '')
    if key in special:
        candidates.insert(0, special[key])

    # 尝试匹配
    for c in candidates:
        c_lower = c.lower().replace(' ', '').replace('@', '-')
        # 精确匹配 Brand.png
        if c_lower in icons and icons[c_lower][1].endswith(f'/{c}/{c}.png'):
            return f'{GITHUB_BASE}/{icons[c_lower][1]}'
        # 精确匹配任意
        if c_lower in icons:
            return f'{GITHUB_BASE}/{icons[c_lower][1]}'
        # 尝试变体（01/02...）
        for suffix in ['01', '02', '03', '04', '05']:
            variant = f'{c_lower}{suffix}'
            if variant in icons:
                return f'{GITHUB_BASE}/{icons[variant][1]}'
        # + 的特殊处理：catchplay+ → catchplay-plus
        for repl in ['-plus', 'plus', '-']:
            alt = c_lower.replace('+', repl)
            if alt in icons:
                return f'{GITHUB_BASE}/{icons[alt][1]}'
            for suffix in ['01', '02', '03', '04', '05']:
                variant = f'{alt}{suffix}'
                if variant in icons:
                    return f'{GITHUB_BASE}/{icons[variant][1]}'

    return None


def brand_dirs():
    """ruleset/ 下参与配置生成的品牌目录名（排除 9 个兜底规则集）"""
    result = []
    for d in sorted(os.listdir(ROOT / 'ruleset')):
        if d in BASE:
            continue
        if not os.path.isdir(ROOT / 'ruleset' / d):
            continue
        if not os.path.isfile(ROOT / 'ruleset' / d / f'{d}.yaml'):
            continue
        result.append(d)
    return result


def build_icon_map():
    """返回 ({策略组名: icon URL}, 未匹配到图标的品牌列表)

    - 显示名以 emoji 开头的策略组一律跳过（emoji 组不配 icon）
    - 显式覆盖 ICON_OVERRIDES 优先，其余按 brand_dirs() 自动匹配
    """
    icons = scan_icons()
    result = {sg: f'{GITHUB_BASE}/{rel}' for sg, rel in ICON_OVERRIDES.items()}
    missing = []
    for b in brand_dirs():
        sg = STRATEGY_GROUP_MAP.get(b, b)
        if is_emoji_group(sg):
            continue
        if sg in result:
            continue
        url = match_icon(b, sg, icons)
        if url:
            result[sg] = url
        else:
            missing.append(b)
    return result, missing


def emoji_skipped_brands():
    """按「emoji 组不配 icon」规则被跳过的品牌（仅供报告）"""
    return [b for b in brand_dirs() if is_emoji_group(STRATEGY_GROUP_MAP.get(b, b))]


def main():
    icon_map, missing = build_icon_map()
    skipped = emoji_skipped_brands()
    print(f'[+] 图标来源: {scan_source()}')
    print(f'[+] 匹配: {len(icon_map)}/{len(brand_dirs())}')
    if skipped:
        print(f'[🚫] emoji 前缀组已跳过 ({len(skipped)}): {", ".join(skipped)}')
    if missing:
        print(f'[!] 缺失 ({len(missing)}): {", ".join(missing)}')

    print()
    print('ICON_MAP = {')
    for sg in sorted(icon_map.keys()):
        print(f'    "{sg}": "{icon_map[sg]}",')
    print('}')


if __name__ == '__main__':
    main()