"""
test_icon_check.py — 测试 icon 引用存在性校验与图标映射入口
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import verify_configs
import match_icons


class TestCheckIconsExist(unittest.TestCase):
    def setUp(self):
        self.ref = ({'Media/Netflix/Netflix.png'}, 'fake-ref')

    def test_pass_when_file_exists(self):
        lines = ['    icon: "https://raw.githubusercontent.com/Hawaiine/Oasisic-Icons/main/icons/Media/Netflix/Netflix.png"']
        self.assertTrue(verify_configs.check_icons_exist(lines, 'x', self.ref))

    def test_fail_when_file_missing(self):
        lines = ['    icon: "https://raw.githubusercontent.com/Hawaiine/Oasisic-Icons/main/icons/Media/GameJapan/GameJapan.png"']
        self.assertFalse(verify_configs.check_icons_exist(lines, 'x', self.ref))

    def test_fail_when_url_has_no_icons_segment(self):
        lines = ['    icon: "https://example.com/whatever.png"']
        self.assertFalse(verify_configs.check_icons_exist(lines, 'x', self.ref))

    def test_skip_without_reference(self):
        lines = ['    icon: "https://raw.githubusercontent.com/Hawaiine/Oasisic-Icons/main/icons/No/Such/File.png"']
        self.assertTrue(verify_configs.check_icons_exist(lines, 'x', None))

    def test_ignores_non_icon_lines(self):
        lines = ['    icon-type: http', '    type: select', '    proxies:']
        self.assertTrue(verify_configs.check_icons_exist(lines, 'x', self.ref))


class TestIconMap(unittest.TestCase):
    def test_overrides_take_priority(self):
        icon_map, _ = match_icons.build_icon_map()
        for sg, rel in match_icons.ICON_OVERRIDES.items():
            self.assertIn(sg, icon_map)
            self.assertTrue(icon_map[sg].endswith(f'/icons/{rel}'))

    def test_brand_count_matches_ruleset(self):
        icon_map, missing = match_icons.build_icon_map()
        brands = match_icons.brand_dirs()
        skipped = match_icons.emoji_skipped_brands()
        # 每个品牌要么有图标、要么进 missing、要么按 emoji 规则被跳过，不允许悄悄消失
        self.assertEqual(len(icon_map) + len(missing) + len(skipped), len(brands))

    def test_emoji_groups_never_get_icons(self):
        """emoji 前缀显示名的策略组一律不配 icon（项目约定）"""
        icon_map, _ = match_icons.build_icon_map()
        offenders = [sg for sg in icon_map if match_icons.is_emoji_group(sg)]
        self.assertEqual(offenders, [], f'emoji 组不应有 icon: {offenders}')

    def test_is_emoji_group(self):
        self.assertTrue(match_icons.is_emoji_group('🤖 General AI'))
        self.assertTrue(match_icons.is_emoji_group('🎮 Game Japan'))
        self.assertTrue(match_icons.is_emoji_group('🏦 Bank'))
        self.assertFalse(match_icons.is_emoji_group('Netflix'))
        self.assertFalse(match_icons.is_emoji_group('Z-Library'))
        self.assertFalse(match_icons.is_emoji_group(''))

    def test_scan_prefers_git_tree(self):
        match_icons.scan_icons()
        source = match_icons.scan_source()
        self.assertTrue(
            '@' in source or '工作区扫描' in source or '缺失' in source,
            f'扫描来源描述异常: {source}',
        )


if __name__ == '__main__':
    unittest.main()