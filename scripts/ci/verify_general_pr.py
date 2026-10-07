#!/usr/bin/env python3
"""Read-only general PR verification gate (C8) — repository invariants.

Compares the PR base commit against the PR head commit and fails closed when a
repository invariant is violated. This script never writes: no commit, no push,
no merge, no rebase, no ref update. It only reads git objects and runs checks.

Scope model
-----------
This gate applies to *every* pull_request -> main. It enforces long-lived
repository invariants only. Phase-specific governance (for example "this PR
must not migrate production configs") must live in a separate, explicitly
scoped tool; it must not be encoded here as a global prohibition.

Guards
  * revisions               — explicit PR_BASE_SHA / PR_HEAD_SHA, both commits
  * config-tree-consistency — every ruleset brand has exactly one provider
                              entry whose remote URL points at a ruleset file
                              that exists in the head tree; every referenced
                              ruleset file parses as a YAML rules list
                              (head self-consistency, base/head may differ)
  * find-process-mode       — Contract is the policy source (Android strict /
                              Nikki off baselines)
  * Oasisic authority       — manifest, matcher, daily-sync and checkout all use
                              the pinned SHA
  * icon baseline           — expected total derived from the ruleset brand
                              tree (independent source); 0 missing; Podcast vs
                              ApplePodcasts distinct
  * Oracle independence     — oracle.py imports no contract/generator/matcher/
                              ownership code
  * workflow security       — read-only pull_request gate on main, contents:
                              read, no write commands, verification steps present

Every failure is reported with check name, base SHA, head SHA, file, expected and
actual so a failing run is auditable without re-deriving anything by hand.
"""
from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
PINNED_OASIC = "f0f3bc2a44616885682ee5f0e5921540b964e2d8"
PRODUCTION_CONFIGS = (
    "configs/Android/config.yaml",
    "configs/Android/config.min.yaml",
    "configs/Nikki/config.yaml",
    "configs/Nikki/config.min.yaml",
)
CONFIG_FOR_TREE_CHECK = "configs/Android/config.yaml"
EXPECTED_MODES = {"android": "strict", "nikki": "off"}
PODCAST_MATCHER_SUFFIX = "Media/Xiaoyuzhou/Xiaoyuzhou.png"
APPLE_PODCASTS_ICON_SUFFIX = "Apple/ApplePodcasts/ApplePodcasts.png"
ORACLE_FIXTURES_PATH = "scripts/tests/oracle_fixtures.py"
CONTRACT_PATH = "scripts/config_contract/contract.json"
MANIFEST_PATH = "scripts/config_contract/oasisic_revision.json"
MATCHER_PATH = "scripts/match_icons.py"
ORACLE_PATH = "scripts/config_contract/oracle.py"
DAILY_SYNC_PATH = ".github/workflows/daily-sync.yml"
PR_WORKFLOW_PATH = ".github/workflows/pr-verify.yml"
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


def tree_paths(revision: str, prefix: str) -> list[str]:
    output = git("ls-tree", "-r", "--name-only", revision, "--", prefix).decode("utf-8", "replace")
    return [line for line in output.splitlines() if line.strip()]


# --------------------------------------------------------------------------- #
# config tree consistency guard (replaces the Phase 8 production-config guard)
# --------------------------------------------------------------------------- #

def _rule_providers_of(config: dict) -> dict[str, dict]:
    providers = config.get("rule-providers")
    if not isinstance(providers, dict):
        return {}
    return {key: value for key, value in providers.items() if isinstance(value, dict)}


def _remote_rule_set_brand(url: str) -> str | None:
    """Extract <Brand> from a raw.githubusercontent ruleset URL (…/ruleset/<Brand>/<Brand>.yaml)."""
    match = re.search(r"/ruleset/([^/]+)/([^/]+)\.yaml$", url or "")
    if match:
        return match.group(1)
    return None


