"""
test_readme_consistency.py — README 统计口径必须与 ruleset/ 实测一致

分两层，原因见下：

* **结构性口径**（品牌数 / 规则集数 / 分类归属 / 徽章 / 配置 README 组数）
  → 只在「增删品牌」这类人工提交里变化，**纳入单测硬门禁**。
* **规则条数类口径**（规则总数 / 类型分布 / 分类规则数）
  → 日更（CI 自动提交 ruleset 数据）每天都会改变它们，而 CI 只提交
  `ruleset/` `configs/` `scripts/`，不提交 README。若把它做成硬门禁，
  日更后 CI 必失败、整条流水线断掉。
  → 因此改用 `python3 scripts/readme_stats.py --check`（人工/定期）
    与 `--update`（一键刷新）维护，不进单测。

README 的数字永远由 `scripts/readme_stats.py` 从 `ruleset/` 实测计算，
不允许手工填数。
"""
import re
import sys
import tempfile
import unittest
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import readme_stats


class TestReadmeConsistency(unittest.TestCase):
    def setUp(self):
        self.stats = readme_stats.compute_stats()

    def test_categories_cover_all_brands(self):
        """分类表必须覆盖全部品牌（漏项/幽灵项在 compute_stats 里直接抛错）"""
        self.assertEqual(
            sum(n for _cat, n, _rules in self.stats['category_stats']),
            self.stats['brand_count'],
        )

    def test_readme_structure_matches(self):
        """结构性口径硬门禁（品牌数/规则集数/分类归属/徽章/配置 README）"""
        errs = readme_stats.check_readme_structure(self.stats)
        self.assertEqual(errs, [], 'README 结构性口径漂移:\n' + '\n'.join(errs))

    def test_update_is_idempotent(self):
        """--update 必须幂等：在已同步的 README 上运行不得产生任何改动"""
        changed = readme_stats.update_readme(self.stats)
        self.assertEqual(changed, [], f'--update 非幂等，改动了: {changed}')

    def test_config_readmes_declare_brand_group_count(self):
        """每个 configs/*/README.md 必须声明「品牌策略组(N个)」且与实测一致。

        否则该文件的品牌数完全不受门禁约束（曾经的静默漏检）。
        """
        for cfg_readme in sorted((readme_stats.ROOT / 'configs').glob('*/README.md')):
            with self.subTest(config=cfg_readme.name):
                text = cfg_readme.read_text(encoding='utf-8')
                found = re.findall(r'品牌策略组\((\d+)个\)', text)
                self.assertTrue(found, f'{cfg_readme} 缺少「品牌策略组(N个)」口径行')
                for n in found:
                    self.assertEqual(int(n), self.stats['brand_count'])

    def test_missing_brand_group_line_is_reported(self):
        """反例（hermetic temp ROOT）：抽掉 config README 的口径行 → check_readme 必须报错。"""
        with tempfile.TemporaryDirectory(prefix='readme-cfg-') as temp:
            fake = Path(temp)
            for name in ('Android', 'Nikki'):
                (fake / 'configs' / name).mkdir(parents=True)
                (fake / 'configs' / name / 'README.md').write_text('# x\n', encoding='utf-8')
            (fake / 'README.md').write_text(
                (readme_stats.ROOT / 'README.md').read_text(encoding='utf-8'), encoding='utf-8')
            original = readme_stats.ROOT
            readme_stats.ROOT = fake
            try:
                errs = readme_stats.check_readme(self.stats)
            finally:
                readme_stats.ROOT = original
        self.assertTrue(any('缺少「品牌策略组(N个)」口径行' in e for e in errs), errs)

    def test_update_syncs_config_readme_brand_count(self):
        """--update 必须能把 config README 的组数改回实测值（反例注入后修复）。"""
        with tempfile.TemporaryDirectory(prefix='readme-cfg-') as temp:
            fake = Path(temp)
            for name in ('Android', 'Nikki'):
                (fake / 'configs' / name).mkdir(parents=True)
                (fake / 'configs' / name / 'README.md').write_text(
                    '| 品牌策略组(1个) | select | x |\n', encoding='utf-8')
            (fake / 'README.md').write_text(
                (readme_stats.ROOT / 'README.md').read_text(encoding='utf-8'), encoding='utf-8')
            original = readme_stats.ROOT
            readme_stats.ROOT = fake
            try:
                changed = readme_stats.update_readme(self.stats)
                texts = [(fake / 'configs' / n / 'README.md').read_text(encoding='utf-8')
                         for n in ('Android', 'Nikki')]
            finally:
                readme_stats.ROOT = original
        self.assertEqual(len([c for c in changed if '品牌策略组数量' in c]), 2, changed)
        for t in texts:
            self.assertIn(f'品牌策略组({self.stats["brand_count"]}个)', t)


if __name__ == '__main__':
    unittest.main()
