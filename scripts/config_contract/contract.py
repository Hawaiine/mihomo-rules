"""Versioned config-contract loader and independent oracle helpers.

This module does not import generator, icon matcher, or ownership resolver code.
Expected values come from contract data and caller-supplied fixtures.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


CONTRACT_DIR = Path(__file__).resolve().parent
REQUIRED_KEYS = {
    "contract_version", "canonical_business_model", "platforms", "full_min",
    "template_policy", "user_local_input_policy", "override_policy", "icon_policy",
    "publication_policy", "system_group_policy",
}


class ContractError(ValueError):
    """Raised when contract data is invalid or output violates it."""


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"invalid contract JSON {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ContractError(f"contract root must be object: {path}")
    return data


def load_contract(path: Path | None = None) -> dict[str, Any]:
    data = load_json(path or CONTRACT_DIR / "contract.json")
    missing = sorted(REQUIRED_KEYS - set(data))
    if missing:
        raise ContractError(f"missing contract keys: {missing}")
    platforms = data["platforms"]
    if set(platforms) != {"shared", "android", "nikki"}:
        raise ContractError("platforms must contain shared, android, and nikki")
    for platform in ("android", "nikki"):
        mode = platforms[platform].get("find-process-mode")
        if not isinstance(mode, dict) or mode.get("status") != "APPROVED_CURRENT_BASELINE":
            raise ContractError(f"{platform} find-process-mode must record an approved current baseline")
        if mode.get("value") not in {"strict", "off", "always"} or not mode.get("official_semantics"):
            raise ContractError(f"{platform} find-process-mode baseline metadata is invalid")
    if platforms["android"]["find-process-mode"]["value"] != "strict" or platforms["nikki"]["find-process-mode"]["value"] != "off":
        raise ContractError("find-process-mode values must match approved production baselines")
    if platforms["android"]["applications_rule"] != "present" or platforms["nikki"]["applications_rule"] != "absent":
        raise ContractError("Applications invariant is not the approved platform policy")
    if data["publication_policy"].get("atomic_four_file_rename_claim") is not False:
        raise ContractError("contract must not claim atomic four-file rename")
    return data


def load_oasisic_revision(path: Path | None = None) -> dict[str, Any]:
    data = load_json(path or CONTRACT_DIR / "oasisic_revision.json")
    revision = data.get("revision")
    if not isinstance(revision, str) or len(revision) != 40 or any(c not in "0123456789abcdef" for c in revision):
        raise ContractError("Oasisic revision must be a pinned 40-character lowercase SHA")
    if data.get("asset_url_mode") != "branch-main":
        raise ContractError("production icon URLs must be built from the main branch ref")
    if data.get("production_url_ref") != "main":
        raise ContractError("production icon URL ref must be the main branch")
    return data


def icon_url(revision: str, relative_icon_path: str) -> str:
    if not relative_icon_path.startswith("icons/"):
        raise ContractError(f"invalid icon path: {relative_icon_path}")
    return f"https://raw.githubusercontent.com/Hawaiine/Oasisic-Icons/{revision}/{relative_icon_path}"


def semantic_projection(config: dict[str, Any]) -> dict[str, Any]:
    """Return routing-relevant data, excluding comments and presentation."""
    groups = [{k: v for k, v in group.items() if k != "icon"} for group in config.get("proxy-groups", [])]
    return {"proxy-groups": groups, "rule-providers": config.get("rule-providers", {}), "rules": config.get("rules", [])}


def check_platform(config: dict[str, Any], platform: str, contract: dict[str, Any]) -> list[str]:
    profile = contract["platforms"][platform]
    errors: list[str] = []
    for key in ("port", "socks-port", "keep-alive-idle", "external-controller"):
        if config.get(key) != profile[key]:
            errors.append(f"{platform} {key}: {config.get(key)!r} != {profile[key]!r}")
    mode = profile["find-process-mode"]
    expected_mode = mode["value"] if isinstance(mode, dict) else mode
    actual_mode = config.get("find-process-mode")
    # PyYAML's YAML 1.1 loader parses the unquoted Mihomo scalar `off` as False.
    # Normalize only that representation so runtime configs compare to the string policy.
    if actual_mode is False:
        actual_mode = "off"
    if actual_mode != expected_mode:
        errors.append(f"{platform} find-process-mode does not match approved current baseline ({expected_mode!r})")
    if config.get("dns", {}).get("listen") != profile["dns.listen"]:
        errors.append(f"{platform} dns.listen mismatch")
    if config.get("tun", {}).get("enable") != profile["tun"]["enable"]:
        errors.append(f"{platform} tun.enable mismatch")
    applications = [rule for rule in config.get("rules", []) if "RULE-SET,Applications," in str(rule)]
    if profile["applications_rule"] == "present" and len(applications) != 1:
        errors.append("Android Applications rule must be present exactly once")
    if profile["applications_rule"] == "absent" and applications:
        errors.append("Nikki Applications rule must be absent")
    return errors


def check_full_min(full: dict[str, Any], minimum: dict[str, Any]) -> list[str]:
    if semantic_projection(full) != semantic_projection(minimum):
        return ["full/min semantic projections differ"]
    return []


def check_generated_unknown(actual_keys: set[str], allowed_keys: set[str]) -> list[str]:
    unknown = sorted(actual_keys - allowed_keys)
    return [f"unknown generated field: {key}" for key in unknown]
