"""
test_hardening_gaps.py — 独立验证发现的三个校验盲区（已修复，防回归）

F1  use: 只解析裸键块式 → 引号/行内 flow 写法的幽灵 provider 引用漏检
F2  README 口径只做 substring 存在性 → 重复出现处其一漂移被另一处掩盖
F3  generate_config 只比 /tmp 暂存文件 → 手工改动过的 configs/ 永不被纠正
"""
import json
import os
import shutil
import subprocess as sp
import sys
import tempfile
import unittest
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import readme_stats
import verify_configs as vc

ROOT = _SCRIPTS.parent
CFG = ROOT / 'configs' / 'Android' / 'config.yaml'
_MANIFEST = ROOT / 'scripts' / 'config_contract' / 'oasisic_revision.json'


def _manifest_revision() -> str:
    """pin 的单一来源：config_contract/oasisic_revision.json（discovery/validation）。"""
    return json.loads(_MANIFEST.read_text(encoding='utf-8'))['revision']


def _lines(p):
    return p.read_text(encoding='utf-8').splitlines(keepends=True)


class TestUseProviderStyle(unittest.TestCase):
    """F1：use 的三种写法都必须被解析到"""

    def setUp(self):
        self.base = _lines(CFG)

    def _with_use(self, use_line):
        out, done = [], False
        for l in self.base:
            if not done and l.strip() == '- name: "AWS"':
                out.append(l)
                out.extend(u + '\n' for u in use_line.rstrip('\n').split('\n'))
                done = True
                continue
            out.append(l)
        self.assertTrue(done, '未找到 AWS 组')
        return out

    def test_flow_style_ghost_provider_fails(self):
        lines = self._with_use('    use: ["provider_does_not_exist"]\n')
        self.assertFalse(vc.check_use_provider_exists(lines, 'android_full'))

    def test_quoted_block_ghost_provider_fails(self):
        lines = self._with_use('    use:\n      - "provider_does_not_exist"\n')
        self.assertFalse(vc.check_use_provider_exists(lines, 'android_full'))

    def test_bare_block_ghost_provider_fails(self):
        lines = self._with_use('    use:\n      - provider_does_not_exist\n')
        self.assertFalse(vc.check_use_provider_exists(lines, 'android_full'))

    def test_real_provider_passes(self):
        lines = self._with_use('    use:\n      - provider_hk\n')
        self.assertTrue(vc.check_use_provider_exists(lines, 'android_full'))


class TestReadmeDuplicateOccurrence(unittest.TestCase):
    """F2：重复出现的口径必须逐处校验"""

    def test_each_occurrence_checked(self):
        stats = readme_stats.compute_stats()
        b, rs = stats['brand_count'], stats['ruleset_count']
        text = (ROOT / 'README.md').read_text(encoding='utf-8')
        self.assertGreaterEqual(len(readme_stats.re.findall(
            r'(\d+) 品牌 · (\d+) 规则集', text)), 2,
            'README 应有多处「N 品牌 · M 规则集」口径，本测试才有意义')

        tmp = Path(tempfile.mkdtemp()) / 'README.md'
        orig_root = readme_stats.ROOT
        try:
            tmp.write_text(text.replace(f'{b} 品牌 · {rs} 规则集',
                                        f'{b - 1} 品牌 · {rs} 规则集', 1),
                           encoding='utf-8')
            orig_root = readme_stats.ROOT
            readme_stats.ROOT = tmp.parent
            (tmp.parent / 'configs').mkdir(exist_ok=True)
            errs = readme_stats.check_readme_structure(stats)
            self.assertTrue(any('口径与实测不一致' in e for e in errs), errs)
        finally:
            readme_stats.ROOT = orig_root
            shutil.rmtree(tmp.parent, ignore_errors=True)


