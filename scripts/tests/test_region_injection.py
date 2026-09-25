"""
test_region_injection.py — 地区组注入结构校验的测试

覆盖 verify_configs 中三项地区组检查，确保它们不是空转（vacuous）检查。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import verify_configs
from verify_configs import (
    REGION_GROUPS, BRAND_GROUP_HEAD, BASIC_REGION_GROUPS,
    check_brand_region_injection, check_basic_group_region_injection,
    check_regions_not_in_use,
)
from verify_configs import SYSTEM_GROUPS


def brand_group(name='Netflix', proxies=None, use=None):
    g = {'name': name, 'type': 'select', 'proxies': proxies if proxies is not None else BRAND_GROUP_HEAD + REGION_GROUPS}
    if use:
        g['use'] = use
    return g


class TestRegionInjectionChecks(unittest.TestCase):
    def test_region_list_is_21(self):
        self.assertEqual(len(REGION_GROUPS), 21)
        self.assertEqual(len(BASIC_REGION_GROUPS), 5)

    def test_brand_group_pass(self):
        groups = [brand_group()]
        self.assertTrue(check_brand_region_injection(groups, 'x'))

    def test_brand_group_missing_regions_fails(self):
        groups = [brand_group(proxies=BRAND_GROUP_HEAD)]
        self.assertFalse(check_brand_region_injection(groups, 'x'))

    def test_brand_group_wrong_order_fails(self):
        scrambled = BRAND_GROUP_HEAD + list(reversed(REGION_GROUPS))
        groups = [brand_group(proxies=scrambled)]
        self.assertFalse(check_brand_region_injection(groups, 'x'))

    def test_system_group_not_checked_as_brand(self):
        groups = [{'name': SYSTEM_GROUPS[0], 'type': 'url-test', 'proxies': REGION_GROUPS}]
        self.assertTrue(check_brand_region_injection(groups, 'x'))

    def test_basic_group_pass(self):
        groups = [{'name': n, 'proxies': ['🎯 全球直连'] + REGION_GROUPS} for n in BASIC_REGION_GROUPS]
        self.assertTrue(check_basic_group_region_injection(groups, 'x'))

    def test_basic_group_missing_fails(self):
        groups = [{'name': n, 'proxies': ['🎯 全球直连'] + REGION_GROUPS[:20]} for n in BASIC_REGION_GROUPS]
        self.assertFalse(check_basic_group_region_injection(groups, 'x'))

    def test_basic_group_absent_fails(self):
        groups = [{'name': 'Netflix', 'proxies': BRAND_GROUP_HEAD + REGION_GROUPS}]
        self.assertFalse(check_basic_group_region_injection(groups, 'x'))

    def test_use_block_clean_passes(self):
        groups = [brand_group(use=['provider1'])]
        self.assertTrue(check_regions_not_in_use(groups, 'x'))

    def test_region_in_use_block_fails(self):
        groups = [brand_group(use=['provider1', REGION_GROUPS[0]])]
        self.assertFalse(check_regions_not_in_use(groups, 'x'))


if __name__ == '__main__':
    unittest.main()