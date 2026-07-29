"""Offline qualification for the official 2023 PA-volume source release.

This module has no transport capability.  Qualification replays the retained
schedule and final-feed receipts, reconstructs the exact official-starter
projection, binds the active PA builder and dependency lock, and publishes a
deterministic authority bundle.  A second, deliberately separate operation
turns an independently digest-anchored attestation into the runtime receipt
consumed by ``shared_pa_pa_volume_source_authority_v1``.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_pa_volume_official_source_release_v1 as source_release
from scripts import capture_pa_volume_official_source_v1 as capture
from src.evaluation.pa_volume_official_feed_projection_v1 import (
    build_official_feed_projection,
    canonical_json_bytes,
)
from src.evaluation import pa_volume_historical_source_access_v1 as historical_access
from src.evaluation.pa_volume_source_truth_v2 import (
    PAVolumeSourceTruthError,
    build_pa_volume_artifact,
    validate_pa_volume_artifact,
)
from src.evaluation.shared_pa_pa_volume_source_authority_v1 import (
    AUTHORITY_SCHEMA,
    COMPLETE_DECISION,
    REBUILD_COMPLETE_DECISION,
    REBUILD_SCHEMA,
    RECEIPT_SCHEMA,
)


EXTERNAL_ATTESTATION_SCHEMA = "pa-volume-source-authority-external-attestation-v1"
AUTHORITY_MANIFEST_RELATIVE = "authority/manifest.json"
DEFAULT_RECEIPT_RELATIVE = "authority/runtime_receipt.json"
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
SOURCE_RELEASE_FILES = {
    "projection.json",
    "schedule_index.json",
    "source_manifest.json",
}
QUALIFIED_FILES = {
    "artifacts/pa_volume.json",
    AUTHORITY_MANIFEST_RELATIVE,
    "locks/requirements.lock",
    "rebuild/manifest.json",
    "source_access/authorization.json",
    "source_access/runtime_policy.json",
    "source_release/projection.json",
    "source_release/schedule_index.json",
    "source_release/source_manifest.json",
    "source_release_external_verification.json",
}
REQUIRED_AUTHORITY = {
    "raw_transport_request_and_response_receipts": True,
    "independent_official_source_receipts": True,
    "exact_reproducible_dependency_lock": True,
    "external_expected_release_digest": True,
}
PROTECTED = {
    "may_2026_accessed": False,
    "selection_2024_accessed": False,
    "spent_hr_confirmation_2025_accessed": False,
    "prices_accessed": False,
    "prospective_evidence_accessed": False,
    "prospective_backfill_performed": False,
}


class PAVolumeQualificationError(ValueError):
    """Official source evidence is incomplete, mutable, or contradictory."""


@dataclass(frozen=True)
class ReplayedSourceRelease:
    manifest: Mapping[str, Any]
    manifest_bytes: bytes
    manifest_sha256: str
    external_verification_bytes: bytes
    external_verification_sha256: str
    schedule_index_bytes: bytes
    projection_bytes: bytes
    projection_rows: tuple[Mapping[str, Any], ...]
    dependency_lock_bytes: bytes
    dependency_lock_sha256: str
    source_access_authorization_bytes: bytes
    source_access_authorization_sha256: str
    runtime_policy_bytes: bytes
    runtime_policy_sha256: str
    latest_source_observation_utc: datetime


@dataclass(frozen=True)
class QualifiedBundle:
    root: Path
    authority: Mapping[str, Any]
    authority_bytes: bytes
    authority_sha256: str
    authority_manifest_relative: str
    source_release_manifest_sha256: str
    source_release_external_verification_sha256: str
    rebuild_manifest_sha256: str
    pa_volume_artifact_sha256: str
    dependency_lock_sha256: str
    source_access_authorization_sha256: str
    runtime_policy_sha256: str
    qualified_at_utc: datetime


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _compact_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise PAVolumeQualificationError(
            f"{label} must be a lowercase SHA-256 digest"
        )
    return value


def _utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise PAVolumeQualificationError(f"{label} must be canonical UTC")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise PAVolumeQualificationError(f"{label} must be canonical UTC") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise PAVolumeQualificationError(f"{label} must be canonical UTC")
    if parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") != value:
        raise PAVolumeQualificationError(f"{label} must be canonical UTC")
    return parsed.astimezone(timezone.utc)


def _microsecond_stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")


def _is_reparse(path: Path) -> bool:
    info = path.lstat()
    attributes = getattr(info, "st_file_attributes", 0)
    return path.is_symlink() or bool(
        attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _guard_ancestors(path: Path, label: str) -> Path:
    value = Path(os.path.abspath(os.fspath(path)))
    cursor = value
    while not cursor.exists() and cursor != cursor.parent:
        cursor = cursor.parent
    while True:
        if _is_reparse(cursor):
            raise PAVolumeQualificationError(
                f"{label} traverses a symlink, junction, or reparse point"
            )
        if cursor == cursor.parent:
            break
        cursor = cursor.parent
    return value


def _input_root(path: Path, label: str) -> Path:
    value = _guard_ancestors(path, label)
    if ".." in path.parts or not value.is_dir() or _is_reparse(value):
        raise PAVolumeQualificationError(f"{label} is missing or unsafe")
    return value


def _input_file(path: Path, label: str) -> Path:
    value = _guard_ancestors(path, label)
    if ".." in path.parts or not value.is_file() or _is_reparse(value):
        raise PAVolumeQualificationError(f"{label} is missing or unsafe")
    return value


def _safe_child(root: Path, relative: str, label: str, *, require: bool = True) -> Path:
    rel = Path(relative)
    if rel.is_absolute() or ".." in rel.parts or rel.as_posix() != relative:
        raise PAVolumeQualificationError(f"{label} path is unsafe")
    candidate = _guard_ancestors(root / rel, label)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise PAVolumeQualificationError(f"{label} escapes its root") from exc
    if require and (not candidate.is_file() or _is_reparse(candidate)):
        raise PAVolumeQualificationError(f"{label} is missing or unsafe")
    return candidate


def _output_root(path: Path) -> Path:
    value = _guard_ancestors(path, "qualification output")
    if ".." in path.parts or value.parent == value or value.exists():
        raise PAVolumeQualificationError(
            "qualification output is unsafe or already exists"
        )
    return value


def _exact_files(root: Path) -> set[str]:
    files: set[str] = set()
    for path in root.rglob("*"):
        if _is_reparse(path):
            raise PAVolumeQualificationError("bundle contains a reparse point")
        if path.is_file():
            files.add(path.relative_to(root).as_posix())
    return files


def _read_json(path: Path, label: str) -> tuple[Mapping[str, Any], bytes]:
    raw = _input_file(path, label).read_bytes()
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PAVolumeQualificationError(f"{label} is not valid JSON") from exc
    if not isinstance(value, Mapping):
        raise PAVolumeQualificationError(f"{label} root is malformed")
    return value, raw


def _write(path: Path, payload: bytes) -> None:
    if path.exists() or path.is_symlink():
        raise PAVolumeQualificationError(f"refusing to overwrite {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    if temporary.exists():
        raise PAVolumeQualificationError("stale qualification temporary exists")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def _active_reviewed_source_files() -> list[dict[str, str]]:
    root = Path(__file__).resolve().parents[1]
    relatives = [
        "scripts/capture_direct_batter_pa_source_transport_v2.py",
        "scripts/capture_pa_volume_official_source_v1.py",
        "scripts/build_pa_volume_official_source_release_v1.py",
        "src/evaluation/pa_volume_official_feed_projection_v1.py",
        "src/evaluation/pa_volume_source_truth_v2.py",
    ]
    return [
        {
            "path": relative,
            "sha256": sha256_bytes((root / relative).read_bytes()),
        }
        for relative in relatives
    ]


def _source_manifest(value: Mapping[str, Any]) -> None:
    expected_keys = {
        "schema_version",
        "status",
        "season",
        "research_only",
        "betting_authorized",
        "model_fitting_performed",
        "probabilities_generated",
        "protected_data",
        "source_access_authorization_id",
        "source_access_authorization_sha256",
        "schedule_capture_observed_digest",
        "schedule_capture_manifest_sha256",
        "feed_capture_observed_digest",
        "feed_capture_manifest_sha256",
        "schedule_index_sha256",
        "projection_sha256",
        "parser_source_sha256",
        "reviewed_source_files",
        "reviewed_source_bundle_sha256",
        "dependency_lock_sha256",
        "game_count",
        "row_count",
    }
    if set(value) != expected_keys:
        raise PAVolumeQualificationError("source manifest positive schema differs")
    if (
        value.get("schema_version") != source_release.SOURCE_SCHEMA
        or value.get("status") != "AWAITING_INDEPENDENT_EXTERNAL_VERIFICATION"
        or value.get("season") != 2023
        or value.get("research_only") is not True
        or value.get("betting_authorized") is not False
        or value.get("model_fitting_performed") is not False
        or value.get("probabilities_generated") is not False
        or value.get("protected_data") != PROTECTED
    ):
        raise PAVolumeQualificationError("source manifest scope or safety state differs")
    for field in (
        "schedule_capture_observed_digest",
        "schedule_capture_manifest_sha256",
        "feed_capture_observed_digest",
        "feed_capture_manifest_sha256",
        "schedule_index_sha256",
        "projection_sha256",
        "parser_source_sha256",
        "reviewed_source_bundle_sha256",
        "dependency_lock_sha256",
        "source_access_authorization_sha256",
    ):
        _sha(value.get(field), f"source manifest {field}")
    if not isinstance(value.get("source_access_authorization_id"), str) or not value[
        "source_access_authorization_id"
    ]:
        raise PAVolumeQualificationError("source-access authorization identity differs")
    game_count = value.get("game_count")
    row_count = value.get("row_count")
    if (
        isinstance(game_count, bool)
        or not isinstance(game_count, int)
        or game_count <= 0
        or isinstance(row_count, bool)
        or not isinstance(row_count, int)
        or row_count != game_count * 18
    ):
        raise PAVolumeQualificationError("source manifest projection coverage differs")


def _feed_documents(
    feed_root: Path, requests: Iterable[Mapping[str, Any]]
) -> dict[int, bytes]:
    result: dict[int, bytes] = {}
    for request in requests:
        game_pk = request["expected"]["game_pk"]
        path = _safe_child(
            feed_root,
            f"feeds/{request['request_id']}/response.json",
            "retained official feed",
        )
        result[game_pk] = path.read_bytes()
    return result


def _replay_schedule_index(
    requests: Iterable[Mapping[str, Any]], feeds: Mapping[int, bytes]
) -> dict[str, Any]:
    games: list[dict[str, Any]] = []
    for request in requests:
        expected = request["expected"]
        game_pk = expected["game_pk"]
        try:
            feed = json.loads(feeds[game_pk])
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PAVolumeQualificationError("retained feed is invalid JSON") from exc
        game_data = feed.get("gameData") if isinstance(feed, Mapping) else None
        if not isinstance(game_data, Mapping):
            raise PAVolumeQualificationError("retained feed lacks gameData")
        games.append(
            {
                "game_pk": game_pk,
                "official_date": (game_data.get("datetime") or {}).get(
                    "officialDate"
                ),
                "game_type": "R",
                "away_team_id": expected["away_team_id"],
                "home_team_id": expected["home_team_id"],
            }
        )
    games.sort(key=lambda row: (str(row["official_date"]), row["game_pk"]))
    return {
        "schema_version": "pa-volume-2023-schedule-index-v1",
        "season": 2023,
        "fields": [
            "game_pk",
            "official_date",
            "game_type",
            "away_team_id",
            "home_team_id",
        ],
        "games": games,
    }


def _source_receipt_times(
    schedule_root: Path,
    feed_root: Path,
    requests: Iterable[Mapping[str, Any]],
) -> tuple[datetime, datetime]:
    request_times: list[datetime] = []
    observations: list[datetime] = []
    schedule_receipt, _ = _read_json(
        schedule_root / "receipt.json", "schedule receipt"
    )
    schedule_response = schedule_receipt.get("response") or {}
    request_times.append(
        _utc(schedule_response.get("requested_at_utc"), "schedule request")
    )
    observations.append(
        _utc(
            schedule_response.get("observed_at_utc"),
            "schedule observation",
        )
    )
    for request in requests:
        receipt, _ = _read_json(
            feed_root / "feeds" / request["request_id"] / "receipt.json",
            "feed receipt",
        )
        response = receipt.get("response") or {}
        request_times.append(
            _utc(response.get("requested_at_utc"), "feed request")
        )
        observations.append(
            _utc(
                response.get("observed_at_utc"),
                "feed observation",
            )
        )
    return min(request_times), max(observations)


def replay_source_release(
    *,
    source_release_dir: Path,
    schedule_capture_dir: Path,
    feed_capture_dir: Path,
    source_release_external_verification_path: Path,
    expected_source_release_external_verification_sha256: str,
    dependency_lock_path: Path,
    source_access_authorization_path: Path,
    expected_source_access_authorization_sha256: str,
    runtime_policy_path: Path,
    expected_runtime_policy_sha256: str,
) -> ReplayedSourceRelease:
    """Independently replay every retained receipt and projection byte."""

    release_root = _input_root(source_release_dir, "source release")
    if _exact_files(release_root) != SOURCE_RELEASE_FILES:
        raise PAVolumeQualificationError("source release exact file set differs")
    schedule_root = _input_root(schedule_capture_dir, "schedule capture")
    feed_root = _input_root(feed_capture_dir, "feed capture")
    lock_path = _input_file(dependency_lock_path, "dependency lock")
    lock_bytes = lock_path.read_bytes()
    if not lock_bytes:
        raise PAVolumeQualificationError("dependency lock is empty")
    lock_sha = sha256_bytes(lock_bytes)
    authorization_path = _input_file(
        source_access_authorization_path, "source-access authorization"
    )
    authorization_bytes = authorization_path.read_bytes()
    expected_authorization_sha = _sha(
        expected_source_access_authorization_sha256,
        "expected source-access authorization",
    )
    if sha256_bytes(authorization_bytes) != expected_authorization_sha:
        raise PAVolumeQualificationError(
            "source-access authorization differs from expected digest"
        )
    policy_path = _input_file(runtime_policy_path, "runtime policy")
    policy_bytes = policy_path.read_bytes()
    if not policy_bytes:
        raise PAVolumeQualificationError("runtime policy is empty")
    expected_policy_sha = _sha(
        expected_runtime_policy_sha256, "expected runtime policy"
    )
    if sha256_bytes(policy_bytes) != expected_policy_sha:
        raise PAVolumeQualificationError(
            "runtime policy differs from expected digest"
        )

    manifest, manifest_bytes = _read_json(
        release_root / "source_manifest.json", "source manifest"
    )
    _source_manifest(manifest)
    schedule_index, schedule_index_bytes = _read_json(
        release_root / "schedule_index.json", "schedule index"
    )
    projection, projection_bytes = _read_json(
        release_root / "projection.json", "official projection"
    )
    if sha256_bytes(schedule_index_bytes) != manifest["schedule_index_sha256"]:
        raise PAVolumeQualificationError("schedule index bytes differ from manifest")
    if sha256_bytes(projection_bytes) != manifest["projection_sha256"]:
        raise PAVolumeQualificationError("projection bytes differ from manifest")
    if lock_sha != manifest["dependency_lock_sha256"]:
        raise PAVolumeQualificationError("dependency lock differs from source release")

    active_reviewed = _active_reviewed_source_files()
    if manifest["reviewed_source_files"] != active_reviewed:
        raise PAVolumeQualificationError("active reviewed source bytes differ")
    reviewed_bundle_sha = sha256_bytes(canonical_json_bytes(active_reviewed))
    if manifest["reviewed_source_bundle_sha256"] != reviewed_bundle_sha:
        raise PAVolumeQualificationError("reviewed source bundle digest differs")
    parser_sha = active_reviewed[3]["sha256"]
    if manifest["parser_source_sha256"] != parser_sha:
        raise PAVolumeQualificationError("active projection parser differs")

    try:
        schedule_manifest = capture.verify_schedule_capture(
            schedule_root, manifest["schedule_capture_observed_digest"]
        )
        feed_manifest = capture.verify_feed_capture(
            feed_root, manifest["feed_capture_observed_digest"]
        )
    except Exception as exc:
        raise PAVolumeQualificationError(
            "retained official capture replay failed"
        ) from exc
    expected_capture_source = capture.capture_source_bundle_sha256()
    if (
        schedule_manifest["source_bundle_sha256"] != expected_capture_source
        or feed_manifest["source_bundle_sha256"] != expected_capture_source
    ):
        raise PAVolumeQualificationError("active capture source bytes differ")

    plan, _ = _read_json(feed_root / "plan.json", "feed plan")
    try:
        requests = capture._plan(plan)
    except Exception as exc:
        raise PAVolumeQualificationError("feed plan replay failed") from exc
    first_request, latest_observation = _source_receipt_times(
        schedule_root, feed_root, requests
    )
    try:
        verified_access = (
            historical_access.verify_historical_source_access_authorization(
                authorization_path=authorization_path,
                expected_authorization_sha256=expected_authorization_sha,
                expected_runtime_policy_sha256=expected_policy_sha,
                expected_source_bundle_sha256=expected_capture_source,
                access_time_utc=_microsecond_stamp(first_request),
            )
        )
    except historical_access.HistoricalSourceAccessError as exc:
        raise PAVolumeQualificationError(
            "source-access authorization verification failed"
        ) from exc
    if (
        feed_manifest["schedule_capture_digest"]
        != schedule_manifest["observed_capture_digest"]
        or verified_access.authorization_id
        != manifest["source_access_authorization_id"]
        or verified_access.authorization_file_sha256
        != manifest["source_access_authorization_sha256"]
        or verified_access.runtime_policy_sha256 != expected_policy_sha
        or verified_access.source_bundle_sha256 != expected_capture_source
        or schedule_manifest["source_access_authorization_id"]
        != manifest["source_access_authorization_id"]
        or feed_manifest["source_access_authorization_id"]
        != manifest["source_access_authorization_id"]
        or schedule_manifest["source_access_authorization_sha256"]
        != manifest["source_access_authorization_sha256"]
        or feed_manifest["source_access_authorization_sha256"]
        != manifest["source_access_authorization_sha256"]
        or manifest["schedule_capture_manifest_sha256"]
        != sha256_bytes((schedule_root / "manifest.json").read_bytes())
        or manifest["feed_capture_manifest_sha256"]
        != sha256_bytes((feed_root / "manifest.json").read_bytes())
    ):
        raise PAVolumeQualificationError("source release capture bindings differ")
    feeds = _feed_documents(feed_root, requests)
    replayed_index = _replay_schedule_index(requests, feeds)
    if canonical_json_bytes(replayed_index) != schedule_index_bytes:
        raise PAVolumeQualificationError(
            "schedule index differs from independently replayed feeds"
        )
    try:
        replayed_projection = build_official_feed_projection(
            schedule_index=replayed_index,
            feeds_by_game_pk=feeds,
            schedule_capture_manifest_sha256=manifest[
                "schedule_capture_manifest_sha256"
            ],
            feed_capture_manifest_sha256=manifest["feed_capture_manifest_sha256"],
            parser_source_sha256=parser_sha,
        )
    except Exception as exc:
        raise PAVolumeQualificationError(
            "official projection semantic replay failed"
        ) from exc
    if canonical_json_bytes(replayed_projection) != projection_bytes:
        raise PAVolumeQualificationError(
            "projection differs from independently replayed official receipts"
        )
    if (
        projection.get("game_count") != manifest["game_count"]
        or projection.get("row_count") != manifest["row_count"]
    ):
        raise PAVolumeQualificationError("source manifest projection counts differ")
    rows = (projection.get("projection") or {}).get("rows")
    if not isinstance(rows, list) or len(rows) != manifest["row_count"]:
        raise PAVolumeQualificationError("official projection rows are missing")

    verification_path = _input_file(
        source_release_external_verification_path,
        "source-release external verification",
    )
    expected_verification_sha = _sha(
        expected_source_release_external_verification_sha256,
        "expected source-release external verification",
    )
    verification_bytes = verification_path.read_bytes()
    if sha256_bytes(verification_bytes) != expected_verification_sha:
        raise PAVolumeQualificationError(
            "source-release external verification differs from expected digest"
        )
    try:
        verification = json.loads(verification_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PAVolumeQualificationError(
            "source-release external verification is invalid JSON"
        ) from exc
    expected_verification = {
        "schema_version": source_release.EXTERNAL_VERIFICATION_SCHEMA,
        "status": "INDEPENDENT_SOURCE_RELEASE_VERIFIED",
        "source_access_authorization_id": manifest[
            "source_access_authorization_id"
        ],
        "source_access_authorization_sha256": manifest[
            "source_access_authorization_sha256"
        ],
        "source_manifest_sha256": sha256_bytes(manifest_bytes),
        "projection_sha256": sha256_bytes(projection_bytes),
        "schedule_capture_observed_digest": manifest[
            "schedule_capture_observed_digest"
        ],
        "feed_capture_observed_digest": manifest[
            "feed_capture_observed_digest"
        ],
        "dependency_lock_sha256": lock_sha,
        "reviewed_source_bundle_sha256": reviewed_bundle_sha,
        "protected_data": PROTECTED,
    }
    if verification != expected_verification:
        raise PAVolumeQualificationError(
            "source-release external verification is contradictory"
        )

    return ReplayedSourceRelease(
        manifest=manifest,
        manifest_bytes=manifest_bytes,
        manifest_sha256=sha256_bytes(manifest_bytes),
        external_verification_bytes=verification_bytes,
        external_verification_sha256=expected_verification_sha,
        schedule_index_bytes=schedule_index_bytes,
        projection_bytes=projection_bytes,
        projection_rows=tuple(rows),
        dependency_lock_bytes=lock_bytes,
        dependency_lock_sha256=lock_sha,
        source_access_authorization_bytes=authorization_bytes,
        source_access_authorization_sha256=expected_authorization_sha,
        runtime_policy_bytes=policy_bytes,
        runtime_policy_sha256=expected_policy_sha,
        latest_source_observation_utc=latest_observation,
    )


def qualify_source_release(
    *,
    source_release_dir: Path,
    schedule_capture_dir: Path,
    feed_capture_dir: Path,
    source_release_external_verification_path: Path,
    expected_source_release_external_verification_sha256: str,
    dependency_lock_path: Path,
    source_access_authorization_path: Path,
    expected_source_access_authorization_sha256: str,
    runtime_policy_path: Path,
    expected_runtime_policy_sha256: str,
    expected_builder_source_sha256: str,
    qualified_at_utc: str,
    output_dir: Path,
) -> dict[str, Any]:
    """Publish an authority bundle only after complete deterministic replay."""

    output = _output_root(output_dir)
    replayed = replay_source_release(
        source_release_dir=source_release_dir,
        schedule_capture_dir=schedule_capture_dir,
        feed_capture_dir=feed_capture_dir,
        source_release_external_verification_path=(
            source_release_external_verification_path
        ),
        expected_source_release_external_verification_sha256=(
            expected_source_release_external_verification_sha256
        ),
        dependency_lock_path=dependency_lock_path,
        source_access_authorization_path=source_access_authorization_path,
        expected_source_access_authorization_sha256=(
            expected_source_access_authorization_sha256
        ),
        runtime_policy_path=runtime_policy_path,
        expected_runtime_policy_sha256=expected_runtime_policy_sha256,
    )
    qualified_at = _utc(qualified_at_utc, "qualified_at_utc")
    if qualified_at < replayed.latest_source_observation_utc:
        raise PAVolumeQualificationError(
            "qualification predates retained source observations"
        )
    builder_path = (
        Path(__file__).resolve().parents[1]
        / "src/evaluation/pa_volume_source_truth_v2.py"
    )
    builder_sha = sha256_bytes(builder_path.read_bytes())
    if builder_sha != _sha(
        expected_builder_source_sha256, "expected builder source"
    ):
        raise PAVolumeQualificationError(
            "active PA-volume builder differs from externally expected digest"
        )
    try:
        artifact = build_pa_volume_artifact(
            rows=replayed.projection_rows,
            source_release_manifest_sha256=replayed.manifest_sha256,
            source_release_external_verification_sha256=(
                replayed.external_verification_sha256
            ),
            dependency_lock_sha256=replayed.dependency_lock_sha256,
            builder_source_sha256=builder_sha,
        )
        validate_pa_volume_artifact(artifact)
    except PAVolumeSourceTruthError as exc:
        raise PAVolumeQualificationError("PA-volume artifact build failed") from exc
    artifact_bytes = canonical_json_bytes(artifact)
    artifact_sha = sha256_bytes(artifact_bytes)
    rebuild = {
        "schema_version": REBUILD_SCHEMA,
        "decision": REBUILD_COMPLETE_DECISION,
        "source_season": 2023,
        "fit_seasons": [2023],
        "source_release_manifest_sha256": replayed.manifest_sha256,
        "source_release_external_verification_sha256": (
            replayed.external_verification_sha256
        ),
        "pa_volume_artifact_path": "artifacts/pa_volume.json",
        "pa_volume_artifact_sha256": artifact_sha,
        "dependency_lock_sha256": replayed.dependency_lock_sha256,
        "manual_coefficients": False,
        "protected_data": PROTECTED,
    }
    rebuild_bytes = canonical_json_bytes(rebuild)
    rebuild_sha = sha256_bytes(rebuild_bytes)
    authority_id = f"pa-volume-official-2023-{artifact_sha[:16]}"
    authority = {
        "schema_version": AUTHORITY_SCHEMA,
        "authority_id": authority_id,
        "decision": COMPLETE_DECISION,
        "research_only": True,
        "betting_authorized": False,
        "eligible_for_model_consumption": True,
        "blockers": [],
        "source_season": 2023,
        "qualified_at_utc": qualified_at_utc,
        "required_authority": REQUIRED_AUTHORITY,
        "observed_authority": REQUIRED_AUTHORITY,
        "protected_data": PROTECTED,
        "source_release_manifest": {
            "path": "source_release/source_manifest.json",
            "sha256": replayed.manifest_sha256,
        },
        "source_release_external_verification": {
            "path": "source_release_external_verification.json",
            "sha256": replayed.external_verification_sha256,
        },
        "rebuild_manifest": {
            "path": "rebuild/manifest.json",
            "sha256": rebuild_sha,
        },
        "pa_volume_artifact": {
            "path": "artifacts/pa_volume.json",
            "sha256": artifact_sha,
        },
        "exact_dependency_lock": {
            "path": "locks/requirements.lock",
            "sha256": replayed.dependency_lock_sha256,
        },
    }
    authority_bytes = canonical_json_bytes(authority)
    staging = output.with_name(output.name + ".staging")
    if staging.exists():
        raise PAVolumeQualificationError("stale qualification staging exists")
    staging.mkdir(parents=True)
    try:
        _write(
            staging / "source_release/source_manifest.json",
            replayed.manifest_bytes,
        )
        _write(
            staging / "source_release/schedule_index.json",
            replayed.schedule_index_bytes,
        )
        _write(
            staging / "source_release/projection.json", replayed.projection_bytes
        )
        _write(
            staging / "source_release_external_verification.json",
            replayed.external_verification_bytes,
        )
        _write(
            staging / "locks/requirements.lock", replayed.dependency_lock_bytes
        )
        _write(
            staging / "source_access/authorization.json",
            replayed.source_access_authorization_bytes,
        )
        _write(
            staging / "source_access/runtime_policy.json",
            replayed.runtime_policy_bytes,
        )
        _write(staging / "artifacts/pa_volume.json", artifact_bytes)
        _write(staging / "rebuild/manifest.json", rebuild_bytes)
        _write(staging / AUTHORITY_MANIFEST_RELATIVE, authority_bytes)
        if _exact_files(staging) != QUALIFIED_FILES:
            raise PAVolumeQualificationError(
                "qualified authority bundle exact file set differs"
            )
        os.replace(staging, output)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {
        "authority_id": authority_id,
        "authority_manifest_sha256": sha256_bytes(authority_bytes),
        "pa_volume_artifact_sha256": artifact_sha,
        "rebuild_manifest_sha256": rebuild_sha,
    }


def _binding(
    root: Path, value: Any, label: str
) -> tuple[str, str, Path]:
    if not isinstance(value, Mapping) or set(value) != {"path", "sha256"}:
        raise PAVolumeQualificationError(f"{label} binding differs")
    relative = value.get("path")
    expected = _sha(value.get("sha256"), f"{label} digest")
    if not isinstance(relative, str):
        raise PAVolumeQualificationError(f"{label} path differs")
    path = _safe_child(root, relative, label)
    if sha256_bytes(path.read_bytes()) != expected:
        raise PAVolumeQualificationError(f"{label} bytes differ")
    return relative, expected, path


def verify_qualified_bundle(
    *, authority_root: Path, authority_manifest_relative: str
) -> QualifiedBundle:
    """Verify a pre-receipt qualification bundle without trusting its claims."""

    root = _input_root(authority_root, "authority root")
    if _exact_files(root) != QUALIFIED_FILES:
        raise PAVolumeQualificationError("qualified bundle exact file set differs")
    authority_path = _safe_child(
        root, authority_manifest_relative, "authority manifest"
    )
    authority, authority_bytes = _read_json(authority_path, "authority manifest")
    expected_keys = {
        "schema_version",
        "authority_id",
        "decision",
        "research_only",
        "betting_authorized",
        "eligible_for_model_consumption",
        "blockers",
        "source_season",
        "qualified_at_utc",
        "required_authority",
        "observed_authority",
        "protected_data",
        "source_release_manifest",
        "source_release_external_verification",
        "rebuild_manifest",
        "pa_volume_artifact",
        "exact_dependency_lock",
    }
    if set(authority) != expected_keys:
        raise PAVolumeQualificationError("authority manifest positive schema differs")
    if (
        authority.get("schema_version") != AUTHORITY_SCHEMA
        or authority.get("decision") != COMPLETE_DECISION
        or authority.get("research_only") is not True
        or authority.get("betting_authorized") is not False
        or authority.get("eligible_for_model_consumption") is not True
        or authority.get("blockers") != []
        or authority.get("source_season") != 2023
        or authority.get("required_authority") != REQUIRED_AUTHORITY
        or authority.get("observed_authority") != REQUIRED_AUTHORITY
        or authority.get("protected_data") != PROTECTED
    ):
        raise PAVolumeQualificationError("authority scope or decision differs")
    authority_id = authority.get("authority_id")
    if not isinstance(authority_id, str) or not authority_id:
        raise PAVolumeQualificationError("authority identity differs")
    qualified_at = _utc(authority.get("qualified_at_utc"), "qualified_at_utc")
    source_relative, source_sha, source_path = _binding(
        root, authority["source_release_manifest"], "source release manifest"
    )
    verification_relative, verification_sha, _ = _binding(
        root,
        authority["source_release_external_verification"],
        "source release external verification",
    )
    rebuild_relative, rebuild_sha, rebuild_path = _binding(
        root, authority["rebuild_manifest"], "rebuild manifest"
    )
    artifact_relative, artifact_sha, artifact_path = _binding(
        root, authority["pa_volume_artifact"], "PA-volume artifact"
    )
    lock_relative, lock_sha, _ = _binding(
        root, authority["exact_dependency_lock"], "dependency lock"
    )
    if (
        source_relative != "source_release/source_manifest.json"
        or verification_relative != "source_release_external_verification.json"
        or rebuild_relative != "rebuild/manifest.json"
        or artifact_relative != "artifacts/pa_volume.json"
        or lock_relative != "locks/requirements.lock"
    ):
        raise PAVolumeQualificationError("authority binding paths differ")
    source_manifest, _ = _read_json(source_path, "qualified source manifest")
    _source_manifest(source_manifest)
    if source_manifest["dependency_lock_sha256"] != lock_sha:
        raise PAVolumeQualificationError("qualified source lock binding differs")
    authorization_path = _safe_child(
        root, "source_access/authorization.json", "source-access authorization"
    )
    authorization_bytes = authorization_path.read_bytes()
    authorization_sha = sha256_bytes(authorization_bytes)
    policy_path = _safe_child(
        root, "source_access/runtime_policy.json", "runtime policy"
    )
    policy_bytes = policy_path.read_bytes()
    if not policy_bytes:
        raise PAVolumeQualificationError("qualified runtime policy is empty")
    policy_sha = sha256_bytes(policy_bytes)
    try:
        authorization_document = json.loads(authorization_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PAVolumeQualificationError(
            "qualified source-access authorization is invalid JSON"
        ) from exc
    if not isinstance(authorization_document, Mapping):
        raise PAVolumeQualificationError(
            "qualified source-access authorization is invalid JSON"
        )
    try:
        verified_access = (
            historical_access.verify_historical_source_access_authorization(
                authorization_path=authorization_path,
                expected_authorization_sha256=authorization_sha,
                expected_runtime_policy_sha256=policy_sha,
                expected_source_bundle_sha256=(
                    capture.capture_source_bundle_sha256()
                ),
                access_time_utc=authorization_document.get("valid_from_utc"),
            )
        )
    except historical_access.HistoricalSourceAccessError as exc:
        raise PAVolumeQualificationError(
            "qualified source-access authorization verification failed"
        ) from exc
    if (
        source_manifest["source_access_authorization_id"]
        != verified_access.authorization_id
        or source_manifest["source_access_authorization_sha256"]
        != authorization_sha
    ):
        raise PAVolumeQualificationError(
            "qualified source-access authorization binding differs"
        )
    artifact, _ = _read_json(artifact_path, "PA-volume artifact")
    try:
        validate_pa_volume_artifact(artifact)
    except PAVolumeSourceTruthError as exc:
        raise PAVolumeQualificationError("PA-volume artifact is invalid") from exc
    bindings = ((artifact.get("source") or {}).get("bindings"))
    builder_path = (
        Path(__file__).resolve().parents[1]
        / "src/evaluation/pa_volume_source_truth_v2.py"
    )
    expected_bindings = {
        "source_release_manifest_sha256": source_sha,
        "source_release_external_verification_sha256": verification_sha,
        "dependency_lock_sha256": lock_sha,
        "builder_source_sha256": sha256_bytes(builder_path.read_bytes()),
    }
    if bindings != expected_bindings:
        raise PAVolumeQualificationError("PA-volume artifact bindings differ")
    rebuild, _ = _read_json(rebuild_path, "rebuild manifest")
    expected_rebuild = {
        "schema_version": REBUILD_SCHEMA,
        "decision": REBUILD_COMPLETE_DECISION,
        "source_season": 2023,
        "fit_seasons": [2023],
        "source_release_manifest_sha256": source_sha,
        "source_release_external_verification_sha256": verification_sha,
        "pa_volume_artifact_path": artifact_relative,
        "pa_volume_artifact_sha256": artifact_sha,
        "dependency_lock_sha256": lock_sha,
        "manual_coefficients": False,
        "protected_data": PROTECTED,
    }
    if rebuild != expected_rebuild:
        raise PAVolumeQualificationError("qualified rebuild manifest differs")
    return QualifiedBundle(
        root=root,
        authority=authority,
        authority_bytes=authority_bytes,
        authority_sha256=sha256_bytes(authority_bytes),
        authority_manifest_relative=authority_manifest_relative,
        source_release_manifest_sha256=source_sha,
        source_release_external_verification_sha256=verification_sha,
        rebuild_manifest_sha256=rebuild_sha,
        pa_volume_artifact_sha256=artifact_sha,
        dependency_lock_sha256=lock_sha,
        source_access_authorization_sha256=authorization_sha,
        runtime_policy_sha256=policy_sha,
        qualified_at_utc=qualified_at,
    )


def emit_external_runtime_receipt(
    *,
    authority_root: Path,
    authority_manifest_relative: str,
    expected_authority_manifest_sha256: str,
    external_attestation_path: Path,
    expected_external_attestation_sha256: str,
    decision_time_utc: str,
    output_relative: str = DEFAULT_RECEIPT_RELATIVE,
) -> dict[str, Any]:
    """Emit the downstream runtime receipt from independent authority only."""

    root = _input_root(authority_root, "authority root")
    output = _safe_child(root, output_relative, "runtime receipt", require=False)
    if output.exists() or output.is_symlink():
        raise PAVolumeQualificationError("refusing to overwrite runtime receipt")
    bundle = verify_qualified_bundle(
        authority_root=root,
        authority_manifest_relative=authority_manifest_relative,
    )
    expected_authority = _sha(
        expected_authority_manifest_sha256,
        "expected authority manifest",
    )
    if bundle.authority_sha256 != expected_authority:
        raise PAVolumeQualificationError(
            "authority manifest differs from external expected digest"
        )
    attestation_path = _input_file(
        external_attestation_path, "external authority attestation"
    )
    expected_attestation = _sha(
        expected_external_attestation_sha256,
        "expected external attestation",
    )
    attestation_bytes = attestation_path.read_bytes()
    if sha256_bytes(attestation_bytes) != expected_attestation:
        raise PAVolumeQualificationError(
            "external authority attestation differs from expected digest"
        )
    try:
        attestation = json.loads(attestation_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PAVolumeQualificationError(
            "external authority attestation is invalid JSON"
        ) from exc
    decision_time = _utc(decision_time_utc, "decision_time_utc")
    expected_attestation_value = {
        "schema_version": EXTERNAL_ATTESTATION_SCHEMA,
        "authority_id": bundle.authority["authority_id"],
        "authority_manifest_path": authority_manifest_relative,
        "authority_manifest_sha256": bundle.authority_sha256,
        "source_release_manifest_sha256": (
            bundle.source_release_manifest_sha256
        ),
        "source_release_external_verification_sha256": (
            bundle.source_release_external_verification_sha256
        ),
        "rebuild_manifest_sha256": bundle.rebuild_manifest_sha256,
        "pa_volume_artifact_sha256": bundle.pa_volume_artifact_sha256,
        "dependency_lock_sha256": bundle.dependency_lock_sha256,
        "source_access_authorization_sha256": (
            bundle.source_access_authorization_sha256
        ),
        "runtime_policy_sha256": bundle.runtime_policy_sha256,
        "observed_at_utc": attestation.get("observed_at_utc")
        if isinstance(attestation, Mapping)
        else None,
        "decision_time_utc": decision_time_utc,
    }
    if attestation != expected_attestation_value:
        raise PAVolumeQualificationError(
            "external authority attestation is contradictory"
        )
    observed = _utc(attestation["observed_at_utc"], "attestation observed_at_utc")
    if not bundle.qualified_at_utc <= observed <= decision_time:
        raise PAVolumeQualificationError(
            "authority observation is late or chronologically invalid"
        )
    unsigned_receipt = {
        "schema_version": RECEIPT_SCHEMA,
        "authority_id": bundle.authority["authority_id"],
        "authority_manifest_path": authority_manifest_relative,
        "authority_manifest_sha256": bundle.authority_sha256,
        "source_release_manifest_sha256": (
            bundle.source_release_manifest_sha256
        ),
        "source_release_external_verification_sha256": (
            bundle.source_release_external_verification_sha256
        ),
        "rebuild_manifest_sha256": bundle.rebuild_manifest_sha256,
        "pa_volume_artifact_sha256": bundle.pa_volume_artifact_sha256,
        "dependency_lock_sha256": bundle.dependency_lock_sha256,
        "observed_at_utc": attestation["observed_at_utc"],
    }
    receipt = {
        **unsigned_receipt,
        "receipt_sha256": sha256_bytes(_compact_bytes(unsigned_receipt)),
    }
    _write(output, canonical_json_bytes(receipt))
    return receipt


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    qualify = sub.add_parser("qualify")
    qualify.add_argument("--source-release-dir", required=True, type=Path)
    qualify.add_argument("--schedule-capture-dir", required=True, type=Path)
    qualify.add_argument("--feed-capture-dir", required=True, type=Path)
    qualify.add_argument(
        "--source-release-external-verification", required=True, type=Path
    )
    qualify.add_argument(
        "--expected-source-release-external-verification-sha256", required=True
    )
    qualify.add_argument("--dependency-lock", required=True, type=Path)
    qualify.add_argument(
        "--source-access-authorization", required=True, type=Path
    )
    qualify.add_argument(
        "--expected-source-access-authorization-sha256", required=True
    )
    qualify.add_argument("--runtime-policy", required=True, type=Path)
    qualify.add_argument("--expected-runtime-policy-sha256", required=True)
    qualify.add_argument("--expected-builder-source-sha256", required=True)
    qualify.add_argument("--qualified-at-utc", required=True)
    qualify.add_argument("--output-dir", required=True, type=Path)

    receipt = sub.add_parser("emit-external-runtime-receipt")
    receipt.add_argument("--authority-root", required=True, type=Path)
    receipt.add_argument(
        "--authority-manifest-relative", default=AUTHORITY_MANIFEST_RELATIVE
    )
    receipt.add_argument("--expected-authority-manifest-sha256", required=True)
    receipt.add_argument("--external-attestation", required=True, type=Path)
    receipt.add_argument("--expected-external-attestation-sha256", required=True)
    receipt.add_argument("--decision-time-utc", required=True)
    receipt.add_argument("--output-relative", default=DEFAULT_RECEIPT_RELATIVE)
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "qualify":
        result = qualify_source_release(
            source_release_dir=args.source_release_dir,
            schedule_capture_dir=args.schedule_capture_dir,
            feed_capture_dir=args.feed_capture_dir,
            source_release_external_verification_path=(
                args.source_release_external_verification
            ),
            expected_source_release_external_verification_sha256=(
                args.expected_source_release_external_verification_sha256
            ),
            dependency_lock_path=args.dependency_lock,
            source_access_authorization_path=args.source_access_authorization,
            expected_source_access_authorization_sha256=(
                args.expected_source_access_authorization_sha256
            ),
            runtime_policy_path=args.runtime_policy,
            expected_runtime_policy_sha256=(
                args.expected_runtime_policy_sha256
            ),
            expected_builder_source_sha256=args.expected_builder_source_sha256,
            qualified_at_utc=args.qualified_at_utc,
            output_dir=args.output_dir,
        )
    elif args.command == "emit-external-runtime-receipt":
        result = emit_external_runtime_receipt(
            authority_root=args.authority_root,
            authority_manifest_relative=args.authority_manifest_relative,
            expected_authority_manifest_sha256=(
                args.expected_authority_manifest_sha256
            ),
            external_attestation_path=args.external_attestation,
            expected_external_attestation_sha256=(
                args.expected_external_attestation_sha256
            ),
            decision_time_utc=args.decision_time_utc,
            output_relative=args.output_relative,
        )
    else:  # pragma: no cover - argparse enforces the command set.
        raise PAVolumeQualificationError("unsupported command")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
