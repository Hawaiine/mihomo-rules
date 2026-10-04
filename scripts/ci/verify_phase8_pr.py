#!/usr/bin/env python3
"""Read-only Phase 8 PR guards (C8).

Compares the PR base commit against the PR head commit and fails closed when a
Phase 8 invariant is violated. This script never writes: no commit, no push,
no merge, no rebase, no ref update. It only reads git objects and runs checks.

Guards:
  * production-config guard  — Android full: YAML semantics equal + comment/blank-only text delta;
                               Android min / Nikki full / Nikki min: byte-identical
  * find-process-mode guard  — Contract is the policy source (Android strict / Nikki off baselines)
  * Oasisic authority guard  — manifest, matcher, daily-sync and checkout all use the pinned SHA
  * icon baseline guard      — 143/143 matched, 0 missing, Podcast vs ApplePodcasts distinct
  * Oracle independence guard— oracle.py imports no contract/generator/matcher/ownership code

Every failure is reported with check name, base SHA, head SHA, file, expected and
actual so a failing run is auditable without re-deriving anything by hand.
"""
from __future__ import annotations

import ast
import difflib
import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
PINNED_OASIC = "f0f3bc2a44616885682ee5f0e5921540b964e2d8"
ANDROID_FULL = "configs/Android/config.yaml"
BYTE_IDENTICAL_CONFIGS = (
    "configs/Android/config.min.yaml",
    "configs/Nikki/config.yaml",
    "configs/Nikki/config.min.yaml",
)
PRODUCTION_CONFIGS = (ANDROID_FULL, *BYTE_IDENTICAL_CONFIGS)
EXPECTED_MODES = {"android": "strict", "nikki": "off"}
EXPECTED_ICON_TOTAL = 143
PODCAST_MATCHER_SUFFIX = "Media/Xiaoyuzhou/Xiaoyuzhou.png"
APPLE_PODCASTS_ICON_SUFFIX = "Apple/ApplePodcasts/ApplePodcasts.png"
ORACLE_FIXTURES_PATH = "scripts/tests/oracle_fixtures.py"
CONTRACT_PATH = "scripts/config_contract/contract.json"
MANIFEST_PATH = "scripts/config_contract/oasisic_revision.json"
MATCHER_PATH = "scripts/match_icons.py"
ORACLE_PATH = "scripts/config_contract/oracle.py"
DAILY_SYNC_PATH = ".github/workflows/daily-sync.yml"
PR_WORKFLOW_PATH = ".github/workflows/pr-verify.yml"
FORBIDDEN_DIFF_PREFIXES = ("ruleset/", "providers/")
ORACLE_FORBIDDEN_IMPORTS = {"contract", "generate_config", "match_icons", "ownership_map", "resolve_ownership"}
WORKFLOW_FORBIDDEN_COMMANDS = ("git commit", "git push", "git merge", "git rebase", "git reset", "git add")
WORKFLOW_REQUIRED_SCRIPTS = (
    "scripts/verify_configs.py",
    "scripts/verify_rulesets.py",
    "scripts/readme_stats.py --check",
    "python3 -m unittest discover -s scripts/tests",
)
# Only the bare environment variable is forbidden; `_OASIC_REVISION` is the manifest-backed name.
ENV_REVISION_FALLBACK = re.compile(r"(?<![A-Za-z0-9_])OASIC_REVISION(?![A-Za-z0-9_])")


class GuardFailure(Exception):
    """Raised when a pre-condition (revision, git object) cannot be verified."""


def normalize_mode(value: object) -> object:
    """PyYAML 1.1 renders the unquoted `off` scalar as False; the project normalizes only that."""
    return "off" if value is False else value


def _short(value: object, limit: int = 160) -> str:
    text = repr(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=ROOT)


def tree_file(revision: str, path: str) -> bytes:
    return git("show", f"{revision}:{path}")


# --------------------------------------------------------------------------- #
# production config guard
# --------------------------------------------------------------------------- #

