#!/usr/bin/env python3
"""跨品牌域名归属过滤（CROSS_BRAND_OWNERSHIP / strip_cross_brand_owned）单元测试。

Phase 4D 人工批准 D#1 / D#3 / D#4 / D#5 后，归属决策落在 lib/ownership_map.py，
由三个上游解析器在解析期剥离非 owner 品牌候选，保证 canonical single
representation（手工改 YAML 会被 batch_update 的 union 回填）。
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))

from lib.ownership_map import CROSS_BRAND_OWNERSHIP  # noqa: E402
from lib.ownership import strip_cross_brand_owned  # noqa: E402
from lib.canonical import CanonicalRule  # noqa: E402


def R(rule_type, value):
    return CanonicalRule(rule_type=rule_type, value=value)


class TestCrossBrandOwnershipTable(unittest.TestCase):
    """表本身：10 条批准域名，owner 与决策一致。"""

    EXPECTED = {
        'openai.com': 'OpenAI',
        'chatgpt.com': 'OpenAI',
        'oaistatic.com': 'OpenAI',
        'oaiusercontent.com': 'OpenAI',
        'openaiapi-site.azureedge.net': 'OpenAI',
        'openaicomproductionae4b.blob.core.windows.net': 'OpenAI',
        'production-openaicom-storage.azureedge.net': 'OpenAI',
        'p-cdn.us': 'Pandora',
        'hotstar.com': 'JioHotstar',
        'githubcopilot.com': 'GitHub',
    }

    def test_table_matches_approved_decisions(self):
        self.assertEqual(CROSS_BRAND_OWNERSHIP, self.EXPECTED)

    def test_legacy_hotstar_domains_not_in_table(self):
        """D#4 只批准 hotstar.com；三个 legacy 域不得被顺手治理。"""
        for d in ('hotstar-cdn.net', 'hotstar-labs.com', 'hotstarext.com'):
            self.assertNotIn(d, CROSS_BRAND_OWNERSHIP)


class TestStripCrossBrandOwned(unittest.TestCase):

    def test_strips_from_non_owner(self):
        rules = [R('DOMAIN-SUFFIX', 'openai.com'), R('DOMAIN-SUFFIX', 'copilot.microsoft.com')]
        kept, dropped = strip_cross_brand_owned(rules, 'Copilot')
        self.assertEqual([r.value for r in kept], ['copilot.microsoft.com'])
        self.assertEqual([r.value for r in dropped], ['openai.com'])

    def test_keeps_for_owner(self):
        rules = [R('DOMAIN-SUFFIX', 'openai.com'), R('DOMAIN-SUFFIX', 'chatgpt.com')]
        kept, dropped = strip_cross_brand_owned(rules, 'OpenAI')
        self.assertEqual(len(kept), 2)
        self.assertEqual(dropped, [])

    def test_domain_and_suffix_both_covered(self):
        rules = [R('DOMAIN', 'openaicomproductionae4b.blob.core.windows.net'),
                 R('DOMAIN-SUFFIX', 'hotstar.com')]
        kept, dropped = strip_cross_brand_owned(rules, 'Disney')
        self.assertEqual(kept, [])
        self.assertEqual(len(dropped), 2)

    def test_other_rule_types_untouched(self):
        """IP / PROCESS / KEYWORD 等不做归属过滤。"""
        rules = [R('IP-CIDR', '1.2.3.0/24'), R('PROCESS-NAME', 'openai.com'),
                 R('DOMAIN-KEYWORD', 'openai')]
        kept, dropped = strip_cross_brand_owned(rules, 'Copilot')
        self.assertEqual(len(kept), 3)
        self.assertEqual(dropped, [])

    def test_case_insensitive_match(self):
        rules = [R('DOMAIN-SUFFIX', 'OpenAI.COM')]
        kept, dropped = strip_cross_brand_owned(rules, 'Copilot')
        self.assertEqual(kept, [])
        self.assertEqual(len(dropped), 1)

    def test_unrelated_domain_kept(self):
        rules = [R('DOMAIN-SUFFIX', 'example.org')]
        kept, dropped = strip_cross_brand_owned(rules, 'Copilot')
        self.assertEqual(len(kept), 1)
        self.assertEqual(dropped, [])

    def test_owner_never_strips_itself_via_any_brand_name(self):
        """owner 名字大小写不同也不得剥离（brand_name 来自目录名，保持精确）。"""
        rules = [R('DOMAIN-SUFFIX', 'githubcopilot.com')]
        kept, dropped = strip_cross_brand_owned(rules, 'GitHub')
        self.assertEqual(len(kept), 1)
        self.assertEqual(dropped, [])


if __name__ == '__main__':
    unittest.main()
