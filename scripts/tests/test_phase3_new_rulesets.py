"""test_phase3_new_rulesets.py — Phase 3 四个新规则集的契约与双向 ownership 不变式

覆盖 §67 / §68 / §77：
  * 四新规则集的目录 / 文件 / Display / 注册点一致性
  * Mutation B（新建 **Child**）：父规则集中的 child-specific 规则必须被剥离，
    不得静默忽略 —— 即对全部 SUB_PARENT 对断言 parent ∩ child == ∅
  * 四新规则集 vs 全库的 reverse re-audit 契约
"""
import sys
import unittest
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import resolve_ownership as ro  # noqa: E402
import readme_stats as rs  # noqa: E402
from commit_writer import STRATEGY_GROUP_MAP  # noqa: E402
from lib.ownership_map import SUB_PARENT  # noqa: E402
from lib.upstream_coverage import MANUAL_BRANDS  # noqa: E402

NEW_BRANDS = {
    'DisneyPlus': 'Disney+',
    'Alibaba': '阿里巴巴',
    'NBCUniversal': 'NBCUniversal',
    'DJI': '大疆创新',
}


_BRANDS = None
_SETS: dict = {}


def _brands():
    global _BRANDS
    if _BRANDS is None:
        _BRANDS = sorted(
            d.name for d in (ro.ROOT / 'ruleset').iterdir()
            if d.is_dir() and (d / f'{d.name}.yaml').exists()
        )
    return _BRANDS


def _sets():
    """全品牌规则集只解析一次（缓存），避免每类重复读 YAML。"""
    if not _SETS:
        for b in _brands():
            _SETS[b] = ro.build_child_rule_set(_brands(), b)
    return _SETS


class NewRulesetContractTest(unittest.TestCase):
    """四新规则集的目录 / Display / 注册点契约。"""

    def test_ruleset_dirs_and_files_exist(self):
        for brand in NEW_BRANDS:
            with self.subTest(brand=brand):
                self.assertTrue((ro.ROOT / 'ruleset' / brand / f'{brand}.yaml').exists())
                self.assertTrue((ro.ROOT / 'ruleset' / brand / 'README.md').exists())

    def test_display_names(self):
        for brand, display in NEW_BRANDS.items():
            with self.subTest(brand=brand):
                self.assertEqual(STRATEGY_GROUP_MAP.get(brand, brand), display)

    def test_rule_name_header_matches_display(self):
        """YAML 头部 # Rule Name 必须等于策略组名（Display）。"""
        for brand, display in NEW_BRANDS.items():
            with self.subTest(brand=brand):
                text = (ro.ROOT / 'ruleset' / brand / f'{brand}.yaml').read_text(encoding='utf-8')
                self.assertIn(f'# Rule Name: {display}', text)

    def test_disneyplus_is_manual_brand(self):
        """DisneyPlus 无独立上游类别 → 必须在 MANUAL_BRANDS（否则 CI 拦截）。"""
        self.assertIn('DisneyPlus', MANUAL_BRANDS)

    def test_parent_links(self):
        self.assertEqual(SUB_PARENT.get('DisneyPlus'), 'Disney')
        self.assertEqual(SUB_PARENT.get('Taobao'), 'Alibaba')
        self.assertEqual(SUB_PARENT.get('DingTalk'), 'Alibaba')
        self.assertEqual(SUB_PARENT.get('Youku'), 'Alibaba')

    def test_every_brand_is_categorised(self):
        """readme_stats 分类必须覆盖全部品牌（新增品牌不得漏分类）。"""
        categorised = {b for members in rs.BRAND_CATEGORIES.values() for b in members}
        missing = [b for b in _brands() if b not in categorised and b not in rs.BASE_BRANDS]
        self.assertEqual(missing, [])


class MutationBNewChildTest(unittest.TestCase):
    """§68 Mutation B：**新建 Child** 时，父规则集中的 child-specific 规则必须被重新检出。

    若只做「新规则集自身查重」而漏掉父集中的历史规则，父集就会残留
    child 专属域 —— 本测试对全部 SUB_PARENT 对断言 parent ∩ child == ∅。
    """

    @classmethod
    def setUpClass(cls):
        cls.brands = _brands()
        cls.sets = _sets()

    def test_parent_child_intersection_is_empty_for_all_pairs(self):
        for child, parent in sorted(SUB_PARENT.items()):
            if child not in self.sets or parent not in self.sets:
                continue
            with self.subTest(child=child, parent=parent):
                self.assertEqual(
                    sorted(self.sets[parent] & self.sets[child]), [],
                    f'{parent} 仍残留 {child} 专属规则（新建 Child 时未被重新检出）',
                )

    def test_disney_no_longer_holds_disneyplus_rules(self):
        """新建 DisneyPlus 后，父 Disney 不得再持有 disneyplus.com。"""
        disney = (ro.ROOT / 'ruleset' / 'Disney' / 'Disney.yaml').read_text(encoding='utf-8')
        self.assertNotIn('  - DOMAIN-SUFFIX,disneyplus.com\n', disney)
        disneyplus = (ro.ROOT / 'ruleset' / 'DisneyPlus' / 'DisneyPlus.yaml').read_text(encoding='utf-8')
        self.assertIn('DOMAIN-SUFFIX,disneyplus.com', disneyplus)

    def test_coverage_invariant_child_still_covers_moved_rule(self):
        """§26 coverage invariant：父剥离后，子必须仍覆盖该域（coverage 不丢）。"""
        for child, parent in sorted(SUB_PARENT.items()):
            if child not in self.sets or parent not in self.sets:
                continue
            moved = self.sets[child] - self.sets[parent]
            with self.subTest(child=child, parent=parent):
                self.assertTrue(moved or self.sets[child] == self.sets[parent])


class NewRulesetReverseAuditTest(unittest.TestCase):
    """§77：四新规则集必须与全库做过 reverse re-audit。"""

    @classmethod
    def setUpClass(cls):
        cls.brands = _brands()
        cls.sets = _sets()

    def test_new_rulesets_do_not_overlap_each_other(self):
        """四个新规则集之间不得有交叉（§50 不得相互复制）。"""
        names = sorted(NEW_BRANDS)
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                with self.subTest(a=a, b=b):
                    self.assertEqual(sorted(self.sets[a] & self.sets[b]), [])

    def test_new_rulesets_have_no_self_duplicates(self):
        for brand in NEW_BRANDS:
            with self.subTest(brand=brand):
                self.assertEqual(ro.build_child_rule_set(self.brands, brand), self.sets[brand])


if __name__ == '__main__':
    unittest.main()