def textual_delta_lines(base_text: str, head_text: str) -> list[str]:
    """Added/removed lines that are neither comments nor blank lines."""
    offenders: list[str] = []
    diff = difflib.unified_diff(base_text.splitlines(), head_text.splitlines(), lineterm="")
    for line in diff:
        if line.startswith(("+++", "---")) or not line.startswith(("+", "-")):
            continue
        payload = line[1:]
        if not payload.strip():
            continue
        if payload.lstrip().startswith("#"):
            continue
        offenders.append(payload)
    return offenders


def _load_yaml(text: str, label: str, problems: list[str]):
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        problems.append(f"{label}: unparseable YAML: {exc}")
        return None


def android_full_problems(base_text: str, head_text: str) -> list[str]:
    """Android full config: semantic equality plus comment/blank-only text delta."""
    problems: list[str] = []
    base_doc = _load_yaml(base_text, ANDROID_FULL, problems)
    head_doc = _load_yaml(head_text, ANDROID_FULL, problems)
    if base_doc is not None and head_doc is not None and base_doc != head_doc:
        for key in sorted(set(base_doc) | set(head_doc), key=str):
            before, after = base_doc.get(key), head_doc.get(key)
            if before != after:
                if key == "find-process-mode":
                    before, after = normalize_mode(before), normalize_mode(after)
                problems.append(
                    f"{ANDROID_FULL}: semantic difference in `{key}`: {_short(before)} -> {_short(after)}"
                )
        if not problems:
            problems.append(f"{ANDROID_FULL}: YAML functional semantics differ from PR base")
    for line in textual_delta_lines(base_text, head_text):
        problems.append(f"{ANDROID_FULL}: non-comment textual change: {line.strip()!r}")
    return problems


def config_problems(path: str, base_bytes: bytes, head_bytes: bytes) -> list[str]:
    if path == ANDROID_FULL:
        return android_full_problems(base_bytes.decode("utf-8"), head_bytes.decode("utf-8"))
    if base_bytes != head_bytes:
        return [
            f"{path}: must be byte-identical between PR base and head; "
            "only configs/Android/config.yaml documentation comments may change"
        ]
    return []


# --------------------------------------------------------------------------- #
# find-process-mode guard (Contract is the policy source)
# --------------------------------------------------------------------------- #

def find_process_mode_problems(contract: dict) -> list[str]:
    problems: list[str] = []
    for platform, expected in EXPECTED_MODES.items():
        entry = contract["platforms"][platform].get("find-process-mode")
        if not isinstance(entry, dict):
            problems.append(f"contract: {platform} find-process-mode must record baseline metadata")
            continue
        if entry.get("value") != expected:
            problems.append(
                f"contract: {platform} find-process-mode does not match approved current baseline "
                f"{expected!r} (actual {entry.get('value')!r})"
            )
        if entry.get("status") != "APPROVED_CURRENT_BASELINE":
            problems.append(
                f"contract: {platform} find-process-mode status {entry.get('status')!r} "
                "is not APPROVED_CURRENT_BASELINE"
            )
        if not entry.get("official_semantics"):
            problems.append(f"contract: {platform} find-process-mode official semantics are missing")
    semantics = contract["platforms"]["android"]["find-process-mode"].get("official_semantics", "").lower()
    if "default" not in semantics:
        problems.append("contract: Android strict semantics must describe the default mode")
    if "all processes" in semantics or "always" in semantics:
        problems.append("contract: Android strict must not be described as always/all-process matching")
    return problems


def production_mode_problems(path: str, config: dict) -> list[str]:
    platform = "android" if "/Android/" in f"/{path}" else "nikki"
    mode = normalize_mode(config.get("find-process-mode"))
    if mode != EXPECTED_MODES[platform]:
        return [
            f"{path}: find-process-mode does not match approved current baseline "
            f"{EXPECTED_MODES[platform]!r} (actual {mode!r})"
        ]
    active = [
        rule for rule in config.get("rules", [])
        if isinstance(rule, str) and rule.startswith("RULE-SET,Applications,")
    ]
    if (len(active) == 1) != (platform == "android"):
        return [f"{path}: Applications active-rule policy mismatch (found {len(active)} active rule(s))"]
    return []


# --------------------------------------------------------------------------- #
# Oasisic authority guard
# --------------------------------------------------------------------------- #

