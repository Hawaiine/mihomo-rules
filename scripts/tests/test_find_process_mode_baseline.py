import copy
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "config_contract"))
sys.path.insert(0, str(ROOT / "scripts" / "tests"))
from contract import check_platform, load_contract
from oracle import evaluate
from oracle_fixtures import ACTUAL, EXPECTED


class FindProcessModeBaselineTest(unittest.TestCase):
    def test_contract_marks_both_observed_modes_as_approved_current_baselines(self):
        contract = load_contract(ROOT / "scripts/config_contract/contract.json")
        for platform, mode in (("android", "strict"), ("nikki", "off")):
            with self.subTest(platform=platform):
                policy = contract["platforms"][platform]["find-process-mode"]
                self.assertEqual(policy["value"], mode)
                self.assertEqual(policy["status"], "APPROVED_CURRENT_BASELINE")

    def test_production_full_and_min_configs_match_their_approved_baselines(self):
        contract = load_contract(ROOT / "scripts/config_contract/contract.json")
        for platform, variant in (("android", "config.yaml"), ("android", "config.min.yaml"), ("nikki", "config.yaml"), ("nikki", "config.min.yaml")):
            config = yaml.safe_load((ROOT / "configs" / platform.title() / variant).read_text(encoding="utf-8"))
            with self.subTest(platform=platform, variant=variant):
                self.assertEqual(check_platform(config, platform, contract), [])

    def test_each_production_variant_rejects_off_and_always_mutations(self):
        contract = load_contract(ROOT / "scripts/config_contract/contract.json")
        expected = {"android": "strict", "nikki": "off"}
        for platform, variant in (("android", "config.yaml"), ("android", "config.min.yaml"), ("nikki", "config.yaml"), ("nikki", "config.min.yaml")):
            path = ROOT / "configs" / platform.title() / variant
            config = yaml.safe_load(path.read_text(encoding="utf-8"))
            for mode in {"off", "always", "strict"} - {expected[platform]}:
                mutated = copy.deepcopy(config)
                mutated["find-process-mode"] = mode
                with self.subTest(platform=platform, variant=variant, mode=mode):
                    errors = check_platform(mutated, platform, contract)
                    self.assertTrue(errors)
                    self.assertTrue(any("does not match approved current baseline" in error for error in errors), errors)

    def test_approved_android_strict_baseline_passes_oracle(self):
        self.assertEqual(evaluate(EXPECTED, ACTUAL), [])

    def _assert_oracle_rejects_nonbaseline_mode(self, platform, mode):
        actual = copy.deepcopy(ACTUAL)
        actual["platforms"][platform]["find-process-mode"] = mode
        errors = evaluate(EXPECTED, actual)
        self.assertIn(f"platforms.{platform}.find-process-mode does not match approved current baseline", errors)

    def test_android_off_does_not_match_approved_current_baseline(self):
        self._assert_oracle_rejects_nonbaseline_mode("android", "off")

    def test_android_always_does_not_match_approved_current_baseline(self):
        self._assert_oracle_rejects_nonbaseline_mode("android", "always")

    def test_approved_nikki_off_baseline_passes_oracle(self):
        self.assertEqual(evaluate(EXPECTED, ACTUAL), [])

    def test_nikki_strict_does_not_match_approved_current_baseline(self):
        self._assert_oracle_rejects_nonbaseline_mode("nikki", "strict")

    def test_nikki_always_does_not_match_approved_current_baseline(self):
        self._assert_oracle_rejects_nonbaseline_mode("nikki", "always")

    def test_off_and_always_are_distinct_from_android_strict_baseline(self):
        expected = EXPECTED["platforms"]["android"]["find-process-mode"]
        self.assertEqual(expected, "strict")
        self.assertNotEqual(expected, "off")
        self.assertNotEqual(expected, "always")


if __name__ == "__main__":
    unittest.main()
