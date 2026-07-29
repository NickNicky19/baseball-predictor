"""Fail-closed source authority for a historical PA-volume artifact.

Artifact byte identity is necessary but is not source authority.  This module
requires an independently digest-anchored authority receipt, a semantically
qualified source manifest, and a rebuild manifest that binds the exact PA
artifact before a parent probability builder may run.

It deliberately does not import or modify any projected-opportunity candidate.
A future candidate must opt in explicitly by calling
``invoke_after_pa_volume_source_authority``.
"""

from __future__ import annotations

import hashlib
import json
import re
import stat
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, TypeVar

from src.evaluation.pa_volume_source_truth_v2 import (
    PAVolumeSourceTruthError,
    build_pa_volume_artifact,
    canonical_json_bytes,
    validate_pa_volume_artifact,
)


class PAVolumeSourceAuthorityError(ValueError):
    """PA-volume source authority is missing, blocked, late, or contradictory."""


AUTHORITY_SCHEMA = "shared-pa-pa-volume-source-authority-v1"
REBUILD_SCHEMA = "shared-pa-pa-volume-qualified-rebuild-v1"
RECEIPT_SCHEMA = "shared-pa-pa-volume-source-authority-runtime-receipt-v1"
COMPLETE_DECISION = "SOURCE_AND_DEPENDENCY_AUTHORITY_COMPLETE"
REBUILD_COMPLETE_DECISION = "QUALIFIED_SOURCE_REBUILD_COMPLETE"
SOURCE_RELEASE_SCHEMA = "pa-volume-official-source-release-v1"
SOURCE_EXTERNAL_VERIFICATION_SCHEMA = (
    "pa-volume-official-source-external-verification-v1"
)
OFFICIAL_PROJECTION_SCHEMA = "pa-volume-official-feed-projection-v1"
OFFICIAL_ROW_SCHEMA = "pa-volume-official-starter-projection-v2"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_T = TypeVar("_T")

_SOURCE_RELEASE_PATHS = {
    "manifest": "source_release/source_manifest.json",
    "schedule": "source_release/schedule_index.json",
    "projection": "source_release/projection.json",
    "verification": "source_release_external_verification.json",
    "rebuild": "rebuild/manifest.json",
    "artifact": "artifacts/pa_volume.json",
    "lock": "locks/requirements.lock",
}
_REVIEWED_SOURCE_PATHS = [
    "scripts/capture_direct_batter_pa_source_transport_v2.py",
    "scripts/capture_pa_volume_official_source_v1.py",
    "scripts/build_pa_volume_official_source_release_v1.py",
    "src/evaluation/pa_volume_official_feed_projection_v1.py",
    "src/evaluation/pa_volume_source_truth_v2.py",
]

_REQUIRED_AUTHORITY = {
    "raw_transport_request_and_response_receipts": True,
    "independent_official_source_receipts": True,
    "exact_reproducible_dependency_lock": True,
    "external_expected_release_digest": True,
}
_PROTECTED = {
    "may_2026_accessed": False,
    "selection_2024_accessed": False,
    "spent_hr_confirmation_2025_accessed": False,
    "prices_accessed": False,
    "prospective_evidence_accessed": False,
    "prospective_backfill_performed": False,
}


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise PAVolumeSourceAuthorityError(
            f"{label} must be a lowercase SHA-256 digest"
        )
    return value


