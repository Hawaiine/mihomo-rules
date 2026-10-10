"""阶段五：Oasisic revision / 生产图标 URL 契约单源化 + 路径解析统一的回归测试。

锁定三件事：

1. **单一运行时来源**：``scripts/config_contract/oasisic_revision.json`` 是 revision
   与 production URL ref 的唯一来源；matcher 不再自带第二份配置。
2. **契约一致**：``contract.json`` 的 ``icon_policy`` 必须与 manifest 语义一致，
   且不得在 contract 里再写一份 revision。
3. **更新 pin 的审阅保护**：guard 自带的 ``PINNED_OASIC`` 是独立信任锚——只改
   manifest / contract / matcher / workflow 无法自行批准一个新 revision。

另含路径解析统一（matcher 与 guard 共用 ``scripts/icon_repo.py``）与各类
单点漂移反例。
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'scripts' / 'ci'))

import icon_repo  # noqa: E402
import match_icons  # noqa: E402
import verify_general_pr as guard  # noqa: E402

MANIFEST = ROOT / 'scripts' / 'config_contract' / 'oasisic_revision.json'
CONTRACT = ROOT / 'scripts' / 'config_contract' / 'contract.json'
MATCHER = ROOT / 'scripts' / 'match_icons.py'
DAILY_SYNC = ROOT / '.github' / 'workflows' / 'daily-sync.yml'
PR_WORKFLOW = ROOT / '.github' / 'workflows' / 'pr-verify.yml'
PINNED = guard.PINNED_OASIC


def read(path):
    return path.read_text(encoding='utf-8')


def real_manifest():
    return json.loads(read(MANIFEST))


def real_contract():
    return json.loads(read(CONTRACT))


class ManifestContractTest(unittest.TestCase):
    """要求 1/3：manifest 字段与 revision 格式必须 fail-closed。"""

    def test_real_manifest_is_accepted(self):
        self.assertEqual(guard.manifest_problems(real_manifest()), [])

    def test_revision_must_be_a_full_40_hex_sha(self):
        for bad in ('main', 'f0f3bc2', 'HEAD', '', None, 123, PINNED.upper()):
            with self.subTest(revision=bad):
                manifest = real_manifest()
                manifest['revision'] = bad
                self.assertTrue(guard.manifest_problems(manifest))

    def test_unapproved_revision_fails_even_when_well_formed(self):
        manifest = real_manifest()
        manifest['revision'] = 'a' * 40
        problems = guard.manifest_problems(manifest)
        self.assertTrue(any('approved pin' in p for p in problems), problems)

    def test_unknown_asset_url_mode_fails_closed(self):
        manifest = real_manifest()
        manifest['asset_url_mode'] = 'whatever'
        self.assertTrue(any('asset_url_mode' in p for p in guard.manifest_problems(manifest)))
        with self.assertRaises(RuntimeError):
            match_icons.asset_url_base(manifest)

    def test_contradictory_mode_and_ref_fails(self):
        manifest = real_manifest()
        manifest['production_url_ref'] = 'dev'
        self.assertTrue(any('production_url_ref' in p for p in guard.manifest_problems(manifest)))
        with self.assertRaises(RuntimeError):
            match_icons.asset_url_ref(manifest)

    def test_missing_asset_url_mode_fails(self):
        manifest = real_manifest()
        del manifest['asset_url_mode']
        self.assertTrue(guard.manifest_problems(manifest))
        with self.assertRaises(RuntimeError):
            match_icons.asset_url_base(manifest)

    def test_wrong_repository_identifier_fails_closed(self):
        for bad in ('not-a-slug', '', 'a/b/c', None, 42):
            with self.subTest(repository=bad):
                manifest = real_manifest()
                manifest['repository'] = bad
                self.assertTrue(guard.manifest_problems(manifest))
                with self.assertRaises(RuntimeError):
                    match_icons.asset_url_base(manifest)

    def test_missing_or_invalid_manifest_file_fails_fast(self):
        with tempfile.TemporaryDirectory(prefix='manifest-') as tmp:
            missing = Path(tmp) / 'nope.json'
            with self.assertRaises(RuntimeError):
                match_icons.load_manifest(missing)
            broken = Path(tmp) / 'broken.json'
            broken.write_text('{not json', encoding='utf-8')
            with self.assertRaises(RuntimeError):
                match_icons.load_manifest(broken)
            empty = Path(tmp) / 'empty.json'
            empty.write_text('{}', encoding='utf-8')
            with self.assertRaises(RuntimeError):
                match_icons.load_manifest(empty)


class ContractManifestAgreementTest(unittest.TestCase):
    """要求 2/3：contract.icon_policy 必须与 manifest 一致，且不得复制 revision。"""

    def test_real_contract_agrees_with_manifest(self):
        self.assertEqual(
            guard.contract_icon_policy_problems(real_contract(), real_manifest()), [])

    def test_url_mode_mismatch_fails(self):
        contract = real_contract()
        contract['icon_policy']['url_mode'] = 'commit_pinned'
        problems = guard.contract_icon_policy_problems(contract, real_manifest())
        self.assertTrue(any('url_mode' in p for p in problems), problems)

    def test_production_url_ref_mismatch_fails(self):
        contract = real_contract()
        contract['icon_policy']['production_url_ref'] = 'dev'
        problems = guard.contract_icon_policy_problems(contract, real_manifest())
        self.assertTrue(any('production_url_ref' in p for p in problems), problems)

    def test_discovery_revision_role_mismatch_fails(self):
        contract = real_contract()
        contract['icon_policy']['discovery_revision_role'] = 'production-url-ref'
        problems = guard.contract_icon_policy_problems(contract, real_manifest())
        self.assertTrue(any('discovery_revision_role' in p for p in problems), problems)

    def test_manifest_path_mismatch_fails(self):
        contract = real_contract()
        contract['icon_policy']['manifest'] = 'scripts/other.json'
        problems = guard.contract_icon_policy_problems(contract, real_manifest())
        self.assertTrue(any('manifest' in p for p in problems), problems)

    def test_missing_icon_policy_fails(self):
        contract = real_contract()
        del contract['icon_policy']
        self.assertTrue(guard.contract_icon_policy_problems(contract, real_manifest()))

    def test_contract_must_not_duplicate_the_revision_sha(self):
        contract = real_contract()
        contract['icon_policy']['revision'] = PINNED
        problems = guard.contract_icon_policy_problems(contract, real_manifest())
        self.assertTrue(any('duplicates a revision SHA' in p for p in problems), problems)


class MatcherSingleSourceTest(unittest.TestCase):
    """要求 1/4：matcher 的 revision 与 URL ref 都来自 manifest。"""

    def test_matcher_url_base_is_derived_from_the_manifest(self):
        manifest = real_manifest()
        expected = f"https://raw.githubusercontent.com/{manifest['repository']}/{manifest['production_url_ref']}/icons"
        self.assertEqual(match_icons.GITHUB_BASE, expected)
        self.assertEqual(match_icons.ASSET_URL_REF, manifest['production_url_ref'])
        self.assertEqual(match_icons._OASIC_REVISION, manifest['revision'])

    def test_generated_icon_urls_use_the_manifest_ref(self):
        manifest = real_manifest()
        prefix = (f"https://raw.githubusercontent.com/{manifest['repository']}"
                  f"/{manifest['production_url_ref']}/icons")
        self.assertEqual(match_icons.GITHUB_BASE, prefix)
        for rel in match_icons.ICON_OVERRIDES.values():
            self.assertTrue(f'{prefix}/{rel}'.startswith(prefix + '/'))

    def test_matcher_has_no_second_runtime_config(self):
        source = read(MATCHER)
        self.assertEqual(guard.matcher_source_problems(source), [])

    def test_hardcoded_asset_url_ref_fails(self):
        source = read(MATCHER).replace(
            'ASSET_URL_REF = asset_url_ref(_MANIFEST)', "ASSET_URL_REF = 'main'")
        self.assertTrue(any('ASSET_URL_REF' in p for p in guard.matcher_source_problems(source)))

    def test_hardcoded_url_base_fails(self):
        source = read(MATCHER).replace(
            'f\'https://raw.githubusercontent.com/{repository}/{ref}/icons\'',
            'f\'https://raw.githubusercontent.com/Hawaiine/Oasisic-Icons/main/icons\'')
        self.assertTrue(guard.matcher_source_problems(source), '硬编码 URL 基址未被拦截')

    def test_environment_revision_fallback_fails(self):
        source = read(MATCHER) + "\nFALLBACK = os.environ['OASIC_REVISION']\n"
        self.assertTrue(any('fallback' in p for p in guard.matcher_source_problems(source)))

    def test_runtime_values_agree_with_manifest(self):
        self.assertEqual(guard.matcher_url_base_problems(real_manifest()), [])

    def test_runtime_check_catches_a_drifted_manifest(self):
        manifest = real_manifest()
        manifest['repository'] = 'evil/Oasisic-Icons'
        problems = guard.matcher_url_base_problems(manifest)
        self.assertTrue(any('GITHUB_BASE' in p for p in problems), problems)


class WorkflowPinTest(unittest.TestCase):
    """要求 8：daily-sync 与 PR workflow 的 Oasisic checkout 必须钉住 manifest revision。"""

    def test_real_workflows_declare_the_manifest_revision(self):
        revision = real_manifest()['revision']
        for path in (DAILY_SYNC, PR_WORKFLOW):
            with self.subTest(workflow=path.name):
                refs = guard.oasisic_checkout_refs(read(path))
                self.assertEqual(refs, [revision], f'{path.name} 的 Oasisic checkout ref 不是 manifest revision')
                self.assertEqual(guard.checkout_ref_problems(read(path), str(path), revision), [])

    def test_floating_ref_fails(self):
        revision = real_manifest()['revision']
        for floating in ('main', 'master', 'latest', 'HEAD'):
            with self.subTest(ref=floating):
                text = read(DAILY_SYNC).replace(f'ref: {revision}', f'ref: {floating}')
                problems = guard.checkout_ref_problems(text, str(DAILY_SYNC), revision)
                self.assertTrue(any('floating' in p or 'approved pin' in p for p in problems), problems)

    def test_missing_ref_fails(self):
        revision = real_manifest()['revision']
        text = read(DAILY_SYNC).replace(f'          ref: {revision}\n', '')
        problems = guard.checkout_ref_problems(text, str(DAILY_SYNC), revision)
        self.assertTrue(any('no ref' in p for p in problems), problems)

    def test_unparseable_workflow_fails_closed(self):
        problems = guard.checkout_ref_problems('jobs: [unclosed', 'x.yml', PINNED)
        self.assertTrue(any('cannot parse' in p for p in problems), problems)

    def test_workflow_without_oasisic_checkout_fails(self):
        problems = guard.checkout_ref_problems('jobs:\n  a:\n    steps: []\n', 'x.yml', PINNED)
        self.assertTrue(any('no Oasisic-Icons checkout step' in p for p in problems), problems)


class PathResolutionSingleSourceTest(unittest.TestCase):
    """要求 6/7：matcher 与 guard 必须共用同一套路径优先级。"""

    def test_guard_and_matcher_share_the_same_helper(self):
        self.assertIs(guard.resolve_icon_repo, match_icons.resolve_icon_repo)
        self.assertIs(guard.resolve_icon_repo, icon_repo.resolve_icon_repo)

    def test_guard_and_matcher_resolve_identically(self):
        for environ in ({}, {'MIHOMO_ICON_REPO': '/tmp/any/icons'}, {'MIHOMO_ICON_REPO': '  '}):
            with self.subTest(environ=environ):
                self.assertEqual(
                    guard.icon_repo_path(environ),
                    match_icons.resolve_icon_repo(environ=environ, root=ROOT))

    def test_explicit_checkout_is_used(self):
        with tempfile.TemporaryDirectory(prefix='icons-') as tmp:
            repo = Path(tmp) / 'repo'
            repo.mkdir()
            self.assertEqual(
                icon_repo.resolve_icon_repo(environ={'MIHOMO_ICON_REPO': str(repo)}, root=tmp), repo)

    def test_invalid_explicit_checkout_fails_without_fallback(self):
        with tempfile.TemporaryDirectory(prefix='icons-') as tmp:
            missing = Path(tmp) / 'definitely-missing'
            candidates = icon_repo.icon_repo_candidates(
                environ={'MIHOMO_ICON_REPO': str(missing)}, root=tmp)
            self.assertEqual([str(p) for p, _ in candidates], [str(missing)],
                             '显式路径无效时不得追加任何回退候选')
            self.assertTrue(icon_repo.icon_repo_problems(missing, PINNED))

    def test_relative_path_used_when_env_absent(self):
        with tempfile.TemporaryDirectory(prefix='icons-') as tmp:
            self.assertEqual(icon_repo.resolve_icon_repo(environ={}, root=tmp),
                             Path(tmp) / 'Oasisic-Icons')

    def test_unusable_checkouts_report_a_clear_message(self):
        with tempfile.TemporaryDirectory(prefix='icons-') as tmp:
            message = icon_repo.icon_repo_required_message(PINNED, environ={}, root=tmp)
            self.assertIn(str(Path(tmp) / 'Oasisic-Icons'), message)
            self.assertIn(PINNED, message)
            self.assertIn('解决方法', message)

    def test_pinned_revision_missing_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix='empty-git-') as tmp:
            repo = Path(tmp)
            subprocess.run(['git', 'init', '-q', str(repo)], check=True)
            self.assertTrue(icon_repo.icon_repo_problems(repo, PINNED))

    def test_non_git_checkout_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix='no-git-') as tmp:
            (Path(tmp) / 'icons').mkdir()
            problems = icon_repo.icon_repo_problems(Path(tmp), PINNED)
            self.assertTrue(any('git 元数据' in p for p in problems), problems)

    def test_usable_checkout_reports_no_problems(self):
        repo = icon_repo.resolve_icon_repo(environ=os.environ, root=ROOT)
        if not repo.is_dir():
            self.skipTest(f'本机没有可用的 Oasisic-Icons 检出: {repo}')
        self.assertEqual(icon_repo.icon_repo_problems(repo, PINNED), [])


class SinglePointDriftTest(unittest.TestCase):
    """要求 9：任一处单点漂移都必须被门禁捕获。"""

    def setUp(self):
        self.manifest = real_manifest()
        self.matcher = read(MATCHER)
        self.daily_sync = read(DAILY_SYNC)
        self.pr_workflow = read(PR_WORKFLOW)
        self.contract = real_contract()

    def _problems(self, **overrides):
        return guard.oasisic_problems(
            overrides.get('manifest', self.manifest),
            overrides.get('matcher', self.matcher),
            overrides.get('daily_sync', self.daily_sync),
            contract=overrides.get('contract', self.contract),
            pr_workflow=overrides.get('pr_workflow', self.pr_workflow),
        )

    def test_baseline_passes(self):
        self.assertEqual(self._problems(), [])

    def test_contract_only_drift_is_caught(self):
        contract = real_contract()
        contract['icon_policy']['url_mode'] = 'commit_pinned'
        self.assertTrue(any('url_mode' in p for p in self._problems(contract=contract)))

    def test_manifest_only_drift_is_caught(self):
        manifest = real_manifest()
        manifest['production_url_ref'] = 'dev'
        self.assertTrue(any('production_url_ref' in p for p in self._problems(manifest=manifest)))

    def test_matcher_ref_only_drift_is_caught(self):
        matcher = self.matcher.replace(
            'ASSET_URL_REF = asset_url_ref(_MANIFEST)', "ASSET_URL_REF = 'main'")
        self.assertTrue(any('ASSET_URL_REF' in p for p in self._problems(matcher=matcher)))

    def test_workflow_ref_only_drift_is_caught(self):
        daily_sync = self.daily_sync.replace(f'ref: {PINNED}', 'ref: main')
        self.assertTrue(any('approved pin' in p or 'floating' in p
                            for p in self._problems(daily_sync=daily_sync)))

    def test_pr_workflow_ref_only_drift_is_caught(self):
        pr_workflow = self.pr_workflow.replace(f'ref: {PINNED}', 'ref: main')
        self.assertTrue(any('approved pin' in p or 'floating' in p
                            for p in self._problems(pr_workflow=pr_workflow)))


class PinApprovalTrustAnchorTest(unittest.TestCase):
    """要求 5：普通 PR 不能仅靠同步改四份文件就自行批准一个新的 revision。"""

    def test_approved_pin_is_an_independent_literal_constant(self):
        source = Path(str(guard.__file__)).read_text(encoding='utf-8')
        self.assertRegex(source, r'PINNED_OASIC\s*=\s*"[0-9a-f]{40}"')
        self.assertNotIn('PINNED_OASIC = manifest', source)

    def test_guard_pin_matches_the_manifest(self):
        self.assertEqual(real_manifest()['revision'], PINNED)

    def test_manifest_only_new_revision_is_rejected(self):
        manifest = real_manifest()
        manifest['revision'] = 'b' * 40
        problems = guard.oasisic_problems(manifest, read(MATCHER), read(DAILY_SYNC))
        self.assertTrue(any('approved pin' in p for p in problems), problems)

    def test_four_file_synchronised_new_revision_is_still_rejected(self):
        """manifest + contract + matcher + 两个 workflow 一起改成新 SHA，仍必须失败。

        这是「信任锚」的核心断言：新 revision 只能通过显式审阅流程更新
        （同时改 guard 常量），而不是由被审 PR 自行批准。
        """
        new = 'c' * 40
        manifest = real_manifest()
        manifest['revision'] = new
        contract = real_contract()
        contract['icon_policy']['revision'] = new        # 试图同步
        matcher = read(MATCHER).replace(PINNED, new)
        daily_sync = read(DAILY_SYNC).replace(PINNED, new)
        pr_workflow = read(PR_WORKFLOW).replace(PINNED, new)
        problems = guard.oasisic_problems(
            manifest, matcher, daily_sync, contract=contract, pr_workflow=pr_workflow)
        self.assertTrue(any('approved pin' in p for p in problems),
                        f'四份文件同步改 pin 后仍必须被拒绝，实际: {problems}')


class ProductionUrlPolicyTest(unittest.TestCase):
    """生产 URL 仍使用 main 分支 ref；discovery/validation 仍只用 pinned SHA。"""

    def test_production_configs_use_the_main_ref_and_no_pinned_sha(self):
        for rel in guard.PRODUCTION_CONFIGS:
            text = (ROOT / rel).read_text(encoding='utf-8')
            with self.subTest(config=rel):
                self.assertIn('/main/icons/', text)
                self.assertNotIn(PINNED, text)

    def test_discovery_revision_is_still_the_pinned_sha(self):
        self.assertEqual(match_icons._OASIC_REVISION, PINNED)
        self.assertIn('for ref in (_OASIC_REVISION,)', read(MATCHER))


if __name__ == '__main__':
    unittest.main()
