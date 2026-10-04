"""Focused tests for the Phase 8 read-only PR guard helper.

These tests use in-memory fixtures only; they never touch the real production
configs, git refs or the network.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "ci"))

import verify_phase8_pr as guard  # noqa: E402

ANDROID_HEADER = "# 进程匹配模式: strict (手机端, 默认模式, 由 Mihomo 判断是否启用进程匹配)\nfind-process-mode: strict\nmode: rule\nmixed-port: 7890\n"
ANDROID_HEADER_OLD = "# 进程匹配模式: strict (手机端, 匹配所有进程)\nfind-process-mode: strict\nmode: rule\nmixed-port: 7890\n"
ANDROID_SEMANTIC = "# 进程匹配模式: strict (手机端, 默认模式, 由 Mihomo 判断是否启用进程匹配)\nfind-process-mode: off\nmode: rule\nmixed-port: 7890\n"
NIKKI_MIN = "mixed-port: 7890\nfind-process-mode: off\nmode: rule\n"


class ProductionConfigGuardTest(unittest.TestCase):
    def test_android_comment_only_change_passes(self):
        self.assertEqual(guard.android_full_problems(ANDROID_HEADER_OLD, ANDROID_HEADER), [])

    def test_android_semantic_change_fails_with_auditable_detail(self):
        problems = guard.android_full_problems(ANDROID_HEADER_OLD, ANDROID_SEMANTIC)
        self.assertTrue(problems)
        summary = "\n".join(problems)
        self.assertIn("configs/Android/config.yaml", summary)
        self.assertIn("find-process-mode", summary)
        self.assertIn("'strict' -> 'off'", summary)

    def test_android_non_comment_textual_change_fails(self):
        quoted = ANDROID_HEADER.replace("mode: rule", 'mode: "rule"')
        problems = guard.android_full_problems(ANDROID_HEADER, quoted)
        self.assertTrue(problems)
        self.assertTrue(any("non-comment textual change" in problem for problem in problems), problems)

    def test_android_unparseable_head_fails(self):
        problems = guard.android_full_problems(ANDROID_HEADER, "mode: [unclosed\n")
        self.assertTrue(problems)

    def test_byte_identical_configs_pass(self):
        for path in guard.BYTE_IDENTICAL_CONFIGS:
            with self.subTest(path=path):
                self.assertEqual(guard.config_problems(path, b"a: 1\n", b"a: 1\n"), [])

    def test_min_and_nikki_change_fails(self):
        self.assertTrue(guard.config_problems("configs/Android/config.min.yaml", b"a: 1\n", b"a: 2\n"))
        self.assertTrue(guard.config_problems("configs/Nikki/config.yaml", b"a: 1\n", b"a: 2\n"))
        self.assertTrue(guard.config_problems("configs/Nikki/config.min.yaml", b"a: 1\n", b""))

    def test_android_full_dispatch_allows_comment_only(self):
        self.assertEqual(
            guard.config_problems(
                guard.ANDROID_FULL,
                ANDROID_HEADER_OLD.encode(),
                ANDROID_HEADER.encode(),
            ),
            [],
        )


class FindProcessModeGuardTest(unittest.TestCase):
    def _contract(self):
        import json

        return json.loads((ROOT / "scripts/config_contract/contract.json").read_text(encoding="utf-8"))

    def test_real_contract_baselines_pass(self):
        self.assertEqual(guard.find_process_mode_problems(self._contract()), [])

    def test_unapproved_android_value_fails(self):
        contract = self._contract()
        contract["platforms"]["android"]["find-process-mode"]["value"] = "off"
        problems = guard.find_process_mode_problems(contract)
        self.assertTrue(any("approved current baseline" in problem for problem in problems), problems)

    def test_status_must_be_approved_current_baseline(self):
        contract = self._contract()
        contract["platforms"]["nikki"]["find-process-mode"]["status"] = "UNKNOWN"
        problems = guard.find_process_mode_problems(contract)
        self.assertTrue(any("APPROVED_CURRENT_BASELINE" in problem for problem in problems), problems)

    def test_strict_must_not_be_described_as_all_processes(self):
        contract = self._contract()
        contract["platforms"]["android"]["find-process-mode"]["official_semantics"] = (
            "always forces process matching for all processes"
        )
        problems = guard.find_process_mode_problems(contract)
        self.assertTrue(any("must not be described" in problem for problem in problems), problems)

    def test_production_config_modes(self):
        self.assertEqual(
            guard.production_mode_problems("configs/Android/config.yaml", {"find-process-mode": "strict", "rules": ["RULE-SET,Applications,DIRECT"]}),
            [],
        )
        self.assertEqual(
            guard.production_mode_problems("configs/Nikki/config.yaml", {"find-process-mode": False, "rules": []}),
            [],
        )
        self.assertTrue(guard.production_mode_problems("configs/Nikki/config.yaml", {"find-process-mode": "strict", "rules": []}))
        self.assertTrue(guard.production_mode_problems("configs/Android/config.yaml", {"find-process-mode": "strict", "rules": []}))

    def test_strict_is_not_always(self):
        contract = self._contract()
        self.assertEqual(contract["platforms"]["android"]["find-process-mode"]["value"], "strict")
        self.assertNotEqual(contract["platforms"]["android"]["find-process-mode"]["value"], "always")


class OasisicAuthorityGuardTest(unittest.TestCase):
    def _files(self):
        import json

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
        self.assertTrue(any("approved pin" in problem for problem in problems), problems)

    def test_environment_fallback_fails(self):
        manifest, matcher, daily_sync = self._files()
        problems = guard.oasisic_problems(manifest, matcher + "\nFALLBACK = os.environ['OASIC_REVISION']\n", daily_sync)
        self.assertTrue(any("fallback" in problem for problem in problems), problems)

    def test_daily_sync_floating_ref_fails(self):
        manifest, matcher, daily_sync = self._files()
        problems = guard.oasisic_problems(manifest, matcher, daily_sync.replace(f"ref: {guard.PINNED_OASIC}", "ref: main"))
        self.assertTrue(any("approved pin" in problem for problem in problems), problems)

    def test_missing_pinned_checkout_fails(self):
        problems = guard.pinned_checkout_problems(Path("/nonexistent-oasisic"))
        self.assertTrue(problems)


class IconBaselineGuardTest(unittest.TestCase):
    PODCAST_URL = "https://example.test/icons/Media/Xiaoyuzhou/Xiaoyuzhou.png"
    APPLE_ICON_URL = (
        "https://raw.githubusercontent.com/Hawaiine/Oasisic-Icons/"
        "f0f3bc2a44616885682ee5f0e5921540b964e2d8/icons/Apple/ApplePodcasts/ApplePodcasts.png"
    )

    def test_podcast_routing_passes(self):
        self.assertEqual(guard.podcast_problems({"Podcast": self.PODCAST_URL}, self.APPLE_ICON_URL), [])

    def test_podcast_off_xiaoyuzhou_fails(self):
        problems = guard.podcast_problems({"Podcast": "https://example.test/icons/Apple/ApplePodcasts/ApplePodcasts.png"}, self.APPLE_ICON_URL)
        self.assertTrue(problems)

    def test_shared_podcast_icon_fails(self):
        shared = self.PODCAST_URL
        problems = guard.podcast_problems({"Podcast": shared, "ApplePodcasts": shared}, self.APPLE_ICON_URL)
        self.assertTrue(any("must not share" in problem for problem in problems), problems)

    def test_missing_podcast_key_fails(self):
        problems = guard.podcast_problems({}, self.APPLE_ICON_URL)
        self.assertTrue(any("missing" in problem for problem in problems), problems)

    def test_fixture_without_independent_applepodcasts_icon_fails(self):
        problems = guard.podcast_problems({"Podcast": self.PODCAST_URL}, "icons/Apple/ApplePodcasts/ApplePodcasts.png")
        self.assertTrue(any("independent" in problem for problem in problems), problems)

    def test_icon_total_and_missing(self):
        self.assertEqual(guard.icon_baseline_problems({f"g{i}": "url" for i in range(142)}, []), [])
        self.assertTrue(guard.icon_baseline_problems({"a": "url"}, ["x"]))
        self.assertTrue(guard.icon_baseline_problems({f"g{i}": "url" for i in range(141)}, []))


class OracleIndependenceGuardTest(unittest.TestCase):
    def test_real_oracle_passes(self):
        source = (ROOT / guard.ORACLE_PATH).read_text(encoding="utf-8")
        self.assertEqual(guard.oracle_independence_problems(source), [])

    def test_contract_import_fails(self):
        problems = guard.oracle_independence_problems("from contract import check_platform\n")
        self.assertTrue(any("prohibited imports" in problem for problem in problems), problems)

    def test_matcher_import_fails(self):
        problems = guard.oracle_independence_problems("import match_icons\n")
        self.assertTrue(any("prohibited imports" in problem for problem in problems), problems)

    def test_unparseable_source_fails(self):
        self.assertTrue(guard.oracle_independence_problems("def broken(:\n"))


class WorkflowGuardTest(unittest.TestCase):
    def _workflow(self):
        return (ROOT / guard.PR_WORKFLOW_PATH).read_text(encoding="utf-8")

    def test_real_workflow_passes(self):
        self.assertEqual(guard.workflow_problems(self._workflow()), [])

    def test_write_token_fails(self):
        text = self._workflow().replace("contents: read", "contents: write")
        problems = guard.workflow_problems(text)
        self.assertTrue(problems)

    def test_pull_request_target_fails(self):
        text = self._workflow().replace("  pull_request:\n", "  pull_request_target:\n")
        problems = guard.workflow_problems(text)
        self.assertTrue(problems)

    def test_write_commands_fail(self):
        text = self._workflow() + "\n# git push origin main\n"
        problems = guard.workflow_problems(text)
        self.assertTrue(any("forbidden write command" in problem for problem in problems), problems)

    def test_missing_verification_step_fails(self):
        text = self._workflow().replace("python3 scripts/verify_rulesets.py", "python3 scripts/verify_configs.py")
        problems = guard.workflow_problems(text)
        self.assertTrue(any("missing required verification step" in problem for problem in problems), problems)

    def test_wrong_branch_fails(self):
        text = self._workflow().replace("      - main\n", "      - develop\n")
        problems = guard.workflow_problems(text)
        self.assertTrue(any("main branch" in problem for problem in problems), problems)


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
    """C6 portability: the guard must follow the caller's icon checkout and never
    embed or guess a machine-specific absolute path."""

    def test_caller_supplied_path_is_honored(self):
        self.assertEqual(
            guard.icon_repo_path({"MIHOMO_ICON_REPO": "/tmp/any/icons"}),
            Path("/tmp/any/icons"),
        )

    def test_never_guesses_an_absolute_machine_path(self):
        resolved = guard.icon_repo_path({})
        self.assertTrue(
            resolved is None or resolved == ROOT / "Oasisic-Icons",
            f"invented a machine path: {resolved!r}",
        )

    def test_helper_source_has_no_machine_specific_default(self):
        source = Path(guard.__file__ or "").read_text(encoding="utf-8")
        for marker in ("/opt/" "data", "/home/" "runner"):
            self.assertNotIn(marker, source)

    def test_missing_checkout_is_surfaced_as_unavailable(self):
        problems = guard.pinned_checkout_problems(Path("/definitely/missing"))
        self.assertTrue(any("not available" in problem for problem in problems), problems)


if __name__ == "__main__":
    unittest.main()
