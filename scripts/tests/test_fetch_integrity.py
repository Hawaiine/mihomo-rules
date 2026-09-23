"""fetch_upstream 完整性守卫的单元测试

这些守卫存在的理由：loyalsoldier 的 branch 曾被改成 master（数据文件全在
release），当时「成功」判据只有 git 操作成功 + 文件数 > 0 —— master 也有
4 个文件，于是故障静默了约 2 个月。所以下列断言必须保持"非空转"。
"""

import os
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))

import fetch_upstream as fu  # noqa: E402


class TestUpstreamConfig(unittest.TestCase):
    """上游配置本身的门禁。"""

    def test_loyalsoldier_branch_is_release(self):
        """loyalsoldier 必须是 release 分支（数据文件只在 release）。"""
        self.assertEqual(fu.UPSTREAM_REPOS['loyalsoldier']['branch'], 'release')

    def test_all_upstreams_have_integrity_rules(self):
        """每个上游都必须有完整性规则，否则守卫对它是空转。"""
        for name in fu.UPSTREAM_REPOS:
            self.assertIn(name, fu.INTEGRITY_RULES,
                          f'{name} 缺少 INTEGRITY_RULES，会出现静默故障')

    def test_loyalsoldier_requires_seven_files_with_floors(self):
        """loyalsoldier 必须校验 7 个数据文件且各有行数下限。"""
        files = fu.INTEGRITY_RULES['loyalsoldier']['files']
        self.assertEqual(
            set(files),
            {'reject.txt', 'direct.txt', 'proxy.txt', 'cncidr.txt',
             'private.txt', 'lancidr.txt', 'applications.txt'},
        )
        for fn, floor in files.items():
            self.assertGreater(floor, 0, f'{fn} 行数下限必须为正')
        # reject / direct / proxy 是三大主力，下限需有实际拦截力
        self.assertGreaterEqual(files['reject.txt'], 100_000)
        self.assertGreaterEqual(files['direct.txt'], 50_000)
        self.assertGreaterEqual(files['proxy.txt'], 10_000)


class TestVerifyIntegrity(unittest.TestCase):
    """校验逻辑：缺文件 / 行数不足 / 相对历史骤降 都要被判失败。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, name, lines):
        p = os.path.join(self.tmp, name)
        with open(p, 'w') as f:
            f.write('\n'.join(f'{i}.example.com' for i in range(lines)) + '\n')
        return p

    def _write_all(self):
        for fn, floor in fu.INTEGRITY_RULES['loyalsoldier']['files'].items():
            self._write(fn, floor)

    def test_missing_files_are_reported(self):
        """空目录 → 7 个必备文件全部报缺失。"""
        problems, _ = fu.verify_integrity('loyalsoldier', self.tmp)
        self.assertEqual(len(problems), 7)
        self.assertTrue(all('缺失' in p for p in problems))

    def test_under_floor_is_reported(self):
        """行数低于下限 → 报错（能拦住 master 分支那种近乎空目录）。"""
        self._write_all()
        # reject.txt 被截断到 10 行
        self._write('reject.txt', 10)
        problems, _ = fu.verify_integrity('loyalsoldier', self.tmp)
        self.assertTrue(any('reject.txt' in p and '低于下限' in p for p in problems))

    def test_complete_dataset_passes(self):
        """齐全且达下限 → 无问题。"""
        self._write_all()
        problems, counts = fu.verify_integrity('loyalsoldier', self.tmp)
        self.assertEqual(problems, [])
        self.assertEqual(counts['reject.txt'],
                         fu.INTEGRITY_RULES['loyalsoldier']['files']['reject.txt'])

    def test_master_like_branch_is_rejected(self):
        """模拟「clone 到 master」：只有 LICENSE/README → 必须失败。"""
        self._write('LICENSE', 3)
        self._write('README.md', 20)
        problems, _ = fu.verify_integrity('loyalsoldier', self.tmp)
        self.assertGreaterEqual(len(problems), 7)
        self.assertFalse(all('缺失' not in p for p in problems))

    def test_history_drop_is_reported(self):
        """相对历史骤降超过阈值 → 报错。"""
        self._write_all()
        floor = fu.INTEGRITY_RULES['loyalsoldier']['files']['proxy.txt']
        # 当前 5.1 万行，历史 100 万行 → 5.1% < 50%
        historical = {'loyalsoldier/proxy.txt': 1_000_000}
        problems, _ = fu.verify_integrity('loyalsoldier', self.tmp, historical)
        self.assertTrue(any('骤降' in p for p in problems), problems)

    def test_file_count_upstreams_are_checked(self):
        """v2fly/bm7 走文件数下限口径。"""
        problems, counts = fu.verify_integrity('v2fly', self.tmp)
        self.assertTrue(any('文件数低于下限' in p for p in problems))
        self.assertEqual(counts['__file_count__'], 0)


class TestStatsAndHistory(unittest.TestCase):
    """stats.json 读写与历史摊平。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.stats = os.path.join(self._tmp.name, 'stats.json')

    def tearDown(self):
        self._tmp.cleanup()

    def test_roundtrip_flat_counts(self):
        """扁平键写入后能被摊平读回（供下次比对）。"""
        fu.save_historical_line_counts(
            self.stats, {'loyalsoldier/reject.txt': 191081, 'v2fly/__file_count__': 1540}
        )
        flat = fu.load_historical_line_counts(self.stats)
        self.assertEqual(flat['loyalsoldier/reject.txt'], 191081)
        self.assertEqual(flat['v2fly/__file_count__'], 1540)

    def test_missing_stats_is_empty_not_error(self):
        """stats.json 不存在 → 空字典（首次同步不应崩）。"""
        self.assertEqual(fu.load_historical_line_counts(self.stats), {})

    def test_corrupt_stats_is_empty_not_error(self):
        """stats.json 损坏 → 空字典（不能因此中断同步）。"""
        with open(self.stats, 'w') as f:
            f.write('{not json')
        self.assertEqual(fu.load_historical_line_counts(self.stats), {})

    def test_save_stats_records_commit(self):
        """stats.json 必须留痕上游 commit（追溯用）。"""
        import json
        fu.save_stats(self.stats, {'loyalsoldier': {
            'commit': 'abc123', 'file_count': 122, 'line_counts': {'reject.txt': 191081}}})
        with open(self.stats) as f:
            data = json.load(f)
        self.assertEqual(data['upstreams']['loyalsoldier']['commit'], 'abc123')
        self.assertIn('generated_at', data)


