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
# 共享的 Oasisic 检出路径解析（与 scripts/match_icons.py 同一实现，避免两处优先级分叉）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from icon_repo import resolve_icon_repo  # noqa: E402

PINNED_OASIC = "f0f3bc2a44616885682ee5f0e5921540b964e2d8"
PRODUCTION_CONFIGS = (
    "configs/Android/config.yaml",
    "configs/Android/config.min.yaml",
    "configs/Nikki/config.yaml",
    "configs/Nikki/config.min.yaml",
)
CONFIG_FOR_TREE_CHECK = "configs/Android/config.yaml"
# 规范规则集 URL 基址。与 scripts/generate_config.py 的 GITHUB_BASE 是同一契约：
# guard 独立声明该常量（不导入生成器，避免与它共享失效模式），两者的一致性由
# scripts/tests/test_general_pr_guard.py::RulesetUrlContractTest 锁定。
RULESET_URL_BASE = "https://raw.githubusercontent.com/Hawaiine/mihomo-rules/main"
_RULESET_URL_RE = re.compile(
    "^" + re.escape(RULESET_URL_BASE) + r"/ruleset/([^/]+)/([^/]+)\.yaml$")
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


def ruleset_url_for(provider_key: str) -> str:
    """生成器契约中 provider key 唯一合法的规则集 URL。"""
    return f"{RULESET_URL_BASE}/ruleset/{provider_key}/{provider_key}.yaml"


def _remote_rule_set_brand(url: str) -> str | None:
    """Extract <Brand> from a *canonical* ruleset URL (…/ruleset/<Brand>/<Brand>.yaml).

    必须同时满足：主机 + 仓库 owner/repo + ref 完全等于 ``RULESET_URL_BASE``，
    且目录名与文件名相同。任何主机/仓库/ref 漂移或目录文件名错配都返回 None——
    宽泛的后缀正则会把外部主机的同名路径当成合法规则集 URL。
    """
    match = _RULESET_URL_RE.match(url or "")
    if match and match.group(1) == match.group(2):
        return match.group(1)
    return None


def config_tree_problems(config: dict, tree_files: set[str], ruleset_root: str = "ruleset/",
                         label: str = CONFIG_FOR_TREE_CHECK) -> list[str]:
    """Head tree self-consistency: providers <-> ruleset files <-> remote URLs.

    This is a structural invariant of the head tree (not a base/head comparison):
    any legitimate feature PR (new brand, rule change, config regeneration)
    passes as long as the three sources of truth agree. It is independent of
    generate_config.py and therefore cannot share its failure modes.

    每个 provider 的 key、URL、path 与 head 树中的规则集文件必须指向**同一个**
    Technical ID：URL 必须精确等于 :func:`ruleset_url_for`，path 必须精确等于
    ``./ruleset/<key>.yaml``。只检查「差异路径是否在白名单内」不够——URL 指向
    另一个品牌（品牌互换）、主机/仓库/ref 漂移都必须失败。

    ``label`` 是被校验配置的路径，用于让四份配置各自的失败信息可区分。
    """
    problems: list[str] = []
    providers = _rule_providers_of(config)
    if not providers:
        return [f"{label}: rule-providers section is missing or empty"]

    tree_rule_files = {path for path in tree_files if path.startswith(ruleset_root)}

    provider_keys: set[str] = set()
    for name, entry in sorted(providers.items()):
        url = entry.get("url", "")
        # URL 必须精确等于规范模板：主机 / 仓库路径 / ref / {ID}/{ID}.yaml 任一项
        # 漂移（含跨品牌映射、错误主机、错误 ref）都不能通过。
        expected_url = ruleset_url_for(name)
        if url != expected_url:
            problems.append(
                f"{label}: rule-provider `{name}`: url must be {expected_url!r}, "
                f"got {_short(url)}")
        # path 必须符合生成器约定 ./ruleset/<key>.yaml（URL 或 path 改错任一项都不能通过）
        expected_path = f"./ruleset/{name}.yaml"
        if entry.get("path") != expected_path:
            problems.append(
                f"{label}: rule-provider `{name}`: path must be {expected_path!r}, "
                f"got {_short(entry.get('path'))!r}")
        brand = _remote_rule_set_brand(url)
        if brand != name:
            # URL 已在上面报错；此处不登记该 provider，避免跨品牌映射冒充覆盖，
            # 把真正缺失的 provider 掩盖过去。
            continue
        remote_file = f"{ruleset_root}{brand}/{brand}.yaml"
        if remote_file not in tree_rule_files:
            problems.append(
                f"{label}: rule-provider `{name}`: remote ruleset {remote_file!r} is missing from the head tree")
        provider_keys.add(name)

    # every brand directory in the head tree must be served by exactly one provider
    tree_brands = {path.split("/", 2)[1] for path in tree_rule_files}
    for brand in sorted(tree_brands):
        if brand not in provider_keys:
            problems.append(f"{label}: ruleset brand {brand!r} has no rule-provider entry")

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
#
# 信任锚：``PINNED_OASIC`` 是本模块自带的 approved pin，**故意**与
# ``scripts/config_contract/oasisic_revision.json`` 分开存放。
#
# * manifest = 运行时的单一数据来源（matcher / workflow 都从它取值）；
# * 本常量   = 「revision 只能通过明确审阅流程更新」的独立门禁。
#
# 只改 manifest / contract / matcher / workflow 而不同步本常量，guard 必然失败；
# 反之要换 pin 就必须同时改到这里，从而在 PR 里显式可见、可独立审阅。
# 这正是不把 pin 折叠成单一字面量的原因。

