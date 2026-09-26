"""自动映射只指向真实品牌；明确手工维护的品牌不自动接线。"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))

from parse_v2fly import V2FLY_BRAND_MAP, parse_v2fly_brand
from parse_blackmatrix7 import BLACKMATRIX7_BRAND_MAP
from parse_loyalsoldier import LOYALSOLDIER_BRAND_MAP
from lib.upstream_coverage import MANUAL_BRANDS

BASE_BRANDS = {
    'Reject', 'Direct', 'Proxy', 'CNCIDR', 'Private', 'Applications',
    'LanCIDR', 'DirectDNS', 'ProxyDNS',
}


def _ruleset_brands():
    return sorted(
        d.name for d in (ROOT / 'ruleset').iterdir()
        if d.is_dir() and (d / f'{d.name}.yaml').is_file() and d.name not in BASE_BRANDS
    )


def _mapping_targets():
    return (set(V2FLY_BRAND_MAP.values())
            | set(BLACKMATRIX7_BRAND_MAP.values())
            | set(LOYALSOLDIER_BRAND_MAP.values()))


class TestMappingPolicy(unittest.TestCase):
    def test_manual_brands_are_not_automatic_targets(self):
        for name, mapping in (
            ('v2fly', V2FLY_BRAND_MAP),
            ('blackmatrix7', BLACKMATRIX7_BRAND_MAP),
            ('loyalsoldier', LOYALSOLDIER_BRAND_MAP),
        ):
            with self.subTest(source=name):
                self.assertFalse(MANUAL_BRANDS & set(mapping.values()))

    def test_every_brand_is_mapped_or_declared_manual(self):
        """没有「既无上游映射、又未登记手工维护」的幽灵品牌。

        这是防「遗漏规则集映射」的核心不变量：新品牌漏接线会直接 CI 失败。
        """
        undeclared = sorted(set(_ruleset_brands()) - _mapping_targets() - MANUAL_BRANDS)
        self.assertEqual(
            undeclared, [],
            f'以下品牌既无上游映射也未登记 MANUAL_BRANDS: {undeclared}')

    def test_manual_brands_all_exist(self):
        """MANUAL_BRANDS 不得留下已删除品牌（清单必须同步收缩）"""
        stale = sorted(MANUAL_BRANDS - set(_ruleset_brands()))
        self.assertEqual(stale, [], f'MANUAL_BRANDS 中存在已不存在的品牌: {stale}')

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
        for mapping in (V2FLY_BRAND_MAP, BLACKMATRIX7_BRAND_MAP, LOYALSOLDIER_BRAND_MAP):
            for brand in mapping.values():
                with self.subTest(brand=brand):
                    self.assertNotIn(brand, BASE_BRANDS)
                    self.assertTrue((ROOT / 'ruleset' / brand / f'{brand}.yaml').is_file())

    def test_reverse_lookup_does_not_silently_discard_sources(self):
        for mapping in (V2FLY_BRAND_MAP, BLACKMATRIX7_BRAND_MAP):
            self.assertEqual(len(mapping), len(set(mapping.values())))


if __name__ == '__main__':
    unittest.main()
