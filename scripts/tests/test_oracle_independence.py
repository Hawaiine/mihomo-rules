import copy
import inspect
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "config_contract"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import oracle
from oracle_fixtures import ACTUAL, EXPECTED


class IndependentOracleTest(unittest.TestCase):
    def test_oracle_does_not_import_contract_business_validators(self):
        source = inspect.getsource(oracle)
        for forbidden in ("check_platform", "check_full_min", "check_generated_unknown", "from contract", "import contract"):
            self.assertNotIn(forbidden, source)

    def test_independent_expected_actual_pass(self):
        self.assertEqual(oracle.evaluate(EXPECTED, ACTUAL), [])

    def test_provider_mutation_rejected(self):
        actual = copy.deepcopy(ACTUAL)
        actual["providers"].remove("ApplePodcasts")
        self.assertTrue(oracle.evaluate(EXPECTED, actual))

    def test_rule_order_mutation_rejected(self):
        actual = copy.deepcopy(ACTUAL)
        actual["rules"].reverse()
        self.assertTrue(oracle.evaluate(EXPECTED, actual))

    def test_icon_url_mutation_rejected(self):
        actual = copy.deepcopy(ACTUAL)
        actual["icons"][0] = actual["icons"][0].replace(".png", ".jpg")
        self.assertTrue(oracle.evaluate(EXPECTED, actual))

    def test_platform_field_mutation_rejected_with_contract_validators_poisoned(self):
        import contract
        actual = copy.deepcopy(ACTUAL)
        actual["platforms"]["android"]["port"] += 1
        with patch.object(contract, "check_platform", side_effect=AssertionError("must not be called")), \
             patch.object(contract, "check_full_min", side_effect=AssertionError("must not be called")), \
             patch.object(contract, "check_generated_unknown", side_effect=AssertionError("must not be called")):
            errors = oracle.evaluate(EXPECTED, actual)
        self.assertTrue(any("platforms.android.port" in error for error in errors), errors)

    def test_full_min_mutation_rejected(self):
        actual = copy.deepcopy(ACTUAL)
        actual["min"]["rules"].reverse()
        self.assertTrue(oracle.evaluate(EXPECTED, actual))

    def test_unknown_generated_field_rejected(self):
        actual = copy.deepcopy(ACTUAL)
        actual["generated_keys"].append("unknown-field")
        self.assertTrue(oracle.evaluate(EXPECTED, actual))

    def test_nikki_applications_mutation_rejected(self):
        actual = copy.deepcopy(ACTUAL)
        actual["platforms"]["nikki"]["rules"].append("RULE-SET,Applications,DIRECT")
        self.assertTrue(oracle.evaluate(EXPECTED, actual))


if __name__ == "__main__":
    unittest.main()
