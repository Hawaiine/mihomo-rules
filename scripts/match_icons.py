#!/usr/bin/env python3
"""
match_icons.py — 从 Oasisic-Icons 仓库自动匹配品牌图标
输出图标映射字典，供 generate_config.py 使用
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 图标仓库路径优先级：环境变量 > 仓库本地克隆 > 旧硬编码路径
ICON_REPO = Path(os.environ.get('MIHOMO_ICON_REPO', str(ROOT / 'Oasisic-Icons')))
if not ICON_REPO.exists():
    ICON_REPO = Path('/opt/data/Oasisic-Icons')
GITHUB_BASE = 'https://raw.githubusercontent.com/Hawaiine/Oasisic-Icons/main/icons'

# 导入品牌映射
sys.path.insert(0, str(ROOT / 'scripts'))
from commit_writer import STRATEGY_GROUP_MAP

BASE = {'Reject', 'Direct', 'Proxy', 'CNCIDR', 'Private', 'Applications', 'LanCIDR'}


def scan_icons():
    """扫描 Oasisic-Icons 仓库，返回 {规范化文件名: (分类, 原文件名)}"""
    icons = {}
    icon_dir = ICON_REPO / 'icons'
    if not icon_dir.exists():
        print(f'❌ Oasisic-Icons 不存在: {icon_dir}')
        return icons
    
    for root, dirs, files in os.walk(icon_dir):
        for f in files:
            if not f.endswith('.png'):
                continue
            rel_dir = os.path.relpath(root, icon_dir)
            # 规范化：去空格、去后缀、去特殊字符
            name = f.rsplit('.', 1)[0]  # 去 .png
            name = name.replace(' ', '')
            name = name.replace('@', '-')
            # 去掉 -1, -2 等数字后缀
            import re
            base_name = re.sub(r'-\d+$', '', name)
            icons[name.lower()] = (rel_dir, f, base_name.lower())
    return icons


def match_icon(brand, sg, icons):
    """为品牌匹配图标"""
    candidates = []
    
    # 1. 用目录名匹配
    candidates.append(brand.replace(' ', ''))
    # 2. 用策略组名匹配
    candidates.append(sg.replace(' ', ''))
    candidates.append(sg.replace(' ', '').replace('@', '-'))
    
    # 3. 特殊映射
    special = {
        'CATCHPLAY': 'CatchPlay-Plus',
        'GoogleAI': 'Gemini',
        'UNext': 'U-NEXT',
        'iCloudPrivateRelay': 'iCloud-Private-Relay',
        'karaokeDAM': 'Karaoke@DAM',
        'FujiTV': 'Fuji-TV',
        'RakutenTV': 'Rakuten-TV',
        'PrimeVideo': 'Prime-Video',
        'DAnimeStore': 'D-Anime-Store',
        'DMMTV': 'DMM-TV',
        'F1TV': 'F1-TV',
        'HOYTV': 'HOY-TV',
        'GameJapan': 'Game-Japan',
        'PornChina': 'Porn-China',
        'PTChina': 'PT-China',
        'MusicJapan': 'Music-Japan',
        'ReadsJapan': 'Reads-Japan',
        'VideoMarket': 'Video-Market',
        'MyTVSuper': 'myTV-Super',
        'LineTV': 'LineTV',
        'LINETV': 'LineTV',
        'NowE': 'Now-E',
        'HamiVideo': 'Hami-Video',
        'ZLibrary': 'Z-Library',
        'GeneralAI': 'General-AI',
        'iCloud': 'iCloud-2',
        'AppleTV': 'AppleTV',
        'SiriAI': 'SiriAI',
        'YouTubeMusic': 'YouTubeMusic',
    }
    if brand in special:
        candidates.insert(0, special[brand].replace(' ', ''))
    
    # 尝试匹配
    for c in candidates:
        c_lower = c.lower().replace(' ', '').replace('@', '-')
        for icon_norm, (cat, fname, base_norm) in icons.items():
            if base_norm == c_lower or icon_norm == c_lower:
                return f'{GITHUB_BASE}/{cat}/{fname}'
            # 尝试 c-1, c-2
            for suffix in ['-1', '-2', '-3', '-4', '-5']:
                if icon_norm == c_lower + suffix:
                    return f'{GITHUB_BASE}/{cat}/{fname}'
    
    return None


def main():
    icons = scan_icons()
    print(f'[+] 扫描到 {len(icons)} 个图标')
    
    brands = []
    for d in sorted(os.listdir(ROOT / 'ruleset')):
        if d in BASE:
            continue
        if not os.path.isdir(ROOT / 'ruleset' / d):
            continue
        if not os.path.isfile(ROOT / 'ruleset' / d / f'{d}.yaml'):
            continue
        brands.append(d)
    
    found = 0
    icon_map = {}
    missing = []
    for b in brands:
        sg = STRATEGY_GROUP_MAP.get(b, b)
        url = match_icon(b, sg, icons)
        if url:
            found += 1
            icon_map[sg] = url
        else:
            missing.append(b)
    
    print(f'[+] 匹配: {found}/{len(brands)}')
    if missing:
        print(f'[!] 缺失 ({len(missing)}): {", ".join(missing)}')
    
    # 输出 Python 字典
    print()
    print('ICON_MAP = {')
    for sg in sorted(icon_map.keys()):
        print(f'    "{sg}": "{icon_map[sg]}",')
    print('}')


if __name__ == '__main__':
    main()