def oasisic_problems(manifest: dict, matcher: str, daily_sync: str) -> list[str]:
    problems: list[str] = []
    if manifest.get("revision") != PINNED_OASIC:
        problems.append(
            f"{MANIFEST_PATH}: revision {manifest.get('revision')!r} != approved pin {PINNED_OASIC}"
        )
    if manifest.get("asset_url_mode") != "commit-pinned":
        problems.append(f"{MANIFEST_PATH}: asset_url_mode must be commit-pinned")
    if f"ref: {PINNED_OASIC}" not in daily_sync:
        problems.append(f"{DAILY_SYNC_PATH}: Oasisic checkout ref does not use the approved pin")
    for floating in ("ref: main", "ref: latest", "ref: master"):
        if floating in daily_sync:
            problems.append(f"{DAILY_SYNC_PATH}: floating revision {floating!r} is forbidden")
    if ENV_REVISION_FALLBACK.search(matcher):
        problems.append(f"{MATCHER_PATH}: environment revision fallback OASIC_REVISION is forbidden")
    if "_REVISION_MANIFEST" not in matcher or "_OASIC_REVISION" not in matcher:
        problems.append(f"{MATCHER_PATH}: matcher must resolve its revision from the manifest")
    if "for ref in (_OASIC_REVISION,)" not in matcher:
        problems.append(f"{MATCHER_PATH}: git tree scan must use the pinned revision only")
    if "/main/icons" in matcher:
        problems.append(f"{MATCHER_PATH}: matcher must not fall back to the /main icon path")
    return problems


def pinned_checkout_problems(repo: Path | None, expected: str = PINNED_OASIC) -> list[str]:
    if repo is None or not (repo / ".git").exists():
        return [f"pinned Oasisic checkout not available: {repo}"]
    head = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    if head != expected:
        return [f"Oasisic checkout HEAD {head} != approved pin {expected}"]
    return []


def icon_repo_path(environ: Mapping[str, str]) -> Path | None:
    """Resolve the pinned Oasisic checkout from the environment that owns it.

    The caller (local shell or the Phase 8 PR workflow) declares the checkout via
    ``MIHOMO_ICON_REPO``; when it is absent, only the repository-relative layout
    used by the workflows is considered. No machine-specific absolute path is
    embedded, and an unresolved checkout is reported instead of silently guessed.
    """
    supplied = (environ.get("MIHOMO_ICON_REPO") or "").strip()
    if supplied:
        return Path(supplied)
    candidate = ROOT / "Oasisic-Icons"
    return candidate if candidate.is_dir() else None


# --------------------------------------------------------------------------- #
# icon baseline / Podcast guard
# --------------------------------------------------------------------------- #

def icon_baseline_problems(icon_map: dict[str, str], missing: list[str]) -> list[str]:
    problems: list[str] = []
    if missing:
        problems.append(f"icon map: missing {len(missing)} icons, e.g. {sorted(missing)[:10]}")
    if len(icon_map) != EXPECTED_ICON_TOTAL:
        problems.append(f"icon map: {len(icon_map)} matched, expected {EXPECTED_ICON_TOTAL}")
    return problems


def podcast_problems(icon_map: dict[str, str], fixture_source: str) -> list[str]:
    """Generic Podcast routes to Xiaoyuzhou; ApplePodcasts stays independent on its own pinned icon."""
    problems: list[str] = []
    podcast = icon_map.get("Podcast", "")
    if not podcast:
        problems.append("icon map: `Podcast` is missing from the matcher output")
    elif not podcast.endswith(PODCAST_MATCHER_SUFFIX):
        problems.append(f"icon map: `Podcast` routes to {podcast} (expected .../{PODCAST_MATCHER_SUFFIX})")
    if podcast.endswith(APPLE_PODCASTS_ICON_SUFFIX):
        problems.append("icon map: generic Podcast must not use the ApplePodcasts icon")
    if icon_map.get("ApplePodcasts") == podcast and podcast:
        problems.append("icon map: ApplePodcasts must not share the generic Podcast icon")
    pinned_apple_icon = f"/{PINNED_OASIC}/icons/{APPLE_PODCASTS_ICON_SUFFIX}"
    if pinned_apple_icon not in fixture_source:
        problems.append(
            f"{ORACLE_FIXTURES_PATH}: ApplePodcasts must stay independent on the pinned icon "
            f"{APPLE_PODCASTS_ICON_SUFFIX}"
        )
    return problems


