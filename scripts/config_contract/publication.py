"""Practical all-or-nothing publication semantics for four config files."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from collections.abc import Callable
from pathlib import Path
from typing import Any


FILES = (
    "configs/Android/config.yaml",
    "configs/Android/config.min.yaml",
    "configs/Nikki/config.yaml",
    "configs/Nikki/config.min.yaml",
)


class PublicationBlocked(RuntimeError):
    """Raised when rollback cannot restore the original publication unit."""


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read(path: Path) -> bytes:
    return path.read_bytes()


def _validate_staged(root: Path, staging: Path) -> dict[str, Any]:
    try:
        manifest = json.loads((staging / "manifest.json").read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            raise ValueError("manifest root must be an object")
        old_entries = manifest.get("old")
        new_entries = manifest.get("new")
        if not isinstance(old_entries, dict) or set(old_entries) != set(FILES):
            raise ValueError("manifest old section must describe exactly the four publication files")
        if not isinstance(new_entries, dict) or set(new_entries) != set(FILES):
            raise ValueError("manifest new section must describe exactly the four publication files")
        for relative in FILES:
            target = _read(root / relative)
            backup = _read(staging / "backup" / relative)
            candidate = _read(staging / "candidate" / relative)
            for label, data, expected in (
                ("original", target, old_entries[relative]),
                ("backup", backup, old_entries[relative]),
                ("candidate", candidate, new_entries[relative]),
            ):
                if not isinstance(expected, dict) or len(data) != expected.get("size") or _hash(data) != expected.get("sha256"):
                    raise ValueError(f"{label} integrity mismatch: {relative}")
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise PublicationBlocked(f"pre-publish integrity check failed: {exc}") from exc
    return manifest


def stage(root: Path, candidates: dict[str, bytes], staging: Path, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    if tuple(candidates) != FILES:
        raise RuntimeError("publication unit must contain exactly the four configs")
    for relative in FILES:
        try:
            _read(root / relative)
        except OSError as exc:
            raise RuntimeError(f"original publication file is missing or unreadable: {relative}") from exc
    staging.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "old": {}, "new": {},
        "metadata": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **(metadata or {}),
        },
    }
    for relative in FILES:
        try:
            source = root / relative
            old = source.read_bytes()
        except OSError as exc:
            raise RuntimeError(f"original publication file is missing or unreadable: {relative}") from exc
        manifest["old"][relative] = {"sha256": _hash(old), "size": len(old)}
        manifest["new"][relative] = {"sha256": _hash(candidates[relative]), "size": len(candidates[relative])}
        (staging / "backup" / relative).parent.mkdir(parents=True, exist_ok=True)
        (staging / "backup" / relative).write_bytes(old)
        (staging / "candidate" / relative).parent.mkdir(parents=True, exist_ok=True)
        (staging / "candidate" / relative).write_bytes(candidates[relative])
    (staging / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return manifest


def publish(root: Path, staging: Path, validate: Callable[[Path], None], fail_at: int | None = None, verify_fail: bool = False, rollback_fail: bool = False) -> dict[str, Any]:
    manifest = _validate_staged(root, staging)
    validate(staging / "candidate")
    try:
        for index, relative in enumerate(FILES, 1):
            if fail_at == index:
                raise OSError(f"injected publish failure {index}")
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_suffix(destination.suffix + ".tmp")
            shutil.copy2(staging / "candidate" / relative, temporary)
            os.replace(temporary, destination)
        for relative in FILES:
            if _hash(_read(root / relative)) != manifest["new"][relative]["sha256"] or verify_fail:
                raise RuntimeError("post-publish verification failed")
    except PublicationBlocked:
        raise
    except Exception as exc:
        try:
            _rollback(root, staging, rollback_fail)
        except PublicationBlocked:
            raise
        raise RuntimeError(f"publication rolled back: {exc}") from exc
    return json.loads((staging / "manifest.json").read_text(encoding="utf-8"))


def _rollback(root: Path, staging: Path, fail: bool) -> None:
    if fail:
        raise PublicationBlocked(f"rollback failed; backup preserved at {staging / 'backup'}")
    try:
        manifest = json.loads((staging / "manifest.json").read_text(encoding="utf-8"))
        for relative in reversed(FILES):
            backup = staging / "backup" / relative
            original = manifest["old"][relative]
            data = backup.read_bytes()
            if len(data) != original["size"] or _hash(data) != original["sha256"]:
                raise ValueError(f"backup integrity mismatch: {relative}")
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_suffix(destination.suffix + ".rollback")
            shutil.copy2(backup, temporary)
            os.replace(temporary, destination)
        for relative in FILES:
            restored = _read(root / relative)
            original = manifest["old"][relative]
            if len(restored) != original["size"] or _hash(restored) != original["sha256"]:
                raise ValueError(f"restored file verification failed: {relative}")
    except Exception as exc:
        raise PublicationBlocked(f"rollback incomplete; evidence preserved at {staging}: {exc}") from exc


def isolated_root() -> Path:
    return Path(tempfile.mkdtemp(prefix="phase8f-publication-"))
