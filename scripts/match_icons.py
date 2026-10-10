#!/usr/bin/env python3
"""
match_icons.py — 品牌图标映射的唯一入口

`build_icon_map()` 返回 {策略组名: icon URL}，供 generate_config.py 使用；
`python3 scripts/match_icons.py` 直接打印映射与缺失清单。

两种用途，必须分清（详见 scripts/config_contract/oasisic_revision.json）：

* **discovery / validation revision**：``revision`` 的完整 40 位 SHA，用于扫描
  Oasisic-Icons 的固定 Git tree、复现图标匹配结果。找不到该 tree 时失败，
  不回退 ``origin/main`` / ``main`` / ``HEAD`` / 工作区文件。
* **production asset URL ref**：``asset_url_mode`` + ``production_url_ref`` 决定的
  消费端 ref，用于生成生产图标 URL。

两者都只从 manifest 读取——本模块不再自带第二份运行时配置。
路径解析（``MIHOMO_ICON_REPO`` 优先，其次仓库相对 ``Oasisic-Icons``）来自
``scripts/icon_repo.py`` 的单一实现，与 PR 门禁共用。
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_REVISION_MANIFEST = ROOT / 'scripts' / 'config_contract' / 'oasisic_revision.json'
_REPO_SLUG = re.compile(r'^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$')

# 生产资产 URL 模式 → 该模式允许的 ``production_url_ref``（None = 由 revision 决定）
ASSET_URL_MODES = {
    'branch-main': 'main',
    'commit-pinned': None,
}


def load_manifest(path=None):
    """读取 revision manifest；缺失 / 非法 JSON / 无 revision 一律抛错（fail-fast）。"""
    manifest_path = _REVISION_MANIFEST if path is None else Path(path)
    try:
        with manifest_path.open(encoding='utf-8') as f:
            manifest = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f'缺少固定 Oasisic revision: {manifest_path}') from exc
    if not isinstance(manifest, dict) or not manifest.get('revision'):
        raise RuntimeError(f'缺少固定 Oasisic revision: {manifest_path}')
    return manifest


def asset_url_ref(manifest):
    """生产资产 URL 使用的 ref：由 ``asset_url_mode`` 唯一决定，并做 fail-closed 校验。"""
    mode = manifest.get('asset_url_mode')
    if mode not in ASSET_URL_MODES:
        raise RuntimeError(
            f'未知的 asset_url_mode {mode!r}（{_REVISION_MANIFEST}）；'
            f'允许值: {sorted(ASSET_URL_MODES)}')
    required_ref = ASSET_URL_MODES[mode]
    supplied = manifest.get('production_url_ref')
    if required_ref is None:
        # commit-pinned：有效 ref 从 revision 推导，production_url_ref 必须省略或 null
        if supplied is not None:
            raise RuntimeError(
                f'asset_url_mode "commit-pinned" 的有效 ref 来自 revision，'
                f'production_url_ref 必须省略或为 null，实际 {supplied!r}（{_REVISION_MANIFEST}）')
        ref = manifest.get('revision')
    else:
        if supplied != required_ref:
            raise RuntimeError(
                f'asset_url_mode {mode!r} 要求 production_url_ref == {required_ref!r}，'
                f'实际 {supplied!r}（{_REVISION_MANIFEST}）')
        ref = supplied
    if not isinstance(ref, str) or not ref:
        raise RuntimeError(f'生产图标 URL 的 ref 为空（{_REVISION_MANIFEST}）')
    return ref


def asset_url_base(manifest):
    """由 manifest 单一来源构建生产图标 URL 基址。

    ``asset_url_mode`` 未知、``production_url_ref`` 与模式自相矛盾、``repository``
    不是 ``owner/name`` 时一律抛错——不允许把任意主机 / 仓库 / ref 注入生产 URL。
    """
    ref = asset_url_ref(manifest)
    repository = manifest.get('repository')
    if not isinstance(repository, str) or not _REPO_SLUG.match(repository):
        raise RuntimeError(
            f'repository 必须是 "owner/name" 形式，实际 {repository!r}（{_REVISION_MANIFEST}）')
    return f'https://raw.githubusercontent.com/{repository}/{ref}/icons'


_MANIFEST = load_manifest()
_OASIC_REVISION = _MANIFEST['revision']
# 生产消费 URL 的 ref：由 manifest 推导，不再硬编码（见 ASSET_URL_MODES）
ASSET_URL_REF = asset_url_ref(_MANIFEST)
GITHUB_BASE = asset_url_base(_MANIFEST)

# 共享路径解析：本模块不再自带一份实现（scripts/icon_repo.py 是唯一来源）。
# 注意顺序：manifest 校验必须先于本导入，缺失 manifest 时要先报“缺少固定 revision”。
sys.path.insert(0, str(ROOT / 'scripts'))
from icon_repo import (  # noqa: E402
    ICON_REPO_ENV_VAR,
    icon_repo_candidates,
    icon_repo_problems,
    icon_repo_relative_path,
    icon_repo_required_message,
    resolve_icon_repo,
)

from commit_writer import STRATEGY_GROUP_MAP  # noqa: E402

ICON_REPO = resolve_icon_repo()

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
