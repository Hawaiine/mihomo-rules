"""Portability regression tests: Oasisic-Icons 检出解析必须可移植且 fail-closed。

背景：生产脚本 `scripts/match_icons.py` 曾在 `MIHOMO_ICON_REPO` 未设置且仓库
相对目录缺失时，静默回退到某台机器专属的绝对路径（`<机器专属>/Oasisic-Icons`）。
这会让同一份代码只在特定机器上“碰巧能用”，并且会静默忽略调用方显式指定的
无效路径。本文件锁定可移植、fail-closed 的解析模型。

覆盖（对应阶段二任务的 6 项要求）：
1. 显式 `MIHOMO_ICON_REPO` 有效 → 使用该路径；
2. 未设置环境变量、仓库相对目录有效 → 正常工作；
3. 显式路径无效 → 明确失败，不静默回退；
4. 环境变量与相对目录都无效 → 明确失败且错误信息有用；
5. 生产脚本中不存在机器专属默认路径；
6. 不可用检出不会让代码偷偷使用 `main` / 工作区 / 非 pinned tree 继续成功。
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))

import match_icons  # noqa: E402

# 拆分书写，避免本文件命中自身的“机器路径”扫描规则
MACHINE_PATH_MARKERS = ('/opt/' 'data', '/home/' 'runner', '/U' 'sers/')
# 被本 PR 移除的旧回退路径；错误信息里绝不能再出现它
LEGACY_MACHINE_FALLBACK = '/opt/' 'data' '/Oasisic-Icons'
PINNED_REVISION = match_icons._OASIC_REVISION
RELATIVE_CHECKOUT = ROOT / 'Oasisic-Icons'


def _tried_paths_in(message: str):
    """抽取错误信息“尝试”行里出现过的绝对路径。

    用于断言“只尝试了预期的候选路径”，而不是笼统地禁止机器前缀子串——
    测试工作区本身可能就位于该前缀之下。
    """
    paths = set()
    for line in message.splitlines():
        if line.strip().startswith('- 尝试'):
            paths.update(re.findall(r'/[^\s：]+', line))
    return paths


def _usable(repo: Path) -> bool:
    return not match_icons.icon_repo_problems(repo, PINNED_REVISION)


class IconRepoResolutionTest(unittest.TestCase):
    """要求 1/2/3：解析优先级与“不静默替换”。"""

    def test_explicit_env_var_is_honored(self):
        resolved = match_icons.resolve_icon_repo(
            environ={'MIHOMO_ICON_REPO': '/tmp/any/icons'}, root='/tmp/project')
        self.assertEqual(resolved, Path('/tmp/any/icons'))

    def test_relative_default_when_env_absent(self):
        resolved = match_icons.resolve_icon_repo(environ={}, root='/tmp/project')
        self.assertEqual(resolved, Path('/tmp/project') / 'Oasisic-Icons')

    def test_blank_env_value_falls_back_to_relative(self):
        resolved = match_icons.resolve_icon_repo(
            environ={'MIHOMO_ICON_REPO': '   '}, root='/tmp/project')
        self.assertEqual(resolved, Path('/tmp/project') / 'Oasisic-Icons')

    def test_explicit_invalid_path_is_not_silently_replaced(self):
        """显式指定无效路径时，候选列表只含该路径——不得追加机器回退。"""
        candidates = match_icons.icon_repo_candidates(
            environ={'MIHOMO_ICON_REPO': '/definitely/not/here'}, root='/tmp/project')
        self.assertEqual([str(p) for p, _ in candidates], ['/definitely/not/here'])
        self.assertTrue(match_icons.icon_repo_problems(Path('/definitely/not/here'), PINNED_REVISION))

    def test_candidates_never_include_a_machine_path(self):
        for environ in ({}, {'MIHOMO_ICON_REPO': '/tmp/any/icons'}):
            for path, origin in match_icons.icon_repo_candidates(environ=environ, root='/tmp/project'):
                text = f'{path}{origin}'
                for marker in MACHINE_PATH_MARKERS:
                    self.assertNotIn(marker, text, f'候选路径含机器专属前缀: {text}')


class IconRepoValidationTest(unittest.TestCase):
    """要求 4/6：只判断“存在”不够，必须是可读 pinned tree 的 git 工作树。"""

    def test_missing_directory_is_reported(self):
        problems = match_icons.icon_repo_problems(Path('/definitely/not/here'), PINNED_REVISION)
        self.assertTrue(problems)
        self.assertIn('不存在', problems[0])

    def test_missing_git_metadata_is_reported(self):
        with tempfile.TemporaryDirectory(prefix='no-git-') as temp:
            (Path(temp) / 'icons').mkdir()
            problems = match_icons.icon_repo_problems(Path(temp), PINNED_REVISION)
            self.assertTrue(problems)
            self.assertIn('git 元数据', problems[0])

    def test_unreadable_pinned_revision_is_reported(self):
        with tempfile.TemporaryDirectory(prefix='empty-git-') as temp:
            repo = Path(temp)
            subprocess.run(['git', 'init', '-q', str(repo)], check=True)
            problems = match_icons.icon_repo_problems(repo, PINNED_REVISION)
            self.assertTrue(problems)
            self.assertIn('固定 revision 在检出中不可读', problems[0])

    def test_usable_checkout_reports_no_problems(self):
        if not _usable(RELATIVE_CHECKOUT):
            self.skipTest(f'没有可用的 Oasisic-Icons 检出: {RELATIVE_CHECKOUT}')
        self.assertEqual(match_icons.icon_repo_problems(RELATIVE_CHECKOUT, PINNED_REVISION), [])


class IconRepoFailClosedTest(unittest.TestCase):
    """要求 3/4/6：错误信息可用，且不可用时绝不静默产出映射。"""

    def test_error_message_lists_tried_paths_and_remedy(self):
        with tempfile.TemporaryDirectory(prefix='no-checkout-') as temp:
            candidate = Path(temp) / 'Oasisic-Icons'
            message = match_icons.icon_repo_required_message(
                PINNED_REVISION, environ={}, root=temp)
        self.assertIn(str(candidate), message)
        self.assertIn('预期条件', message)
        self.assertIn('解决方法', message)
        self.assertIn(PINNED_REVISION, message)
        self.assertNotIn(LEGACY_MACHINE_FALLBACK, message)
        self.assertEqual(_tried_paths_in(message), {str(candidate)},
                         '错误信息尝试了预期之外的路径')

    def test_scan_icons_fails_closed_or_uses_a_real_pinned_checkout(self):
        """真实调用生产入口：要么成功（相对检出可用），要么 fail-closed 报错。"""
        script = (
            'import sys; sys.path.insert(0, "scripts")\n'
            'import match_icons\n'
            'try:\n'
            '    icons = match_icons.scan_icons()\n'
            'except RuntimeError as exc:\n'
            '    print("RAISED"); print(exc); raise SystemExit(0)\n'
            'print("SCANNED", len(icons))\n'
        )
        env = {k: v for k, v in os.environ.items() if k != 'MIHOMO_ICON_REPO'}
        result = subprocess.run([sys.executable, '-c', script], cwd=ROOT, env=env,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        if _usable(RELATIVE_CHECKOUT):
            self.assertIn('SCANNED', result.stdout)
        else:
            self.assertIn('RAISED', result.stdout)
            self.assertIn('无法定位可用的 Oasisic-Icons 检出', result.stdout)
            self.assertNotIn(LEGACY_MACHINE_FALLBACK, result.stdout, '错误信息泄露旧机器回退路径')
            self.assertEqual(_tried_paths_in(result.stdout), {str(RELATIVE_CHECKOUT)},
                             '生产入口尝试了预期之外的路径')

    def test_unusable_repo_does_not_produce_a_mapping(self):
        """不可用检出时 `match_icons.py` 必须非零退出，且不打印任何映射。"""
        if _usable(RELATIVE_CHECKOUT):
            self.skipTest('本机存在可用的相对检出，fail-closed 分支不适用')
        env = {k: v for k, v in os.environ.items() if k != 'MIHOMO_ICON_REPO'}
        result = subprocess.run([sys.executable, 'scripts/match_icons.py'],
                                cwd=ROOT, env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('ICON_MAP', result.stdout)


class ProductionSourceScanTest(unittest.TestCase):
    """要求 5：门禁必须扫描实际生产源码，而不是只看 guard 自身。"""

    def _production_sources(self):
        return [p for p in sorted((ROOT / 'scripts').rglob('*.py'))
                if 'tests' not in p.relative_to(ROOT / 'scripts').parts
                and '__pycache__' not in p.parts]

    def test_production_sources_are_found(self):
        sources = self._production_sources()
        self.assertGreater(len(sources), 10, '未扫描到生产源码，扫描范围有误')
        self.assertIn(ROOT / 'scripts' / 'match_icons.py', sources)

    def test_no_machine_specific_paths_in_production_sources(self):
        hits = []
        for path in self._production_sources():
            text = path.read_text(encoding='utf-8')
            for marker in MACHINE_PATH_MARKERS:
                if marker in text:
                    hits.append(f'{path.relative_to(ROOT)}: {marker}')
        self.assertEqual(hits, [], f'生产脚本仍含机器专属路径: {hits}')


class ReorderNodeFilesPortabilityTest(unittest.TestCase):
    """要求 5：`reorder_node_files.py` 同样不得依赖机器绝对路径。"""

    SCRIPT = ROOT / 'scripts' / 'reorder_node_files.py'

    def test_script_source_has_no_machine_path(self):
        text = self.SCRIPT.read_text(encoding='utf-8')
        for marker in MACHINE_PATH_MARKERS:
            self.assertNotIn(marker, text)

    def test_script_runs_against_a_caller_supplied_repo(self):
        with tempfile.TemporaryDirectory(prefix='reorder-repo-') as temp:
            repo = Path(temp)
            (repo / 'providers' / 'nodes' / 'vmess').mkdir(parents=True)
            for name in ('vmess-grpc', 'vmess-tcp', 'vmess-ws'):
                (repo / 'providers' / 'nodes' / 'vmess' / f'{name}.yaml').write_text('proxies: []\n',
                                                                                    encoding='utf-8')
            env = {**os.environ, 'MIHOMO_RULES_REPO': str(repo)}
            result = subprocess.run([sys.executable, str(self.SCRIPT)],
                                    cwd=ROOT, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            remaining = sorted(p.name for p in (repo / 'providers' / 'nodes' / 'vmess').glob('*.yaml'))
            self.assertEqual(remaining, ['vmess-grpc.yaml', 'vmess-tcp.yaml', 'vmess-ws.yaml'])
            self.assertFalse(list((repo / 'providers' / 'nodes' / 'vmess').glob('.tmp_*')))

    def test_script_fails_closed_without_a_nodes_directory(self):
        with tempfile.TemporaryDirectory(prefix='reorder-empty-') as temp:
            env = {**os.environ, 'MIHOMO_RULES_REPO': temp}
            result = subprocess.run([sys.executable, str(self.SCRIPT)],
                                    cwd=ROOT, env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('找不到节点模板目录', result.stderr + result.stdout)

    def test_script_does_not_write_outside_the_supplied_repo(self):
        with tempfile.TemporaryDirectory(prefix='reorder-guard-') as temp:
            repo = Path(temp)
            (repo / 'providers' / 'nodes').mkdir(parents=True)
            real_nodes = ROOT / 'providers' / 'nodes'
            before = {p: p.stat().st_mtime_ns for p in real_nodes.rglob('*')}
            env = {**os.environ, 'MIHOMO_RULES_REPO': str(repo)}
            subprocess.run([sys.executable, str(self.SCRIPT)], cwd=ROOT, env=env,
                           capture_output=True, text=True)
            after = {p: p.stat().st_mtime_ns for p in real_nodes.rglob('*')}
            # 显式指定仓库时，真实仓库的节点模板不得被改写
            self.assertEqual(before, after, '脚本改写了 MIHOMO_RULES_REPO 之外的仓库')


if __name__ == '__main__':
    unittest.main()
