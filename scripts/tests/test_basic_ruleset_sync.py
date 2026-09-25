"""H2-A：基础 7 集进入日更，DNS 手工集与品牌集不得被升格。"""
import ast
import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestBasicSyncScope(unittest.TestCase):
    def test_sync_targets_are_exactly_seven_and_exclude_manual_dns(self):
        batch = _load('batch_update_under_test', 'scripts/batch_update.py')
        source = (ROOT / 'scripts' / 'batch_update.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        self.assertTrue(hasattr(batch, 'write_basic_rulesets'),
                        '基础集必须有独立重建入口，不能混进品牌循环')
        expected = {'Reject', 'Direct', 'Proxy', 'CNCIDR',
                    'Private', 'LanCIDR', 'Applications'}
        self.assertEqual(set(batch.BASIC_SYNC_RULESETS), expected)
        self.assertEqual(set(batch.MANUAL_BASE_RULESETS), {'DirectDNS', 'ProxyDNS'})
        self.assertTrue(expected.isdisjoint(batch.MANUAL_BASE_RULESETS))
        self.assertIn('parse_loyalsoldier_basic', source)
        # 品牌循环仍排除全部 9 个基础集，避免基础集被当作品牌二次处理。
        brand_loop = next(node for node in ast.walk(tree)
                          if isinstance(node, ast.Call)
                          and any(isinstance(k, ast.Compare) and
                                  getattr(getattr(k, 'left', None), 'attr', '') == 'name'
                                  for k in ast.walk(node)))
        self.assertIn(brand_loop, ast.walk(tree))

    def test_dns_and_brand_rulesets_are_not_rewritten(self):
        batch = _load('batch_update_under_test', 'scripts/batch_update.py')
        loyalsoldier = _load('loyalsoldier_under_test', 'scripts/parse_loyalsoldier.py')
        with tempfile.TemporaryDirectory(prefix='basic-scope-') as tmp:
            root = Path(tmp)
            ruleset = root / 'ruleset'
            protected = {
                'DirectDNS': 'manual-direct-dns\n',
                'ProxyDNS': 'manual-proxy-dns\n',
                'iCloud': 'brand-icloud\n',
                'Apple': 'brand-apple\n',
                'Google': 'brand-google\n',
            }
            for name, content in protected.items():
                directory = ruleset / name
                directory.mkdir(parents=True)
                (directory / f'{name}.yaml').write_text(content, encoding='utf-8')
            upstream = root / 'upstream'
            upstream.mkdir()
            samples = {
                'reject.txt': "+.ads.example",
                'direct.txt': "+.direct.example",
                'proxy.txt': "+.proxy.example",
                'private.txt': "+.private.example",
                'cncidr.txt': "1.2.3.0/24",
                'lancidr.txt': "10.0.0.0/8",
                'applications.txt': "PROCESS-NAME,ExampleApp",
            }
            for filename, value in samples.items():
                (upstream / filename).write_text(
                    f"payload:\n  - '{value}'\n", encoding='utf-8')

            written = batch.write_basic_rulesets(upstream, ruleset, dry_run=True)
            self.assertEqual(set(written), set(batch.BASIC_SYNC_RULESETS))
            for name, content in protected.items():
                self.assertEqual((ruleset / name / f'{name}.yaml').read_text(encoding='utf-8'),
                                 content, f'{name} 不得被基础集逻辑改写')
            self.assertEqual(
                {item['ruleset'] for item in loyalsoldier.LOYALSOLDIER_BASE_MAP.values()},
                set(batch.BASIC_SYNC_RULESETS))
            self.assertTrue({'icloud.txt', 'telegramcidr.txt'}.issubset(
                loyalsoldier.LOYALSOLDIER_BRAND_MAP))
            self.assertTrue({'apple.txt', 'google.txt', 'tld-not-cn.txt'}.isdisjoint(
                loyalsoldier.LOYALSOLDIER_BASE_MAP))


if __name__ == '__main__':
    unittest.main()
