"""Fail-closed loader for one independently authorized immutable snapshot."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from pydantic import ValidationError

from src.omega_contracts.canonical import require_sha256
from src.omega_contracts.chronology import (
    assert_may_safe_path,
    parse_canonical_utc,
    require_event_eligibility,
    require_load_freshness,
)
from src.omega_contracts.errors import ContractError

from .contracts import (
    DisplayManifest,
    PredictionDisplaySnapshot,
    reject_prohibited_semantics,
)


class SnapshotUnavailable(RuntimeError):
    """The exact configured display snapshot is absent, unauthorized, or invalid."""


@dataclass(frozen=True)
class LoadedSnapshot:
    manifest: DisplayManifest
    snapshot: PredictionDisplaySnapshot
    manifest_bytes: bytes
    snapshot_bytes: bytes
    manifest_sha256: str
    snapshot_sha256: str
    loaded_age_seconds: int


Clock = Callable[[], datetime]
ReadPhaseHook = Callable[[str, str, Path], None]


def _parse_json(raw: bytes, *, label: str) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"duplicate JSON key: {key}")
            value[key] = item
        return value

    def reject_constant(token: str) -> None:
        raise ValueError(f"non-finite numeric token: {token}")

    try:
        parsed = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise SnapshotUnavailable(f"{label} is not strict UTF-8 JSON") from exc
    if not isinstance(parsed, dict):
        raise SnapshotUnavailable(f"{label} must be a JSON object")
    return parsed


def _is_reparse_point(info: os.stat_result) -> bool:
    attributes = info.st_file_attributes if hasattr(info, "st_file_attributes") else 0
    marker = (
        stat.FILE_ATTRIBUTE_REPARSE_POINT
        if hasattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT")
        else 0x400
    )
    return bool(attributes & marker)


def _same_identity(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_dev,
        left.st_ino,
        left.st_mode,
    ) == (
        right.st_dev,
        right.st_ino,
        right.st_mode,
    )


def _windows_has_any_write_capability(path: Path, *, directory: bool) -> bool:
    """Probe effective Windows access without writing or changing the source."""

    if os.name != "nt":
        raise SnapshotUnavailable(
            "Windows access verification invoked on another platform"
        )
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    create_file.restype = ctypes.c_void_p
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int

    share_all = 0x00000001 | 0x00000002 | 0x00000004
    open_existing = 3
    flags = 0x00200000 | (0x02000000 if directory else 0)
    access_masks = [0x00010000, 0x00040000, 0x00080000]
    access_masks.extend(
        [0x00000002, 0x00000004, 0x00000040] if directory else [0x00000002, 0x00000004]
    )
    invalid = ctypes.c_void_p(-1).value
    for desired in access_masks:
        ctypes.set_last_error(0)
        handle = create_file(
            str(path), desired, share_all, None, open_existing, flags, None
        )
        if handle not in (None, invalid):
            close_handle(handle)
            return True
        error = ctypes.get_last_error()
        if error not in {5, 32}:
            raise SnapshotUnavailable(
                f"source permission verification is inconclusive on Windows (error {error})"
            )
    return False


def _assert_not_writable(path: Path, info: os.stat_result, *, directory: bool) -> None:
    if os.name == "nt":
        if _windows_has_any_write_capability(path, directory=directory):
            raise SnapshotUnavailable(
                "snapshot source or parent is writable by the dashboard runtime"
            )
        return
    if os.name == "posix":
        if info.st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH):
            raise SnapshotUnavailable(
                "snapshot source or parent has a writable mode bit"
            )
        if (
            os.access not in os.supports_effective_ids
            or os.access not in os.supports_follow_symlinks
        ):
            raise SnapshotUnavailable(
                "effective POSIX permission verification is unsupported"
            )
        try:
            effectively_writable = os.access(
                path,
                os.W_OK,
                effective_ids=True,
                follow_symlinks=False,
            )
        except (NotImplementedError, OSError) as exc:
            raise SnapshotUnavailable(
                "effective POSIX permission verification is inconclusive"
            ) from exc
        if effectively_writable:
            raise SnapshotUnavailable(
                "snapshot source or parent is effectively writable by the dashboard runtime"
            )
        return
    raise SnapshotUnavailable(
        "source permission verification is unsupported on this platform"
    )


def _safe_relative_path(value: Path | str, *, label: str) -> PurePosixPath:
    text = str(value)
    relative = PurePosixPath(text)
    if (
        not text
        or relative.is_absolute()
        or "." in relative.parts
        or ".." in relative.parts
        or "\\" in text
        or ":" in text
    ):
        raise SnapshotUnavailable(f"{label} must be a safe relative POSIX path")
    return relative


def _validated_chain(
    *, root: Path, anchor: Path, relative: PurePosixPath, label: str
) -> tuple[Path, os.stat_result]:
    try:
        root_absolute = root.absolute()
        anchor_absolute = anchor.absolute()
        root_resolved = assert_may_safe_path(
            root_absolute, allowed_root=anchor_absolute
        ).resolve(strict=True)
        anchor_resolved = assert_may_safe_path(anchor_absolute).resolve(strict=True)
        root_resolved.relative_to(anchor_resolved)
    except (OSError, ValueError, ContractError) as exc:
        raise SnapshotUnavailable(
            "snapshot root/immutability anchor is invalid"
        ) from exc
    target = root_resolved.joinpath(*relative.parts)
    try:
        target.relative_to(root_resolved)
    except ValueError as exc:
        raise SnapshotUnavailable(f"{label} escapes the configured root") from exc
    try:
        assert_may_safe_path(target, allowed_root=root_resolved)
        target_info = target.lstat()
    except (OSError, ContractError) as exc:
        raise SnapshotUnavailable(f"{label} is unavailable") from exc
    if (
        not stat.S_ISREG(target_info.st_mode)
        or stat.S_ISLNK(target_info.st_mode)
        or _is_reparse_point(target_info)
    ):
        raise SnapshotUnavailable(f"{label} is not an eligible regular file")

    current = target.parent
    while True:
        try:
            info = current.lstat()
        except OSError as exc:
            raise SnapshotUnavailable(f"{label} parent chain is unavailable") from exc
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or _is_reparse_point(info)
        ):
            raise SnapshotUnavailable(
                f"{label} crosses a symlink, junction, or reparse point"
            )
        _assert_not_writable(current, info, directory=True)
        if current == anchor_resolved:
            break
        if current == current.parent:
            raise SnapshotUnavailable(f"{label} escaped its immutability anchor")
        current = current.parent
    _assert_not_writable(target, target_info, directory=False)
    return target, target_info


def _read_verified_file(
    *,
    root: Path,
    anchor: Path,
    relative: PurePosixPath,
    maximum_bytes: int,
    label: str,
    hook: ReadPhaseHook | None,
) -> bytes:
    target, pre_info = _validated_chain(
        root=root, anchor=anchor, relative=relative, label=label
    )
    if pre_info.st_size <= 0 or pre_info.st_size > maximum_bytes:
        raise SnapshotUnavailable(
            f"{label} violates its configured operational byte bound"
        )
    if hook is not None:
        hook(label, "before_open", target)
    flags = os.O_RDONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(target, flags)
    except OSError as exc:
        raise SnapshotUnavailable(f"{label} could not be opened safely") from exc
    try:
        opened_info = os.fstat(descriptor)
        if not stat.S_ISREG(opened_info.st_mode) or not _same_identity(
            pre_info, opened_info
        ):
            raise SnapshotUnavailable(
                f"{label} identity changed before the verified open"
            )
        if hook is not None:
            hook(label, "after_open", target)
        chunks: list[bytes] = []
        remaining = maximum_bytes + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) <= 0 or len(raw) > maximum_bytes:
            raise SnapshotUnavailable(
                f"{label} violates its configured operational byte bound"
            )
        after_read = os.fstat(descriptor)
        if (
            not _same_identity(opened_info, after_read)
            or opened_info.st_size != after_read.st_size
            or opened_info.st_mtime_ns != after_read.st_mtime_ns
            or len(raw) != after_read.st_size
        ):
            raise SnapshotUnavailable(f"{label} changed during the verified read")
    finally:
        os.close(descriptor)
    if hook is not None:
        hook(label, "after_read", target)
    try:
        post_info = target.lstat()
    except OSError as exc:
        raise SnapshotUnavailable(
            f"{label} disappeared after the verified read"
        ) from exc
    if (
        not _same_identity(pre_info, post_info)
        or pre_info.st_mtime_ns != post_info.st_mtime_ns
    ):
        raise SnapshotUnavailable(f"{label} path identity changed during load")
    _validated_chain(root=root, anchor=anchor, relative=relative, label=label)
    return raw


class SnapshotStore:
    """Load one exact manifest authorized by an out-of-band SHA-256 trust root."""

    def __init__(
        self,
        *,
        root: Path,
        manifest_path: Path | str | None,
        allowed_producers: frozenset[str],
        trusted_manifest_sha256: str | None = None,
        maximum_snapshot_age_seconds: int | None = None,
        maximum_manifest_bytes: int | None = None,
        maximum_snapshot_bytes: int | None = None,
        immutability_anchor: Path | None = None,
        clock: Clock | None = None,
        read_phase_hook: ReadPhaseHook | None = None,
    ) -> None:
        self.root = root
        self.manifest_path = manifest_path
        self.allowed_producers = allowed_producers
        self.trusted_manifest_sha256 = trusted_manifest_sha256
        self.maximum_snapshot_age_seconds = maximum_snapshot_age_seconds
        self.maximum_manifest_bytes = maximum_manifest_bytes
        self.maximum_snapshot_bytes = maximum_snapshot_bytes
        self.immutability_anchor = immutability_anchor
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.read_phase_hook = read_phase_hook

    def _required_operational_configuration(self) -> tuple[str, int, int, int, Path]:
        if self.trusted_manifest_sha256 is None:
            raise SnapshotUnavailable(
                "out-of-band trusted manifest digest is not configured"
            )
        try:
            digest = require_sha256(
                self.trusted_manifest_sha256,
                label="out-of-band trusted manifest digest",
            )
        except ContractError as exc:
            raise SnapshotUnavailable(
                "out-of-band trusted manifest digest is malformed"
            ) from exc
        values = (
            ("maximum snapshot age", self.maximum_snapshot_age_seconds),
            ("maximum manifest bytes", self.maximum_manifest_bytes),
            ("maximum snapshot bytes", self.maximum_snapshot_bytes),
        )
        normalized: list[int] = []
        for label, value in values:
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise SnapshotUnavailable(
                    f"{label} operational configuration is absent or invalid"
                )
            normalized.append(value)
        if self.immutability_anchor is None:
            raise SnapshotUnavailable("source immutability anchor is not configured")
        return (
            digest,
            normalized[0],
            normalized[1],
            normalized[2],
            self.immutability_anchor,
        )

    def load(self) -> LoadedSnapshot:
        if self.manifest_path is None:
            raise SnapshotUnavailable("no prediction snapshot is configured")
        if not self.allowed_producers:
            raise SnapshotUnavailable("no authorized snapshot producer is configured")
        trusted_digest, max_age, max_manifest, max_snapshot, anchor = (
            self._required_operational_configuration()
        )
        manifest_relative = _safe_relative_path(
            self.manifest_path, label="manifest path"
        )
        manifest_bytes = _read_verified_file(
            root=self.root,
            anchor=anchor,
            relative=manifest_relative,
            maximum_bytes=max_manifest,
            label="snapshot manifest",
            hook=self.read_phase_hook,
        )
        manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
        if manifest_digest != trusted_digest:
            raise SnapshotUnavailable(
                "manifest is not authorized by the out-of-band trust root"
            )
        manifest_data = _parse_json(manifest_bytes, label="snapshot manifest")
        try:
            reject_prohibited_semantics(manifest_data)
            manifest = DisplayManifest.model_validate(manifest_data)
        except (ValidationError, ValueError, ContractError) as exc:
            raise SnapshotUnavailable(
                "snapshot manifest failed its positive schema"
            ) from exc
        if manifest.producer.producer_id not in self.allowed_producers:
            raise SnapshotUnavailable("snapshot producer is not allowlisted")

        snapshot_relative = _safe_relative_path(
            manifest.snapshot_path, label="snapshot_path"
        )
        snapshot_bytes = _read_verified_file(
            root=self.root,
            anchor=anchor,
            relative=snapshot_relative,
            maximum_bytes=max_snapshot,
            label="prediction snapshot",
            hook=self.read_phase_hook,
        )
        if len(snapshot_bytes) != manifest.snapshot_byte_size:
            raise SnapshotUnavailable("snapshot byte size does not match the manifest")
        snapshot_digest = hashlib.sha256(snapshot_bytes).hexdigest()
        if snapshot_digest != manifest.snapshot_sha256:
            raise SnapshotUnavailable("snapshot SHA-256 does not match the manifest")
        snapshot_data = _parse_json(snapshot_bytes, label="prediction snapshot")
        try:
            reject_prohibited_semantics(snapshot_data)
            snapshot = PredictionDisplaySnapshot.model_validate(snapshot_data)
        except (ValidationError, ValueError, ContractError) as exc:
            raise SnapshotUnavailable(
                "prediction snapshot failed its positive schema"
            ) from exc

        self._validate_bindings(manifest, snapshot)
        try:
            now = self.clock()
            loaded_age = require_load_freshness(
                captured_at=snapshot.captured_at_utc,
                now_utc=now,
                maximum_age_seconds=max_age,
            )
            now_utc = parse_canonical_utc(now, label="load clock")
            for row in snapshot.rows:
                event_start = parse_canonical_utc(
                    row.event_start_utc, label="event start"
                )
                if now_utc >= event_start:
                    raise ContractError("event has started at load time")
        except ContractError as exc:
            raise SnapshotUnavailable(
                "snapshot failed load-time chronology/freshness"
            ) from exc

        return LoadedSnapshot(
            manifest=manifest,
            snapshot=snapshot,
            manifest_bytes=manifest_bytes,
            snapshot_bytes=snapshot_bytes,
            manifest_sha256=manifest_digest,
            snapshot_sha256=snapshot_digest,
            loaded_age_seconds=loaded_age,
        )

    @staticmethod
    def _validate_bindings(
        manifest: DisplayManifest, snapshot: PredictionDisplaySnapshot
    ) -> None:
        exact_pairs = (
            (
                "official_slate_date",
                manifest.official_slate_date,
                snapshot.official_slate_date,
            ),
            ("captured_at_utc", manifest.captured_at_utc, snapshot.captured_at_utc),
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
            (
                "supported_markets",
                manifest.supported_markets,
                snapshot.supported_markets,
            ),
            ("row_count", manifest.row_count, len(snapshot.rows)),
        )
        for field_name, expected, observed in exact_pairs:
            if expected != observed:
                raise SnapshotUnavailable(
                    f"manifest/snapshot {field_name} binding mismatch"
                )

        identity = {item.sha256: item for item in manifest.receipt_bindings.identity}
        sources = {item.sha256: item for item in manifest.receipt_bindings.source}
        chronology = {
            item.sha256: item for item in manifest.receipt_bindings.chronology
        }
        allowed_models = {
            snapshot.model_banner.model_id,
            snapshot.model_banner.current_live_model_id,
            snapshot.model_banner.frozen_comparator_model_id,
            snapshot.model_banner.research_candidate_model_id,
        }
        for row in snapshot.rows:
            if row.model_id not in allowed_models:
                raise SnapshotUnavailable(
                    "row model_id is not bound by the model banner"
                )
            identity_receipt = identity.get(row.identity_receipt_sha256)
            source_receipt = sources.get(row.source_receipt_sha256)
            chronology_receipt = chronology.get(row.chronology_decision_receipt_sha256)
            if (
                identity_receipt is None
                or source_receipt is None
                or chronology_receipt is None
            ):
                raise SnapshotUnavailable(
                    "row references a receipt not bound by the manifest"
                )
            batter_identity = (
                identity_receipt.subject_role,
                identity_receipt.game_pk,
                identity_receipt.mlb_player_id,
                identity_receipt.mlb_team_id,
                identity_receipt.mlb_opponent_team_id,
                identity_receipt.player_name,
                identity_receipt.team_name,
                identity_receipt.opponent_name,
                identity_receipt.identity_state,
            )
            row_identity = (
                "batter",
                row.game_pk,
                row.mlb_player_id,
                row.mlb_team_id,
                row.mlb_opponent_team_id,
                row.player_name,
                row.team,
                row.opponent,
                row.identity_state,
            )
            if batter_identity != row_identity:
                raise SnapshotUnavailable(
                    "row identity does not match its bound identity receipt"
                )
            source_identity = (
                source_receipt.game_pk,
                source_receipt.mlb_player_id,
                source_receipt.mlb_team_id,
                source_receipt.mlb_opponent_team_id,
                source_receipt.observed_at_utc,
            )
            if source_identity != (
                row.game_pk,
                row.mlb_player_id,
                row.mlb_team_id,
                row.mlb_opponent_team_id,
                row.source_observed_utc,
            ):
                raise SnapshotUnavailable(
                    "row source semantics do not match its bound source receipt"
                )
            chronology_identity = (
                chronology_receipt.game_pk,
                chronology_receipt.official_slate_date,
                chronology_receipt.event_start_utc,
                chronology_receipt.decision_horizon_utc,
                chronology_receipt.decision,
            )
            if chronology_identity != (
                row.game_pk,
                snapshot.official_slate_date,
                row.event_start_utc,
                row.decision_horizon_utc,
                "ELIGIBLE",
            ):
                raise SnapshotUnavailable(
                    "row chronology does not match its bound chronology receipt"
                )
            identity_observed = parse_canonical_utc(
                identity_receipt.observed_at_utc,
                label="identity receipt observation",
            )
            decision_horizon = parse_canonical_utc(
                row.decision_horizon_utc,
                label="decision horizon",
            )
            if identity_observed >= decision_horizon:
                raise SnapshotUnavailable(
                    "identity receipt is not point-in-time eligible"
                )
            try:
                require_event_eligibility(
                    observed_at=source_receipt.observed_at_utc,
                    decision_horizon=chronology_receipt.decision_horizon_utc,
                    captured_at=snapshot.captured_at_utc,
                    event_start=chronology_receipt.event_start_utc,
                )
            except ContractError as exc:
                raise SnapshotUnavailable("receipt chronology is not eligible") from exc
            if row.opposing_starter is not None:
                pitcher = identity.get(row.opposing_starter.identity_receipt_sha256)
                if pitcher is None:
                    raise SnapshotUnavailable(
                        "opposing starter identity receipt is unbound"
                    )
                if (
                    pitcher.subject_role,
                    pitcher.game_pk,
                    pitcher.mlb_player_id,
                    pitcher.mlb_team_id,
                    pitcher.mlb_opponent_team_id,
                    pitcher.player_name,
                    pitcher.identity_state,
                ) != (
                    "opposing_starter",
                    row.game_pk,
                    row.opposing_starter.mlb_player_id,
                    row.mlb_opponent_team_id,
                    row.mlb_team_id,
                    row.opposing_starter.name,
                    row.opposing_starter.identity_state,
                ):
                    raise SnapshotUnavailable(
                        "opposing starter does not match its identity receipt"
                    )
