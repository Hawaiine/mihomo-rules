"""
test_full_comment_invariant.py — full 版注释 RULE-SET 序列不变量

锁死历史 bug（zip() 静默截断 + 基础集注释被「跳过即忽略」）：
    品牌组 150 / 注释 151（多出 Applications）曾被判 PASS。
现在 check_full_comment_order 要求整段注释序列 == generate_config 生成结果，
基础集注释也必须由生成器产出才允许存在。
"""
import sys
import unittest
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import verify_configs as vc

ROOT = _SCRIPTS.parent
ANDROID_FULL = ROOT / 'configs' / 'Android' / 'config.yaml'
NIKKI_FULL = ROOT / 'configs' / 'Nikki' / 'config.yaml'


def _lines(p):
    return p.read_text(encoding='utf-8').splitlines(keepends=True)


def _insert_before_match(lines, needle, new_line):
    """在第一条包含 needle 的行之前插入"""
    out = []
    done = False
    for l in lines:
        if not done and needle in l:
            out.append(new_line)
            done = True
        out.append(l)
    assert done, f'未找到锚点 {needle!r}'
    return out


class TestFullCommentInvariant(unittest.TestCase):
    def test_baseline_passes(self):
        self.assertTrue(vc.check_full_comment_order(_lines(ANDROID_FULL), 'android_full'))
        self.assertTrue(vc.check_full_comment_order(_lines(NIKKI_FULL), 'nikki_full'))

    def test_min_variant_skipped(self):
        self.assertTrue(vc.check_full_comment_order(_lines(ANDROID_FULL), 'android_min'))

    def test_extra_base_provider_comment_fails(self):
        """Android full 不应有 Applications 注释；凭空多一行必须 FAIL（历史盲区）"""
        lines = _insert_before_match(
            _lines(ANDROID_FULL), '# - RULE-SET,AWS,AWS',
            '                                                    # - RULE-SET,Applications,🎯 全球直连\n')
        self.assertFalse(vc.check_full_comment_order(lines, 'android_full'))

    def test_missing_brand_comment_fails(self):
        """删掉一条品牌注释 → 条数断言必须 FAIL（旧 zip() 会静默截断）"""
        lines = [l for l in _lines(ANDROID_FULL) if '# - RULE-SET,AWS,AWS' not in l]
        self.assertFalse(vc.check_full_comment_order(lines, 'android_full'))

    def test_duplicate_brand_comment_fails(self):
        lines = _insert_before_match(
            _lines(ANDROID_FULL), '# - RULE-SET,AWS,AWS',
            '                                                    # - RULE-SET,AWS,AWS\n')
        self.assertFalse(vc.check_full_comment_order(lines, 'android_full'))

    def test_swapped_brand_comments_fail(self):
        """顺序颠倒必须 FAIL（顺序不变量）"""
        lines = _lines(ANDROID_FULL)
        idx = [i for i, l in enumerate(lines) if '# - RULE-SET,' in l and l.strip().startswith('#')]
        a, b = idx[0], idx[1]
        lines[a], lines[b] = lines[b], lines[a]
        self.assertFalse(vc.check_full_comment_order(lines, 'android_full'))

    def test_nikki_applications_comment_is_allowed(self):
        """Nikki full 的 Applications 注释由生成器产出，属合法存在"""
        lines = _lines(NIKKI_FULL)
        self.assertTrue(any('Applications' in l for l in lines))
        self.assertTrue(vc.check_full_comment_order(lines, 'nikki_full'))


if __name__ == '__main__':
    unittest.main()