_REVISION_SHA = re.compile(r"^[0-9a-f]{40}$")
_REPO_SLUG = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
_ASSET_URL_MODES = {"branch-main": "main", "commit-pinned": None}
_FLOATING_REFS = {"main", "master", "latest", "head", "develop", "dev", "trunk"}


def manifest_problems(manifest: dict) -> list[str]:
    """manifest 自身：revision 必须是完整 40 位 SHA 且等于 approved pin，且字段自洽。"""
    problems: list[str] = []
    revision = manifest.get("revision")
    if not isinstance(revision, str) or not _REVISION_SHA.match(revision):
        problems.append(f"{MANIFEST_PATH}: revision must be a full 40-hex SHA, got {_short(revision)}")
    elif revision != PINNED_OASIC:
        problems.append(f"{MANIFEST_PATH}: revision {revision!r} != approved pin {PINNED_OASIC}")
    mode = manifest.get("asset_url_mode")
    if mode not in _ASSET_URL_MODES:
        problems.append(
            f"{MANIFEST_PATH}: unknown asset_url_mode {_short(mode)}; allowed {sorted(_ASSET_URL_MODES)}")
    else:
        required = _ASSET_URL_MODES[mode]
        ref = manifest.get("production_url_ref")
        if required is not None and ref != required:
            problems.append(
                f"{MANIFEST_PATH}: asset_url_mode {mode!r} requires production_url_ref {required!r}, "
                f"got {_short(ref)}")
    repository = manifest.get("repository")
    if not isinstance(repository, str) or not _REPO_SLUG.match(repository):
        problems.append(f"{MANIFEST_PATH}: repository must be 'owner/name', got {_short(repository)}")
    return problems


def contract_icon_policy_problems(contract: dict, manifest: dict) -> list[str]:
    """contract.json 的 icon_policy 必须与 manifest 语义一致（解析真实 JSON 值）。"""
    policy = contract.get("icon_policy")
    if not isinstance(policy, dict):
        return [f"{CONTRACT_PATH}: icon_policy missing or not a mapping"]
    problems: list[str] = []
    if policy.get("manifest") != MANIFEST_PATH:
        problems.append(
            f"{CONTRACT_PATH}: icon_policy.manifest {_short(policy.get('manifest'))} != {MANIFEST_PATH}")
    if policy.get("url_mode") != manifest.get("asset_url_mode"):
        problems.append(
            f"{CONTRACT_PATH}: icon_policy.url_mode {_short(policy.get('url_mode'))} "
            f"!= manifest asset_url_mode {_short(manifest.get('asset_url_mode'))}")
    if policy.get("production_url_ref") != manifest.get("production_url_ref"):
        problems.append(
            f"{CONTRACT_PATH}: icon_policy.production_url_ref {_short(policy.get('production_url_ref'))} "
            f"!= manifest production_url_ref {_short(manifest.get('production_url_ref'))}")
    if policy.get("discovery_revision_role") != manifest.get("revision_role"):
        problems.append(
            f"{CONTRACT_PATH}: icon_policy.discovery_revision_role "
            f"{_short(policy.get('discovery_revision_role'))} "
            f"!= manifest revision_role {_short(manifest.get('revision_role'))}")
    # contract 里不得再写一份 revision：第二份副本必然会各自漂移
    for key, value in policy.items():
        if isinstance(value, str) and _REVISION_SHA.match(value.strip()):
            problems.append(
                f"{CONTRACT_PATH}: icon_policy.{key} duplicates a revision SHA; "
                f"{MANIFEST_PATH} is the only source")
    return problems


