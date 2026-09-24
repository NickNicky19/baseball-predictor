"""Semantic fitted-artifact eligibility and immutable publication.

The candidate does not fit an artifact. These functions establish a future
fail-closed boundary and are exercised only with synthetic fixtures.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Mapping

from .activation import assert_research_only_manifest
from .canonical import canonical_json_bytes, require_nonempty_text, require_sha256, sha256_bytes, sha256_file
from .chronology import assert_may_safe_path, parse_aware_utc, parse_date
from .durability import flush_sync_and_harden
from .errors import ContractError

_HASH_FIELDS = (
    "artifact_sha256",
    "code_sha256",
    "config_sha256",
    "data_manifest_sha256",
    "feature_schema_sha256",
    "dependency_lock_sha256",
    "test_manifest_sha256",
)
ARTIFACT_MANIFEST_SCHEMA = "omega-artifact-manifest-v1"


@dataclass(frozen=True)
class ArtifactManifest:
    schema_version: str
    artifact_type: str
    candidate_id: str
    qualification_state: str
    active: bool
    betting_authorized: bool
    artifact_sha256: str
    artifact_size: int
    code_sha256: str
    config_sha256: str
    data_manifest_sha256: str
    feature_schema_sha256: str
    dependency_lock_sha256: str
    test_manifest_sha256: str
    fit_start: date
    fit_end: date
    selection_start: date
    selection_end: date
    created_at_utc: datetime
    published_at_utc: datetime
    frozen_comparator_sha256: str | None = None
    promotion_approval_sha256: str | None = None

    @classmethod
    def from_mapping(cls, payload: Mapping[str, object]) -> "ArtifactManifest":
        expected = set(cls.__dataclass_fields__)
        if set(payload) != expected:
            raise ContractError("artifact manifest fields do not exactly match schema")
        hashes = {field: require_sha256(payload[field], label=field) for field in _HASH_FIELDS}
        comparator = payload["frozen_comparator_sha256"]
        if comparator is not None:
            comparator = require_sha256(comparator, label="frozen_comparator_sha256")
        if payload["promotion_approval_sha256"] is not None:
            raise ContractError("unqualified candidate cannot carry a promotion approval")
        if isinstance(payload["artifact_size"], bool) or not isinstance(payload["artifact_size"], int):
            raise ContractError("artifact_size must be an integer")
        if payload["artifact_size"] <= 0:
            raise ContractError("artifact_size must be positive")
        fit_start = parse_date(payload["fit_start"], label="fit_start")
        fit_end = parse_date(payload["fit_end"], label="fit_end")
        select_start = parse_date(payload["selection_start"], label="selection_start")
        select_end = parse_date(payload["selection_end"], label="selection_end")
        if not fit_start <= fit_end < select_start <= select_end:
            raise ContractError("artifact fit and selection windows are not strictly ordered")
        created = parse_aware_utc(payload["created_at_utc"], label="created_at_utc")
        published = parse_aware_utc(payload["published_at_utc"], label="published_at_utc")
        if created > published:
            raise ContractError("artifact creation cannot follow publication")
        if created.date() <= select_end:
            raise ContractError("artifact creation must follow completion of selection inputs")
        state_payload = {
            "qualification_state": payload["qualification_state"],
            "active": payload["active"],
            "betting_authorized": payload["betting_authorized"],
            "promotion_approval_sha256": payload["promotion_approval_sha256"],
        }
        assert_research_only_manifest(state_payload)
        return cls(
            schema_version=_require_artifact_schema(payload["schema_version"]),
            artifact_type=require_nonempty_text(payload["artifact_type"], label="artifact_type"),
            candidate_id=require_nonempty_text(payload["candidate_id"], label="candidate_id"),
            qualification_state=str(payload["qualification_state"]),
            active=False,
            betting_authorized=False,
            artifact_size=payload["artifact_size"],
            fit_start=fit_start,
            fit_end=fit_end,
            selection_start=select_start,
            selection_end=select_end,
            created_at_utc=created,
            published_at_utc=published,
            frozen_comparator_sha256=comparator,
            promotion_approval_sha256=None,
            **hashes,
        )

    def canonical_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "artifact_type": self.artifact_type,
            "candidate_id": self.candidate_id,
            "qualification_state": self.qualification_state,
            "active": self.active,
            "betting_authorized": self.betting_authorized,
            "artifact_sha256": self.artifact_sha256,
            "artifact_size": self.artifact_size,
            "code_sha256": self.code_sha256,
            "config_sha256": self.config_sha256,
            "data_manifest_sha256": self.data_manifest_sha256,
            "feature_schema_sha256": self.feature_schema_sha256,
            "dependency_lock_sha256": self.dependency_lock_sha256,
            "test_manifest_sha256": self.test_manifest_sha256,
            "fit_start": self.fit_start.isoformat(),
            "fit_end": self.fit_end.isoformat(),
            "selection_start": self.selection_start.isoformat(),
            "selection_end": self.selection_end.isoformat(),
            "created_at_utc": self.created_at_utc.isoformat().replace("+00:00", "Z"),
            "published_at_utc": self.published_at_utc.isoformat().replace("+00:00", "Z"),
            "frozen_comparator_sha256": self.frozen_comparator_sha256,
            "promotion_approval_sha256": self.promotion_approval_sha256,
        }


@dataclass(frozen=True)
class ValidatedArtifact:
    manifest: ArtifactManifest
    artifact_bytes: bytes
    artifact_sha256: str


def validate_artifact(
    *,
    manifest: ArtifactManifest,
    artifact_path: Path,
    evaluation_date: date | str,
    decision_horizon_utc: datetime,
    expected_candidate_id: str,
    expected_artifact_type: str,
    referenced_files: Mapping[str, Path],
) -> ValidatedArtifact:
    if manifest.schema_version != ARTIFACT_MANIFEST_SCHEMA:
        raise ContractError("artifact manifest schema is not approved")
    if manifest.candidate_id != require_nonempty_text(
        expected_candidate_id, label="expected_candidate_id"
    ):
        raise ContractError("artifact candidate identity mismatch")
    if manifest.artifact_type != require_nonempty_text(
        expected_artifact_type, label="expected_artifact_type"
    ):
        raise ContractError("artifact type mismatch")
    expected_reference_hashes = {
        "code_sha256": manifest.code_sha256,
        "config_sha256": manifest.config_sha256,
        "data_manifest_sha256": manifest.data_manifest_sha256,
        "feature_schema_sha256": manifest.feature_schema_sha256,
        "dependency_lock_sha256": manifest.dependency_lock_sha256,
        "test_manifest_sha256": manifest.test_manifest_sha256,
    }
    if manifest.frozen_comparator_sha256 is not None:
        expected_reference_hashes["frozen_comparator_sha256"] = (
            manifest.frozen_comparator_sha256
        )
    if set(referenced_files) != set(expected_reference_hashes):
        raise ContractError("artifact referenced-file set does not exactly match its manifest")
    for field, expected_hash in expected_reference_hashes.items():
        reference_path = assert_may_safe_path(referenced_files[field])
        if reference_path.is_symlink() or not reference_path.is_file():
            raise ContractError(f"artifact reference is not an eligible regular file: {field}")
        if sha256_file(reference_path) != expected_hash:
            raise ContractError(f"artifact referenced bytes mismatch: {field}")
    target_date = parse_date(evaluation_date, label="evaluation_date")
    horizon = parse_aware_utc(decision_horizon_utc, label="decision_horizon_utc")
    if manifest.selection_end >= target_date:
        raise ContractError("artifact selection window must end before evaluation date")
    if manifest.published_at_utc >= horizon:
        raise ContractError("artifact must be published strictly before the decision horizon")
    path = assert_may_safe_path(artifact_path)
    if path.is_symlink() or not path.is_file():
        raise ContractError("artifact is not an eligible regular file")
    try:
        artifact_bytes = path.read_bytes()
    except OSError as exc:
        raise ContractError("artifact cannot be opened") from exc
    digest = sha256_bytes(artifact_bytes)
    if len(artifact_bytes) != manifest.artifact_size or digest != manifest.artifact_sha256:
        raise ContractError("artifact bytes do not match manifest")
    return ValidatedArtifact(manifest, artifact_bytes, digest)


def _require_artifact_schema(value: object) -> str:
    if value != ARTIFACT_MANIFEST_SCHEMA:
        raise ContractError("artifact manifest schema is not approved")
    return ARTIFACT_MANIFEST_SCHEMA


def publish_immutable_artifact(
    *, artifact_bytes: bytes, manifest_payload: Mapping[str, object], artifact_root: Path
) -> tuple[Path, Path]:
    """Publish content-addressed bytes first and its authoritative manifest last.

    A crash can leave an unreferenced artifact object, never a trusted partial pair.
    """
    manifest = ArtifactManifest.from_mapping(manifest_payload)
    if sha256_bytes(artifact_bytes) != manifest.artifact_sha256 or len(artifact_bytes) != manifest.artifact_size:
        raise ContractError("artifact payload does not match proposed manifest")
    root = assert_may_safe_path(artifact_root)
    root.mkdir(parents=True, exist_ok=True)
    artifact_path = assert_may_safe_path(root / f"{manifest.artifact_sha256}.artifact", allowed_root=root)
    manifest_bytes = canonical_json_bytes(manifest.canonical_dict())
    manifest_hash = sha256_bytes(manifest_bytes)
    manifest_path = assert_may_safe_path(root / f"{manifest_hash}.manifest.json", allowed_root=root)
    _publish_once(artifact_path, artifact_bytes, root)
    _publish_once(manifest_path, manifest_bytes, root)
    return artifact_path, manifest_path


def _publish_once(target: Path, data: bytes, root: Path) -> None:
    if target.exists():
        if target.read_bytes() != data:
            raise ContractError("immutable publication conflict")
        return
    descriptor, temp_name = tempfile.mkstemp(prefix=".omega-publish-", dir=root)
    temp = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            flush_sync_and_harden(handle)
        try:
            os.link(temp, target)
        except FileExistsError:
            if target.read_bytes() != data:
                raise ContractError("concurrent immutable publication conflict")
        if hasattr(os, "O_DIRECTORY"):
            directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass
