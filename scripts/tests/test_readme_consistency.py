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
import sys
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


if __name__ == '__main__':
    unittest.main()
