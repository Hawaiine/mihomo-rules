"""针对抓取复审的行为回归：真实子进程/本地 Git，不连接网络。"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import fetch_upstream as fu


class TestForeignGitLocks(unittest.TestCase):
    def test_old_foreign_lock_is_not_deleted(self):
        with tempfile.TemporaryDirectory(prefix='fetch-lock-test-') as tmp:
            repo = Path(tmp) / 'repo'
            subprocess.run(['git', 'init', '-q', str(repo)], check=True)
            lock = repo / '.git' / 'shallow.lock'
            lock.write_bytes(b'owned by another git operation\n')
            os.utime(lock, (1, 1))
            before = lock.read_bytes()
            # 即便锁极旧，也不能自动删除未知所属的 Git 锁。
            try:
                fu._check_git_locks(str(repo))
            except RuntimeError:
                pass  # 安全拒绝抓取是允许的结果。
            self.assertTrue(lock.exists(), '锁年龄不是无主证明，不能自动删除')
            self.assertEqual(lock.read_bytes(), before)


class TestProcessCleanup(unittest.TestCase):
    @unittest.skipUnless(sys.platform.startswith('linux'), '用 /proc 区分退出与运行状态')
    def test_stubborn_grandchild_dies_even_when_leader_exits(self):
        import json
        import signal
        with tempfile.TemporaryDirectory(prefix='fetch-process-test-') as tmp:
            ready = Path(tmp) / 'ready.json'
            child_code = (
                "import json,os,signal,time; from pathlib import Path; "
                "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                f"Path({str(ready)!r}).write_text(json.dumps({{'pid':os.getpid(),'pgid':os.getpgrp()}})); "
                "time.sleep(30)"
            )
            leader_code = (
                "import subprocess,sys,time; "
                f"subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
                "time.sleep(30)"
            )
            details = None
            try:
                code, out = fu._run_cmd([sys.executable, '-c', leader_code], timeout=0.8)
                self.assertEqual(code, -1, out)
                self.assertTrue(ready.is_file(), '必须证明孙进程已安装 SIGTERM 忽略处理器')
                details = json.loads(ready.read_text())
                self.assertNotEqual(details['pgid'], os.getpgrp())
                stat = Path('/proc') / str(details['pid']) / 'stat'
                if stat.exists():
                    state = stat.read_text().rsplit(') ', 1)[1].split()[0]
                    self.assertIn(state, {'Z', 'X'}, '父进程退出不代表整组退出，孙进程仍在运行')
            finally:
                if details is None and ready.is_file():
                    details = json.loads(ready.read_text())
                if details and details['pgid'] != os.getpgrp():
                    try:
                        os.killpg(details['pgid'], signal.SIGKILL)
                    except ProcessLookupError:
                        pass


class TestWholeFetchBudget(unittest.TestCase):
    def test_all_upstreams_share_one_deadline(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory(prefix='fetch-budget-test-') as tmp:
            calls = []

            def slow_fetch(name, config, cache_dir, historical=None, **kwargs):
                deadline = kwargs.get('deadline')
                calls.append((name, deadline))
                if len(calls) == 1:
                    time.sleep(0.12)
                return fu.FetchResult(name, False, 0, 0, error='test-only offline failure')

            with patch.object(fu, 'FETCH_TOTAL_TIMEOUT', 0.05, create=True), \
                    patch.object(fu, 'fetch_upstream', side_effect=slow_fetch):
                results = fu.fetch_all(tmp, write_stats=False)
            self.assertEqual(len(results), len(fu.UPSTREAM_REPOS))
            self.assertEqual(len(calls), 1, '总预算耗尽后不可再启动其他上游抓取')
            self.assertIsNotNone(calls[0][1], '必须把同一个 deadline 传到内部 Git 命令')
            self.assertTrue(all(not result.success for result in results.values()))


class TestGitLayout(unittest.TestCase):
    def test_separate_git_dir_lock_is_detected_without_deletion(self):
        with tempfile.TemporaryDirectory(prefix='fetch-layout-test-') as tmp:
            repo = Path(tmp) / 'worktree'
            gitdir = Path(tmp) / 'metadata'
            subprocess.run(['git', 'init', '-q', '--separate-git-dir',
                            str(gitdir), str(repo)], check=True)
            self.assertIsNone(fu._check_git_locks(str(repo)))
            lock = gitdir / 'index.lock'
            lock.write_bytes(b'do not delete')
            with self.assertRaisesRegex(RuntimeError, 'index.lock'):
                fu._check_git_locks(str(repo))
            self.assertEqual(lock.read_bytes(), b'do not delete')
            (repo / 'data.txt').write_bytes(b'abc')
            self.assertEqual(fu._count_files(str(repo)), (1, 3))

    def test_git_metadata_does_not_replace_required_loyalsoldier_files(self):
        with tempfile.TemporaryDirectory(prefix='fetch-integrity-test-') as tmp:
            repo = Path(tmp)
            (repo / '.git').mkdir()
            (repo / '.git' / 'objects').write_bytes(b'not rule data')
            (repo / 'README.md').write_text('no payload', encoding='utf-8')
            problems, counts = fu.verify_integrity('loyalsoldier', str(repo))
            self.assertEqual(len(problems), 7)
            self.assertTrue(all('缺失' in problem for problem in problems))
            self.assertEqual(set(counts.values()), {-1})


class TestCommandBudget(unittest.TestCase):
    def test_missing_executable_returns_diagnostic(self):
        with tempfile.TemporaryDirectory(prefix='fetch-command-test-') as tmp:
            code, out = fu._run_cmd([str(Path(tmp) / 'not-a-command')], timeout=1)
            self.assertEqual(code, -2)
            self.assertIn('命令未找到', out)

    def test_expired_deadline_does_not_start_command(self):
        with tempfile.TemporaryDirectory(prefix='fetch-command-test-') as tmp:
            marker = Path(tmp) / 'must-not-exist'
            command = [sys.executable, '-c',
                       f'from pathlib import Path; Path({str(marker)!r}).touch()']
            with self.assertRaises(TimeoutError):
                fu._run_cmd(command, timeout=5, deadline=time.monotonic() - 1)
            self.assertFalse(marker.exists())

    def test_retry_backoff_stops_at_shared_deadline(self):
        with tempfile.TemporaryDirectory(prefix='fetch-retry-test-') as tmp:
            attempts = Path(tmp) / 'attempts'
            command = [sys.executable, '-c',
                       f"from pathlib import Path; p=Path({str(attempts)!r}); "
                       "p.write_text((p.read_text() if p.exists() else '')+'attempt\\n'); "
                       "raise SystemExit(1)"]
            started = time.monotonic()
            with self.assertRaises(TimeoutError):
                fu._retry_cmd(command, timeout=5, max_retries=3, base_delay=1,
                              deadline=started + 0.3)
            self.assertEqual(attempts.read_text().splitlines(), ['attempt'])
            self.assertLess(time.monotonic() - started, 2)


if __name__ == '__main__':
    unittest.main()