class TestCheckTruncation(unittest.TestCase):
    """check_truncation 已接线（历史教训：曾经 0 处调用 = 死代码）。"""

    def test_zero_lines_is_bad(self):
        ok, msg = fu.check_truncation('x.txt', 0, {})
        self.assertFalse(ok)
        self.assertIn('空', msg)

    def test_no_history_passes(self):
        ok, _ = fu.check_truncation('x.txt', 10, {})
        self.assertTrue(ok)

    def test_keyed_history_is_used(self):
        ok, msg = fu.check_truncation('reject.txt', 10, {'loyalsoldier/reject.txt': 1000},
                                      key='loyalsoldier/reject.txt')
        self.assertFalse(ok)
        self.assertIn('骤降', msg)

    def test_within_threshold_passes(self):
        ok, _ = fu.check_truncation('x.txt', 600, {'x.txt': 1000})
        self.assertTrue(ok)

    def test_check_truncation_is_wired(self):
        """守卫必须真的调用它（源码级断言，防止退回死代码）。"""
        src = (ROOT / 'scripts' / 'fetch_upstream.py').read_text(encoding='utf-8')
        self.assertIn('check_truncation(', src)
        # 调用点不能只有函数定义本身
        self.assertGreater(src.count('check_truncation('), 1)


class TestGitLockSafety(unittest.TestCase):
    """锁是否可删除不能靠年龄判断；只检查并安全失败。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = os.path.join(self._tmp.name, 'r')
        subprocess.run(['git', 'init', '-q', self.repo], check=True)

    def tearDown(self):
        self._tmp.cleanup()

    def test_repository_without_locks_is_allowed(self):
        self.assertIsNone(fu._check_git_locks(self.repo))

    def test_fresh_lock_is_kept(self):
        lock = Path(self.repo) / '.git' / 'index.lock'
        lock.write_bytes(b'')
        with self.assertRaisesRegex(RuntimeError, '未删除'):
            fu._check_git_locks(self.repo)
        self.assertTrue(lock.exists())


if __name__ == '__main__':
    unittest.main()