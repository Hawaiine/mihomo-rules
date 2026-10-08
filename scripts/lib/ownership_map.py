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