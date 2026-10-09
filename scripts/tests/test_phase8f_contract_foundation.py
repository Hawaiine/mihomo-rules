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


def _require_icon_repo(environ) -> Path:
    """Fail closed unless the caller supplied a usable Oasisic checkout."""
    repo = _resolved_icon_repo(environ)
    if repo is None:
        supplied = (environ.get("MIHOMO_ICON_REPO") or "<unset>").strip()
        raise AssertionError(
            f"MIHOMO_ICON_REPO={supplied} does not contain config/brands.json"
        )
    if not (repo / "config" / "brands.json").is_file():
        raise AssertionError(
            f"MIHOMO_ICON_REPO={repo} does not contain config/brands.json"
        )
    return repo


def _resolved_icon_repo(environ=None):
    """Return the icon checkout named by the supplied environment or repo layout."""
    env = os.environ if environ is None else environ
    value = env.get("MIHOMO_ICON_REPO")
    if value:
        # An explicit but unusable caller path is an error, not permission to
        # silently fall back to a different checkout.
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
        changed["icons"][0] = changed["icons"][0].replace(".png", ".jpg")
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

    def test_no_machine_specific_paths_in_production_sources(self):
        """生产脚本（scripts/**，排除 tests/ 与缓存）同样不得含机器专属默认路径。

        旧门禁只扫描测试、CI 与 workflow，因此生产脚本里残留的机器路径
        （如 match_icons.py 曾有的机器专属回退）可以长期不被发现。
        """
        production = [
            path for path in sorted((ROOT / "scripts").rglob("*.py"))
            if "tests" not in path.relative_to(ROOT / "scripts").parts
            and "__pycache__" not in path.parts
        ]
        self.assertGreater(len(production), 10, "未扫描到生产源码，扫描范围有误")
        self.assertIn(ROOT / "scripts" / "match_icons.py", production)
        hits = []
        for path in production:
            text = path.read_text(encoding="utf-8")
            for marker in self.MACHINE_PATH_MARKERS:
                if marker in text:
                    hits.append(f"{path.relative_to(ROOT)}: {marker}")
        self.assertEqual(hits, [], f"生产脚本仍含机器专属路径: {hits}")

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
        repo = _require_icon_repo(os.environ)
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

    def test_invalid_repo_path_fails_closed_with_explicit_message(self):
        """A missing path must fail immediately with the requested diagnostic."""
        with tempfile.TemporaryDirectory(prefix="icon-repo-missing-") as temp:
            missing = Path(temp) / "absent-Oasisic-Icons"
            with self.assertRaisesRegex(
                AssertionError,
                rf"MIHOMO_ICON_REPO={missing} does not contain config/brands\.json",
            ):
                _require_icon_repo({"MIHOMO_ICON_REPO": str(missing)})

    def test_existing_repo_without_brands_manifest_fails_closed(self):
        """An existing but incomplete checkout is not accepted as a valid fixture."""
        with tempfile.TemporaryDirectory(prefix="icon-repo-no-manifest-") as temp:
            incomplete = Path(temp) / "Oasisic-Icons"
            incomplete.mkdir()
            with self.assertRaisesRegex(
                AssertionError,
                rf"MIHOMO_ICON_REPO={incomplete} does not contain config/brands\.json",
            ):
                _require_icon_repo({"MIHOMO_ICON_REPO": str(incomplete)})

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
        """The child harness must fail (not skip) and name an invalid repo path."""
        with tempfile.TemporaryDirectory(prefix="icon-repo-missing-") as temp:
            missing = Path(temp) / "absent-Oasisic-Icons"
            env = _child_env({"MIHOMO_ICON_REPO": str(missing)})
            child = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "from scripts.tests.test_phase8f_contract_foundation import _require_icon_repo; import os; _require_icon_repo(os.environ)",
                ],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
            )
        output = child.stdout + child.stderr
        self.assertNotEqual(child.returncode, 0, output)
        self.assertIn(
            f"MIHOMO_ICON_REPO={missing} does not contain config/brands.json",
            output,
        )
        self.assertNotIn("skipped", output)


if __name__ == "__main__":
    unittest.main()
