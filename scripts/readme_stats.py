#!/usr/bin/env python3
"""
readme_stats.py — README 统计口径的唯一来源与漂移检查

README.md 里的品牌数 / 规则集数 / 规则总数 / 类型分布 / 分类统计
全部由本脚本从 `ruleset/` 实际数据计算，不允许手工填数。

用法：
    python3 scripts/readme_stats.py           # 打印实际统计（用于更新 README）
    python3 scripts/readme_stats.py --check   # 校验 README.md 与实测一致，不一致 exit≠0
    python3 scripts/readme_stats.py --table   # 只打印分类统计表行，便于粘贴

分类归属是编辑口径（BRAND_CATEGORIES），但**必须覆盖全部品牌**：
漏一个品牌脚本直接报错，避免分类表静默漏项。
"""

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

BASE_BRANDS = {
    'Reject', 'Direct', 'Proxy', 'CNCIDR', 'Private', 'Applications',
    'LanCIDR', 'DirectDNS', 'ProxyDNS',
}

TYPES_ORDER = [
    'DOMAIN-KEYWORD', 'DOMAIN-REGEX', 'DOMAIN', 'DOMAIN-SUFFIX',
    'IP-CIDR', 'IP-CIDR6', 'IP-ASN', 'PROCESS-NAME',
]

# 品牌分类（编辑口径）。键为分类标题，值为技术 ID 列表。
BRAND_CATEGORIES: dict[str, list[str]] = {
    '🎬 流媒体': [
        'AbemaTV', 'Bahamut', 'Bangumi', 'Bilibili', 'CATCHPLAY', 'Crunchyroll',
        'DAZN', 'DAnimeStore', 'DMMTV', 'Disney', 'Douyin', 'F1TV', 'FujiTV',
        'GameJapan', 'HBO', 'HOYTV', 'HamiVideo', 'Hotstar', 'Hulu', 'KKTV',
        'LINETV', 'Lemino', 'LiTV', 'MangoTV', 'MusicJapan', 'MyVideo', 'NHK',
        'Netflix', 'Niconico', 'NowE', 'ParamountPlus', 'PeacockTV', 'Podcast',
        'PrimeVideo', 'Radiko', 'RakutenTV', 'ReadJapan', 'RedNote', 'TVer',
        'Telasa', 'TencentVideo', 'Tubi', 'Twitch', 'UNext', 'VideoMarket',
        'Viu', 'WOWOW', 'YouTube', 'Youku', 'AppleNews', 'friDayVideo',
        'iQIYI', 'karaokeDAM', 'myTVSUPER',
    ],
    '🤖 AI': [
        'Anthropic', 'Copilot', 'Cursor', 'DeepSeek', 'Doubao', 'GeneralAI',
        'GoogleAI', 'Grok', 'Manus', 'NousResearch', 'OpenAI', 'Perplexity',
        'Poe', 'SiriAI',
    ],
    '📱 社交': [
        'Bluesky', 'Discord', 'Facebook', 'Instagram', 'KakaoTalk', 'Messenger',
        'NetEaseMail', 'Pinterest', 'Pixiv', 'QQ', 'QQMail', 'Reddit', 'Snapchat',
        'Telegram', 'Threads', 'TikTok', 'WeChat', 'Weibo', 'WhatsApp', 'X', 'Zhihu',
    ],
    '☁️ 云服务': [
        'AWS', 'AppStore', 'Azure', 'Bing', 'Cloudflare', 'Docker', 'GitHub',
        'Gmail', 'Google', 'GoogleDrive', 'GoogleMaps', 'GoogleNews',
        'GooglePhotos', 'GooglePlay', 'GoogleVoice', 'Microsoft', 'OneDrive',
        'Oracle', 'Outlook', 'Synology', 'iCloud', 'iCloudPrivateRelay',
    ],
    '🎮 游戏': ['Nintendo', 'PlayStation', 'Steam', 'Xbox'],
    '🛍️ 电商': ['AliPay', 'Amazon', 'JD', 'Meituan', 'PayPal', 'Pinduoduo', 'Taobao'],
    '🎵 音乐': [
        'AppleMusic', 'Deezer', 'Mora', 'Musixmatch', 'NetEaseCloudMusic',
        'Pandora', 'QQMusic', 'Qobuz', 'SoundCloud', 'Spotify', 'TIDAL',
        'YouTubeMusic',
    ],
    '🏢 企业': [
        'Apple', 'AppleFitnessPlus', 'AppleTV', 'Bank', 'DingTalk', 'Lark',
        'MetaBrainz', 'OasisicSelf', 'PT', 'PTChina', 'Porn', 'PornChina',
        'TMDB', 'WSJ', 'Wallpaper', 'ZLibrary',
    ],
}


