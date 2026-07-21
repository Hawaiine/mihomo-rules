"""
批量生成品牌规则集（指定范围）
用法: python generate_brands.py <起始序号> [数量]
示例: python generate_brands.py 0 10   # 第 1-10 个
      python generate_brands.py 10 10  # 第 11-20 个
"""
import sys
from pathlib import Path
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from merge_and_dedup import merge_with_stats
from parse_v2fly import parse_v2fly_brand
from parse_loyalsoldier import parse_loyalsoldier_brand
from parse_blackmatrix7 import parse_blackmatrix7_brand
from commit_writer import write_ruleset
from lib.canonical import count_by_type

# 所有品牌列表（字母序，来自完整品牌列表）
ALL_BRANDS = [
    "AWS", "AbemaTV", "Amazon", "Anthropic", "Apple",
    "AppleTV", "Bahamut", "Bangumi", "Bank", "Bilibili",
    "Bluesky", "CatchPlay", "Cloudflare", "Cursor", "DAZN",
    "DAnimeStore", "DMMTV", "Deezer", "Discord", "Disney",
    "Docker", "F1TV", "Facebook", "FridayVideo", "FujiTV",
    "GameJapan", "GeneralAI", "GitHub", "Google", "GoogleAI",
    "HBO", "HOYTV", "HamiVideo", "Hotstar", "Hulu",
    "Instagram", "KKTV", "KaraokeDam", "Lemino", "LiTV",
    "LineTV", "Manus", "Messenger", "MetaBrainz", "Microsoft",
    "Mora", "MusicJp", "Musixmatch", "MyTVSuper", "MyVideo",
    "NHK", "Netflix", "Niconico", "Nintendo", "NowE",
    "OneDrive", "OpenAI", "PT", "PTChina", "PayPal",
    "Perplexity", "Pinterest", "Pixiv", "Podcast", "Poe",
    "Porn", "PornChina", "PrimeVideo", "Qobuz", "Radiko",
    "RakutenTV", "ReadsJapan", "Reddit", "SiriAI", "Spotify",
    "Steam", "Synology", "TMDB", "TVer", "Telasa",
    "Telegram", "Threads", "Tidal", "TikTok", "Tubi",
    "Twitch", "UNext", "VideoMarket", "Viu", "WOWOW",
    "WSJ", "Wallpaper", "WhatsApp", "X", "YouTube",
    "YouTubeMusic", "ZLibrary", "iCloud", "iCloudPrivateRelay",
]

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python generate_brands.py <起始序号> [数量]")
        print("示例: python generate_brands.py 0 10   # 第 1-10 个")
        print("      python generate_brands.py 10 10  # 第 11-20 个")
        sys.exit(1)

    start = int(sys.argv[1])
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    batch = ALL_BRANDS[start:start + count]

    if not batch:
        print(f"⚠️ 序号 {start} 开始没有品牌")
        sys.exit(1)

    print(f"📥 批量生成品牌规则集 ({start+1}-{start+len(batch)}/{len(ALL_BRANDS)})")
    print(f"   品牌: {', '.join(batch)}")
    print()

    total_before = 0
    total_after = 0
    total_dedup = 0
    success_count = 0
    skip_count = 0

    for brand_name in batch:
        print(f"  {brand_name}...", end=" ", flush=True)

        # 解析三源
        v2fly_rules = []
        ls_rules = []
        bm7_rules = []

        try:
            v2fly_result = parse_v2fly_brand(brand_name, "upstream/v2fly/data")
            v2fly_rules = v2fly_result.get("main", [])
        except Exception:
            pass

        try:
            ls_rules = parse_loyalsoldier_brand(brand_name, "upstream/loyalsoldier")
        except Exception:
            pass

        try:
            bm7_rules = parse_blackmatrix7_brand(brand_name, "upstream/blackmatrix7/rule/Clash")
        except Exception:
            pass

        total = len(v2fly_rules) + len(ls_rules) + len(bm7_rules)

        if total == 0:
            print("跳过（无数据）")
            skip_count += 1
            continue

        # 合并去重
        merged = merge_with_stats(v2fly_rules, ls_rules, bm7_rules)
        rules = merged["rules"]

        total_before += merged["total_before"]
        total_after += merged["total_after"]
        total_dedup += merged["dedup_count"]

        # 写入
        result = write_ruleset(brand_name, rules)

        if result.success:
            type_counts = count_by_type(rules)
            type_str = " + ".join([f"{t}:{c}" for t, c in sorted(type_counts.items())])
            print(f"✅ {len(rules)} 条 [{type_str}]")
            success_count += 1
        else:
            print(f"❌ {result.error}")

    print()
    print(f"📊 批次统计 ({start+1}-{start+len(batch)}):")
    print(f"   成功: {success_count}")
    print(f"   跳过: {skip_count}")
    print(f"   合并前: {total_before} 条")
    print(f"   去重: {total_dedup} 条")
    print(f"   合并后: {total_after} 条")