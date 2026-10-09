"""Focused tests for the general PR verification guard helper.

These tests never modify the real production configs, the repository's git
refs or the network; fixtures are in-memory or throwaway temp git
repositories. They describe the *target* architecture:

* Phase 8-specific prohibitions (base/head config-identical, forbidden
  ruleset/providers paths) are gone.
* They are replaced by head-tree self-consistency invariants
  (config<->ruleset-tree, provider file presence + YAML validity) that any
  legitimate feature PR can satisfy.
* The icon baseline is dynamic (derived from the ruleset brand tree) and
  compared as a *set* against the committed config, so a missing icon can
  never be hidden by the count.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

import verify_general_pr as guard  # noqa: E402


def _rs_url(brand: str) -> str:
    return f"https://raw.githubusercontent.com/Hawaiine/Oasisic-Icons/{guard.PINNED_OASIC}/ruleset/{brand}/{brand}.yaml"


def _config(providers: dict) -> dict:
    """构造测试用配置；provider 的 path 按生成器约定补全为 ./ruleset/<key>.yaml。"""
    full = {}
    for key, entry in providers.items():
        entry = dict(entry)
        entry.setdefault("path", f"./ruleset/{key}.yaml")
        full[key] = entry
    return {"rule-providers": full, "proxy-groups": []}


class ConfigTreeConsistencyGuardTest(unittest.TestCase):
    def test_consistent_tree_passes(self):
        config = _config({"Google": {"url": _rs_url("Google")}, "BiliBili": {"url": _rs_url("BiliBili")}})
        tree = {"ruleset/Google/Google.yaml", "ruleset/BiliBili/BiliBili.yaml", "ruleset/Google/README.md"}
        self.assertEqual(guard.config_tree_problems(config, tree), [])

    def test_provider_without_matching_tree_file_fails(self):
        config = _config({"Google": {"url": _rs_url("Google")}})
        # Google present in config but its ruleset file is absent from the tree.
        tree = {"ruleset/BiliBili/BiliBili.yaml"}
        problems = guard.config_tree_problems(config, tree)
        self.assertTrue(any("Google/Google.yaml" in p and "missing from the head tree" in p for p in problems), problems)

    def test_tree_brand_without_provider_fails(self):
        config = _config({"Google": {"url": _rs_url("Google")}})
        tree = {"ruleset/Google/Google.yaml", "ruleset/BiliBili/BiliBili.yaml"}
        problems = guard.config_tree_problems(config, tree)
        self.assertTrue(any("BiliBili" in p and "no rule-provider" in p for p in problems), problems)

    def test_non_ruleset_url_fails(self):
        config = _config({"Weird": {"url": "https://example.com/not-a-ruleset.txt"}})
        problems = guard.config_tree_problems(config, {"ruleset/Weird/Weird.yaml"})
        self.assertTrue(any("not a ruleset URL" in p for p in problems), problems)

    def test_wrong_provider_path_fails(self):
        """URL 正确但 path 不符合生成器约定，必须失败（URL 或 path 改错任一项都不能通过）。"""
        config = _config({"Google": {"url": _rs_url("Google"), "path": "./ruleset/GoogleX.yaml"}})
        problems = guard.config_tree_problems(config, {"ruleset/Google/Google.yaml"})
        self.assertTrue(any("path must be" in p for p in problems), problems)

    def test_missing_provider_path_fails(self):
        config = {"rule-providers": {"Google": {"url": _rs_url("Google")}}, "proxy-groups": []}
        problems = guard.config_tree_problems(config, {"ruleset/Google/Google.yaml"})
        self.assertTrue(any("path must be" in p for p in problems), problems)

    def test_url_directory_and_filename_mismatch_fails(self):
        """ruleset/Google/GoogleX.yaml 这种错配不得冒充 Google 的 provider。"""
        config = _config({"Google": {"url": "https://raw.githubusercontent.com/Hawaiine/"
                                    "mihomo-rules/main/ruleset/Google/GoogleX.yaml"}})
        problems = guard.config_tree_problems(config, {"ruleset/Google/Google.yaml"})
        self.assertTrue(any("not a ruleset URL" in p for p in problems), problems)

    def test_missing_or_empty_providers_fails(self):
        self.assertTrue(guard.config_tree_problems({"proxy-groups": []}, {"ruleset/A/A.yaml"}))
        self.assertTrue(guard.config_tree_problems({"rule-providers": {}}, set()))

    def test_legitimate_new_brand_passes(self):
        # A feature PR that adds a brand to BOTH the tree and the providers
        # config must pass (this is exactly what the old forbidden-file guard
        # blocked).
        config = _config({
            "Google": {"url": _rs_url("Google")},
            "ApplePodcasts": {"url": _rs_url("ApplePodcasts")},
        })
        tree = {"ruleset/Google/Google.yaml", "ruleset/ApplePodcasts/ApplePodcasts.yaml"}
        self.assertEqual(guard.config_tree_problems(config, tree), [])


class ProviderFileGuardTest(unittest.TestCase):
    """Referenced ruleset files must exist in the head tree and parse as YAML.

    Negative cases run against a throwaway temp git repo (never the real refs
    or production configs); ``guard.ROOT`` is swapped for the duration of each
    such test and restored in ``tearDown``.
    """

    def setUp(self):
        self._saved_root = guard.ROOT
        self._tmp = tempfile.TemporaryDirectory(prefix="gp-guard-provider-")
        self.repo = Path(self._tmp.name)
        self._git("init", "-q")
        self._git("config", "user.email", "guard-test@example.invalid")
        self._git("config", "user.name", "guard-test")

    def tearDown(self):
        guard.ROOT = self._saved_root
        self._tmp.cleanup()

    def _git(self, *args: str) -> str:
        return subprocess.check_output(["git", *args], cwd=self.repo, text=True)

    def _commit_ruleset(self, brand: str, content: str) -> str:
        path = self.repo / "ruleset" / brand / f"{brand}.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-q", "-m", f"fixture {brand}", "--allow-empty")
        return self._git("rev-parse", "HEAD").strip()

    def _google_config(self) -> dict:
        return _config({"Google": {"url": _rs_url("Google")}})

    def test_real_repo_provider_files_parse(self):
        # The real repository's referenced ruleset files must all be valid.
        self.assertEqual(guard.provider_file_problems(self._google_config(), "HEAD"), [])

    def test_valid_provider_file_passes(self):
        rev = self._commit_ruleset("Google", "payload:\n  - '+.google.com'\n")
        guard.ROOT = self.repo
        self.assertEqual(guard.provider_file_problems(self._google_config(), rev), [])

    def test_invalid_yaml_fails(self):
        rev = self._commit_ruleset("Google", "payload: [unclosed\n")
        guard.ROOT = self.repo
        problems = guard.provider_file_problems(self._google_config(), rev)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("ruleset/Google/Google.yaml: cannot load ruleset file from head:", problems[0])

    def test_missing_payload_key_fails(self):
        rev = self._commit_ruleset("Google", "rules:\n  - DOMAIN,google.com\n")
        guard.ROOT = self.repo
        problems = guard.provider_file_problems(self._google_config(), rev)
        self.assertEqual(
            problems,
            ["ruleset/Google/Google.yaml: must parse to a mapping with a `payload` list"],
        )

    def test_payload_non_list_fails(self):
        rev = self._commit_ruleset("Google", "payload:\n  nested: value\n")
        guard.ROOT = self.repo
        problems = guard.provider_file_problems(self._google_config(), rev)
        self.assertEqual(
            problems,
            ["ruleset/Google/Google.yaml: must parse to a mapping with a `payload` list"],
        )

    def test_missing_ruleset_file_fails(self):
        # Commit a different brand only, so ruleset/Google/Google.yaml is absent.
        rev = self._commit_ruleset("Placeholder", "payload: []\n")
        guard.ROOT = self.repo
        problems = guard.provider_file_problems(self._google_config(), rev)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("ruleset/Google/Google.yaml: cannot load ruleset file from head:", problems[0])


class FindProcessModeGuardTest(unittest.TestCase):
    def _contract(self):
        return json.loads((ROOT / "scripts/config_contract/contract.json").read_text(encoding="utf-8"))

    def test_real_contract_baselines_pass(self):
        self.assertEqual(guard.find_process_mode_problems(self._contract()), [])

    def test_unapproved_android_value_fails(self):
        contract = self._contract()
        contract["platforms"]["android"]["find-process-mode"]["value"] = "off"
        problems = guard.find_process_mode_problems(contract)
        self.assertTrue(any("approved current baseline" in p for p in problems), problems)

    def test_status_must_be_approved_current_baseline(self):
        contract = self._contract()
        contract["platforms"]["nikki"]["find-process-mode"]["status"] = "UNKNOWN"
        problems = guard.find_process_mode_problems(contract)
        self.assertTrue(any("APPROVED_CURRENT_BASELINE" in p for p in problems), problems)

    def test_strict_must_not_be_described_as_all_processes(self):
        contract = self._contract()
        contract["platforms"]["android"]["find-process-mode"]["official_semantics"] = (
            "always forces process matching for all processes"
        )
        problems = guard.find_process_mode_problems(contract)
        self.assertTrue(any("must not be described" in p for p in problems), problems)

    def test_production_config_modes(self):
        self.assertEqual(
            guard.production_mode_problems(
                "configs/Android/config.yaml",
                {"find-process-mode": "strict", "rules": ["RULE-SET,Applications,DIRECT"]},
            ),
            [],
        )
        self.assertEqual(
            guard.production_mode_problems("configs/Nikki/config.yaml", {"find-process-mode": False, "rules": []}),
            [],
        )
        self.assertTrue(guard.production_mode_problems("configs/Nikki/config.yaml", {"find-process-mode": "strict", "rules": []}))
        self.assertTrue(guard.production_mode_problems("configs/Android/config.yaml", {"find-process-mode": "strict", "rules": []}))


class OasisicAuthorityGuardTest(unittest.TestCase):
    def _files(self):
        manifest = json.loads((ROOT / guard.MANIFEST_PATH).read_text(encoding="utf-8"))
        matcher = (ROOT / guard.MATCHER_PATH).read_text(encoding="utf-8")
        daily_sync = (ROOT / guard.DAILY_SYNC_PATH).read_text(encoding="utf-8")
        return manifest, matcher, daily_sync

    def test_real_authority_files_pass(self):
        self.assertEqual(guard.oasisic_problems(*self._files()), [])

    def test_floating_manifest_revision_fails(self):
        manifest, matcher, daily_sync = self._files()
        manifest["revision"] = "main"
        problems = guard.oasisic_problems(manifest, matcher, daily_sync)
        self.assertTrue(any("approved pin" in p for p in problems), problems)

    def test_environment_fallback_fails(self):
        manifest, matcher, daily_sync = self._files()
        problems = guard.oasisic_problems(manifest, matcher + "\nFALLBACK = os.environ['OASIC_REVISION']\n", daily_sync)
        self.assertTrue(any("fallback" in p for p in problems), problems)

    def test_daily_sync_floating_ref_fails(self):
        manifest, matcher, daily_sync = self._files()
        problems = guard.oasisic_problems(manifest, matcher, daily_sync.replace(f"ref: {guard.PINNED_OASIC}", "ref: main"))
        self.assertTrue(any("approved pin" in p for p in problems), problems)

    def test_missing_pinned_checkout_fails(self):
        problems = guard.pinned_checkout_problems(Path("/nonexistent-oasisic"))
        self.assertTrue(problems)


class IconBaselineDynamicGuardTest(unittest.TestCase):
    """The expected icon set derives from the ruleset brand tree, and is
    compared as a SET against the committed config's icon-carrying groups.
    Neither side is `actual == actual`, so a missing icon cannot be hidden."""

    def _is_emoji(self, name):
        return name.startswith("🇪")  # sentinel for a stand-in emoji group

    def _map(self, mapping=None):
        return mapping if mapping is not None else {}

    def test_consistent_set_passes(self):
        brand_dirs = ["Google", "BiliBili", "Xiaoyuzhou"]
        sgm = {"Xiaoyuzhou": "Xiaoyuzhou"}
        config = {"proxy-groups": [{"name": g, "icon": "u"} for g in ("Google", "BiliBili", "Xiaoyuzhou")]}
        self.assertEqual(
            guard.icon_baseline_problems(config, [], brand_dirs, self._map(sgm), self._is_emoji),
            [],
        )

    def test_missing_icon_in_config_fails(self):
        brand_dirs = ["Google", "BiliBili"]
        config = {"proxy-groups": [{"name": "Google", "icon": "u"}]}  # BiliBili absent
        problems = guard.icon_baseline_problems(config, [], brand_dirs, self._map({}), self._is_emoji)
        self.assertTrue(any("committed config is missing" in p and "BiliBili" in p for p in problems), problems)

    def test_stray_icon_in_config_fails(self):
        brand_dirs = ["Google"]
        config = {"proxy-groups": [{"name": "Google", "icon": "u"}, {"name": "Ghost", "icon": "u"}]}
        problems = guard.icon_baseline_problems(config, [], brand_dirs, self._map({}), self._is_emoji)
        self.assertTrue(any("ruleset tree does not include" in p and "Ghost" in p for p in problems), problems)

    def test_missing_icon_list_fails(self):
        brand_dirs = ["Google"]
        config = {"proxy-groups": [{"name": "Google", "icon": "u"}]}
        problems = guard.icon_baseline_problems(config, ["Google"], brand_dirs, self._map({}), self._is_emoji)
        self.assertTrue(any("missing" in p and "icons" in p for p in problems), problems)

    def test_emoji_brand_is_excluded_from_expected(self):
        # An emoji-named brand must not be expected to carry an icon, so its
        # absence from the config is not a failure.
        brand_dirs = ["Google", "🇪EmojiBrand"]
        sgm = {"🇪EmojiBrand": "🇪EmojiBrand"}
        config = {"proxy-groups": [{"name": "Google", "icon": "u"}]}
        self.assertEqual(
            guard.icon_baseline_problems(config, [], brand_dirs, self._map(sgm), self._is_emoji),
            [],
        )

    def test_display_name_mapping_is_respected(self):
        # A brand dir maps to a different display name via the strategy group
        # map; the config uses the display name, not the dir name.
        brand_dirs = ["ApplePodcasts"]
        sgm = {"ApplePodcasts": "Apple Podcasts"}
        config = {"proxy-groups": [{"name": "Apple Podcasts", "icon": "u"}]}
        self.assertEqual(
            guard.icon_baseline_problems(config, [], brand_dirs, self._map(sgm), self._is_emoji),
            [],
        )

    def test_expected_total_is_tree_derived_not_count(self):
        # expected total comes from the tree (non-emoji groups), not from the
        # config count.
        brand_dirs = ["Google", "BiliBili", "Xiaoyuzhou"]
        self.assertEqual(guard.expected_icon_total(brand_dirs, self._map({}), self._is_emoji), 3)

    def test_brand_dirs_from_tree_paths_excludes_base_and_bad_shape(self):
        paths = [
            "ruleset/Google/Google.yaml",
            "ruleset/Direct/Direct.yaml",      # BASE routing ruleset -> excluded
            "ruleset/Google/README.md",         # not a <Brand>/<Brand>.yaml
            "ruleset/ApplePodcasts/ApplePodcasts.yaml",
            "ruleset/Podcast/Podcast.yaml",
            "ruleset/Podcast/extra.yaml",       # wrong stem -> excluded
            "other/x/x.yaml",                    # wrong root -> excluded
        ]
        got = guard._brand_dirs_from_tree_paths(paths)
        self.assertIn("Google", got)
        self.assertIn("ApplePodcasts", got)
        self.assertNotIn("Direct", got)
        self.assertNotIn("extra", got)


class IconBaselineRealTreeTest(unittest.TestCase):
    def test_head_tree_matches_committed_config(self):
        import yaml

        sys.path.insert(0, str(ROOT / "scripts"))
        import match_icons

        head = "HEAD"
        config = yaml.safe_load(guard.tree_file(head, guard.CONFIG_FOR_TREE_CHECK))
        brand_dirs = guard._brand_dirs_from_tree_paths(guard.tree_paths(head, "ruleset/"))
        problems = guard.icon_baseline_problems(
            config,
            [],
            brand_dirs,
            match_icons.STRATEGY_GROUP_MAP,
            match_icons.is_emoji_group,
        )
        self.assertEqual(problems, [])


class PodcastIndependenceGuardTest(unittest.TestCase):
    PODCAST_URL = "https://example.test/icons/Media/Xiaoyuzhou/Xiaoyuzhou.png"
    APPLE_ICON_URL = (
        "https://raw.githubusercontent.com/Hawaiine/Oasisic-Icons/main/icons/Apple/ApplePodcasts/ApplePodcasts.png"
    )

    def test_podcast_routing_passes(self):
        self.assertEqual(guard.podcast_problems({"Podcast": self.PODCAST_URL}, self.APPLE_ICON_URL), [])

    def test_podcast_off_xiaoyuzhou_fails(self):
        problems = guard.podcast_problems({"Podcast": "https://example.test/icons/Apple/ApplePodcasts/ApplePodcasts.png"}, self.APPLE_ICON_URL)
        self.assertTrue(problems)

    def test_shared_podcast_icon_fails(self):
        shared = self.PODCAST_URL
        problems = guard.podcast_problems({"Podcast": shared, "Apple Podcasts": shared}, self.APPLE_ICON_URL)
        self.assertTrue(any("must not share" in p for p in problems), problems)

    def test_missing_podcast_key_fails(self):
        problems = guard.podcast_problems({}, self.APPLE_ICON_URL)
        self.assertTrue(any("missing" in p for p in problems), problems)

    def test_fixture_without_independent_applepodcasts_icon_fails(self):
        problems = guard.podcast_problems({"Podcast": self.PODCAST_URL}, "icons/Apple/ApplePodcasts/ApplePodcasts.png")
        self.assertTrue(any("independent" in p for p in problems), problems)


class OracleIndependenceGuardTest(unittest.TestCase):
    def test_real_oracle_passes(self):
        source = (ROOT / guard.ORACLE_PATH).read_text(encoding="utf-8")
        self.assertEqual(guard.oracle_independence_problems(source), [])

    def test_contract_import_fails(self):
        problems = guard.oracle_independence_problems("from contract import check_platform\n")
        self.assertTrue(any("prohibited imports" in p for p in problems), problems)

    def test_matcher_import_fails(self):
        problems = guard.oracle_independence_problems("import match_icons\n")
        self.assertTrue(any("prohibited imports" in p for p in problems), problems)

    def test_unparseable_source_fails(self):
        self.assertTrue(guard.oracle_independence_problems("def broken(:\n"))


class WorkflowSecurityGuardTest(unittest.TestCase):
    def _workflow(self):
        return (ROOT / guard.PR_WORKFLOW_PATH).read_text(encoding="utf-8")

    def test_real_workflow_passes(self):
        self.assertEqual(guard.workflow_problems(self._workflow()), [])

    def test_write_token_fails(self):
        problems = guard.workflow_problems(self._workflow().replace("contents: read", "contents: write"))
        self.assertTrue(problems)

    def test_pull_request_target_only_fails(self):
        # `on: pull_request_target:` replaces the trigger entirely.
        text = self._workflow().replace("  pull_request:\n", "  pull_request_target:\n")
        problems = guard.workflow_problems(text)
        self.assertIn(
            f"{guard.PR_WORKFLOW_PATH}: pull_request_target is forbidden",
            problems,
        )

    def test_pull_request_and_target_together_fails(self):
        # A workflow that keeps pull_request but also adds pull_request_target
        # must still be rejected specifically on the pull_request_target branch.
        text = self._workflow().replace(
            "  pull_request:\n",
            "  pull_request:\n  pull_request_target:\n",
            1,
        )
        problems = guard.workflow_problems(text)
        self.assertIn(
            f"{guard.PR_WORKFLOW_PATH}: pull_request_target is forbidden",
            problems,
        )
        self.assertNotIn(
            f"{guard.PR_WORKFLOW_PATH}: must trigger on pull_request",
            problems,
        )

    def test_abnormal_trigger_inputs_fail_closed(self):
        # `on` parsing to a non-mapping (null / list / bool) must fail closed:
        # never crash, and always report rather than silently pass.
        for label, value in (("null", "null"), ("empty-list", "[]"), ("false", "false")):
            with self.subTest(trigger=label):
                text = self._workflow().replace(
                    "on:\n  pull_request:\n    branches:\n      - main\n",
                    f"on: {value}\n",
                )
                problems = guard.workflow_problems(text)
                self.assertIn(
                    f"{guard.PR_WORKFLOW_PATH}: must trigger on pull_request",
                    problems,
                )

    def test_write_commands_fail(self):
        problems = guard.workflow_problems(self._workflow() + "\n# git push origin main\n")
        self.assertTrue(any("forbidden write command" in p for p in problems), problems)

    def test_missing_verification_step_fails(self):
        text = self._workflow().replace("python3 scripts/verify_rulesets.py", "python3 scripts/verify_configs.py")
        problems = guard.workflow_problems(text)
        self.assertTrue(any("missing required verification step" in p for p in problems), problems)

    def test_wrong_branch_fails(self):
        problems = guard.workflow_problems(self._workflow().replace("      - main\n", "      - develop\n"))
        self.assertTrue(any("main branch" in p for p in problems), problems)

    def test_floating_oasisic_ref_fails(self):
        text = self._workflow().replace(f"ref: {guard.PINNED_OASIC}", "ref: main")
        problems = guard.workflow_problems(text)
        self.assertTrue(any("approved pin" in p or "floating revision" in p for p in problems), problems)

    def test_unapproved_oasisic_pin_fails(self):
        text = self._workflow().replace(f"ref: {guard.PINNED_OASIC}", "ref: " + "a" * 40)
        problems = guard.workflow_problems(text)
        self.assertTrue(any("approved pin" in p for p in problems), problems)


class ProductionIconUrlGuardTest(unittest.TestCase):
    """production-icon-url-guard：生产 config 必须用 /main/icons/ 消费者 URL，
    禁止出现 pinned SHA；pinned SHA 只允许存在于 discovery/validation 来源。"""

    _OK = "icon: https://raw.githubusercontent.com/Hawaiine/Oasisic-Icons/main/icons/Apple/Apple/Apple.png"

    def test_main_branch_icon_url_passes(self):
        self.assertEqual(guard.production_icon_url_problems({"configs/Android/config.yaml": self._OK}), [])

    def test_pinned_sha_in_production_config_fails(self):
        pinned = self._OK.replace("/main/icons/", f"/{guard.PINNED_OASIC}/icons/")
        problems = guard.production_icon_url_problems({"configs/Android/config.yaml": pinned})
        self.assertTrue(any("must not reference the pinned SHA" in p for p in problems), problems)

    def test_missing_main_icons_ref_fails(self):
        problems = guard.production_icon_url_problems({"configs/Nikki/config.yaml": "icon: https://example.com/x.png"})
        self.assertTrue(any("no /main/icons/" in p for p in problems), problems)

    def test_pinned_sha_is_not_flagged_in_discovery_source(self):
        """pin 属于 discovery/validation：manifest 保留 pin 是合法的，
        本 guard 只作用于 production config，不得把两者混为一谈。"""
        self.assertEqual(guard.production_icon_url_problems({}), [])
        manifest = json.loads((ROOT / guard.MANIFEST_PATH).read_text(encoding="utf-8"))
        self.assertEqual(manifest["revision"], guard.PINNED_OASIC)
        self.assertEqual(manifest["revision_role"], "discovery-validation-only")
        self.assertEqual(manifest["asset_url_mode"], "branch-main")
        self.assertEqual(manifest["production_url_ref"], "main")

    def test_real_production_configs_pass(self):
        configs = {p: (ROOT / p).read_text(encoding="utf-8") for p in guard.PRODUCTION_CONFIGS}
        self.assertEqual(guard.production_icon_url_problems(configs), [])

    def test_real_production_configs_hold_zero_pinned_sha(self):
        for path in guard.PRODUCTION_CONFIGS:
            with self.subTest(config=path):
                text = (ROOT / path).read_text(encoding="utf-8")
                self.assertNotIn(guard.PINNED_OASIC, text)
                self.assertIn("/main/icons/", text)


class PhaseSpecificRemovalTest(unittest.TestCase):
    """The two Phase 8-specific prohibitions must no longer exist in the guard."""

    def test_no_forbidden_file_prefixes_constant(self):
        self.assertFalse(hasattr(guard, "FORBIDDEN_DIFF_PREFIXES"), "forbidden-file guard not removed")

    def test_no_production_config_base_head_identical_helpers(self):
        self.assertFalse(hasattr(guard, "config_problems"), "production-config guard not removed")
        self.assertFalse(hasattr(guard, "android_full_problems"), "production-config guard not removed")
        self.assertFalse(hasattr(guard, "BYTE_IDENTICAL_CONFIGS"), "production-config guard not removed")

    def test_no_hardcoded_icon_total(self):
        self.assertFalse(hasattr(guard, "EXPECTED_ICON_TOTAL"), "icon total not made dynamic")


class RevisionsTest(unittest.TestCase):
    def test_missing_env_fails_closed(self):
        with self.assertRaises(guard.GuardFailure):
            guard.revisions_from_env({})
        with self.assertRaises(guard.GuardFailure):
            guard.revisions_from_env({"PR_BASE_SHA": "abc"})

    def test_provided_revisions_pass(self):
        self.assertEqual(
            guard.revisions_from_env({"PR_BASE_SHA": "base", "PR_HEAD_SHA": "head"}),
            ("base", "head"),
        )


class IconRepoPathResolverTest(unittest.TestCase):
    def test_caller_supplied_path_is_honored(self):
        self.assertEqual(guard.icon_repo_path({"MIHOMO_ICON_REPO": "/tmp/any/icons"}), Path("/tmp/any/icons"))

    def test_never_guesses_an_absolute_machine_path(self):
        resolved = guard.icon_repo_path({})
        self.assertTrue(resolved is None or resolved == ROOT / "Oasisic-Icons", f"invented a machine path: {resolved!r}")

    def test_helper_source_has_no_machine_specific_default(self):
        source = Path(guard.__file__ or "").read_text(encoding="utf-8")
        opt_marker = "/opt/" + "data"
        runner_marker = "/home/" + "runner"
        for marker in (opt_marker, runner_marker):
            self.assertNotIn(marker, source)

    def test_missing_checkout_is_surfaced_as_unavailable(self):
        problems = guard.pinned_checkout_problems(Path("/definitely/missing"))
        self.assertTrue(any("not available" in p for p in problems), problems)


class ConfigTreeAllVariantsTest(unittest.TestCase):
    """C3：head 树自洽检查必须覆盖四份生产配置，而不是只有 Android full。

    用一次性临时 git 仓库构造最小树（两个品牌 + 四份配置），把 guard.ROOT
    指向它，从而真实走 ``git show <head>:<path>`` 代码路径。
    """

    BRANDS = ("Alpha", "Beta")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="guard-c3-")
        self.repo = Path(self._tmp.name)
        self._orig_root = guard.ROOT
        guard.ROOT = self.repo
        self.addCleanup(self._restore)
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True, capture_output=True)
        for brand in self.BRANDS:
            self._write(f"ruleset/{brand}/{brand}.yaml", "payload:\n  - DOMAIN,x.example\n")
        for rel in guard.PRODUCTION_CONFIGS:
            self._write_config(rel)
        self._commit("init")

    def _restore(self):
        guard.ROOT = self._orig_root
        self._tmp.cleanup()

    def _write(self, rel, text):
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def _url(self, brand):
        return (f"https://raw.githubusercontent.com/Hawaiine/mihomo-rules/main/"
                f"ruleset/{brand}/{brand}.yaml")

    def _write_config(self, rel, urls=None, paths=None, skip=()):
        urls = urls or {}
        paths = paths or {}
        lines = ["rule-providers:"]
        for brand in self.BRANDS:
            if brand in skip:
                continue
            lines += [
                f"  {brand}:",
                "    type: http",
                "    behavior: classical",
                f'    url: "{urls.get(brand, self._url(brand))}"',
                "    interval: 86400",
                f"    path: {paths.get(brand, f'./ruleset/{brand}.yaml')}",
            ]
        self._write(rel, "\n".join(lines) + "\n")

    def _commit(self, message):
        subprocess.run(["git", "add", "-A"], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(["git", "-c", "user.email=t@example.test", "-c", "user.name=t",
                        "commit", "-qm", message], cwd=self.repo, check=True, capture_output=True)

    def _problems(self):
        return guard.config_tree_guard_problems("HEAD")

    # -- 正样本 ---------------------------------------------------------- #

    def test_production_configs_covers_all_four_variants(self):
        self.assertEqual(
            tuple(guard.PRODUCTION_CONFIGS),
            ("configs/Android/config.yaml", "configs/Android/config.min.yaml",
             "configs/Nikki/config.yaml", "configs/Nikki/config.min.yaml"),
        )

    def test_consistent_four_configs_pass(self):
        self.assertEqual(self._problems(), [])

    # -- 每一份都必须被检查：Nikki / min 不再漏检 ------------------------ #

    def test_nikki_full_url_corrupted_fails(self):
        self._write_config("configs/Nikki/config.yaml", urls={"Alpha": self._url("Beta")})
        self._commit("corrupt nikki full url")
        problems = self._problems()
        self.assertTrue(any("Nikki/config.yaml" in p for p in problems), problems)

    def test_nikki_full_path_corrupted_fails(self):
        self._write_config("configs/Nikki/config.yaml", paths={"Alpha": "./ruleset/AlphaX.yaml"})
        self._commit("corrupt nikki full path")
        problems = self._problems()
        self.assertTrue(any("Nikki/config.yaml" in p and "path must be" in p for p in problems), problems)

    def test_android_min_url_corrupted_fails(self):
        self._write_config("configs/Android/config.min.yaml", urls={"Alpha": self._url("Beta")})
        self._commit("corrupt android min url")
        problems = self._problems()
        self.assertTrue(any("Android/config.min.yaml" in p for p in problems), problems)

    def test_nikki_min_path_corrupted_fails(self):
        self._write_config("configs/Nikki/config.min.yaml", paths={"Beta": "./ruleset/BetaX.yaml"})
        self._commit("corrupt nikki min path")
        problems = self._problems()
        self.assertTrue(any("Nikki/config.min.yaml" in p for p in problems), problems)

    def test_android_full_url_corrupted_fails(self):
        self._write_config("configs/Android/config.yaml", urls={"Alpha": self._url("Beta")})
        self._commit("corrupt android full url")
        self.assertTrue(self._problems())

    # -- key / URL / path 三方错配 --------------------------------------- #

    def test_url_dir_and_file_mismatch_fails(self):
        """ruleset/Alpha/AlphaX.yaml 这种错配不得冒充 Alpha 的 provider。"""
        self._write_config("configs/Nikki/config.min.yaml",
                           urls={"Alpha": "https://raw.githubusercontent.com/Hawaiine/mihomo-rules/main/ruleset/Alpha/AlphaX.yaml"})
        self._commit("dir/file mismatch")
        problems = self._problems()
        self.assertTrue(any("not a ruleset URL" in p for p in problems), problems)

    def test_two_keys_pointing_to_same_brand_fails(self):
        self._write_config("configs/Android/config.min.yaml", urls={"Beta": self._url("Alpha")})
        self._commit("two keys same brand")
        problems = self._problems()
        self.assertTrue(any("has no rule-provider entry" in p for p in problems), problems)

    def test_variant_missing_a_brand_provider_fails(self):
        self._write_config("configs/Nikki/config.yaml", skip=("Beta",))
        self._commit("nikki full missing brand")
        problems = self._problems()
        self.assertTrue(any("Nikki/config.yaml" in p for p in problems), problems)

    def test_url_pointing_at_absent_ruleset_file_fails(self):
        self._write_config("configs/Android/config.min.yaml", urls={"Alpha": self._url("Gamma")})
        self._commit("url points at absent brand")
        problems = self._problems()
        self.assertTrue(any("missing from the head tree" in p for p in problems), problems)


class IconBaselineAllVariantsTest(unittest.TestCase):
    """C3（图标）：四份配置各自参与图标覆盖比对。"""

    def _icon_repo(self):
        env = os.environ.get("MIHOMO_ICON_REPO")
        if env and Path(env).is_dir():
            return Path(env)
        candidate = ROOT / "Oasisic-Icons"
        return candidate if candidate.is_dir() else None

    def test_every_config_participates_in_icon_coverage(self):
        if self._icon_repo() is None:
            self.skipTest("没有可用的 Oasisic-Icons 检出")
        with tempfile.TemporaryDirectory(prefix="guard-icon-") as tmp:
            repo = Path(tmp)
            original = guard.ROOT
            guard.ROOT = repo
            try:
                subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
                for rel in (*guard.PRODUCTION_CONFIGS, guard.ORACLE_FIXTURES_PATH):
                    src = original / rel
                    dst = repo / rel
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
                # 按真实仓库的布局镜像 ruleset 树（仅需路径结构，payload 用占位内容）
                ruleset_root = original / "ruleset"
                if ruleset_root.is_dir():
                    for src in ruleset_root.rglob("*"):
                        if src.is_file():
                            dst = repo / src.relative_to(original)
                            dst.parent.mkdir(parents=True, exist_ok=True)
                            dst.write_text("payload:\n  - DOMAIN,x.example\n", encoding="utf-8")
                subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
                subprocess.run(["git", "-c", "user.email=t@example.test", "-c", "user.name=t",
                                "commit", "-qm", "init"], cwd=repo, check=True, capture_output=True)
                self.assertEqual(guard.icon_baseline_guard_problems("HEAD"), [])

                # 只给 Nikki min 去掉一个 icon → 该份配置的图标覆盖缺失，必须被点名报出
                target = repo / "configs/Nikki/config.min.yaml"
                mutated, count = re.subn(r"^\s*icon: .*$", "", target.read_text(encoding="utf-8"),
                                         count=1, flags=re.M)
                self.assertEqual(count, 1, "未能在 Nikki min 中找到可移除的 icon 行")
                target.write_text(mutated, encoding="utf-8")
                subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
                subprocess.run(["git", "-c", "user.email=t@example.test", "-c", "user.name=t",
                                "commit", "-qm", "mutate"], cwd=repo, check=True, capture_output=True)
                problems = guard.icon_baseline_guard_problems("HEAD")
                self.assertTrue(any("Nikki/config.min.yaml" in p for p in problems), problems)
            finally:
                guard.ROOT = original


if __name__ == "__main__":
    unittest.main()
