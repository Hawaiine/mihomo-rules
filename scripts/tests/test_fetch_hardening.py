"""H1 复审发现问题的回归测试（2026-09）

四个问题的共同点：都会让「完整性守卫」或「故障恢复」静默失效，
所以每个都配一条非空转断言（断言真实行为，不是断言代码存在）。
"""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))


def _load_fetch_upstream():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'fetch_upstream_under_test', ROOT / 'scripts' / 'fetch_upstream.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestH1Hardening(unittest.TestCase):

    def test_count_files_excludes_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / '.git').mkdir()
            (d / '.git' / 'obj').write_text('x')
            (d / 'a.txt').write_text('x')
            (d / 'b.txt').write_text('x')
            fu = _load_fetch_upstream()
            count, _size = fu._count_files(str(d))
            self.assertEqual(count, 2, '.git 不得计入文件数')

    def test_lock_min_age_exceeds_fetch_timeout(self):
        fu = _load_fetch_upstream()
        self.assertGreaterEqual(
            fu.LOCK_MIN_AGE, fu.FETCH_TIMEOUT,
            "锁清理阈值必须 >= fetch 超时，否则会删掉正在运行的 fetch 的锁")

    def test_fetch_timeout_less_than_batch_step_timeout(self):
        fu = _load_fetch_upstream()
        batch_src = (ROOT / 'scripts' / 'batch_update.py').read_text()
        self.assertIn('("fetch_upstream", "拉取上游数据", 300)', batch_src)
        self.assertLess(
            fu.FETCH_TIMEOUT, 300,
            "fetch 子超时若 >= 外层 300s，内部重试与清理逻辑无法执行")

    def test_timeout_kills_descendants(self):
        """超时必须能杀到孙进程（git 孙进程会持有 .git 锁）。"""
        fu = _load_fetch_upstream()
        tmp = Path(tempfile.mkdtemp())
        marker = tmp / 'gc.pid'
        prog = tmp / 'spawner.py'
        prog.write_text(
            "import subprocess,sys,time\n"
            "gc=%r\n"
            "p=subprocess.Popen([sys.executable,'-c',"
            "\"import time;open(%r,'w').write('alive');time.sleep(60)\"])\n"
            "open(gc,'w').write(str(p.pid))\n"
            "time.sleep(60)\n" % (str(marker) + '.parent', str(marker))
        )
        code, out = fu._run_cmd([sys.executable, str(prog)], timeout=3)
        self.assertEqual(code, -1, '应报告超时')
        time.sleep(0.5)
        pf = Path(str(marker) + '.parent')
        if pf.exists():
            try:
                pid = int(pf.read_text().strip())
                os.kill(pid, 0)
                self.fail('父进程已被杀，但孙进程存活（孤儿，会持有 git 锁）')
            except (ProcessLookupError, ValueError):
                pass


if __name__ == '__main__':
    unittest.main()
