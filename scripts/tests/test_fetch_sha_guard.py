"""M7 防护（阶段十一路径 A）测试 — 隔离副本内新增

背景：`git rev-parse HEAD` 非零退出时，`upstream_commit()` 返回空串且不抛异常，
`fetch_upstream()` 成功分支不做任何 SHA 校验 → 该上游仍被判为成功 → `fetch_all()`
用空值覆盖历史统计 → CLI 退出码 0 → `batch_update.py` 不触发 rollback，
后续 batch_write / resolve_ownership / generate_config 继续执行。

测试层级（互不替代）：
- U 函数单元：TestShaGuardUnit（T0/T1/T2/T3）
- A 聚合：TestAggregationRetention（T7/T8）
- C CLI 退出码：随 U/A 断言一并核验（真实 CLI 主体）
- F 后续步骤调用记录：TestM7EndToEndBlocking（真实 batch_update.main() 步骤循环 +
  调用记录器；不执行任何真实同步动作）
- D 真实 CI 部署验证：本文件**不覆盖**

安全约束：不运行生产同步；batch_write 分支在进入其函数体之前即以哨兵异常中止；
`subprocess.run` 全程被调用记录器替换，不启动任何真实子进程。
"""
import ast
import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))

import fetch_upstream as fu  # noqa: E402
import batch_update as bu  # noqa: E402  （模块级仅定义常量，导入不执行 main）

VALID_SHA = 'a' * 40
OTHER_VALID = 'b' * 40
OLD_RECORD = {'commit': 'c' * 40, 'file_count': 111, 'line_counts': {'__file_count__': 111}}