# --------------------------------------------------------------------------- #
# Oracle independence guard
# --------------------------------------------------------------------------- #

def oracle_independence_problems(source: str) -> list[str]:
    try:
        parsed = ast.parse(source)
    except SyntaxError as exc:
        return [f"{ORACLE_PATH}: unparseable source: {exc}"]
    imported: set[str] = set()
    for node in ast.walk(parsed):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".", 1)[0])
    offending = sorted(imported & ORACLE_FORBIDDEN_IMPORTS)
    if offending:
        return [f"{ORACLE_PATH}: prohibited imports {offending}"]
    return []


# --------------------------------------------------------------------------- #
# workflow static guard
# --------------------------------------------------------------------------- #

def workflow_problems(text: str) -> list[str]:
    problems: list[str] = []
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return [f"{PR_WORKFLOW_PATH}: unparseable YAML: {exc}"]
    if not isinstance(document, dict):
        return [f"{PR_WORKFLOW_PATH}: workflow root must be a mapping"]
    # PyYAML parses the bare `on` key as boolean True.
    triggers = document.get("on", document.get(True))
    if not isinstance(triggers, dict) or "pull_request" not in triggers:
        problems.append(f"{PR_WORKFLOW_PATH}: must trigger on pull_request")
    else:
        branches = (triggers["pull_request"] or {}).get("branches", [])
        if "main" not in branches:
            problems.append(f"{PR_WORKFLOW_PATH}: pull_request must target the main branch")
    if "pull_request_target" in triggers if isinstance(triggers, dict) else False:
        problems.append(f"{PR_WORKFLOW_PATH}: pull_request_target is forbidden")
    permissions = document.get("permissions")
    if permissions != {"contents": "read"}:
        problems.append(f"{PR_WORKFLOW_PATH}: permissions must be exactly contents: read (actual {permissions!r})")
    if "contents: write" in text:
        problems.append(f"{PR_WORKFLOW_PATH}: write token is forbidden in the read-only PR gate")
    for command in WORKFLOW_FORBIDDEN_COMMANDS:
        if command in text:
            problems.append(f"{PR_WORKFLOW_PATH}: forbidden write command {command!r}")
    for required in WORKFLOW_REQUIRED_SCRIPTS:
        if required not in text:
            problems.append(f"{PR_WORKFLOW_PATH}: missing required verification step {required!r}")
    return problems


# --------------------------------------------------------------------------- #
# runner
# --------------------------------------------------------------------------- #

def revisions_from_env(env: Mapping[str, str]) -> tuple[str, str]:
    base = (env.get("PR_BASE_SHA") or "").strip()
    head = (env.get("PR_HEAD_SHA") or "").strip()
    if not base or not head:
        raise GuardFailure("PR_BASE_SHA and PR_HEAD_SHA are required (no origin/main fallback)")
    return base, head


def assert_commit(revision: str) -> None:
    try:
        git("cat-file", "-e", f"{revision}^{{commit}}")
    except subprocess.CalledProcessError as exc:
        raise GuardFailure(f"not a commit in this checkout: {revision}") from exc


def _report(name: str, expected: str, problems: list[str], base: str, head: str) -> int:
    if not problems:
        print(f"[PASS] {name}")
        return 0
    print(f"[FAIL] {name}")
    print(f"  base: {base}")
    print(f"  head: {head}")
    print(f"  expected: {expected}")
    for problem in problems:
        print(f"  actual: {problem}")
    return 1


def changed_paths(base: str, head: str) -> list[str]:
    output = git("diff", "--name-only", base, head).decode("utf-8", "replace")
    return [line for line in output.splitlines() if line.strip()]


