"""Resolve mihomo icon identities independently from display names and paths.

Design principles:
- Primary key is the stable mihomo Technical ID (the ruleset directory name),
  never a Display Name, Strategy Group, or icon filename.
- Values are Oasisic canonical IDs; the asset path stays owned by the Oasisic
  SSOT (`config/brands.json`), so icon-repository reshuffles never touch the mapping.
- `tree_paths` is the set of asset paths relative to the Oasisic `icons/`
  directory (e.g. "Media/Netflix/Netflix.png"), or None when unavailable.

Decision kinds per brand:
- mapping  : reviewed Technical ID -> Oasisic canonical ID   (target state)
- review   : identity pending a human decision               -> REQUIRES_REVIEW
- no_icon  : explicit policy that the brand gets no icon     -> SKIPPED
- override : temporary display-name-keyed legacy path        (migration debt)
- emoji    : display-name policy; emoji groups never get icons -> SKIPPED

Conflict contract (never resolved by fallback order):
- any two of {mapping, review, no_icon} for one brand is a configuration error;
- a legacy override may only exist for brands WITHOUT an explicit decision;
- an emoji-policy brand may carry at most a redundant `no_icon` entry.
resolve_icon() raises ValueError on every such conflict, and validate_mapping()
reports the same conflicts as errors — validation and runtime cannot disagree.
Priority order applies only between legal inputs.

Verification contract: FOUND requires a real asset-tree hit. Without an asset
tree a declared path resolves to UNVERIFIED — consumers MUST NOT treat
UNVERIFIED as FOUND.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
from pathlib import Path
from typing import Mapping, Sequence


class IconStatus(str, Enum):
    FOUND = "FOUND"
    NOT_FOUND = "NOT_FOUND"
    SKIPPED = "SKIPPED"
    REQUIRES_REVIEW = "REQUIRES_REVIEW"
    STALE_OVERRIDE = "STALE_OVERRIDE"
    UNVERIFIED = "UNVERIFIED"


@dataclass(frozen=True)
class IconResolution:
    status: IconStatus
    technical_id: str
    canonical_id: str | None = None
    icon_path: str | None = None
    source: str = ""
    reason: str = ""


# Primary key: stable mihomo Technical ID — never display name, strategy group, or icon filename.
# Values: Oasisic canonical IDs; icon_path is resolved from the Oasisic SSOT, not stored here.
# The registry holds only human-approved mappings (Phase 6 decision, PR #11 merged).
TECHNICAL_TO_CANONICAL: dict[str, str] = {
    'ABEMA': 'ABEMA',
    'AppleNewsPlus': 'AppleNewsPlus',
    'ApplePodcasts': 'ApplePodcasts',
    'JioHotstar': 'JioHotstar',
    'Peacock': 'Peacock',
}


def load_catalog(path: Path, tree_paths: set[str] | None = None) -> dict[str, dict]:
    """Load Oasisic brands.json and optionally validate declared assets against its tree.

    This is the single SSOT adapter for the Oasisic brand schema: no other module
    in this repository may re-parse `config/brands.json`.
    """
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot load icon catalog {path}: {exc}") from exc
    records = document.get("brands") if isinstance(document, dict) else None
    if not isinstance(records, list):
        raise ValueError("Icon catalog must contain a brands list")
    catalog: dict[str, dict] = {}
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("Icon catalog brand entry must be an object")
        canonical_id = record.get("id")
        if not isinstance(canonical_id, str) or not canonical_id:
            raise ValueError("Icon catalog brand entry has missing/invalid id")
        if canonical_id in catalog:
            raise ValueError(f"Duplicate Oasisic canonical ID: {canonical_id}")
        icon_path = record.get("icon_path")
        if icon_path is not None and (not isinstance(icon_path, str) or not icon_path.startswith("icons/")):
            raise ValueError(f"Invalid icon_path for {canonical_id}: {icon_path!r}")
        if icon_path and tree_paths is not None and icon_path.removeprefix("icons/") not in tree_paths:
            raise ValueError(f"Stale icon_path for {canonical_id}: {icon_path}")
        catalog[canonical_id] = record
    return catalog


def validate_mapping(
    technical_ids: Sequence[str],
    group_of: Mapping[str, str],
    emoji_ids: Sequence[str],
    catalog: Mapping[str, Mapping],
    tree_paths: set[str] | None,
    mapping: Mapping[str, str] = TECHNICAL_TO_CANONICAL,
    overrides: Mapping[str, str] | None = None,
    review: Mapping[str, Mapping] | None = None,
    no_icon: Sequence[str] = (),
    allow_shared: Sequence[str] = (),
) -> list[str]:
    """Validate icon decisions for a set of brands and report every inconsistency.

    `group_of` maps every Technical ID under validation to its current display
    name (Technical IDs missing from it are reported as GROUP_OF_MISSING); the
    pairing lets the validator detect conflicts that involve the display-name-keyed
    legacy overrides. `emoji_ids` lists brands whose group name matches the
    project's emoji policy — caller-supplied, because the classification lives
    with the generator, and this validator does not re-implement it.

    Checks mirror resolve_icon(): every conflict the runtime rejects is reported
    here as an error, so validation and runtime cannot disagree. Policy: many
    Technical IDs sharing one canonical icon is allowed only when the canonical
    ID is explicitly listed in `allow_shared`.
    """
    known_ids = set(technical_ids)
    known_groups = set(group_of.values())
    review_manifest: Mapping[str, Mapping] = review or {}
    overrides = overrides or {}
    errors: list[str] = []
    for technical_id in technical_ids:
        if technical_id not in group_of:
            errors.append(f"GROUP_OF_MISSING: {technical_id}")
    reverse: dict[str, list[str]] = {}
    for technical_id, canonical_id in mapping.items():
        if technical_id not in known_ids:
            errors.append(f"TECHNICAL_ID_UNKNOWN: {technical_id}")
        if technical_id in review_manifest:
            errors.append(f"MAPPING_REVIEW_CONFLICT: {technical_id}")
        reverse.setdefault(canonical_id, []).append(technical_id)
        record = catalog.get(canonical_id)
        if record is None:
            errors.append(f"CANONICAL_ID_UNKNOWN: {technical_id} -> {canonical_id}")
            continue
        icon_path = record.get("icon_path")
        if not icon_path:
            errors.append(f"CANONICAL_ICON_PATH_MISSING: {technical_id} -> {canonical_id}")
        elif tree_paths is None:
            errors.append(f"CANONICAL_ICON_UNVERIFIED: {technical_id} -> {icon_path}")
        elif icon_path.removeprefix("icons/") not in tree_paths:
            errors.append(f"CANONICAL_ICON_FILE_MISSING: {technical_id} -> {icon_path}")
    shared_ids = {canonical_id for canonical_id, ids in reverse.items() if len(ids) > 1}
    for canonical_id in sorted(shared_ids):
        if canonical_id not in allow_shared:
            errors.append(f"SHARED_CANONICAL_NOT_APPROVED: {canonical_id} <- {', '.join(sorted(reverse[canonical_id]))}")
    for canonical_id in allow_shared:
        if canonical_id not in shared_ids:
            errors.append(f"SHARED_CANONICAL_NOT_USED: {canonical_id}")
    for technical_id, entry in review_manifest.items():
        if technical_id not in known_ids:
            errors.append(f"REVIEW_TECHNICAL_ID_UNKNOWN: {technical_id}")
        if not isinstance(entry, Mapping):
            errors.append(f"REVIEW_ENTRY_INVALID: {technical_id}")
        elif not entry.get("reason"):
            errors.append(f"REVIEW_REASON_MISSING: {technical_id}")
    for technical_id in no_icon:
        if technical_id not in known_ids:
            errors.append(f"NO_ICON_TECHNICAL_ID_UNKNOWN: {technical_id}")
        if technical_id in mapping or technical_id in review_manifest:
            errors.append(f"NO_ICON_POLICY_CONFLICT: {technical_id}")
    for strategy_group, icon_path in overrides.items():
        if not isinstance(strategy_group, str) or not isinstance(icon_path, str) or not icon_path:
            errors.append(f"OVERRIDE_ENTRY_INVALID: {strategy_group!r}")
            continue
        if strategy_group not in known_groups:
            errors.append(f"STALE_OVERRIDE_KEY: {strategy_group}")
        if tree_paths is None:
            errors.append(f"OVERRIDE_ICON_UNVERIFIED: {strategy_group} -> {icon_path}")
        elif icon_path not in tree_paths:
            errors.append(f"STALE_OVERRIDE: {strategy_group} -> {icon_path}")
    for technical_id, strategy_group in group_of.items():
        if strategy_group not in overrides:
            continue
        if technical_id in mapping:
            errors.append(f"MAPPING_OVERRIDE_CONFLICT: {technical_id}")
        if technical_id in review_manifest:
            errors.append(f"REVIEW_OVERRIDE_CONFLICT: {technical_id}")
        if technical_id in no_icon:
            errors.append(f"NO_ICON_OVERRIDE_CONFLICT: {technical_id}")
    for technical_id in emoji_ids:
        if technical_id in mapping:
            errors.append(f"EMOJI_MAPPING_CONFLICT: {technical_id}")
        if technical_id in review_manifest:
            errors.append(f"EMOJI_REVIEW_CONFLICT: {technical_id}")
        if group_of.get(technical_id, "") in overrides:
            errors.append(f"EMOJI_OVERRIDE_CONFLICT: {technical_id}")
    return errors


def find_uncovered(
    technical_ids: Sequence[str],
    mapping: Mapping[str, str] = TECHNICAL_TO_CANONICAL,
    review: Mapping[str, Mapping] | None = None,
    no_icon: Sequence[str] = (),
) -> list[str]:
    """Return Technical IDs without any explicit decision (mapping / review / no-icon).

    Legacy overrides are migration debt, not a decision: brands resolved only by
    an override stay in this list until they receive an explicit canonical mapping.
    Callers wiring the layer into generation must treat a non-empty result as
    unfinished migration rather than letting fuzzy matching silently fill the gap.
    """
    covered = set(mapping) | set(review or {}) | set(no_icon)
    return sorted(set(technical_ids) - covered)


def resolve_icon(
    technical_id: str,
    strategy_group: str,
    catalog: Mapping[str, Mapping],
    tree_paths: set[str] | None,
    mapping: Mapping[str, str] = TECHNICAL_TO_CANONICAL,
    overrides: Mapping[str, str] | None = None,
    review: Mapping[str, Mapping] | None = None,
    no_icon: Sequence[str] = (),
    emoji_group: bool = False,
    allow_shared: Sequence[str] = (),
) -> IconResolution:
    """Resolve one icon identity. Priority (only between legal inputs):

    1. conflict contract -> ValueError (see module docstring; never fallback order)
    2. emoji policy -> SKIPPED
    3. explicit canonical mapping -> FOUND / NOT_FOUND / UNVERIFIED / REQUIRES_REVIEW
    4. review manifest -> REQUIRES_REVIEW
    5. legacy display-key override (temporary) -> FOUND / STALE_OVERRIDE / UNVERIFIED
    6. explicit no-icon policy -> SKIPPED
    7. no decision at all -> REQUIRES_REVIEW (unmapped)

    Without an asset tree (`tree_paths is None`) a declared path yields
    UNVERIFIED, never FOUND: declaration is not proof of existence, and
    consumers must not substitute UNVERIFIED for FOUND.
    """
    overrides = overrides or {}
    review_manifest: Mapping[str, Mapping] = review or {}
    if technical_id in mapping and technical_id in review_manifest:
        raise ValueError(f"Conflicting mapping and review entries for {technical_id}")
    if technical_id in mapping and technical_id in no_icon:
        raise ValueError(f"Conflicting mapping and no-icon policy for {technical_id}")
    if technical_id in review_manifest and technical_id in no_icon:
        raise ValueError(f"Conflicting review entry and no-icon policy for {technical_id}")
    if strategy_group in overrides:
        if technical_id in mapping:
            raise ValueError(f"Legacy override conflicts with canonical mapping for {technical_id}")
        if technical_id in review_manifest:
            raise ValueError(f"Legacy override conflicts with review entry for {technical_id}")
        if technical_id in no_icon:
            raise ValueError(f"Legacy override conflicts with no-icon policy for {technical_id}")
    if emoji_group:
        if technical_id in mapping:
            raise ValueError(f"Emoji-policy group cannot carry a canonical mapping: {technical_id}")
        if technical_id in review_manifest:
            raise ValueError(f"Emoji-policy group cannot carry a review entry: {technical_id}")
        if strategy_group in overrides:
            raise ValueError(f"Emoji-policy group cannot carry a legacy override: {technical_id}")
        return IconResolution(IconStatus.SKIPPED, technical_id, source="emoji-policy",
                              reason="emoji strategy groups do not receive icons")
    canonical_id = mapping.get(technical_id)
    if canonical_id is not None:
        sharers = [key for key, value in mapping.items() if value == canonical_id]
        if len(sharers) > 1 and canonical_id not in allow_shared:
            raise ValueError(f"Unapproved shared canonical mapping: {canonical_id}")
        record = catalog.get(canonical_id)
        if record is None:
            return IconResolution(IconStatus.REQUIRES_REVIEW, technical_id, canonical_id,
                                  source="canonical-map", reason="canonical ID is absent")
        icon_path = record.get("icon_path")
        if not icon_path:
            return IconResolution(IconStatus.NOT_FOUND, technical_id, canonical_id,
                                  source="canonical-map", reason="canonical record has no icon_path")
        if tree_paths is None:
            return IconResolution(IconStatus.UNVERIFIED, technical_id, canonical_id, icon_path,
                                  "canonical-map", "asset tree was not supplied")
        if icon_path.removeprefix("icons/") not in tree_paths:
            return IconResolution(IconStatus.NOT_FOUND, technical_id, canonical_id, icon_path,
                                  "canonical-map", "icon file is absent from current tree")
        return IconResolution(IconStatus.FOUND, technical_id, canonical_id, icon_path, "canonical-map")
    if technical_id in review_manifest:
        entry = review_manifest[technical_id]
        if not isinstance(entry, Mapping):
            raise ValueError(f"Invalid review entry for {technical_id}")
        return IconResolution(IconStatus.REQUIRES_REVIEW, technical_id, source="review-manifest",
                              reason=str(entry.get("reason", "review required")))
    if strategy_group in overrides:
        override = overrides[strategy_group]
        if tree_paths is None:
            return IconResolution(IconStatus.UNVERIFIED, technical_id, icon_path=f"icons/{override}",
                                  source="legacy-override", reason="asset tree was not supplied")
        if override not in tree_paths:
            return IconResolution(IconStatus.STALE_OVERRIDE, technical_id, icon_path=f"icons/{override}",
                                  source="legacy-override", reason="path is absent from current tree")
        return IconResolution(IconStatus.FOUND, technical_id, icon_path=f"icons/{override}",
                              source="legacy-override", reason="temporary compatibility exception")
    if technical_id in no_icon:
        return IconResolution(IconStatus.SKIPPED, technical_id, source="no-icon-policy",
                              reason="explicit no-icon policy")
    return IconResolution(IconStatus.REQUIRES_REVIEW, technical_id, source="unmapped",
                          reason="no explicit canonical mapping or approved exception")
