import copy
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "config_contract"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from contract import icon_url, load_contract, load_oasisic_revision
from oracle import evaluate
from oracle_fixtures import ACTUAL, EXPECTED
from publication import FILES, PublicationBlocked, publish, stage


ROOT = Path(__file__).resolve().parents[2]


class ContractFoundationTest(unittest.TestCase):
    def test_schema_and_pin(self):
        contract = load_contract(ROOT / "scripts/config_contract/contract.json")
        revision = load_oasisic_revision(ROOT / "scripts/config_contract/oasisic_revision.json")
        self.assertEqual(contract["platforms"]["android"]["applications_rule"], "present")
        self.assertEqual(contract["platforms"]["nikki"]["applications_rule"], "absent")
        pinned_url = icon_url(revision["revision"], "icons/test-category/test-brand.png")
        self.assertIn(f"/{revision['revision']}/", pinned_url)
        self.assertNotIn("/main/", pinned_url)

    def test_independent_expected_and_actual_mutations(self):
        self.assertEqual(evaluate(EXPECTED, ACTUAL), [])
        mutations = []
        changed = copy.deepcopy(ACTUAL)
        changed["providers"].remove("ApplePodcasts")
        mutations.append(changed)
        changed = copy.deepcopy(ACTUAL)
        changed["rules"].reverse()
        mutations.append(changed)
        changed = copy.deepcopy(ACTUAL)
        changed["icons"][0] = changed["icons"][0].replace("f0f3bc2a", "main")
        mutations.append(changed)
        changed = copy.deepcopy(ACTUAL)
        changed["platforms"]["android"]["port"] += 1
        mutations.append(changed)
        changed = copy.deepcopy(ACTUAL)
        changed["min"]["rules"].reverse()
        mutations.append(changed)
        changed = copy.deepcopy(ACTUAL)
        changed["generated_keys"].append("unexpected")
        mutations.append(changed)
        changed = copy.deepcopy(ACTUAL)
        changed["platforms"]["nikki"]["rules"].append("RULE-SET,Applications,DIRECT")
        mutations.append(changed)
        self.assertEqual(len(mutations), 7)
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.assertTrue(evaluate(EXPECTED, mutation))

    def test_transaction_and_failures(self):
        contract_root = Path(tempfile.mkdtemp())
        candidates = {}
        for relative in FILES:
            path = contract_root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"old-" + relative.encode())
            candidates[relative] = b"new-" + relative.encode()
        staging = Path(tempfile.mkdtemp())
        stage(contract_root, candidates, staging)
        publish(contract_root, staging, lambda _: None)
        self.assertTrue(all((contract_root / item).read_bytes().startswith(b"new-") for item in FILES))
        for index in range(1, 5):
            for relative in FILES:
                (contract_root / relative).write_bytes(b"old")
            staging = Path(tempfile.mkdtemp())
            stage(contract_root, candidates, staging)
            with self.assertRaises(RuntimeError):
                publish(contract_root, staging, lambda _: None, fail_at=index)
            self.assertTrue(all((contract_root / item).read_bytes() == b"old" for item in FILES))
        staging = Path(tempfile.mkdtemp())
        stage(contract_root, candidates, staging)
        with self.assertRaises(RuntimeError):
            publish(contract_root, staging, lambda _: (_ for _ in ()).throw(RuntimeError("validation")))
        staging = Path(tempfile.mkdtemp())
        stage(contract_root, candidates, staging)
        with self.assertRaises(PublicationBlocked):
            publish(contract_root, staging, lambda _: None, verify_fail=True, rollback_fail=True)

    def test_production_configs_are_immutable(self):
        files = [ROOT / relative for relative in FILES]
        before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
        isolated_parent = Path(tempfile.mkdtemp(prefix="phase8f-immutability-"))
        self.addCleanup(shutil.rmtree, isolated_parent, True)
        isolated = isolated_parent / "repo"
        shutil.copytree(ROOT, isolated, ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"))
        child = subprocess.run(
            [sys.executable, "-W", "ignore", "-m", "unittest", "-v",
             "scripts.tests.test_hardening_gaps.TestGenerateConfigDetectsConfigDrift",
             "scripts.tests.test_icon_mapping_integration"],
            cwd=isolated, check=False, capture_output=True, text=True,
            env={**os.environ, "MIHOMO_ICON_REPO": "/opt/data/Oasisic-Icons"},
        )
        self.assertEqual(child.returncode, 0, child.stdout + child.stderr)
        after = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
        self.assertEqual(before, after)

    def test_immutability_check_fails_when_child_fails(self):
        result = subprocess.CompletedProcess(args=["child"], returncode=1, stdout="", stderr="injected child failure")
        with patch("scripts.tests.test_phase8f_contract_foundation.subprocess.run", return_value=result):
            with self.assertRaisesRegex(AssertionError, "injected child failure"):
                self.test_production_configs_are_immutable()


if __name__ == "__main__":
    unittest.main()
