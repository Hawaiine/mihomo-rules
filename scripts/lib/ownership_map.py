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
    'Hotstar': 'Disney',
    'Hulu': 'Disney',
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
    'AppleNews': 'Apple',
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
    'Grok': 'X',
}