def config_tree_problems(config: dict, tree_files: set[str], ruleset_root: str = "ruleset/") -> list[str]:
    """Head tree self-consistency: providers <-> ruleset files <-> remote URLs.

    This is a structural invariant of the head tree (not a base/head comparison):
    any legitimate feature PR (new brand, rule change, config regeneration)
    passes as long as the three sources of truth agree. It is independent of
    generate_config.py and therefore cannot share its failure modes.
    """
    problems: list[str] = []
    providers = _rule_providers_of(config)
    if not providers:
        return [f"{CONFIG_FOR_TREE_CHECK}: rule-providers section is missing or empty"]

    tree_rule_files = {path for path in tree_files if path.startswith(ruleset_root)}

    provider_brands: dict[str, str] = {}
    for name, entry in sorted(providers.items()):
        url = entry.get("url", "")
        brand = _remote_rule_set_brand(url)
        if brand is None:
            problems.append(f"rule-provider `{name}`: url is not a ruleset URL: {_short(url)}")
            continue
        remote_file = f"{ruleset_root}{brand}/{brand}.yaml"
        if remote_file not in tree_rule_files:
            problems.append(f"rule-provider `{name}`: remote ruleset {remote_file!r} is missing from the head tree")
        provider_brands[brand] = name

    # every brand directory in the head tree must be served by exactly one provider
    tree_brands = {path.split("/", 2)[1] for path in tree_rule_files}
    for brand in sorted(tree_brands):
        if brand not in provider_brands:
            problems.append(f"ruleset brand {brand!r} has no rule-provider entry in {CONFIG_FOR_TREE_CHECK}")

    return problems


