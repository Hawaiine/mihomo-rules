"""
ownership_map.py — SUB_PARENT 父子品牌映射单源

所有需要 SUB_PARENT 的模块从这里 import，禁止双份拷贝。
"""

# 子品牌 → 父品牌映射
SUB_PARENT: dict[str, str] = {
    'AppleTV': 'Apple',
    'SiriAI': 'Apple',
    'iCloud': 'Apple',
    'GoogleAI': 'Google',
    'YouTube': 'Google',
    'YouTubeMusic': 'YouTube',
    'AWS': 'Amazon',
    'PrimeVideo': 'Amazon',
    'Peacock': 'NBCUniversal',
    'Hulu': 'Disney',
    'HBOMax': 'HBO',
    'OneDrive': 'Microsoft',
    'GitHub': 'Microsoft',
    'Instagram': 'Facebook',
    'Messenger': 'Facebook',
    'WhatsApp': 'Facebook',
    'Threads': 'Facebook',
    'iCloudPrivateRelay': 'iCloud',
    'GooglePlay': 'Google',
    # ── 2026-09-25 新增品牌 ──
    'AppleFitnessPlus': 'Apple',
    'AppleMusic': 'Apple',
    'AppleNewsPlus': 'Apple',
    'ApplePodcasts': 'Apple',
    'AppStore': 'Apple',
    'Azure': 'Microsoft',
    'Outlook': 'Microsoft',
    'Bing': 'Microsoft',
    'Copilot': 'Microsoft',
    'Xbox': 'Microsoft',
    'Gmail': 'Google',
    'GoogleDrive': 'Google',
    'GooglePhotos': 'Google',
    'GoogleMaps': 'Google',
    'GoogleNews': 'Google',
    'GoogleVoice': 'Google',
    'Grok': 'xAI',
    # ── 2026-10-08 Phase 3：新建父规则集（Oasisic parent_brand 一致）──
    'Taobao': 'Alibaba',
    'DingTalk': 'Alibaba',
    'Youku': 'Alibaba',
    'DisneyPlus': 'Disney',
}

# 跨品牌域名归属（非父子关系）—— 域名 → canonical owner 单源。
#
# 生成管道在解析上游时，会从「非 owner 品牌」的候选规则中剥离这些域名，
# 以保证 canonical single representation：只手工改 YAML 无效，因为
# batch_update 的品牌写入是 merged(上游) ∪ manual(现存文件) 的并集，
# 会把被删规则重新长回来。归属决策必须表达在这里。
#
# 只作用于 DOMAIN / DOMAIN-SUFFIX；owner 自身与 Base 基础规则集不受影响。
# 决策来源：Phase 4B / 4D 人工批准（D#1 / D#3 / D#4 / D#5）。
CROSS_BRAND_OWNERSHIP: dict[str, str] = {
    # D#1 Copilot → OpenAI
    'openai.com': 'OpenAI',
    'chatgpt.com': 'OpenAI',
    'oaistatic.com': 'OpenAI',
    'oaiusercontent.com': 'OpenAI',
    'openaiapi-site.azureedge.net': 'OpenAI',
    'openaicomproductionae4b.blob.core.windows.net': 'OpenAI',
    'production-openaicom-storage.azureedge.net': 'OpenAI',
    # D#3 SoundCloud → Pandora
    'p-cdn.us': 'Pandora',
    # D#4 Disney → JioHotstar（仅 hotstar.com；hotstar-cdn.net /
    # hotstar-labs.com / hotstarext.com 为 legacy，本轮明确不动）
    'hotstar.com': 'JioHotstar',
    # D#5 Copilot → GitHub
    'githubcopilot.com': 'GitHub',
}