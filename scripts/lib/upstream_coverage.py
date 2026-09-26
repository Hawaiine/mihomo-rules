"""upstream_coverage.py — 手工维护品牌清单（无上游数据、必须人工维护的规则集）

用途：让「规则集存在但没有上游映射」这件事**无法悄悄发生**。
`scripts/tests/test_mapping_policy.py` 断言：ruleset/ 下每个品牌要么是
上游映射目标（v2fly / Loyalsoldier / blackmatrix7），要么显式登记在本清单里。

因此：
- 新增一个手工品牌 → 必须显式加进 MANUAL_BRANDS，否则 CI 失败；
- 某个品牌的映射被误删 → 它不在本清单 → CI 失败；
- 上游补了数据、品牌转为自动同步 → 从本清单删除（测试会提醒它已多余）。

注意：本清单只描述「有没有上游接线」，不描述数据质量。已确认不需要接线的
品牌（如 GoogleNews / GoogleVoice：blackmatrix7 的 GoogleVoice 目录只有
lens.l.google.com，属 Google Lens 而非 Voice 服务域名）也在此登记，
避免被误当作遗漏映射而接上错误数据。
"""

MANUAL_BRANDS = frozenset({
    # 项目自组规则集（无上游对应类别）
    'Bank', 'GameJapan', 'GeneralAI', 'MusicJapan', 'OasisicSelf',
    'PT', 'PTChina', 'PornChina', 'ReadJapan', 'Wallpaper',
    # 手工维护的品牌集（上游无对应，或已确认不应接线）
    'DAnimeStore', 'F1TV', 'FujiTV', 'HOYTV', 'Lemino', 'Mora',
    'MyVideo', 'Podcast', 'Telasa', 'VideoMarket', 'WOWOW',
    'karaokeDAM', 'friDayVideo',
    # 子品牌：从父品牌手工提取服务域名
    'Gmail', 'GoogleMaps', 'GoogleNews', 'GooglePhotos', 'GoogleVoice',
    'Outlook', 'SiriAI',
    # 手工维护的国内 / 社区品牌（三源均无独立类别）
    'Crunchyroll', 'NetEaseCloudMusic', 'NetEaseMail', 'NousResearch',
    'QQ', 'QQMail', 'QQMusic', 'RedNote', 'Taobao', 'TencentVideo',
    'WeChat', 'Weibo',
})