def oasisic_checkout_refs(workflow_text: str) -> list | None:
    """解析 workflow 中 Oasisic-Icons checkout 步骤声明的 ref（解析 YAML，不做子串匹配）。

    返回 ``None`` 表示 workflow 无法解析（fail-closed）。
    """
    try:
        doc = yaml.safe_load(workflow_text)
    except yaml.YAMLError:
        return None
    if not isinstance(doc, dict):
        return None
    jobs = doc.get("jobs")
    if not isinstance(jobs, dict):
        return None
    refs: list = []
    for job in jobs.values():
        if not isinstance(job, dict):
            continue
        for step in job.get("steps") or []:
            if not isinstance(step, dict):
                continue
            block = step.get("with")
            if not isinstance(block, dict):
                continue
            if str(block.get("repository", "")).lower().endswith("oasisic-icons"):
                refs.append(block.get("ref"))
    return refs


def checkout_ref_problems(workflow_text: str, path: str, revision: str) -> list[str]:
    """Oasisic checkout 必须使用 manifest 指定的固定 revision，不得 checkout 浮动 ref。"""
    refs = oasisic_checkout_refs(workflow_text)
    if refs is None:
        return [f"{path}: cannot parse workflow YAML to verify the Oasisic checkout ref"]
    if not refs:
        return [f"{path}: no Oasisic-Icons checkout step found"]
    problems: list[str] = []
    for ref in refs:
        text = "" if ref is None else str(ref).strip()
        if not text:
            problems.append(
                f"{path}: Oasisic checkout declares no ref (would float to the default branch)")
        elif text.lower() in _FLOATING_REFS:
            problems.append(
                f"{path}: floating Oasisic checkout ref {text!r} is forbidden; use the approved pin")
        elif text != revision:
            problems.append(f"{path}: Oasisic checkout ref {text!r} != approved pin {revision}")
    return problems


def matcher_source_problems(matcher: str) -> list[str]:
    """matcher 必须从 manifest 取 revision 与 URL ref，不得自带第二份运行时配置。"""
    problems: list[str] = []
    if ENV_REVISION_FALLBACK.search(matcher):
        problems.append(f"{MATCHER_PATH}: environment revision fallback OASIC_REVISION is forbidden")
    for symbol in ("_REVISION_MANIFEST", "_OASIC_REVISION", "asset_url_base"):
        if symbol not in matcher:
            problems.append(f"{MATCHER_PATH}: matcher must resolve {symbol} from the manifest")
    if "for ref in (_OASIC_REVISION,)" not in matcher:
        problems.append(f"{MATCHER_PATH}: git tree scan must use the pinned revision only")
    if re.search(r"raw\.githubusercontent\.com/[A-Za-z0-9._-]+/", matcher):
        problems.append(
            f"{MATCHER_PATH}: production icon URL base must be derived from the manifest, "
            "not hardcoded with a literal host/owner path")
    if re.search(r"ASSET_URL_REF\s*=\s*['\"]", matcher):
        problems.append(f"{MATCHER_PATH}: ASSET_URL_REF must be derived from the manifest, not a literal")
    return problems


def oasisic_problems(manifest: dict, matcher: str, daily_sync: str,
                     contract: dict | None = None, pr_workflow: str | None = None) -> list[str]:
    """Oasisic revision / URL 契约：解析真实值，不做字符串包含式判断。"""
    problems = manifest_problems(manifest)
    problems += matcher_source_problems(matcher)
    revision = manifest.get("revision")
    if isinstance(revision, str):
        problems += checkout_ref_problems(daily_sync, DAILY_SYNC_PATH, revision)
        if pr_workflow is not None:
            problems += checkout_ref_problems(pr_workflow, PR_WORKFLOW_PATH, revision)
    if contract is not None:
        problems += contract_icon_policy_problems(contract, manifest)
    return problems


def matcher_url_base_problems(manifest: dict) -> list[str]:
    """运行时值检查：真实 import matcher，比对它推导出的 revision / URL 基址与 manifest。"""
    scripts_dir = Path(__file__).resolve().parents[1]
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    import match_icons

    try:
        expected_base = match_icons.asset_url_base(manifest)
        expected_ref = match_icons.asset_url_ref(manifest)
    except (RuntimeError, KeyError) as exc:
        return [f"{MANIFEST_PATH}: cannot derive the production icon URL base: {exc}"]
    problems: list[str] = []
    if match_icons.GITHUB_BASE != expected_base:
        problems.append(
            f"{MATCHER_PATH}: GITHUB_BASE {match_icons.GITHUB_BASE!r} != manifest-derived {expected_base!r}")
    if match_icons.ASSET_URL_REF != expected_ref:
        problems.append(
            f"{MATCHER_PATH}: ASSET_URL_REF {match_icons.ASSET_URL_REF!r} != manifest-derived {expected_ref!r}")
    if match_icons._OASIC_REVISION != manifest.get("revision"):
        problems.append(
            f"{MATCHER_PATH}: scanned revision {match_icons._OASIC_REVISION!r} "
            f"!= manifest revision {manifest.get('revision')!r}")
    return problems