def _utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise PAVolumeSourceAuthorityError(f"{label} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PAVolumeSourceAuthorityError(
            f"{label} is not a valid ISO timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PAVolumeSourceAuthorityError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _is_reparse(path: Path) -> bool:
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    return path.is_symlink() or bool(
        attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _safe_file(root: Path, relative: Any, label: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise PAVolumeSourceAuthorityError(f"{label} path must be non-empty")
    rel = Path(relative)
    if rel.is_absolute() or ".." in rel.parts:
        raise PAVolumeSourceAuthorityError(f"{label} path is unsafe")
    candidate = root / rel
    if not candidate.is_file():
        raise PAVolumeSourceAuthorityError(f"{label} is missing")
    root_resolved = root.resolve(strict=True)
    resolved = candidate.resolve(strict=True)
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise PAVolumeSourceAuthorityError(f"{label} escapes authority root") from exc
    current = candidate
    while True:
        if _is_reparse(current):
            raise PAVolumeSourceAuthorityError(
                f"{label} traverses a symlink, junction, or reparse point"
            )
        if current.resolve(strict=True) == root_resolved:
            break
        current = current.parent
    return candidate


def _read_json(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise PAVolumeSourceAuthorityError(f"{label} is unreadable") from exc
    if not isinstance(value, dict):
        raise PAVolumeSourceAuthorityError(f"{label} must be an object")
    return value, raw


def _binding(
    value: Any, *, root: Path, label: str
) -> tuple[Path, str, str]:
    if not isinstance(value, Mapping) or set(value) != {"path", "sha256"}:
        raise PAVolumeSourceAuthorityError(f"{label} binding changed")
    relative = value.get("path")
    expected = _sha(value.get("sha256"), f"{label}.sha256")
    path = _safe_file(root, relative, label)
    actual = _sha256_bytes(path.read_bytes())
    if actual != expected:
        raise PAVolumeSourceAuthorityError(f"{label} bytes differ")
    return path, expected, str(relative)


def _canonical_json(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    value, raw = _read_json(path, label)
    if raw != canonical_json_bytes(value):
        raise PAVolumeSourceAuthorityError(f"{label} is not canonical JSON")
    return value, raw


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise PAVolumeSourceAuthorityError(f"{label} must be a positive integer")
    return value


def _date_2023(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise PAVolumeSourceAuthorityError(f"{label} must be a canonical 2023 date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise PAVolumeSourceAuthorityError(
            f"{label} must be a canonical 2023 date"
        ) from exc
    if parsed.year != 2023 or parsed.isoformat() != value:
        raise PAVolumeSourceAuthorityError(f"{label} must be a canonical 2023 date")
    return value


def _active_reviewed_source_files() -> list[dict[str, str]]:
    repo = Path(__file__).resolve().parents[2]
    rows: list[dict[str, str]] = []
    for relative in _REVIEWED_SOURCE_PATHS:
        path = repo / relative
        if not path.is_file() or _is_reparse(path):
            raise PAVolumeSourceAuthorityError(
                f"reviewed source is missing or unsafe: {relative}"
            )
        rows.append({"path": relative, "sha256": _sha256_bytes(path.read_bytes())})
    return rows


def _verify_exact_dependency_lock(lock_path: Path, lock_sha: str) -> None:
    """Require the release lock to be the exact audited source-runtime lock."""
    repo_lock = Path(__file__).resolve().parents[2] / (
        "requirements-direct-batter-pa-source-authority.lock"
    )
    if not repo_lock.is_file() or _is_reparse(repo_lock):
        raise PAVolumeSourceAuthorityError("audited dependency lock is unavailable")
    expected = repo_lock.read_bytes()
    observed = lock_path.read_bytes()
    if not observed or observed != expected or _sha256_bytes(observed) != lock_sha:
        raise PAVolumeSourceAuthorityError(
            "qualified dependency lock differs from the exact audited lock"
        )


def _verify_schedule_index(value: Mapping[str, Any]) -> dict[int, dict[str, Any]]:
    fields = [
        "game_pk",
        "official_date",
        "game_type",
        "away_team_id",
        "home_team_id",
    ]
    if set(value) != {"schema_version", "season", "fields", "games"} or (
        value.get("schema_version") != "pa-volume-2023-schedule-index-v1"
        or value.get("season") != 2023
        or value.get("fields") != fields
        or not isinstance(value.get("games"), list)
        or not value["games"]
    ):
        raise PAVolumeSourceAuthorityError("copied schedule index schema differs")
    games: dict[int, dict[str, Any]] = {}
    canonical: list[dict[str, Any]] = []
    for raw in value["games"]:
        if not isinstance(raw, Mapping) or set(raw) != set(fields):
            raise PAVolumeSourceAuthorityError("copied schedule game schema differs")
        game_pk = _positive_int(raw.get("game_pk"), "schedule game_pk")
        if game_pk in games:
            raise PAVolumeSourceAuthorityError("copied schedule game is duplicated")
        game = {
            "game_pk": game_pk,
            "official_date": _date_2023(raw.get("official_date"), "official_date"),
            "game_type": raw.get("game_type"),
            "away_team_id": _positive_int(raw.get("away_team_id"), "away team"),
            "home_team_id": _positive_int(raw.get("home_team_id"), "home team"),
        }
        if game["game_type"] != "R" or game["away_team_id"] == game["home_team_id"]:
            raise PAVolumeSourceAuthorityError("copied schedule identity differs")
        games[game_pk] = game
        canonical.append(game)
    if canonical != sorted(canonical, key=lambda row: (row["official_date"], row["game_pk"])):
        raise PAVolumeSourceAuthorityError("copied schedule index is not sorted")
    return games


def _verify_projection(
    value: Mapping[str, Any], schedule: Mapping[int, Mapping[str, Any]]
) -> list[Mapping[str, Any]]:
    keys = {
        "schema_version",
        "status",
        "season",
        "research_only",
        "betting_authorized",
        "prospective_evidence_claimed",
        "protected_data",
        "bindings",
        "game_count",
        "row_count",
        "feed_hashes",
        "projection_sha256",
        "projection",
    }
    if set(value) != keys or (
        value.get("schema_version") != OFFICIAL_PROJECTION_SCHEMA
        or value.get("status") != "COMPLETE_OFFICIAL_2023_FINAL_FEED_PROJECTION"
        or value.get("season") != 2023
        or value.get("research_only") is not True
        or value.get("betting_authorized") is not False
        or value.get("prospective_evidence_claimed") is not False
        or value.get("protected_data") != _PROTECTED
    ):
        raise PAVolumeSourceAuthorityError("copied official projection schema differs")
    bindings = value.get("bindings")
    if not isinstance(bindings, Mapping) or set(bindings) != {
        "schedule_capture_manifest_sha256",
        "feed_capture_manifest_sha256",
        "parser_source_sha256",
    }:
        raise PAVolumeSourceAuthorityError("copied projection bindings differ")
    for key in bindings:
        _sha(bindings[key], f"projection binding {key}")
    projection = value.get("projection")
    row_fields = [
        "game_pk",
        "official_date",
        "side",
        "team_id",
        "player_id",
        "lineup_slot",
        "out_pa",
    ]
    if not isinstance(projection, Mapping) or set(projection) != {
        "schema_version", "season", "fields", "rows"
    } or (
        projection.get("schema_version") != OFFICIAL_ROW_SCHEMA
        or projection.get("season") != 2023
        or projection.get("fields") != row_fields
        or not isinstance(projection.get("rows"), list)
    ):
        raise PAVolumeSourceAuthorityError("copied projection row schema differs")
    if _sha256_bytes(canonical_json_bytes(projection)) != _sha(
        value.get("projection_sha256"), "projection semantic digest"
    ):
        raise PAVolumeSourceAuthorityError("copied projection semantic digest differs")
    rows = projection["rows"]
    if (
        value.get("game_count") != len(schedule)
        or value.get("row_count") != len(rows)
        or len(rows) != len(schedule) * 18
    ):
        raise PAVolumeSourceAuthorityError("copied projection coverage differs")
    feed_hashes = value.get("feed_hashes")
    if not isinstance(feed_hashes, list) or len(feed_hashes) != len(schedule):
        raise PAVolumeSourceAuthorityError("copied projection feed hashes differ")
    observed_feed_ids: list[int] = []
    for row in feed_hashes:
        if not isinstance(row, Mapping) or set(row) != {"game_pk", "sha256"}:
            raise PAVolumeSourceAuthorityError("copied feed-hash schema differs")
        observed_feed_ids.append(_positive_int(row.get("game_pk"), "feed game_pk"))
        _sha(row.get("sha256"), "feed hash")
    if observed_feed_ids != list(schedule):
        raise PAVolumeSourceAuthorityError("copied feed-hash identity differs")
    for row in rows:
        if not isinstance(row, Mapping):
            raise PAVolumeSourceAuthorityError("copied projection row is malformed")
        game_pk = row.get("game_pk")
        game = schedule.get(game_pk)
        if game is None or row.get("official_date") != game["official_date"]:
            raise PAVolumeSourceAuthorityError("projection and schedule identity differ")
        side = row.get("side")
        expected_team = game.get(f"{side}_team_id") if side in {"away", "home"} else None
        if row.get("team_id") != expected_team:
            raise PAVolumeSourceAuthorityError("projection team and schedule differ")
    return rows


def _verify_source_semantics(
    *,
    root: Path,
    source_path: Path,
    source_sha: str,
    source_verification_path: Path,
    source_verification_sha: str,
    lock_path: Path,
    lock_sha: str,
    pa_raw: bytes,
) -> dict[str, Any]:
    source, source_raw = _canonical_json(source_path, "qualified source manifest")
    source_keys = {
        "schema_version", "status", "season", "research_only",
        "betting_authorized", "model_fitting_performed", "probabilities_generated",
        "protected_data", "source_access_authorization_id",
        "source_access_authorization_sha256", "schedule_capture_observed_digest",
        "schedule_capture_manifest_sha256", "feed_capture_observed_digest",
        "feed_capture_manifest_sha256", "schedule_index_sha256",
        "projection_sha256", "parser_source_sha256", "reviewed_source_files",
        "reviewed_source_bundle_sha256", "dependency_lock_sha256", "game_count",
        "row_count",
    }
    if set(source) != source_keys or (
        source.get("schema_version") != SOURCE_RELEASE_SCHEMA
        or source.get("status") != "AWAITING_INDEPENDENT_EXTERNAL_VERIFICATION"
        or source.get("season") != 2023
        or source.get("research_only") is not True
        or source.get("betting_authorized") is not False
        or source.get("model_fitting_performed") is not False
        or source.get("probabilities_generated") is not False
        or source.get("protected_data") != _PROTECTED
    ):
        raise PAVolumeSourceAuthorityError("qualified source manifest schema differs")
    if _sha256_bytes(source_raw) != source_sha:
        raise PAVolumeSourceAuthorityError("qualified source manifest digest differs")
    source_access_id = source.get("source_access_authorization_id")
    if not isinstance(source_access_id, str) or not source_access_id:
        raise PAVolumeSourceAuthorityError("source-access authorization ID is invalid")
    for field in (
        "source_access_authorization_sha256", "schedule_capture_observed_digest",
        "schedule_capture_manifest_sha256", "feed_capture_observed_digest",
        "feed_capture_manifest_sha256", "schedule_index_sha256", "projection_sha256",
        "parser_source_sha256", "reviewed_source_bundle_sha256",
        "dependency_lock_sha256",
    ):
        _sha(source.get(field), f"source manifest {field}")
    active_reviewed = _active_reviewed_source_files()
    if source.get("reviewed_source_files") != active_reviewed or source.get(
        "reviewed_source_bundle_sha256"
    ) != _sha256_bytes(canonical_json_bytes(active_reviewed)):
        raise PAVolumeSourceAuthorityError("reviewed source bundle differs from active bytes")
    if source.get("parser_source_sha256") != active_reviewed[3]["sha256"]:
        raise PAVolumeSourceAuthorityError("projection parser binding differs")
    _verify_exact_dependency_lock(lock_path, lock_sha)
    if source.get("dependency_lock_sha256") != lock_sha:
        raise PAVolumeSourceAuthorityError("source manifest lock binding differs")

    schedule_path = _safe_file(
        root, _SOURCE_RELEASE_PATHS["schedule"], "copied schedule index"
    )
    projection_path = _safe_file(
        root, _SOURCE_RELEASE_PATHS["projection"], "copied official projection"
    )
    schedule, schedule_raw = _canonical_json(schedule_path, "copied schedule index")
    projection, projection_raw = _canonical_json(
        projection_path, "copied official projection"
    )
    if _sha256_bytes(schedule_raw) != source.get("schedule_index_sha256") or (
        _sha256_bytes(projection_raw) != source.get("projection_sha256")
    ):
        raise PAVolumeSourceAuthorityError("copied source-release bytes differ from manifest")
    schedule_games = _verify_schedule_index(schedule)
    rows = _verify_projection(projection, schedule_games)
    if source.get("game_count") != len(schedule_games) or source.get("row_count") != len(rows):
        raise PAVolumeSourceAuthorityError("source manifest coverage differs")
    projection_bindings = projection["bindings"]
    if (
        projection_bindings["schedule_capture_manifest_sha256"]
        != source["schedule_capture_manifest_sha256"]
        or projection_bindings["feed_capture_manifest_sha256"]
        != source["feed_capture_manifest_sha256"]
        or projection_bindings["parser_source_sha256"] != source["parser_source_sha256"]
    ):
        raise PAVolumeSourceAuthorityError("projection and source-manifest bindings differ")

    verification, verification_raw = _canonical_json(
        source_verification_path, "source-release external verification"
    )
    expected_verification = {
        "schema_version": SOURCE_EXTERNAL_VERIFICATION_SCHEMA,
        "status": "INDEPENDENT_SOURCE_RELEASE_VERIFIED",
        "source_access_authorization_id": source_access_id,
        "source_access_authorization_sha256": source["source_access_authorization_sha256"],
        "source_manifest_sha256": source_sha,
        "projection_sha256": _sha256_bytes(projection_raw),
        "schedule_capture_observed_digest": source["schedule_capture_observed_digest"],
        "feed_capture_observed_digest": source["feed_capture_observed_digest"],
        "dependency_lock_sha256": lock_sha,
        "reviewed_source_bundle_sha256": source["reviewed_source_bundle_sha256"],
        "protected_data": _PROTECTED,
    }
    if _sha256_bytes(verification_raw) != source_verification_sha or verification != expected_verification:
        raise PAVolumeSourceAuthorityError(
            "source-release external verification is not semantically bound"
        )

    builder_path = Path(__file__).resolve().parent / "pa_volume_source_truth_v2.py"
    builder_sha = _sha256_bytes(builder_path.read_bytes())
    rebuilt = build_pa_volume_artifact(
        rows=rows,
        source_release_manifest_sha256=source_sha,
        source_release_external_verification_sha256=source_verification_sha,
        dependency_lock_sha256=lock_sha,
        builder_source_sha256=builder_sha,
    )
    if canonical_json_bytes(rebuilt) != pa_raw:
        raise PAVolumeSourceAuthorityError(
            "PA-volume artifact differs from deterministic source rebuild"
        )
    return rebuilt


@dataclass(frozen=True)
class VerifiedPAVolumeSourceAuthority:
    authority_id: str
    authority_manifest_path: Path
    authority_manifest_sha256: str
    external_receipt_path: Path
    external_receipt_sha256: str
    source_release_manifest_path: Path
    source_release_manifest_sha256: str
    source_release_external_verification_path: Path
    source_release_external_verification_sha256: str
    rebuild_manifest_path: Path
    rebuild_manifest_sha256: str
    pa_volume_artifact_path: Path
    pa_volume_artifact_sha256: str
    dependency_lock_path: Path
    dependency_lock_sha256: str
    qualified_at_utc: str
    observed_at_utc: str

    def binding(self) -> dict[str, str]:
        """Return the exact identities a downstream candidate must serialize."""
        return {
            "pa_volume_source_authority_id": self.authority_id,
            "pa_volume_source_authority_manifest_sha256": (
                self.authority_manifest_sha256
            ),
            "pa_volume_source_authority_receipt_sha256": (
                self.external_receipt_sha256
            ),
            "pa_volume_source_release_manifest_sha256": (
                self.source_release_manifest_sha256
            ),
            "pa_volume_source_release_external_verification_sha256": (
                self.source_release_external_verification_sha256
            ),
            "pa_volume_rebuild_manifest_sha256": self.rebuild_manifest_sha256,
            "pa_volume_artifact_sha256": self.pa_volume_artifact_sha256,
            "pa_volume_dependency_lock_sha256": self.dependency_lock_sha256,
        }


def verify_pa_volume_source_authority(
    *,
    authority_root: str | Path,
    authority_manifest_relative: str,
    external_receipt_relative: str,
    expected_external_receipt_sha256: str,
    expected_pa_volume_artifact_sha256: str,
    decision_time_utc: str,
) -> VerifiedPAVolumeSourceAuthority:
    """Verify complete source authority before any probability may be built."""
    root = Path(authority_root).resolve(strict=True)
    expected_receipt = _sha(
        expected_external_receipt_sha256, "external authority receipt"
    )
    expected_pa = _sha(
        expected_pa_volume_artifact_sha256, "expected PA-volume artifact"
    )
    decision_time = _utc(decision_time_utc, "decision_time_utc")

    authority_path = _safe_file(
        root, authority_manifest_relative, "source-authority manifest"
    )
    receipt_path = _safe_file(
        root, external_receipt_relative, "external authority receipt"
    )
    receipt, receipt_raw = _read_json(receipt_path, "external authority receipt")
    if _sha256_bytes(receipt_raw) != expected_receipt:
        raise PAVolumeSourceAuthorityError(
            "external authority receipt differs from independent expected digest"
        )
    receipt_keys = {
        "schema_version",
        "authority_id",
        "authority_manifest_path",
        "authority_manifest_sha256",
        "source_release_manifest_sha256",
        "source_release_external_verification_sha256",
        "rebuild_manifest_sha256",
        "pa_volume_artifact_sha256",
        "dependency_lock_sha256",
        "observed_at_utc",
        "receipt_sha256",
    }
    if set(receipt) != receipt_keys or receipt.get("schema_version") != RECEIPT_SCHEMA:
        raise PAVolumeSourceAuthorityError("external authority receipt schema changed")
    unsigned_receipt = dict(receipt)
    supplied_receipt_hash = _sha(
        unsigned_receipt.pop("receipt_sha256", None), "receipt.receipt_sha256"
    )
    if _sha256_bytes(_canonical_bytes(unsigned_receipt)) != supplied_receipt_hash:
        raise PAVolumeSourceAuthorityError("external authority receipt self-hash differs")
    if receipt.get("authority_manifest_path") != authority_manifest_relative:
        raise PAVolumeSourceAuthorityError(
            "external receipt names a different authority manifest"
        )

    authority, authority_raw = _read_json(
        authority_path, "source-authority manifest"
    )
    authority_sha = _sha256_bytes(authority_raw)
    if _sha(receipt.get("authority_manifest_sha256"), "receipt authority digest") != authority_sha:
        raise PAVolumeSourceAuthorityError(
            "authority manifest differs from external receipt"
        )
    authority_keys = {
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
    if set(authority) != authority_keys or authority.get("schema_version") != AUTHORITY_SCHEMA:
        raise PAVolumeSourceAuthorityError("source-authority manifest schema changed")
    authority_id = authority.get("authority_id")
    if not isinstance(authority_id, str) or not authority_id:
        raise PAVolumeSourceAuthorityError("source-authority ID is invalid")
    if authority_id != receipt.get("authority_id"):
        raise PAVolumeSourceAuthorityError("source-authority identity differs")
    if (
        authority.get("decision") != COMPLETE_DECISION
        or authority.get("eligible_for_model_consumption") is not True
        or authority.get("blockers") != []
    ):
        raise PAVolumeSourceAuthorityError(
            "PA-volume source authority is blocked or ineligible for model consumption"
        )
    if (
        authority.get("research_only") is not True
        or authority.get("betting_authorized") is not False
        or authority.get("source_season") != 2023
        or authority.get("required_authority") != _REQUIRED_AUTHORITY
        or authority.get("observed_authority") != _REQUIRED_AUTHORITY
        or authority.get("protected_data") != _PROTECTED
    ):
        raise PAVolumeSourceAuthorityError(
            "source authority, chronology, or protected-data boundary changed"
        )

    source_path, source_sha, source_relative = _binding(
        authority.get("source_release_manifest"),
        root=root,
        label="qualified source-release manifest",
    )
    source_verification_path, source_verification_sha, verification_relative = _binding(
        authority.get("source_release_external_verification"),
        root=root,
        label="source-release external verification",
    )
    rebuild_path, rebuild_sha, rebuild_relative = _binding(
        authority.get("rebuild_manifest"), root=root, label="PA-volume rebuild manifest"
    )
    pa_path, pa_sha, pa_relative = _binding(
        authority.get("pa_volume_artifact"), root=root, label="PA-volume artifact"
    )
    lock_path, lock_sha, lock_relative = _binding(
        authority.get("exact_dependency_lock"), root=root, label="exact dependency lock"
    )
    if {
        "manifest": source_relative,
        "verification": verification_relative,
        "rebuild": rebuild_relative,
        "artifact": pa_relative,
        "lock": lock_relative,
    } != {
        key: _SOURCE_RELEASE_PATHS[key]
        for key in ("manifest", "verification", "rebuild", "artifact", "lock")
    }:
        raise PAVolumeSourceAuthorityError("qualified authority binding paths differ")
    if pa_sha != expected_pa:
        raise PAVolumeSourceAuthorityError(
            "qualified PA-volume artifact differs from candidate expected digest"
        )

    pa_value, pa_raw = _canonical_json(pa_path, "PA-volume artifact")
    rebuilt = _verify_source_semantics(
        root=root,
        source_path=source_path,
        source_sha=source_sha,
        source_verification_path=source_verification_path,
        source_verification_sha=source_verification_sha,
        lock_path=lock_path,
        lock_sha=lock_sha,
        pa_raw=pa_raw,
    )
    if rebuilt != pa_value:
        raise PAVolumeSourceAuthorityError(
            "PA-volume artifact semantic rebuild differs"
        )
    try:
        validate_pa_volume_artifact(pa_value)
    except PAVolumeSourceTruthError as exc:
        raise PAVolumeSourceAuthorityError(
            "qualified PA-volume artifact fails source-truth validation"
        ) from exc
    pa_source = pa_value.get("source")
    pa_bindings = pa_source.get("bindings") if isinstance(pa_source, Mapping) else None
    if (
        not isinstance(pa_bindings, Mapping)
        or pa_bindings.get("source_release_manifest_sha256") != source_sha
        or pa_bindings.get("source_release_external_verification_sha256")
        != source_verification_sha
        or pa_bindings.get("dependency_lock_sha256") != lock_sha
    ):
        raise PAVolumeSourceAuthorityError(
            "PA-volume artifact does not bind the qualified source release and lock"
        )

    receipt_digests = {
        "source_release_manifest_sha256": source_sha,
        "source_release_external_verification_sha256": source_verification_sha,
        "rebuild_manifest_sha256": rebuild_sha,
        "pa_volume_artifact_sha256": pa_sha,
        "dependency_lock_sha256": lock_sha,
    }
    if any(
        _sha(receipt.get(key), f"receipt.{key}") != expected
        for key, expected in receipt_digests.items()
    ):
        raise PAVolumeSourceAuthorityError(
            "external receipt does not bind the exact source, rebuild, artifact, and lock"
        )

    rebuild, _ = _read_json(rebuild_path, "PA-volume rebuild manifest")
    expected_rebuild = {
        "schema_version": REBUILD_SCHEMA,
        "decision": REBUILD_COMPLETE_DECISION,
        "source_season": 2023,
        "fit_seasons": [2023],
        "source_release_manifest_sha256": source_sha,
        "source_release_external_verification_sha256": source_verification_sha,
        "pa_volume_artifact_path": pa_relative,
        "pa_volume_artifact_sha256": pa_sha,
        "dependency_lock_sha256": lock_sha,
        "manual_coefficients": False,
        "protected_data": _PROTECTED,
    }
    if rebuild != expected_rebuild:
        raise PAVolumeSourceAuthorityError(
            "PA-volume rebuild does not bind the exact qualified source and artifact"
        )

    qualified_time = _utc(authority.get("qualified_at_utc"), "qualified_at_utc")
    observed_time = _utc(receipt.get("observed_at_utc"), "observed_at_utc")
    if qualified_time > observed_time or observed_time > decision_time:
        raise PAVolumeSourceAuthorityError(
            "source qualification, external observation, and decision chronology is invalid"
        )

    return VerifiedPAVolumeSourceAuthority(
        authority_id=authority_id,
        authority_manifest_path=authority_path,
        authority_manifest_sha256=authority_sha,
        external_receipt_path=receipt_path,
        external_receipt_sha256=expected_receipt,
        source_release_manifest_path=source_path,
        source_release_manifest_sha256=source_sha,
        source_release_external_verification_path=source_verification_path,
        source_release_external_verification_sha256=source_verification_sha,
        rebuild_manifest_path=rebuild_path,
        rebuild_manifest_sha256=rebuild_sha,
        pa_volume_artifact_path=pa_path,
        pa_volume_artifact_sha256=pa_sha,
        dependency_lock_path=lock_path,
        dependency_lock_sha256=lock_sha,
        qualified_at_utc=str(authority["qualified_at_utc"]),
        observed_at_utc=str(receipt["observed_at_utc"]),
    )


def invoke_after_pa_volume_source_authority(
    parent_probability_builder: Callable[[VerifiedPAVolumeSourceAuthority], _T],
    **authority_arguments: Any,
) -> _T:
    """Invoke ``parent_probability_builder`` only after complete authority passes."""
    authority = verify_pa_volume_source_authority(**authority_arguments)
    return parent_probability_builder(authority)
