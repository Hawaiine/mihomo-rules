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
# The pinned icon checkout is owned by the execution environment (local shell or the
# Phase 8 PR workflow); the harness only passes it through. Never hardcode a machine path.
ICON_REPO_CANDIDATE = ROOT / "Oasisic-Icons"


def _child_env(environ=None) -> dict:
    """Environment for child test runs, sourcing the icon checkout dynamically.

    Priority: whatever the caller supplied via ``MIHOMO_ICON_REPO``, else the
    repository-relative checkout layout used by the workflows. Nothing is invented
    and no absolute machine path is embedded.
    """
    env = dict(os.environ if environ is None else environ)
    if not env.get("MIHOMO_ICON_REPO") and ICON_REPO_CANDIDATE.is_dir():
        env["MIHOMO_ICON_REPO"] = str(ICON_REPO_CANDIDATE)
    return env


def _resolved_icon_repo():
    """Return the icon checkout used by the caller, or None when unavailable."""
    value = os.environ.get("MIHOMO_ICON_REPO")
    if value and Path(value).is_dir():
        return Path(value)
    return ICON_REPO_CANDIDATE if ICON_REPO_CANDIDATE.is_dir() else None


def _run_child(cwd, env, *modules):
    return subprocess.run(
        [sys.executable, "-W", "ignore", "-m", "unittest", "-v", *modules],
        cwd=cwd, check=False, capture_output=True, text=True, env=env,
    )


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
        child = _run_child(
            isolated,
            _child_env(),
            "scripts.tests.test_hardening_gaps.TestGenerateConfigDetectsConfigDrift",
            "scripts.tests.test_icon_mapping_integration",
        )
        self.assertEqual(child.returncode, 0, child.stdout + child.stderr)
        after = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
        self.assertEqual(before, after)

    def test_immutability_check_fails_when_child_fails(self):
        result = subprocess.CompletedProcess(args=["child"], returncode=1, stdout="", stderr="injected child failure")
        with patch("scripts.tests.test_phase8f_contract_foundation.subprocess.run", return_value=result):
            with self.assertRaisesRegex(AssertionError, "injected child failure"):
                self.test_production_configs_are_immutable()


class IconRepoPortabilityTest(unittest.TestCase):
    """C6 portability regression: the harness must follow the caller's icon checkout.

    The Phase 8 foundation subprocess used to embed a machine-specific absolute
    path, which fails on a CI runner. These tests lock the environment-driven model.
    """

    # Split so this file does not match its own forbidden-pattern scan.
    MACHINE_PATH_MARKERS = ("/opt/" "data", "/home/" "runner", "/U" "sers/")

    def test_no_machine_specific_paths_in_test_and_ci_sources(self):
        targets = (
            sorted((ROOT / "scripts" / "tests").glob("*.py"))
            + sorted((ROOT / "scripts" / "ci").glob("*.py"))
            + sorted((ROOT / ".github" / "workflows").glob("*.yml"))
        )
        self.assertTrue(targets, "no sources found to scan")
        hits = []
        for path in targets:
            text = path.read_text(encoding="utf-8")
            for marker in self.MACHINE_PATH_MARKERS:
                if marker in text:
                    hits.append(f"{path.relative_to(ROOT)}: {marker}")
        self.assertEqual(hits, [])

    def test_child_env_passes_through_caller_supplied_repo(self):
        env = _child_env({"MIHOMO_ICON_REPO": "/tmp/arbitrary-icon-repo"})
        self.assertEqual(env["MIHOMO_ICON_REPO"], "/tmp/arbitrary-icon-repo")

    def test_child_env_does_not_invent_an_absolute_machine_path(self):
        env = _child_env({})
        value = env.get("MIHOMO_ICON_REPO")
        self.assertTrue(
            value is None or value.startswith(str(ROOT)),
            f"child env invented a machine-specific path: {value!r}",
        )

    def test_child_run_honors_the_caller_supplied_repo_path(self):
        """A temporary, caller-supplied repository location must work unchanged."""
        repo = _resolved_icon_repo()
        if repo is None:
            self.skipTest("no Oasisic checkout available (set MIHOMO_ICON_REPO)")
        with tempfile.TemporaryDirectory(prefix="icon-repo-link-") as temp:
            link = Path(temp) / "Oasisic-Icons"
            link.symlink_to(repo, target_is_directory=True)
            env = _child_env({"MIHOMO_ICON_REPO": str(link)})
            self.assertEqual(env["MIHOMO_ICON_REPO"], str(link))
            result = _run_child(
                ROOT,
                env,
                "scripts.tests.test_hardening_gaps.TestGenerateConfigDetectsConfigDrift",
                "scripts.tests.test_icon_mapping_integration",
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("skipped", result.stderr + result.stdout)

    def test_child_run_fails_loudly_when_repo_lacks_the_pinned_revision(self):
        """A repository that exists but lacks the pinned revision must fail the
        run, never silently pass (the same hardening child returns 0 with a valid
        checkout, so a non-zero here proves the unusable repo is the cause)."""
        with tempfile.TemporaryDirectory(prefix="icon-repo-empty-") as temp:
            repo = Path(temp) / "Oasisic-Icons"
            (repo / "icons").mkdir(parents=True)
            (repo / "config").mkdir()
            (repo / "config" / "brands.json").write_text("{}\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
            env = _child_env({"MIHOMO_ICON_REPO": str(repo)})
            result = _run_child(
                ROOT,
                env,
                "scripts.tests.test_hardening_gaps.TestGenerateConfigDetectsConfigDrift",
            )
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_child_run_names_the_caller_supplied_path_for_an_invalid_repo(self):
        """An unusable MIHOMO_ICON_REPO must be reported, never silently tolerated."""
        with tempfile.TemporaryDirectory(prefix="icon-repo-missing-") as temp:
            missing = Path(temp) / "absent-Oasisic-Icons"
            env = _child_env({"MIHOMO_ICON_REPO": str(missing)})
            result = _run_child(ROOT, env, "scripts.tests.test_icon_mapping_integration")
        output = result.stdout + result.stderr
        self.assertIn(f"MIHOMO_ICON_REPO={missing}", output)
        self.assertIn("skipped", output)


if __name__ == "__main__":
    unittest.main()