def _load_fetch_upstream_fresh():
    """按项目既有方式加载被测模块（与 test_fetch_hardening.py 一致）。"""
    spec = importlib.util.spec_from_file_location(
        'fetch_upstream_under_test', ROOT / 'scripts' / 'fetch_upstream.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _cli_body_code():
    """提取 fetch_upstream.py 的 `if __name__ == '__main__':` 主体并编译。

    目的：在**不启动子进程**的前提下核验 CLI 退出码逻辑（层级 C）。
    """
    tree = ast.parse((ROOT / 'scripts' / 'fetch_upstream.py').read_text())
    node = tree.body[-1]
    assert isinstance(node, ast.If), 'CLI 主体结构变化，测试需重新定位'
    return compile(ast.Module(body=node.body, type_ignores=[]), '<cli-body>', 'exec')


class _Completed:
    """subprocess.run 的受控替身返回值。"""

    def __init__(self, returncode=0, stdout='', stderr=''):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


class _StepReached(BaseException):
    """哨兵：进入某个步骤的**函数体之前**中止（用于安全地只验证控制流）。

    继承 BaseException 而非 Exception：`batch_update.main()` 的外层
    `except Exception` 会把普通异常吞掉并转成 rollback，哨兵必须穿透它，
    才能区分「流程正常推进到下一步」与「被阻断后回滚」。
    """

    def __init__(self, step):
        super().__init__(step)
        self.step = step


class _FetchHarness:
    """在临时目录中运行真实 fetch_all/fetch_upstream，仅替换外部 I/O。

    - `_ensure_upstream_repo`：建临时目录（**不克隆、不联网**）
    - `_run_cmd`：注入 `git rev-parse HEAD` 的 (退出码, 输出)
    - `_count_files` / `_collect_counts` / `verify_integrity`：受控（聚焦 SHA 路径）

    补丁在 `__enter__` 即全部生效，因此直接调用 `fetch_upstream()` 与
    调用 `fetch_all()` 都是密闭的（首轮曾因补丁只在 run_fetch_all 内生效
    而真实联网，已修正）。
    """

    def __init__(self, injected, seed_old_record=True):
        self.injected = injected
        self.seed_old_record = seed_old_record

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.stats_path = Path(self.tmp.name) / 'stats.json'
        if self.seed_old_record:
            fu.save_stats(str(self.stats_path), {'v2fly': dict(OLD_RECORD)})
        self._patches = [
            patch.object(fu, '_ensure_upstream_repo', self._ensure),
            patch.object(fu, '_run_cmd', self._run_cmd),
            patch.object(fu, 'verify_integrity', return_value=([], {})),
            patch.object(fu, '_count_files', return_value=(9, 9)),
            patch.object(fu, '_collect_counts', return_value={'__file_count__': 9}),
        ]
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.stop()
        self.tmp.cleanup()
        return False

    def _ensure(self, name, config, cache_dir, **kwargs):
        d = Path(cache_dir) / name
        d.mkdir(parents=True, exist_ok=True)
        return str(d), str(d)

    def _run_cmd(self, cmd, **kwargs):
        assert cmd == ['git', 'rev-parse', 'HEAD'], f'非预期命令: {cmd}'
        if Path(kwargs['cwd']).name == 'v2fly':
            return self.injected
        return (0, VALID_SHA)

    def run_fetch_all(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            results = fu.fetch_all(self.tmp.name)
        return results, out.getvalue()

    def cli_exit(self, results):
        """用真实 CLI 主体计算退出码（不启动子进程）。"""
        code = _cli_body_code()
        ns = dict(fu.__dict__)
        ns['fetch_all'] = lambda *a, **kw: results
        out = io.StringIO()
        with contextlib.redirect_stdout(out), patch.object(
                sys, 'argv', ['fetch_upstream.py', self.tmp.name]):
            try:
                exec(code, ns)
            except SystemExit as exc:
                return exc.code, out.getvalue()
        raise AssertionError('CLI 主体未退出')

    def stats_after(self):
        return json.loads(self.stats_path.read_text())['upstreams']


class TestShaGuardUnit(unittest.TestCase):
    """T0–T3：SHA 可读性与格式校验（函数单元层 U）。"""

    def _one(self, injected):
        with _FetchHarness(injected) as h:
            return fu.fetch_upstream('v2fly', fu.UPSTREAM_REPOS['v2fly'],
                                     cache_dir=h.tmp.name)

    def test_T1_revparse_nonzero_exit_must_fail(self):
        res = self._one((1, ''))
        self.assertFalse(res.success, 'rev-parse 非零退出时上游必须失败')
        self.assertIn('SHA', res.error, '失败必须给出可诊断的 SHA 错误信息')

    def test_T2_zero_exit_empty_stdout_must_fail(self):
        res = self._one((0, ''))
        self.assertFalse(res.success, '退出码 0 但 stdout 为空时上游必须失败')

    def test_T3_illegal_sha_forms_must_fail(self):
        for bad in ('abc123', 'Z' * 40, 'A' * 40, 'a' * 39, 'a' * 41, 'a' * 39 + 'g'):
            with self.subTest(sha=bad):
                res = self._one((0, bad))
                self.assertFalse(res.success, f'非法 SHA {bad!r} 必须导致失败')

    def test_T0_valid_sha_not_rejected(self):
        res = self._one((0, VALID_SHA))
        self.assertTrue(res.success, '合法 40 位小写 SHA 不得被误拒')
        self.assertEqual(res.commit, VALID_SHA, '成功结果必须保留完整 SHA')


class TestAggregationRetention(unittest.TestCase):
    """聚合层（A）与 CLI 层（C）的**分层**测试。

    - T7 是 **CLI 层受控测试**：失败结果在 `fetch_all()` **运行结束之后**才被替换，
      因此它只验证「CLI 如何响应一个失败结果」，**不覆盖**聚合函数的写回路径。
    - T8 是 **聚合层真实测试**：失败在 `fetch_upstream()` 内部真实发生，
      经真实 `fetch_all()` 聚合与写回，断言混合结果、退出码与两侧统计写回。
    两个层级不得互相替代（原 T7 名称声称覆盖聚合，与实际行为不符，已更正）。
    """

    def _run(self, injected, controlled_failure=False):
        with _FetchHarness(injected) as h:
            results, fetch_out = h.run_fetch_all()
            if controlled_failure:
                # 受控替换为失败结果 —— 注意：发生在 fetch_all() 结束之后，
                # 因此只影响 CLI 层，不影响聚合/写回（写回已用本轮成功结果完成）
                results['v2fly'] = fu.FetchResult(
                    'v2fly', False, 0, 0, error='受控失败（测试替身）')
            exit_code, cli_out = h.cli_exit(results)
            return results, exit_code, cli_out, fetch_out, h.stats_after()

    def test_T7_cli_reports_controlled_failure_as_nonzero_exit(self):
        """CLI 层（C）：给定一个失败结果，CLI 必须非零退出且不宣称全部成功。

        范围限定：本测试**不**验证聚合函数面对部分上游失败的行为，
        也不验证统计写回（那由 T8 覆盖）。
        """
        results, exit_code, cli_out, _, stats = self._run(
            (0, VALID_SHA), controlled_failure=True)
        # 夹具自检：失败结果确实被替换（聚合结果已被本行改动，不代表聚合层行为）
        self.assertFalse(results['v2fly'].success, '夹具自检：v2fly 应为失败结果')
        self.assertTrue(results['loyalsoldier'].success, '其余上游本轮成功')
        self.assertNotEqual(exit_code, 0, '存在失败结果时 CLI 必须非零退出')
        self.assertIn('部分上游拉取失败', cli_out)
        self.assertNotIn('全部上游拉取成功', cli_out)

    def test_T8_partial_upstream_failure_aggregates_and_retains_stats(self):
        """聚合层（A）＋CLI 层（C）：一个上游真实失败、其余成功时的完整行为。

        失败在 `fetch_upstream()` 内部真实发生（注入不可读/非法 SHA），
        经真实 `fetch_all()` 聚合、写回与真实 CLI 主体判定退出码。
        断言：混合结果正确、CLI 非零退出、失败上游旧记录原样保留、
        **两个**成功上游的本轮记录都正常更新。
        """
        for injected in ((0, ''), (0, 'abc123')):
            with self.subTest(injected=injected):
                results, exit_code, _, _, stats = self._run(injected)
                # 混合结果
                self.assertFalse(results['v2fly'].success, '不可用 SHA 的上游必须失败')
                self.assertTrue(results['loyalsoldier'].success, '其余上游必须成功')
                self.assertTrue(results['blackmatrix7'].success, '其余上游必须成功')
                # 聚合 → CLI
                self.assertNotEqual(exit_code, 0, '一个上游失败时 CLI 必须非零退出')
                # 写回：失败上游保留旧记录，成功上游正常更新
                self.assertEqual(stats['v2fly'], OLD_RECORD,
                                 '失败上游的历史统计必须原样保留（不得被覆盖）')
                self.assertEqual(stats['loyalsoldier']['commit'], VALID_SHA,
                                 '成功上游的本轮记录必须正常更新')
                self.assertEqual(stats['blackmatrix7']['commit'], VALID_SHA,
                                 '成功上游的本轮记录必须正常更新')


class TestShaContractBoundary(unittest.TestCase):
    """T9：SHA 校验函数的**契约边界**（函数单元层 U）。

    背景：`^[0-9a-f]{40}$` 配合 `re.match()` 会接受「40 位十六进制 + 恰好一个换行」，
    因为 Python 的 `$` 允许匹配字符串末尾换行之前的位置。T9 断言校验函数只接受
    **恰好** 40 位小写十六进制；T9b 证明该边界在**当前生产路径上不可达**
    （`upstream_commit()` 已对输出 `strip()`），因此 T9 属健壮性改进，
    不是生产路径的可利用缺陷。
    """

    def test_T9_sha_contract_rejects_trailing_whitespace(self):
        good = '0123456789abcdef' * 2 + '01234567'  # 恰好 40 位小写十六进制
        for bad in (good + '\n', good + '\r', good + '\r\n', good + ' ',
                    '\n' + good, '  ' + good + '  '):
            with self.subTest(sha=repr(bad)):
                self.assertTrue(fu.require_valid_sha(bad),
                                f'{bad!r} 必须被拒绝（SHA 契约要求恰好 40 位小写十六进制）')
        for bad_len in (good[:39], good + 'a'):
            with self.subTest(sha=bad_len):
                self.assertTrue(fu.require_valid_sha(bad_len), '长度不符必须被拒绝')
        self.assertEqual(fu.require_valid_sha(good), '', '合法 40 位小写 SHA 必须通过')
        self.assertTrue(fu.require_valid_sha(good.upper()), '大写必须被拒绝')
        self.assertTrue(fu.require_valid_sha('g' * 40), '非十六进制必须被拒绝')

    def test_T9b_production_path_strips_before_validation(self):
        """生产路径不可达性：`upstream_commit()` 先 `strip()`，带换行的输出不会到达校验函数。"""
        orig = fu._run_cmd
        fu._run_cmd = lambda cmd, **kw: (0, '0123456789abcdef' * 2 + '01234567\n')
        try:
            with tempfile.TemporaryDirectory() as d:
                got = fu.upstream_commit(d)
        finally:
            fu._run_cmd = orig
        self.assertEqual(len(got), 40, 'upstream_commit() 必须返回 strip 后的 40 位值')
        self.assertNotIn('\n', got, '生产路径上换行在进入校验函数前已被 strip()')


class _BatchHarness:
    """真实 batch_update.main() 的受控运行器。

    - `subprocess.run` → 调用记录器（不启动任何真实子进程）
    - fetch 步骤的退出码由调用方注入
    - 进入 `batch_write` 步骤函数体之前抛出哨兵异常（保证不产生真实写入）
    """

    def __init__(self, fetch_returncode):
        self.fetch_returncode = fetch_returncode
        self.calls = []
        self.logs = []
        self.notifications = []

    def __enter__(self):
        h = self

        def fake_run(cmd, **kwargs):
            h.calls.append(list(cmd))
            if any('fetch_upstream.py' in str(c) for c in cmd):
                return _Completed(h.fetch_returncode)
            return _Completed(0)

        def fake_log(msg):
            h.logs.append(str(msg))
            if '批量写入规则集' in str(msg):
                raise _StepReached('batch_write')

        self._patches = [
            patch.object(bu.subprocess, 'run', fake_run),
            patch.object(bu, 'log', fake_log),
            patch.object(bu, 'acquire_lock', lambda: None),
            patch.object(bu, 'release_lock', lambda: None),
            patch.object(bu, 'backup', lambda: None),
            patch.object(bu, 'validate', lambda: None),
            patch.object(bu, 'collect_repo_stats', lambda: {}),
            patch.object(bu, 'send_discord', lambda *a, **kw: None),
            patch.object(bu, 'notify_pushed', lambda *a, **kw: None),
            patch.object(bu, 'notify_skip', lambda *a, **kw: None),
            patch.object(bu, 'notify_failure',
                         lambda *a, **kw: h.notifications.append(kw)),
        ]
        for p in self._patches:
            p.start()
        self._argv = patch.object(sys, 'argv', ['batch_update.py', '--no-commit'])
        self._argv.start()
        self._stdout = contextlib.redirect_stdout(io.StringIO())
        self._stdout.__enter__()
        return self

    def __exit__(self, *exc):
        self._stdout.__exit__(*exc)
        self._argv.stop()
        for p in reversed(self._patches):
            p.stop()
        return False

    def run_main(self):
        """返回 (退出码, 是否进入 batch_write, 被调用的脚本名列表)。"""
        reached = None
        exit_code = None
        try:
            bu.main()
        except _StepReached as exc:
            reached = exc.step
        except SystemExit as exc:
            exit_code = exc.code
        scripts = [str(c[1]) for c in self.calls if len(c) > 1 and 'scripts/' in str(c[1])]
        return exit_code, reached, scripts


class TestM7EndToEndBlocking(unittest.TestCase):
    """M7 十条断言（F 层控制流 ＋ 复用 U/A/C 层真实结果）。

    关键：抓取步骤的**退出码不是硬编码的**，而是由真实 `fetch_upstream.py`
    （真实 fetch_all/fetch_upstream/CLI 主体，仅 mock 外部 I/O）在注入空 SHA 后算出，
    再交给真实 `batch_update.main()` 的步骤循环。这样「SHA 不可读 → 是否阻断后续」
    才是一条完整的链路，而不是两个各自独立的断言。
    """

    def _chained(self, injected):
        with _FetchHarness(injected) as h:
            results, fetch_out = h.run_fetch_all()
            exit_code, cli_out = h.cli_exit(results)
            stats = h.stats_after()
        with _BatchHarness(exit_code) as b:
            batch_exit, reached, scripts = b.run_main()
        return dict(fetch_out=fetch_out, cli_out=cli_out, cli_exit=exit_code,
                    stats=stats, results=results, batch_exit=batch_exit,
                    reached=reached, scripts=scripts,
                    logs=list(b.logs), notifications=list(b.notifications))

    def test_M7_assertions_1_to_9(self):
        r = self._chained((1, ''))  # SHA 读取失败
        # 1. 上游失败
        self.assertFalse(r['results']['v2fly'].success, 'M7-1 上游必须失败')
        # 2. CLI 非零退出码
        self.assertNotEqual(r['cli_exit'], 0, 'M7-2 CLI 必须非零退出')
        # 3. 失败上游旧统计未被覆盖
        self.assertEqual(r['stats']['v2fly'], OLD_RECORD,
                         'M7-3 失败上游旧统计不得被空值覆盖')
        # 4. 日志不得宣称全部上游成功
        self.assertNotIn('全部上游拉取成功', r['cli_out'], 'M7-4 不得宣称全部成功')
        # 5. 整体同步被阻断
        self.assertEqual(r['batch_exit'], 1, 'M7-5 整体同步必须中止（退出码 1）')
        # 6-8. 后续三步均未被调用
        for step in ('batch_write', 'resolve_ownership', 'generate_config'):
            with self.subTest(step=step):
                self.assertIsNone(r['reached'], f'M7 后续步骤 {step} 不得被执行')
        for script in ('batch_write.py', 'resolve_ownership.py', 'generate_config.py'):
            with self.subTest(script=script):
                self.assertNotIn(script, r['scripts'], f'M7 不得调用 {script}')
        # 9. 通知行为：rollback 必须发出失败通知（step = fetch_upstream）
        self.assertTrue(any(n.get('step') == 'fetch_upstream' for n in r['notifications']),
                        'M7-9 rollback 必须发出失败通知')

    def test_M7_assertion_10_valid_input_not_blocked(self):
        r = self._chained((0, VALID_SHA))  # 合法 SHA
        self.assertTrue(r['results']['v2fly'].success, 'M7-10 合法输入不得被误判失败')
        self.assertEqual(r['cli_exit'], 0, 'M7-10 合法输入时 CLI 退出码应为 0')
        self.assertEqual(r['reached'], 'batch_write',
                         'M7-10 合法输入时流程必须继续到下一步（未被错误阻断）')
        self.assertFalse(any('回滚' in l for l in r['logs']), 'M7-10 不得触发回滚')

    def test_regression_nonzero_fetch_step_blocks_flow(self):
        """回归：fetch 步骤退出码非零时，后续步骤必须被阻断（与 SHA 无关）。"""
        with _BatchHarness(1) as b:
            exit_code, reached, scripts = b.run_main()
        self.assertEqual(exit_code, 1)
        self.assertIsNone(reached)
        self.assertNotIn('batch_write.py', scripts)


if __name__ == '__main__':
    unittest.main()
