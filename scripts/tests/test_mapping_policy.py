"""自动映射只指向真实品牌；明确手工维护的品牌不自动接线。"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))

from parse_v2fly import V2FLY_BRAND_MAP, parse_v2fly_brand
from parse_blackmatrix7 import BLACKMATRIX7_BRAND_MAP


class TestMappingPolicy(unittest.TestCase):
    def test_manual_brands_are_not_automatic_targets(self):
        manual = {'WeChat', 'Weibo', 'TencentVideo'}
        for name, mapping in (
            ('v2fly', V2FLY_BRAND_MAP),
            ('blackmatrix7', BLACKMATRIX7_BRAND_MAP),
        ):
            with self.subTest(source=name):
                self.assertFalse(manual & set(mapping.values()))

    def test_weibo_source_reappearing_does_not_enable_sync(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'weibo').write_text('full:new.weibo.example\n', encoding='utf-8')
            result = parse_v2fly_brand('Weibo', tmp)
            self.assertEqual(result, {'main': [], 'ads': [], 'cn': []})

    def test_technical_ids_are_exact(self):
        expected = {'iqiyi': 'iQIYI', 'catchplay': 'CATCHPLAY', 'mytvsuper': 'myTVSUPER'}
        for source, brand in expected.items():
            self.assertEqual(V2FLY_BRAND_MAP[source], brand)
        self.assertEqual(BLACKMATRIX7_BRAND_MAP['LineTV'], 'LINETV')

    def test_targets_exist_and_do_not_include_base_sets(self):
        base = {'Reject', 'Direct', 'Proxy', 'CNCIDR', 'Private', 'Applications',
                'LanCIDR', 'DirectDNS', 'ProxyDNS'}
        for mapping in (V2FLY_BRAND_MAP, BLACKMATRIX7_BRAND_MAP):
            for brand in mapping.values():
                with self.subTest(brand=brand):
                    self.assertNotIn(brand, base)
                    self.assertTrue((ROOT / 'ruleset' / brand / f'{brand}.yaml').is_file())

    def test_reverse_lookup_does_not_silently_discard_sources(self):
        for mapping in (V2FLY_BRAND_MAP, BLACKMATRIX7_BRAND_MAP):
            self.assertEqual(len(mapping), len(set(mapping.values())))


if __name__ == '__main__':
    unittest.main()