def pinned_checkout_problems(repo: Path | None, expected: str = PINNED_OASIC) -> list[str]:
    if repo is None or not (repo / ".git").exists():
        return [f"pinned Oasisic checkout not available: {repo}"]
    head = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    if head != expected:
        return [f"Oasisic checkout HEAD {head} != approved pin {expected}"]
    return []


def icon_repo_path(environ: Mapping[str, str]) -> Path:
    """Oasisic 检出路径：优先级来自 scripts/icon_repo.py 的单一实现（与 matcher 共用）。

    显式 ``MIHOMO_ICON_REPO`` 优先；未设置时只用仓库相对 ``<ROOT>/Oasisic-Icons``。
    不嵌入任何机器专属绝对路径，也不静默回退到别处。
    """
    return resolve_icon_repo(environ=environ, root=ROOT)


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


def config_tree_guard_problems(head: str) -> list[str]:
    """C3：对**四份**生产配置逐一做 head 树自洽检查。

    原先只用单一 ``CONFIG_FOR_TREE_CHECK``（Android full），导致 Nikki 侧
    URL/path 被改坏时没有任何检查发现。此处对四份配置各自校验
    provider key ↔ URL ↔ path ↔ 规则集树，并对被引用的规则集文件做一次
    存在性/可解析性检查（该检查只取决于 URL 集合，四份合并后做一次即可）。
    """
    problems: list[str] = []
    head_rule_files = set(tree_paths(head, "ruleset/"))
    merged_providers: dict[str, dict] = {}
    for config_path in PRODUCTION_CONFIGS:
        try:
            config = yaml.safe_load(tree_file(head, config_path))
        except (subprocess.CalledProcessError, yaml.YAMLError) as exc:
            problems.append(f"{config_path}: cannot evaluate from PR head: {exc}")
            continue
        if not isinstance(config, dict):
            problems.append(f"{config_path}: must parse to a mapping")
            continue
        problems += config_tree_problems(config, head_rule_files, label=config_path)
        merged_providers.update(_rule_providers_of(config))
    problems += provider_file_problems({"rule-providers": merged_providers}, head)
    return problems


def icon_baseline_guard_problems(head: str) -> list[str]:
    """C3：对**四份**生产配置逐一校验「图标覆盖 == head 规则集品牌树」。"""
    # 注意：使用本模块自身所在目录导入 match_icons，而不是可能被测试替换的 ROOT
    scripts_dir = Path(__file__).resolve().parents[1]
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    import match_icons

    problems: list[str] = []
    icon_map, missing = match_icons.build_icon_map()
    brand_dirs = _brand_dirs_from_tree_paths(tree_paths(head, "ruleset/"))
    # 「0 missing」只取决于图标仓库本身，与具体配置无关，只报一次
    if missing:
        problems.append(f"icon map: missing {len(missing)} icons, e.g. {sorted(missing)[:10]}")
    for config_path in PRODUCTION_CONFIGS:
        config = yaml.safe_load(tree_file(head, config_path))
        if not isinstance(config, dict):
            problems.append(f"{config_path}: must parse to a mapping for icon coverage")
            continue
        for problem in icon_baseline_problems(
                config, [], brand_dirs,
                match_icons.STRATEGY_GROUP_MAP, match_icons.is_emoji_group):
            problems.append(f"{config_path}: {problem}")
    problems += podcast_problems(
        icon_map, tree_file(head, ORACLE_FIXTURES_PATH).decode("utf-8", "replace")
    )
    return problems


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
        "file parses as YAML; validated for all four production configs "
        "(base/head may legitimately differ)"
    )
    results.append(("config-tree-consistency-guard", expected, config_tree_guard_problems(head)))

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
        f"manifest pin {PINNED_OASIC} (discovery/validation) is the single runtime source; "
        "contract icon_policy agrees with it; daily-sync and PR workflows check out that pin; "
        "matcher derives its revision and production URL ref from the manifest"
    )
    problems = []
    try:
        manifest = json.loads(tree_file(head, MANIFEST_PATH))
        problems += oasisic_problems(
            manifest,
            tree_file(head, MATCHER_PATH).decode("utf-8", "replace"),
            tree_file(head, DAILY_SYNC_PATH).decode("utf-8", "replace"),
            contract=json.loads(tree_file(head, CONTRACT_PATH)),
            pr_workflow=tree_file(head, PR_WORKFLOW_PATH).decode("utf-8", "replace"),
        )
        problems += matcher_url_base_problems(manifest)
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
        "derived dynamically; 0 missing; validated for all four production configs; "
        "Podcast -> Xiaoyuzhou, ApplePodcasts independent"
    )
    problems = []
    try:
        problems = icon_baseline_guard_problems(head)
    except Exception as exc:  # noqa: BLE001 - report any matcher failure verbatim
        problems = [f"icon matcher could not be evaluated: {exc!r}"]
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