def main() -> int:
    try:
        base, head = revisions_from_env(os.environ)
        assert_commit(base)
        assert_commit(head)
    except GuardFailure as exc:
        print(f"[FAIL] revisions\n  actual: {exc}")
        return 1

    print(f"[phase8-pr-verify] base={base} head={head}")
    failures = 0
    results: list[tuple[str, str, list[str]]] = []

    expected = "Android full config: YAML semantics equal to base and only comment/blank-line text changes; other production configs byte-identical"
    problems: list[str] = []
    for path in PRODUCTION_CONFIGS:
        try:
            problems += config_problems(path, tree_file(base, path), tree_file(head, path))
        except subprocess.CalledProcessError as exc:
            problems.append(f"{path}: cannot read from base/head: {exc}")
    results.append(("production-config-guard", expected, problems))

    expected = "Android strict / Nikki off are APPROVED_CURRENT_BASELINE in Contract and in all four production configs"
    problems = []
    try:
        contract = json.loads(tree_file(head, CONTRACT_PATH))
        problems += find_process_mode_problems(contract)
    except (subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        problems.append(f"{CONTRACT_PATH}: cannot load from PR head: {exc}")
    for path in PRODUCTION_CONFIGS:
        try:
            problems += production_mode_problems(path, yaml.safe_load(tree_file(head, path)))
        except (subprocess.CalledProcessError, yaml.YAMLError) as exc:
            problems.append(f"{path}: cannot evaluate find-process-mode: {exc}")
    results.append(("find-process-mode-guard", expected, problems))

    expected = f"manifest, matcher, daily-sync and the pinned checkout all use {PINNED_OASIC}"
    problems = []
    try:
        manifest = json.loads(tree_file(head, MANIFEST_PATH))
        problems += oasisic_problems(
            manifest,
            tree_file(head, MATCHER_PATH).decode("utf-8", "replace"),
            tree_file(head, DAILY_SYNC_PATH).decode("utf-8", "replace"),
        )
    except (subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        problems.append(f"{MANIFEST_PATH}: cannot load from PR head: {exc}")
    icon_repo = icon_repo_path(os.environ)
    problems += pinned_checkout_problems(icon_repo)
    results.append(("oasisic-authority-guard", expected, problems))

    expected = f"matcher reports {EXPECTED_ICON_TOTAL} icons with 0 missing; Podcast -> Xiaoyuzhou, ApplePodcasts independent"
    problems = []
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        import match_icons

        icon_map, missing = match_icons.build_icon_map()
        problems += icon_baseline_problems(icon_map, missing)
        problems += podcast_problems(
            icon_map, tree_file(head, ORACLE_FIXTURES_PATH).decode("utf-8", "replace")
        )
    except Exception as exc:  # noqa: BLE001 - report any matcher failure verbatim
        problems.append(f"icon matcher could not be evaluated: {exc!r}")
    results.append(("icon-baseline-guard", expected, problems))

    expected = "oracle.py imports no contract/generator/matcher/ownership code"
    problems = []
    try:
        problems += oracle_independence_problems(tree_file(head, ORACLE_PATH).decode("utf-8", "replace"))
    except subprocess.CalledProcessError as exc:
        problems.append(f"{ORACLE_PATH}: cannot read from PR head: {exc}")
    results.append(("oracle-independence-guard", expected, problems))

    expected = "read-only pull_request gate on main with contents: read; no write commands; verification steps present"
    problems = []
    try:
        problems += workflow_problems(tree_file(head, PR_WORKFLOW_PATH).decode("utf-8", "replace"))
    except subprocess.CalledProcessError as exc:
        problems.append(f"{PR_WORKFLOW_PATH}: cannot read from PR head: {exc}")
    results.append(("workflow-guard", expected, problems))

    expected = "PR diff must not touch ruleset/ or providers/ (Phase 7A / PR #15 history stays out of this PR)"
    problems = [
        f"forbidden path changed between PR base and head: {path}"
        for path in changed_paths(base, head)
        if path.startswith(FORBIDDEN_DIFF_PREFIXES)
    ]
    results.append(("forbidden-file-guard", expected, problems))

    for name, expected, problems in results:
        failures += _report(name, expected, problems, base, head)

    if failures:
        print(f"[FAIL] phase8-pr-verify: {failures} guard(s) failed")
        return 1
    print("[PASS] phase8-pr-verify: all guards satisfied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
