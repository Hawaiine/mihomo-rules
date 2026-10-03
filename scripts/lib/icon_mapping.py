"""Stable mapping between mihomo Technical IDs and Oasisic icon identities."""
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


@dataclass(frozen=True)
class IconResolution:
    status: IconStatus
    technical_id: str
    canonical_id: str | None = None
    icon_path: str | None = None
    source: str = ""
    reason: str = ""


# Key by stable mihomo Technical ID, not display name, strategy group, or file path.
TECHNICAL_TO_CANONICAL: dict[str, str] = {}

# Unresolved identity decisions are explicit and are not mapped to canonical IDs.
REQUIRES_REVIEW = {
    "AbemaTV": "ABEMA has a different canonical ID; identity needs review.",
    "AppleNews": "AppleNewsPlus is the current icon identity; continuity needs review.",
    "Hotstar": "JioHotstar is the current icon identity; continuity needs review.",
    "PeacockTV": "Peacock is the current icon identity; continuity needs review.",
    "Podcast": "Generic podcasts must not be assumed to mean Apple Podcasts.",
}
NO_ICON = frozenset()


def load_catalog(path: Path, tree_paths: set[str] | None = None) -> dict[str, dict]:
    """Load Oasisic brands.json; reject malformed IDs and stale declared paths."""
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
    catalog: Mapping[str, Mapping],
    tree_paths: set[str] | None,
    mapping: Mapping[str, str] = TECHNICAL_TO_CANONICAL,
    overrides: Mapping[str, str] | None = None,
    review: Mapping[str, str] = REQUIRES_REVIEW,
) -> list[str]:
    """Report unknown IDs, missing assets, duplicate values, conflicts, and stale overrides."""
    known_ids = set(technical_ids)
    errors: list[str] = []
    seen: dict[str, str] = {}
    for technical_id, canonical_id in mapping.items():
        if technical_id not in known_ids:
            errors.append(f"TECHNICAL_ID_UNKNOWN: {technical_id}")
        if canonical_id in seen:
            errors.append(f"DUPLICATE_MAPPING: {seen[canonical_id]} and {technical_id} -> {canonical_id}")
        seen[canonical_id] = technical_id
        record = catalog.get(canonical_id)
        if record is None:
            errors.append(f"CANONICAL_ID_UNKNOWN: {technical_id} -> {canonical_id}")
            continue
        icon_path = record.get("icon_path")
        if not icon_path:
            errors.append(f"CANONICAL_ICON_PATH_MISSING: {technical_id} -> {canonical_id}")
        elif tree_paths is not None and icon_path.removeprefix("icons/") not in tree_paths:
            errors.append(f"CANONICAL_ICON_FILE_MISSING: {technical_id} -> {icon_path}")
    for technical_id, reason in review.items():
        if technical_id not in known_ids:
            errors.append(f"REVIEW_TECHNICAL_ID_UNKNOWN: {technical_id}")
        if not reason:
            errors.append(f"REVIEW_REASON_MISSING: {technical_id}")
    conflict = set(mapping) & set(review)
    if conflict:
        errors.append(f"MAPPING_REVIEW_CONFLICT: {sorted(conflict)}")
    for display_name, icon_path in (overrides or {}).items():
        if tree_paths is not None and icon_path not in tree_paths:
            errors.append(f"STALE_OVERRIDE: {display_name} -> {icon_path}")
    return errors


def resolve_icon(
    technical_id: str,
    strategy_group: str,
    catalog: Mapping[str, Mapping],
    tree_paths: set[str] | None,
    overrides: Mapping[str, str],
    emoji_group: bool = False,
) -> IconResolution:
    """Resolve explicit identity first; do not guess or silently accept a stale override."""
    if emoji_group:
        return IconResolution(IconStatus.SKIPPED, technical_id, source="emoji-policy")
    if technical_id in REQUIRES_REVIEW:
        return IconResolution(IconStatus.REQUIRES_REVIEW, technical_id, source="review-queue", reason=REQUIRES_REVIEW[technical_id])
    canonical_id = TECHNICAL_TO_CANONICAL.get(technical_id)
    if canonical_id is not None:
        record = catalog.get(canonical_id)
        if record is None:
            return IconResolution(IconStatus.REQUIRES_REVIEW, technical_id, canonical_id, source="canonical-map", reason="canonical ID is absent")
        icon_path = record.get("icon_path")
        if not icon_path:
            return IconResolution(IconStatus.NOT_FOUND, technical_id, canonical_id, source="canonical-map", reason="canonical record has no icon_path")
        if tree_paths is not None and icon_path.removeprefix("icons/") not in tree_paths:
            return IconResolution(IconStatus.NOT_FOUND, technical_id, canonical_id, icon_path, "canonical-map", "icon file is absent from current tree")
        return IconResolution(IconStatus.FOUND, technical_id, canonical_id, icon_path, "canonical-map")
    override = overrides.get(strategy_group)
    if override is not None:
        if tree_paths is not None and override not in tree_paths:
            return IconResolution(IconStatus.STALE_OVERRIDE, technical_id, icon_path=f"icons/{override}", source="legacy-override", reason="path is absent from current tree")
        return IconResolution(IconStatus.FOUND, technical_id, icon_path=f"icons/{override}", source="legacy-override")
    if technical_id in NO_ICON:
        return IconResolution(IconStatus.NOT_FOUND, technical_id, source="no-icon-policy")
    return IconResolution(IconStatus.REQUIRES_REVIEW, technical_id, source="unmapped", reason="no explicit canonical mapping or approved override")
