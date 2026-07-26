"""Content-addressed, read-only snapshot loader.

No fallback selection is performed: one exact configured manifest is either
valid or the dashboard has no data.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote

from pydantic import ValidationError

from .contracts import (
    DisplayManifest,
    PredictionDisplaySnapshot,
    parse_utc_timestamp,
    reject_forbidden_display_language,
)


# OPERATIONAL resource bounds. They do not filter players or change predictions.
MAX_MANIFEST_BYTES = 1 * 1024 * 1024
MAX_SNAPSHOT_BYTES = 10 * 1024 * 1024

_MAY_PATH_PATTERNS = (
    re.compile(r"(?<!\d)2026[-_/.\\ ]0?5(?:[-_/.\\ t]|$)", re.IGNORECASE),
    re.compile(r"(?<!\d)0?5[-_/.\\ ]2026(?:[-_/.\\ t]|$)", re.IGNORECASE),
    re.compile(r"(?<!\d)202605(?:\d{2})?(?!\d)"),
    re.compile(r"(?<![a-z])may(?:[-_/.\\\s]+)2026(?!\d)", re.IGNORECASE),
    re.compile(r"(?<!\d)2026(?:[-_/.\\\s]+)may(?![a-z])", re.IGNORECASE),
)


class SnapshotUnavailable(RuntimeError):
    """The exact configured display snapshot is missing or invalid."""


@dataclass(frozen=True)
class LoadedSnapshot:
    manifest: DisplayManifest
    snapshot: PredictionDisplaySnapshot
    manifest_bytes: bytes
    snapshot_bytes: bytes
    snapshot_sha256: str


def _contains_may_2026_token(value: str) -> bool:
    decoded = value
    for _ in range(3):
        newer = unquote(decoded)
        if newer == decoded:
            break
        decoded = newer
    return any(pattern.search(decoded) for pattern in _MAY_PATH_PATTERNS)


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _reject_nonfinite(token: str) -> None:
    raise ValueError(f"non-finite JSON numeric token: {token}")


def _parse_json(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_nonfinite,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise SnapshotUnavailable(f"{label} is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise SnapshotUnavailable(f"{label} must be a JSON object")
    return value


def _read_bounded_file(path: Path, *, maximum: int, label: str) -> bytes:
    if _contains_may_2026_token(str(path)):
        raise SnapshotUnavailable("May 2026 paths are sealed")
    if not path.exists() or not path.is_file() or path.is_symlink():
        raise SnapshotUnavailable(f"{label} is not an eligible regular file")
    size = path.stat().st_size
    if size <= 0 or size > maximum:
        raise SnapshotUnavailable(f"{label} violates its operational byte bound")
    raw = path.read_bytes()
    if len(raw) != size:
        raise SnapshotUnavailable(f"{label} changed while it was read")
    return raw


def _resolve_beneath(root: Path, candidate: Path, *, label: str) -> Path:
    if _contains_may_2026_token(str(candidate)):
        raise SnapshotUnavailable("May 2026 paths are sealed")
    try:
        root_resolved = root.resolve(strict=True)
    except OSError as exc:
        raise SnapshotUnavailable("snapshot root is unavailable") from exc
    if root.is_symlink():
        raise SnapshotUnavailable("snapshot root cannot be a symlink")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise SnapshotUnavailable(f"{label} does not exist") from exc
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise SnapshotUnavailable(f"{label} escapes the configured snapshot root") from exc
    current = candidate
    while current != root and current != current.parent:
        if current.is_symlink():
            raise SnapshotUnavailable(f"{label} crosses a symlink")
        current = current.parent
    return resolved


class SnapshotStore:
    """Load one exact authorized snapshot manifest from an allowlisted root."""

    def __init__(
        self,
        *,
        root: Path,
        manifest_path: Path | None,
        allowed_producers: frozenset[str],
    ) -> None:
        self.root = root
        self.manifest_path = manifest_path
        self.allowed_producers = allowed_producers

    def load(self) -> LoadedSnapshot:
        if self.manifest_path is None:
            raise SnapshotUnavailable("no prediction snapshot is configured")
        if not self.allowed_producers:
            raise SnapshotUnavailable("no authorized snapshot producer is configured")

        manifest_path = _resolve_beneath(
            self.root, self.manifest_path, label="snapshot manifest"
        )
        manifest_bytes = _read_bounded_file(
            manifest_path, maximum=MAX_MANIFEST_BYTES, label="snapshot manifest"
        )
        manifest_data = _parse_json(manifest_bytes, label="snapshot manifest")
        try:
            reject_forbidden_display_language(manifest_data)
            manifest = DisplayManifest.model_validate(manifest_data)
        except (ValidationError, ValueError) as exc:
            raise SnapshotUnavailable("snapshot manifest failed its positive schema") from exc

        if manifest.producer.producer_id not in self.allowed_producers:
            raise SnapshotUnavailable("snapshot producer is not allowlisted")

        relative = PurePosixPath(manifest.snapshot_path)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or "." in relative.parts
            or "\\" in manifest.snapshot_path
        ):
            raise SnapshotUnavailable("snapshot_path must be a safe relative POSIX path")
        snapshot_path = _resolve_beneath(
            self.root, self.root.joinpath(*relative.parts), label="prediction snapshot"
        )
        snapshot_bytes = _read_bounded_file(
            snapshot_path, maximum=MAX_SNAPSHOT_BYTES, label="prediction snapshot"
        )
        if len(snapshot_bytes) != manifest.snapshot_byte_size:
            raise SnapshotUnavailable("snapshot byte size does not match the manifest")
        digest = hashlib.sha256(snapshot_bytes).hexdigest()
        if digest != manifest.snapshot_sha256:
            raise SnapshotUnavailable("snapshot SHA-256 does not match the manifest")

        snapshot_data = _parse_json(snapshot_bytes, label="prediction snapshot")
        try:
            reject_forbidden_display_language(snapshot_data)
            snapshot = PredictionDisplaySnapshot.model_validate(snapshot_data)
        except (ValidationError, ValueError) as exc:
            raise SnapshotUnavailable("prediction snapshot failed its positive schema") from exc

        self._validate_bindings(manifest, snapshot)
        return LoadedSnapshot(
            manifest=manifest,
            snapshot=snapshot,
            manifest_bytes=manifest_bytes,
            snapshot_bytes=snapshot_bytes,
            snapshot_sha256=digest,
        )

    @staticmethod
    def _validate_bindings(
        manifest: DisplayManifest, snapshot: PredictionDisplaySnapshot
    ) -> None:
        exact_pairs = (
            ("official_slate_date", manifest.official_slate_date, snapshot.official_slate_date),
            ("created_utc", manifest.created_utc, snapshot.created_utc),
            ("source_commit", manifest.source_commit, snapshot.source_commit),
            ("producer", manifest.producer, snapshot.producer),
            ("model_id", manifest.model_id, snapshot.model_banner.model_id),
            (
                "frozen_comparator_model_id",
                manifest.frozen_comparator_model_id,
                snapshot.model_banner.frozen_comparator_model_id,
            ),
            ("config_sha256", manifest.config_sha256, snapshot.config_sha256),
            ("artifact_sha256s", manifest.artifact_sha256s, snapshot.artifact_sha256s),
            (
                "qualification_state",
                manifest.qualification_state,
                snapshot.model_banner.qualification_state,
            ),
            (
                "betting_authorized",
                manifest.betting_authorized,
                snapshot.model_banner.betting_authorized,
            ),
            ("supported_markets", manifest.supported_markets, snapshot.supported_markets),
            ("row_count", manifest.row_count, len(snapshot.rows)),
        )
        for field_name, expected, observed in exact_pairs:
            if expected != observed:
                raise SnapshotUnavailable(f"manifest/snapshot {field_name} binding mismatch")

        created = parse_utc_timestamp(snapshot.created_utc, field_name="created_utc")
        for row in snapshot.rows:
            if row.model_id not in {
                snapshot.model_banner.model_id,
                snapshot.model_banner.current_live_model_id,
                snapshot.model_banner.frozen_comparator_model_id,
                snapshot.model_banner.research_candidate_model_id,
            }:
                raise SnapshotUnavailable("row model_id is not bound by the model banner")
            if row.source_observed_utc is not None:
                observed = parse_utc_timestamp(
                    row.source_observed_utc, field_name="source_observed_utc"
                )
                if observed > created:
                    raise SnapshotUnavailable("row source observation is after snapshot creation")