def _iter_rules(yaml_path):
    """逐行产出 payload 中的规则行 (type, value)，忽略注释与 header"""
    in_payload = False
    for line in open(yaml_path, encoding='utf-8'):
        s = line.strip()
        if s.startswith('payload'):
            in_payload = True
            continue
        if not in_payload or not s or s.startswith('#'):
            continue
        m = re.match(r'^-\s*([A-Z][A-Z0-9_-]+)\s*,\s*(.+)$', s)
        if m:
            yield m.group(1), m.group(2).strip()


def compute_stats() -> dict:
    ruleset_dir = ROOT / 'ruleset'
    base_sets, brand_sets = [], []
    type_counts = {t: 0 for t in TYPES_ORDER}
    per_brand = {}
    for d in sorted(ruleset_dir.iterdir()):
        yaml_path = d / f'{d.name}.yaml'
        if not d.is_dir() or not yaml_path.is_file():
            continue
        n = 0
        for rtype, _value in _iter_rules(yaml_path):
            n += 1
            type_counts[rtype] = type_counts.get(rtype, 0) + 1
        per_brand[d.name] = n
        (base_sets if d.name in BASE_BRANDS else brand_sets).append(d.name)

    # 分类覆盖校验：不允许漏品牌、不允许幽灵品牌
    declared = [b for brands in BRAND_CATEGORIES.values() for b in brands]
    dupes = sorted({b for b in declared if declared.count(b) > 1})
    missing = sorted(set(brand_sets) - set(declared))
    ghost = sorted(set(declared) - set(brand_sets))
    if dupes or missing or ghost:
        raise SystemExit(
            f'BRAND_CATEGORIES 与 ruleset/ 不一致:\n'
            f'  重复登记: {dupes}\n  未分类品牌: {missing}\n  幽灵品牌: {ghost}')

    category_stats = [
        (cat, len(brands), sum(per_brand[b] for b in brands))
        for cat, brands in BRAND_CATEGORIES.items()
    ]

    base_rules = sum(v for k, v in per_brand.items() if k in BASE_BRANDS)
    brand_rules = sum(v for k, v in per_brand.items() if k not in BASE_BRANDS)
    return {
        'brand_count': len(brand_sets),
        'ruleset_count': len(base_sets) + len(brand_sets),
        'base_count': len(base_sets),
        'base_rules': base_rules,
        'brand_rules': brand_rules,
        'total_rules': base_rules + brand_rules,
        'type_counts': type_counts,
        'category_stats': category_stats,
    }


def print_stats(stats: dict) -> None:
    print(f"品牌 {stats['brand_count']} · 规则集 {stats['ruleset_count']} "
          f"· 规则总数 {stats['total_rules']} ({stats['total_rules'] / 10000:.1f} 万)")
    print(f"基础 {stats['base_count']} 集 / {stats['base_rules']} 条 · "
          f"品牌 {stats['brand_count']} 集 / {stats['brand_rules']} 条")
    dist = ' · '.join(f'{t}({stats["type_counts"].get(t, 0):,})' for t in TYPES_ORDER)
    print(f'类型分布：{dist}')
    print('分类统计：')
    for cat, n, rules in stats['category_stats']:
        print(f'  {cat} | {n} | {rules:,}')


