"""Independent comparisons over caller-supplied expected and actual fixtures."""
from __future__ import annotations

from typing import Any


PLATFORM_KEYS = (
    "port",
    "socks-port",
    "keep-alive-idle",
    "find-process-mode",
    "external-controller",
    "dns_listen",
    "tun_enabled",
)
ENTITY_KEYS = ("brands", "parents", "providers", "groups", "rules", "icons", "overrides", "preserved")


def _semantic_projection(config: dict[str, Any]) -> dict[str, Any]:
    """Compare routing semantics while ignoring per-variant presentation icons."""
    groups = [
        {key: value for key, value in group.items() if key != "icon"}
        for group in config.get("proxy-groups", [])
    ]
    return {
        "rules": config.get("rules", []),
        "rule-providers": config.get("rule-providers", {}),
        "proxy-groups": groups,
    }


def evaluate(expected: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    """Return mismatches between independently supplied fixture dictionaries."""
    errors: list[str] = []
    for key in ENTITY_KEYS:
        if actual.get(key) != expected.get(key):
            errors.append(f"{key} mismatch")

    expected_platforms = expected.get("platforms", {})
    actual_platforms = actual.get("platforms", {})
    for platform in ("android", "nikki"):
        want = expected_platforms.get(platform, {})
        got = actual_platforms.get(platform, {})
        for key in PLATFORM_KEYS:
            if got.get(key) != want.get(key):
                errors.append(f"platforms.{platform}.{key} does not match approved current baseline")
        applications = [rule for rule in got.get("rules", []) if "RULE-SET,Applications," in str(rule)]
        policy = want.get("applications_rule")
        if policy == "present" and len(applications) != 1:
            errors.append("platforms.android Applications rule must be present exactly once")
        elif policy == "absent" and applications:
            errors.append("platforms.nikki Applications rule must be absent")
        elif policy not in {"present", "absent"}:
            errors.append(f"platforms.{platform}.applications_rule expectation is invalid")

    expected_full = expected.get("full", {})
    expected_min = expected.get("min", {})
    actual_full = actual.get("full", {})
    actual_min = actual.get("min", {})
    if _semantic_projection(expected_full) != _semantic_projection(expected_min):
        errors.append("expected full/min semantic projections differ")
    if _semantic_projection(actual_full) != _semantic_projection(actual_min):
        errors.append("actual full/min semantic projections differ")
    for label, want, got in (
        ("full", expected_full, actual_full),
        ("min", expected_min, actual_min),
    ):
        if _semantic_projection(want) != _semantic_projection(got):
            errors.append(f"{label} semantic projection mismatch")

    allowed = set(expected.get("allowed_generated_keys", []))
    unexpected = sorted(set(actual.get("generated_keys", [])) - allowed)
    if unexpected:
        errors.append(f"unknown generated fields: {unexpected}")
    if actual.get("manual_generated_edit") != expected.get("manual_generated_edit"):
        errors.append("manual generated edit state mismatch")
    return errors