class TestGenerateConfigDetectsConfigDrift(unittest.TestCase):
    """F3：判定依据必须是隔离副本中的 configs/，不能写真实 production。"""

    def _isolated_repo(self):
        tmp = Path(tempfile.mkdtemp(prefix='generate-config-test-'))
        isolated = tmp / 'repo'
        shutil.copytree(
            ROOT,
            isolated,
            # 必须排除 Oasisic-Icons：copytree 同时剥离了它的 .git，
            # 而 match_icons.scan_icons() 需要 `git ls-tree <固定 revision>`，
            # 无 .git 的副本会让 generate_config 在图标步骤 RuntimeError 退出。
            ignore=shutil.ignore_patterns('.git', '__pycache__', '.pytest_cache', 'Oasisic-Icons'),
        )
        self.addCleanup(shutil.rmtree, tmp, True)
        return isolated

    def _icon_env(self):
        """子进程必须拿到**绝对路径**且含 .git 的图标仓库。

        隔离副本的 cwd 在 /tmp，match_icons 的相对默认值（<repo>/Oasisic-Icons）
        在那里指向一个被剥离 .git 的副本 → 固定 revision 不可读。
        """
        env = dict(os.environ)
        if not env.get('MIHOMO_ICON_REPO'):
            candidate = ROOT / 'Oasisic-Icons'
            if (candidate / '.git').exists():
                env['MIHOMO_ICON_REPO'] = str(candidate)
        return env

    def test_generate_config_removes_manual_noise(self):
        isolated = self._isolated_repo()
        cfg = isolated / 'configs' / 'Android' / 'config.yaml'
        original = cfg.read_text(encoding='utf-8')
        cfg.write_text(original + '\n# 手工插入的脏行\n', encoding='utf-8')
        sp.run(['python3', 'scripts/generate_config.py'],
               cwd=isolated, capture_output=True, text=True, env=self._icon_env())
        after = cfg.read_text(encoding='utf-8')
        self.assertNotIn('手工插入的脏行', after, 'generate_config 未纠正手工改动')

    def test_second_run_is_idempotent_after_icon_revision(self):
        isolated = self._isolated_repo()
        # 注入漂移：把四份生产配置的 icon URL 改回旧契约的 pinned SHA 形式
        pinned = _manifest_revision()
        for path in sorted((isolated / 'configs').glob('*/config*.yaml')):
            path.write_text(
                path.read_text(encoding='utf-8').replace('/main/icons/', f'/{pinned}/icons/'),
                encoding='utf-8',
            )
        cfg = isolated / 'configs' / 'Android' / 'config.yaml'
        first = sp.run(['python3', 'scripts/generate_config.py'],
                       cwd=isolated, capture_output=True, text=True, env=self._icon_env())
        revised = cfg.read_text(encoding='utf-8')
        self.assertIn('/main/icons/', revised)
        self.assertNotIn(pinned, revised)
        self.assertNotIn('无变化', first.stdout)
        second = sp.run(['python3', 'scripts/generate_config.py'],
                        cwd=isolated, capture_output=True, text=True, env=self._icon_env())
        self.assertEqual(cfg.read_text(encoding='utf-8'), revised)
        self.assertIn('无变化', second.stdout)

    def test_isolated_copy_excludes_icon_repo(self):
        """回归（daily-sync #479–#481 根因）：隔离副本不得携带被剥离 .git 的图标仓库。"""
        isolated = self._isolated_repo()
        self.assertFalse(
            (isolated / 'Oasisic-Icons').exists(),
            '隔离副本包含 Oasisic-Icons（.git 已被剥离）→ generate_config 将无法读取固定 revision',
        )

    def test_child_env_supplies_absolute_icon_repo(self):
        """子进程必须拿到**绝对路径**的图标仓库；自动解析的那条路径还必须是 git 仓库。

        调用方显式传入的 MIHOMO_ICON_REPO 一律原样透传（portability 测试会传
        非 git 的临时假仓库），只有自动解析（<repo>/Oasisic-Icons）才要求 .git。
        """
        inherited = os.environ.get('MIHOMO_ICON_REPO')
        env = self._icon_env()
        repo = env.get('MIHOMO_ICON_REPO')
        if inherited:
            self.assertEqual(repo, inherited, '调用方显式传入的图标仓库必须原样透传')
            return
        if not repo:
            self.skipTest('本机无 Oasisic-Icons 检出（CI 由 checkout 提供）')
        self.assertTrue(Path(repo).is_absolute(), repo)
        self.assertTrue((Path(repo) / '.git').exists(),
                        f'{repo} 缺少 .git，无法 git ls-tree 固定 revision')