def check_readme(stats: dict) -> list[str]:
    """校验 README.md 中所有可机读的统计口径与实测一致"""
    readme = (ROOT / 'README.md').read_text(encoding='utf-8')
    errs = []
    b, rs, total = stats['brand_count'], stats['ruleset_count'], stats['total_rules']
    wan = f'{total / 10000:.1f} 万'

    checks = [
        (f'{b} 品牌 · {rs} 规则集 · {wan}规则',
         'README <em> 简介行'),
        (f'badge/rulesets-{rs}-blue', 'README rulesets 徽章'),
        (f'badge/brands-{b}-orange', 'README brands 徽章'),
        (f'| **合计** | **{stats["base_count"]}** | **{b}** | **{rs}** | **{total:,}** |',
         'README 规则集统计合计行'),
    ]
    for needle, what in checks:
        if needle not in readme:
            errs.append(f'{what} 未与实测一致，期望包含: {needle!r}')

    # 类型分布行
    dist = ' · '.join(f'{t}({stats["type_counts"].get(t, 0):,})' for t in TYPES_ORDER)
    if dist not in readme:
        errs.append(f'README 规则类型分布未与实测一致，期望: {dist}')

    # 分类统计表：品牌数与规则数
    for cat, n, rules in stats['category_stats']:
        pattern = rf'^\|\s*{re.escape(cat)}\s*\|\s*{n}\s*\|\s*{rules:,}\s*\|'
        if not re.search(pattern, readme, re.M):
            errs.append(f'README 分类统计行未与实测一致，期望: | {cat} | {n} | {rules:,} |')

    # configs/*/README.md 中的「品牌策略组(N个)」口径
    for cfg_readme in sorted((ROOT / 'configs').glob('*/README.md')):
        text = cfg_readme.read_text(encoding='utf-8')
        for m in re.finditer(r'品牌策略组\((\d+)个\)', text):
            if int(m.group(1)) != b:
                errs.append(
                    f'{cfg_readme.relative_to(ROOT)} 品牌策略组数量 {m.group(1)} ≠ 实测 {b}')

    return errs


def check_readme_structure(stats: dict) -> list[str]:
    """结构性口径检查（不会因日更数据变化而漂移，可作为 CI 硬门禁）

    只覆盖「品牌数 / 规则集数 / 分类归属」这类**只在增删品牌时变化**的口径。
    规则条数类口径（规则总数 / 类型分布 / 分类规则数）由 check_readme 覆盖，
    因为日更会改变它们而 README 不参与日更提交。
    """
    readme = (ROOT / 'README.md').read_text(encoding='utf-8')
    errs = []
    b, rs = stats['brand_count'], stats['ruleset_count']
    for needle, what in [
        (f'{b} 品牌 · {rs} 规则集', 'README <em> 简介行'),
        (f'（{b} 品牌，emoji 前缀组不配图标）', 'README 图标注入行'),
        (f'等 {b} 品牌', 'README 规则匹配顺序品牌数'),
        (f'badge/rulesets-{rs}-blue', 'README rulesets 徽章'),
        (f'badge/brands-{b}-orange', 'README brands 徽章'),
        (f'| **合计** | **{stats["base_count"]}** | **{b}** | **{rs}** |', 'README 统计表合计行'),
        (f'{b} 品牌图标库', 'README 相关资源图标库'),
    ]:
        if needle not in readme:
            errs.append(f'{what} 未与实测一致，期望包含: {needle!r}')

    for cat, n, _rules in stats['category_stats']:
        if not re.search(rf'^\|\s*{re.escape(cat)}\s*\|\s*{n}\s*\|', readme, re.M):
            errs.append(f'README 分类统计行品牌数未与实测一致，期望: | {cat} | {n} |')

    for cfg_readme in sorted((ROOT / 'configs').glob('*/README.md')):
        text = cfg_readme.read_text(encoding='utf-8')
        for m in re.finditer(r'品牌策略组\((\d+)个\)', text):
            if int(m.group(1)) != b:
                errs.append(
                    f'{cfg_readme.relative_to(ROOT)} 品牌策略组数量 {m.group(1)} ≠ 实测 {b}')
    return errs


