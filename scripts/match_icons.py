#!/usr/bin/env python3
"""
match_icons.py — 品牌图标映射的唯一入口

`build_icon_map()` 返回 {策略组名: icon URL}，供 generate_config.py 使用；
`python3 scripts/match_icons.py` 直接打印映射与缺失清单。

扫描基准（discovery/validation source）只读取 oasisic_revision.json 固定的 pinned SHA。
找不到该 tree 时失败，不回退 origin/main、main、HEAD 或工作区文件。
生产消费 icon URL（consumer asset URL）固定使用 main 分支 ref，
与 discovery/validation 的 pinned SHA 分离——两者不得混用。
"""
import os
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ICON_REPO_ENV_VAR = 'MIHOMO_ICON_REPO'


def icon_repo_candidates(environ=None, root=None):
    """按优先级返回 [(候选路径, 来源说明)]。

    优先级：显式 ``MIHOMO_ICON_REPO`` → 仓库相对 ``<root>/Oasisic-Icons``。

    显式指定时**不再追加任何机器专属回退**——调用方给出的值必须被尊重，
    即使它无效，也要在 :func:`icon_repo_problems` 中显式报错，
    而不是被静默替换成另一台机器上的目录。
    """
    env = os.environ if environ is None else environ
    project_root = ROOT if root is None else Path(root)
    supplied = (env.get(ICON_REPO_ENV_VAR) or '').strip()
    if supplied:
        return [(Path(supplied), f'{ICON_REPO_ENV_VAR}={supplied}')]
    relative = project_root / 'Oasisic-Icons'
    return [(relative, f'仓库相对路径 {relative}')]


def resolve_icon_repo(environ=None, root=None):
    """解析 Oasisic-Icons 检出位置（只做选择，不做可用性判断）。"""
    return icon_repo_candidates(environ=environ, root=root)[0][0]


def icon_repo_problems(repo, revision, runner=None):
    """检查检出是否可用于读取 pinned tree，返回问题列表（空 = 可用）。

    只判断“路径存在”是不够的：必须同时确认它是可用的 git 工作树，
    且能读取 ``oasisic_revision.json`` 指定的固定 revision。
    """
    run = subprocess.run if runner is None else runner
    repo = Path(repo)
    if not repo.is_dir():
        return [f'路径不存在或不是目录: {repo}']
    if not (repo / '.git').exists():
        return [f'缺少 git 元数据 {repo / ".git"}（需要完整 clone；仅复制文件无法读取 pinned tree）']
    try:
        probe = run(
            ['git', '-C', str(repo), 'cat-file', '-e', f'{revision}^{{commit}}'],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return [f'无法执行 git 校验固定 revision {revision}: {exc}']
    if probe.returncode != 0:
        return [f'固定 revision 在检出中不可读: {revision}']
    return []


def icon_repo_required_message(revision, environ=None, root=None):
    """构造 fail-closed 错误信息：尝试过的路径 + 预期条件 + 解决方法。"""
    lines = ['无法定位可用的 Oasisic-Icons 检出（生产图标映射 fail-closed，不回退到任何机器专属目录）']
    for repo, origin in icon_repo_candidates(environ=environ, root=root):
        problems = icon_repo_problems(repo, revision)
        lines.append(f'  - 尝试 {origin} → {repo}：{"可用" if not problems else "; ".join(problems)}')
    lines.append(f'预期条件：完整 git clone，且能读取固定 revision {revision}'
                 '（scripts/config_contract/oasisic_revision.json）')
    lines.append('解决方法：将 Hawaiine/Oasisic-Icons clone 到 <仓库根>/Oasisic-Icons，'
                 f'或设置 {ICON_REPO_ENV_VAR} 指向其绝对路径')
    return '\n'.join(lines)


ICON_REPO = resolve_icon_repo()
_REVISION_MANIFEST = ROOT / 'scripts' / 'config_contract' / 'oasisic_revision.json'
try:
    with _REVISION_MANIFEST.open(encoding='utf-8') as f:
        _OASIC_REVISION = json.load(f)['revision']
except (OSError, KeyError, json.JSONDecodeError) as exc:
    raise RuntimeError(f'缺少固定 Oasisic revision: {_REVISION_MANIFEST}') from exc
if not _OASIC_REVISION:
    raise RuntimeError(f'缺少固定 Oasisic revision: {_REVISION_MANIFEST}')
# 生产消费 URL 固定使用 main 分支 ref；discovery/validation 的 tree 扫描仍只用 pinned SHA
ASSET_URL_REF = 'main'
GITHUB_BASE = f'https://raw.githubusercontent.com/Hawaiine/Oasisic-Icons/{ASSET_URL_REF}/icons'

sys.path.insert(0, str(ROOT / 'scripts'))
from commit_writer import STRATEGY_GROUP_MAP

BASE = {'Reject', 'Direct', 'Proxy', 'CNCIDR', 'Private', 'Applications', 'LanCIDR', 'DirectDNS', 'ProxyDNS'}

# 显式覆盖（优先级高于自动匹配）
# 临时兼容层：key 为策略组显示名（非 Technical ID），待 canonical mapping 迁移完成后逐条移除。
# key 失效（STALE_OVERRIDE_KEY）与路径失效（STALE_OVERRIDE）都会被 icon_mapping 校验器检出。
ICON_OVERRIDES = {
    # 图标仓库改过名 / 改过分类，自动匹配不到或需固定指向（对齐 Oasisic-Icons 2026-09 最新结构）
    'friDay影音': 'Media/friDayVideo/friDayVideo.png',
    # Oasisic canonical path changes (fixes the invalid historical overrides)
    'Cloudflare': 'Infrastructure/Cloudflare/Cloudflare.png',
    'Disney': 'Disney/DisneyPlus/DisneyPlus.png',
    'HBO': 'WarnerBrosDiscovery/HBOMax/HBOMax.png',
    '网易云音乐': 'NetEase/NetEaseCloudMusic/NetEaseCloudMusic.png',
    'Podcast': 'Media/Xiaoyuzhou/Xiaoyuzhou.png',
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

    raise RuntimeError(icon_repo_required_message(_OASIC_REVISION))


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