class TestReadmeCheckIsPerOccurrence(unittest.TestCase):
    """F2 补充：--check（check_readme）也必须逐处校验，不能只验一处"""

    def _mutate(self, text, which):
        b, rs = readme_stats.compute_stats()['brand_count'], \
            readme_stats.compute_stats()['ruleset_count']
        lines = text.splitlines(keepends=True)
        idx = [i for i, l in enumerate(lines) if f'{b} 品牌 · {rs} 规则集' in l]
        self.assertEqual(len(idx), 2, f'README 应有 2 处口径，实际 {len(idx)}')
        lines[idx[which]] = lines[idx[which]].replace(
            f'{b} 品牌 · {rs} 规则集', f'{b - 1} 品牌 · {rs} 规则集')
        return ''.join(lines)

    def test_check_readme_catches_each_occurrence(self):
        stats = readme_stats.compute_stats()
        text = (ROOT / 'README.md').read_text(encoding='utf-8')
        for which in (0, 1):
            tmp = Path(tempfile.mkdtemp())
            orig = readme_stats.ROOT
            try:
                (tmp / 'README.md').write_text(self._mutate(text, which), encoding='utf-8')
                (tmp / 'configs').mkdir(exist_ok=True)
                readme_stats.ROOT = tmp
                errs = readme_stats.check_readme(stats)
                self.assertTrue(any('口径与实测不一致' in e for e in errs),
                                f'第 {which} 处漂移未被 check_readme 发现')
            finally:
                readme_stats.ROOT = orig
                shutil.rmtree(tmp, ignore_errors=True)


class TestChangelogArithmetic(unittest.TestCase):
    """Section 22：CHANGELOG「新增 N 个」必须与同行 A → B 差值自洽"""

    def setUp(self):
        self.stats = readme_stats.compute_stats()
        self.orig = readme_stats.ROOT
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        readme_stats.ROOT = self.orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _with(self, text):
        (self.tmp / 'CHANGELOG.md').write_text(text, encoding='utf-8')
        readme_stats.ROOT = self.tmp
        return readme_stats.check_changelog_arithmetic(self.stats)

    def test_baseline_consistent(self):
        text = (ROOT / 'CHANGELOG.md').read_text(encoding='utf-8')
        self.assertEqual(self._with(text), [])

    def test_added_count_mismatch_fails(self):
        text = (ROOT / 'CHANGELOG.md').read_text(encoding='utf-8')
        bad = text.replace('121 → 150 品牌', '121 → 149 品牌', 1)
        self.assertNotEqual(bad, text, '未命中「121 → 150 品牌」')
        errs = self._with(bad)
        self.assertTrue(any('不自洽' in e for e in errs), errs)

    def test_base_count_mismatch_fails(self):
        text = (ROOT / 'CHANGELOG.md').read_text(encoding='utf-8')
        bad = text.replace('含 9 兜底共 161', '含 8 兜底共 161', 1)
        self.assertNotEqual(bad, text, '未命中「含 9 兜底共 161」——变异目标已随 CHANGELOG 更新而漂移')
        errs = self._with(bad)
        self.assertTrue(any('兜底数' in e for e in errs), errs)

    def test_total_ruleset_mismatch_fails(self):
        text = (ROOT / 'CHANGELOG.md').read_text(encoding='utf-8')
        bad = text.replace('含 9 兜底共 161', '含 9 兜底共 158', 1)
        self.assertNotEqual(bad, text, '未命中「含 9 兜底共 161」——变异目标已随 CHANGELOG 更新而漂移')
        errs = self._with(bad)
        self.assertTrue(any('规则集' in e for e in errs), errs)


if __name__ == '__main__':
    unittest.main()