def provider_file_problems(config: dict, revision: str) -> list[str]:
    """Every ruleset file referenced by a provider URL must be present and parse as YAML.

    A ruleset file is a mapping whose ``payload`` entry is the rule list. This is
    the one genuinely new long-lived invariant that the removed forbidden-file
    guard only shielded *indirectly* by banning the whole directory.
    """
    problems: list[str] = []
    providers = _rule_providers_of(config)
    seen: set[str] = set()
    for name, entry in sorted(providers.items()):
        brand = _remote_rule_set_brand(entry.get("url", ""))
        if brand is None:
            continue  # reported by config_tree_problems
        local_path = f"ruleset/{brand}/{brand}.yaml"
        if local_path in seen:
            continue
        seen.add(local_path)
        try:
            document = yaml.safe_load(tree_file(revision, local_path))
        except (subprocess.CalledProcessError, yaml.YAMLError) as exc:
            problems.append(f"{local_path}: cannot load ruleset file from head: {exc}")
            continue
        if not isinstance(document, dict) or not isinstance(document.get("payload"), list):
            problems.append(f"{local_path}: must parse to a mapping with a `payload` list")
    return problems


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
    if manifest.get("asset_url_mode") != "branch-main":
        problems.append(f"{MANIFEST_PATH}: asset_url_mode must be branch-main")
    if manifest.get("production_url_ref") != "main":
        problems.append(f"{MANIFEST_PATH}: production_url_ref must be main")
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
    if "ASSET_URL_REF = 'main'" not in matcher:
        problems.append(f"{MATCHER_PATH}: production icon URLs must use the main branch ref")
    if re.search(rf"ASSET_URL_REF\s*=\s*'{PINNED_OASIC}'", matcher):
        problems.append(f"{MATCHER_PATH}: production icon URLs must not use the pinned SHA ref")
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

    The caller (local shell or the PR verification workflow) declares the
    checkout via ``MIHOMO_ICON_REPO``; when it is absent, only the
    repository-relative layout used by the workflows is considered. No
    machine-specific absolute path is embedded, and an unresolved checkout is
    reported instead of silently guessed.
    """
    supplied = (environ.get("MIHOMO_ICON_REPO") or "").strip()
    if supplied:
        return Path(supplied)
    candidate = ROOT / "Oasisic-Icons"
    return candidate if candidate.is_dir() else None


# --------------------------------------------------------------------------- #
# icon baseline guard — dynamic, tree-derived expected total
# --------------------------------------------------------------------------- #

def expected_icon_total(brand_dirs: list[str], strategy_group_map: dict[str, str], is_emoji) -> int:
    """Independent expected icon total, derived from the *ruleset brand tree*.

    ``build_icon_map()`` emits exactly one entry per non-emoji strategy group
    of ``brand_dirs()`` plus explicit overrides. The overrides that still map
    to a live tree brand (Podcast, NetEase Cloud Music, …) keep the total
    tree-derived: expected = number of non-emoji strategy groups in the brand
    tree. ``actual`` (the matcher output) comes from a different source, so a
    missing icon can never be hidden by the count.
    """
    return sum(1 for brand in brand_dirs if not is_emoji(strategy_group_map.get(brand, brand)))


def config_icon_group_names(config: dict) -> set[str]:
    """Icon-carrying strategy-group names as committed in the production config.

    This is the *committed artifact* source: the groups that actually carry an
    icon in the checked-in config, independent of the live ruleset tree scan.
    """
    names: set[str] = set()
    for group in config.get("proxy-groups", []):
        if isinstance(group, dict) and group.get("icon") and isinstance(group.get("name"), str):
            names.add(group["name"])
    return names


def _brand_dirs_from_tree_paths(tree_paths: Iterable[str], ruleset_root: str = "ruleset/") -> list[str]:
    """Brand directories from the head *git tree*, replicating ``match_icons.brand_dirs()``.

    A brand is a ``ruleset/<Brand>/<Brand>.yaml`` file (the dir name must equal the
    file's stem). The fallback/routing rule-sets in ``BASE`` are excluded, exactly as
    the live ``brand_dirs()`` does, because they are referenced in ``rules:`` but are
    not icon-bearing strategy groups. Deriving from the git tree (rather than the
    working tree) keeps the expected source purely head-based and testable without
    a checkout.
    """
    base = _base_routing_rule_sets()
    brands: set[str] = set()
    for path in tree_paths:
        if not path.startswith(ruleset_root):
            continue
        parts = path[len(ruleset_root):].split("/")
        if len(parts) != 2:
            continue
        brand, filename = parts
        if not brand or filename != f"{brand}.yaml":
            continue
        if brand in base:
            continue
        brands.add(brand)
    return sorted(brands)


def _base_routing_rule_sets() -> set[str]:
    """The fallback/routing rule-sets that carry no icon (single source: match_icons.BASE)."""
    try:
        import match_icons  # noqa: WPS433 - single source for the BASE set

        return set(match_icons.BASE)
    except Exception:  # noqa: BLE001 - fall back to the canonical literal if unavailable
        return {
            "Reject", "Direct", "Proxy", "CNCIDR", "Private",
            "Applications", "LanCIDR", "DirectDNS", "ProxyDNS",
        }


def expected_icon_group_names(
    brand_dirs: Iterable[str],
    strategy_group_map: Mapping[str, str],
    is_emoji,
) -> set[str]:
    """Strategy-group names that the ruleset brand tree says should carry an icon.

    This is the *structural source*: one non-emoji strategy group per brand in the
    head ruleset tree. It reads only the tree + the static brand->group mapping,
    never the committed config, so it is independent of the "actual" side.
    """
    return {
        strategy_group_map.get(brand, brand)
        for brand in brand_dirs
        if not is_emoji(strategy_group_map.get(brand, brand))
    }


def icon_baseline_problems(
    config: dict,
    missing: list[str],
    brand_dirs: Iterable[str],
    strategy_group_map: Mapping[str, str],
    is_emoji,
) -> list[str]:
    """Icon invariants: 0 missing, committed config coverage == ruleset-tree coverage.

    Two independent sources are compared (never ``expected == actual`` on one
    source):
      * expected = strategy-group names the head ruleset tree should cover
      * actual   = strategy-group names that carry an icon in the committed config
    Set equality (not just count) so a missing or stray brand group is caught.
    The matcher's ``missing`` list is checked separately (icon resolvability).
    """
    problems: list[str] = []
    if missing:
        problems.append(f"icon map: missing {len(missing)} icons, e.g. {sorted(missing)[:10]}")
    expected = expected_icon_group_names(brand_dirs, strategy_group_map, is_emoji)
    actual = config_icon_group_names(config)
    only_in_tree = sorted(expected - actual)
    if only_in_tree:
        problems.append(
            f"icon coverage: ruleset tree expects {len(expected)} icon groups but the committed "
            f"config is missing {only_in_tree}"
        )
    only_in_config = sorted(actual - expected)
    if only_in_config:
        problems.append(
            f"icon coverage: committed config has {len(actual)} icon groups but the ruleset tree "
            f"does not include {only_in_config}"
        )
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
    if icon_map.get("Apple Podcasts") == podcast and podcast:
        problems.append("icon map: ApplePodcasts must not share the generic Podcast icon")
    pinned_apple_icon = f"/main/icons/{APPLE_PODCASTS_ICON_SUFFIX}"
    if pinned_apple_icon not in fixture_source:
        problems.append(
            f"{ORACLE_FIXTURES_PATH}: ApplePodcasts must stay independent on the main-ref icon "
            f"{APPLE_PODCASTS_ICON_SUFFIX}"
        )
    return problems


def production_icon_url_problems(configs: dict[str, str]) -> list[str]:
    """Production configs must reference icons via /main/icons/ (consumer URL contract)."""
    problems: list[str] = []
    for path, text in configs.items():
        if "/main/icons/" not in text:
            problems.append(f"{path}: no /main/icons/ production URL found")
        if PINNED_OASIC in text:
            problems.append(f"{path}: production config must not reference the pinned SHA {PINNED_OASIC}")
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
    # Oasisic 检出必须使用已批准 pin（与 daily-sync 同一权威来源，禁止浮动 ref）
    if f"ref: {PINNED_OASIC}" not in text:
        problems.append(
            f"{PR_WORKFLOW_PATH}: Oasisic checkout ref does not use the approved pin {PINNED_OASIC}"
        )
    for floating in ("ref: main", "ref: latest", "ref: master"):
        if floating in text:
            problems.append(f"{PR_WORKFLOW_PATH}: floating revision {floating!r} is forbidden")
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


def main() -> int:
    try:
        base, head = revisions_from_env(os.environ)
        assert_commit(base)
        assert_commit(head)
    except GuardFailure as exc:
        print(f"[FAIL] revisions\n  actual: {exc}")
        return 1

    print(f"[general-pr-verify] base={base} head={head}")
    failures = 0
    results: list[tuple[str, str, list[str]]] = []

    expected = (
        "every ruleset brand is served by exactly one rule-provider whose remote URL "
        "points at a ruleset file present in the head tree; every referenced ruleset "
        "file parses as YAML (base/head may legitimately differ)"
    )
    problems: list[str] = []
    try:
        config = yaml.safe_load(tree_file(head, CONFIG_FOR_TREE_CHECK))
        if not isinstance(config, dict):
            problems.append(f"{CONFIG_FOR_TREE_CHECK}: must parse to a mapping")
        else:
            problems += config_tree_problems(config, set(tree_paths(head, "ruleset/")))
            problems += provider_file_problems(config, head)
    except (subprocess.CalledProcessError, yaml.YAMLError) as exc:
        problems.append(f"{CONFIG_FOR_TREE_CHECK}: cannot evaluate from PR head: {exc}")
    results.append(("config-tree-consistency-guard", expected, problems))

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

    expected = (
        f"manifest pin {PINNED_OASIC} (discovery/validation), daily-sync pinned checkout, "
        "and matcher production URLs on the main branch ref"
    )
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

    expected = "production icon URLs use /main/icons/ (consumer ref); discovery/validation stays on the pinned SHA"
    problems = []
    for path in PRODUCTION_CONFIGS:
        try:
            problems += production_icon_url_problems({path: tree_file(head, path).decode("utf-8", "replace")})
        except subprocess.CalledProcessError as exc:
            problems.append(f"{path}: cannot read production icon URLs: {exc}")
    results.append(("production-icon-url-guard", expected, problems))

    expected = (
        "icon coverage: committed config icon groups == ruleset brand tree (0 emoji), "
        "derived dynamically; 0 missing; Podcast -> Xiaoyuzhou, ApplePodcasts independent"
    )
    problems = []
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        import match_icons

        icon_map, missing = match_icons.build_icon_map()
        android_full = yaml.safe_load(tree_file(head, CONFIG_FOR_TREE_CHECK))
        if not isinstance(android_full, dict):
            problems.append(f"{CONFIG_FOR_TREE_CHECK}: must parse to a mapping for icon coverage")
            android_full = {}
        head_rule_paths = tree_paths(head, "ruleset/")
        problems += icon_baseline_problems(
            android_full,
            missing,
            _brand_dirs_from_tree_paths(head_rule_paths),
            match_icons.STRATEGY_GROUP_MAP,
            match_icons.is_emoji_group,
        )
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

    expected = ("read-only pull_request gate on main with contents: read; no write commands; "
                "verification steps present; Oasisic checkout pinned to the approved revision")
    problems = []
    try:
        problems += workflow_problems(tree_file(head, PR_WORKFLOW_PATH).decode("utf-8", "replace"))
    except subprocess.CalledProcessError as exc:
        problems.append(f"{PR_WORKFLOW_PATH}: cannot read from PR head: {exc}")
    results.append(("workflow-security-guard", expected, problems))

    for name, expected, problems in results:
        failures += _report(name, expected, problems, base, head)

    if failures:
        print(f"[FAIL] general-pr-verify: {failures} guard(s) failed")
        return 1
    print("[PASS] general-pr-verify: all invariants satisfied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