def update_readme(stats: dict) -> list[str]:
    """把 README.md 的统计口径重写为实测值（幂等）。返回变更项描述。"""
    path = ROOT / 'README.md'
    t = path.read_text(encoding='utf-8')
    orig = t
    b, rs, total = stats['brand_count'], stats['ruleset_count'], stats['total_rules']
    base_n, base_rules = stats['base_count'], stats['base_rules']
    wan = f'{total / 10000:.1f} 万'
    changed = []

    def sub(pattern: str, repl: str, label: str, count: int = 1) -> None:
        nonlocal t
        new, n = re.subn(pattern, repl, t, count=count)
        if n and new != t:
            changed.append(label)
        t = new

    sub(r'· \d+ 品牌 · \d+ 规则集 · [\d.]+ 万规则', f'· {b} 品牌 · {rs} 规则集 · {wan}规则', '简介行')
    sub(r'badge/rulesets-\d+-blue', f'badge/rulesets-{rs}-blue', 'rulesets 徽章')
    sub(r'badge/brands-\d+-orange', f'badge/brands-{b}-orange', 'brands 徽章')
    sub(r'（[^（）]*\d+ 品牌 · \d+ 规则集）', f'（流媒体 / AI / 社交 / 云服务 / 游戏等 {b} 品牌 · {rs} 规则集）', '概述段')
    sub(r'（\d+ 品牌，emoji 前缀组不配图标）', f'（{b} 品牌，emoji 前缀组不配图标）', '图标注入行')
    sub(r'Netflix/Bilibili 等 \d+ 品牌', f'Netflix/Bilibili 等 {b} 品牌', '匹配顺序')
    sub(r'— \d+ 品牌图标库', f'— {b} 品牌图标库', '相关资源图标库')
    sub(r'\| 基础 \| \d+ \| — \| \d+ \| [\d,]+ \|',
        f'| 基础 | {base_n} | — | {base_n} | {base_rules:,} |', '统计表-基础行')
    sub(r'\| 品牌 \| — \| \d+ \| \d+ \| [\d,]+ \|',
        f'| 品牌 | — | {b} | {b} | {stats["brand_rules"]:,} |', '统计表-品牌行')
    sub(r'\| \*\*合计\*\* \| \*\*\d+\*\* \| \*\*\d+\*\* \| \*\*\d+\*\* \| \*\*[\d,]+\*\* \|',
        f'| **合计** | **{base_n}** | **{b}** | **{rs}** | **{total:,}** |', '统计表-合计行')
    dist = ' · '.join(f'{tp}({stats["type_counts"].get(tp, 0):,})' for tp in TYPES_ORDER)
    sub(r'规则类型分布：.*', f'规则类型分布：{dist}', '类型分布')

    rows = [f'| {cat} | {n} | {rules:,} | '
            + ' · '.join(BRAND_CATEGORIES[cat]) + ' |'
            for cat, n, rules in stats['category_stats']]
    table = ('| 类别 | 品牌数 | 规则数 | 品牌 |\n|------|:-----:|:------:|------|\n'
             + '\n'.join(rows))
    new_t, n = re.subn(r'\| 类别 \| 品牌数 \| 规则数 \| 品牌 \|\n\|[-|: ]+\|\n(?:\|.*\n)+',
                       lambda _m: table + '\n', t, count=1)
    if n and new_t != t:
        changed.append('分类统计表')
    t = new_t

    if t != orig:
        path.write_text(t, encoding='utf-8')
    return changed


def main() -> int:
    stats = compute_stats()
    if '--check' in sys.argv:
        errs = check_readme(stats)
        if errs:
            print('❌ README 统计口径与实测不一致：')
            for e in errs:
                print(f'  - {e}')
            return 1
        print('✅ README 统计口径与实测一致')
        return 0
    if '--check-structure' in sys.argv:
        errs = check_readme_structure(stats)
        if errs:
            print('❌ README 结构性口径与实测不一致：')
            for e in errs:
                print(f'  - {e}')
            return 1
        print('✅ README 结构性口径与实测一致')
        return 0
    if '--update' in sys.argv:
        changed = update_readme(stats)
        print('✅ README 已按实测刷新' + (f'（{len(changed)} 处）' if changed else '（无变化）'))
        for c in changed:
            print(f'  - {c}')
        return 0
    if '--table' in sys.argv:
        for cat, n, rules in stats['category_stats']:
            print(f'| {cat} | {n} | {rules:,} |')
        return 0
    print_stats(stats)
    return 0


if __name__ == '__main__':
    sys.exit(main())
