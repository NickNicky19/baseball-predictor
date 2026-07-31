"""Externally anchored authorization for raw 2023 MLB boxscore capture.

This authority is intentionally narrower than the earlier PA-volume capture:
the already-certified schedule supplies the exact 2,430 game identities and the
only permitted network resource is the official final boxscore for each game.
The capture may retain raw bytes and receipts, but may not construct features,
fit or score a model, generate predictions, or access protected evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import stat
from typing import Any, Mapping


SCHEMA = "shared-pa-outcome-historical-source-access-authorization-v1"
STATUS = "AUTHORIZED_RESEARCH_ONLY_2023_OFFICIAL_MLB_BOXSCORES"
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")

SOURCE_SCOPE: Mapping[str, Any] = {
    "authority": "official_mlb_stats_api",
    "scheme": "https",
    "host": "statsapi.mlb.com",
    "methods": ["GET"],
    "season": 2023,
    "game_types": ["R"],
    "resources": [
        {
            "source_kind": "official_mlb_2023_final_boxscore",
            "path_template": "/api/v1/game/{game_pk}/boxscore",
            "query": None,
        }
    ],
    "game_pks_from_certified_schedule_index_only": True,
    "expected_game_count": 2430,
    "redirects_allowed": False,
}

AUTHORIZED_ACTIONS: Mapping[str, bool] = {
    "raw_historical_boxscore_capture": True,
    "historical_schedule_capture": False,
    "play_by_play_capture": False,
    "feature_construction": False,
    "model_fitting": False,
    "model_scoring": False,
    "prediction_generation": False,
    "economic_evaluation": False,
    "prospective_evidence": False,
    "prospective_backfill": False,
}

PROTECTED_BOUNDARIES: Mapping[str, bool] = {
    "may_2026_accessed": False,
    "selection_2024_accessed": False,
    "spent_hr_confirmation_2025_accessed": False,
    "market_prices_accessed": False,
    "economic_evidence_accessed": False,
    "prospective_evidence_accessed": False,
}

_DOCUMENT_KEYS = {
    "schema_version",
    "authorization_id",
    "status",
    "authorized_at_utc",
    "valid_from_utc",
    "expires_at_utc",
    "research_only",
    "betting_authorized",
    "network_fetch_authorized",
    "source_scope",
    "authorized_actions",
    "protected_boundaries",
    "runtime_policy_sha256",
    "source_bundle_sha256",
    "request_plan_sha256",
    "authorization_sha256",
}


class OutcomeHistoricalSourceAccessError(ValueError):
    """The proposed boxscore access is absent, unanchored, late, or broader."""


@dataclass(frozen=True)
class VerifiedOutcomeHistoricalSourceAccess:
    authorization_id: str
    authorization_path: Path
    authorization_file_sha256: str
    runtime_policy_sha256: str
    source_bundle_sha256: str
    request_plan_sha256: str
    authorized_at_utc: str
    valid_from_utc: str
    expires_at_utc: str


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        + "\n"
    ).encode("utf-8")


def authorization_semantic_sha256(value: Mapping[str, Any]) -> str:
    unsigned = dict(value)
    unsigned.pop("authorization_sha256", None)
    return hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise OutcomeHistoricalSourceAccessError(f"{label} must be a lowercase SHA-256")
    return value


def _utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise OutcomeHistoricalSourceAccessError(f"{label} must be canonical UTC")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise OutcomeHistoricalSourceAccessError(f"{label} is invalid") from exc
    canonical = parsed.astimezone(timezone.utc).isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")
    if parsed.tzinfo is None or value != canonical:
        raise OutcomeHistoricalSourceAccessError(f"{label} is not canonical UTC")
    return parsed


def _is_link_or_reparse(path: Path) -> bool:
    info = path.lstat()
    attributes = getattr(info, "st_file_attributes", 0)
    return path.is_symlink() or bool(
        attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _authorization_file(path: str | Path) -> Path:
    candidate = Path(path)
    if not candidate.exists():
        raise OutcomeHistoricalSourceAccessError("source-access authorization is missing")
    cursor = candidate.absolute()
    while True:
        if cursor.exists() and _is_link_or_reparse(cursor):
            raise OutcomeHistoricalSourceAccessError(
                "source-access authorization has a symlink or reparse ancestor"
            )
        if cursor == cursor.parent:
            break
        cursor = cursor.parent
    resolved = candidate.resolve(strict=True)
    if not resolved.is_file() or _is_link_or_reparse(resolved):
        raise OutcomeHistoricalSourceAccessError(
            "source-access authorization must be a regular file"
        )
    return resolved


def verify_outcome_historical_source_access_authorization(
    *,
    authorization_path: str | Path,
    expected_authorization_sha256: str,
    expected_runtime_policy_sha256: str,
    expected_source_bundle_sha256: str,
    expected_request_plan_sha256: str,
    access_time_utc: str,
) -> VerifiedOutcomeHistoricalSourceAccess:
    """Verify exact official-boxscore scope before any network caller runs."""
    expected_file = _sha(expected_authorization_sha256, "external authorization digest")
    expected_runtime = _sha(expected_runtime_policy_sha256, "runtime-policy digest")
    expected_source = _sha(expected_source_bundle_sha256, "source-bundle digest")
    expected_plan = _sha(expected_request_plan_sha256, "request-plan digest")
    access_time = _utc(access_time_utc, "access_time_utc")
    path = _authorization_file(authorization_path)
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_file:
        raise OutcomeHistoricalSourceAccessError(
            "source-access authorization differs from the external expected digest"
        )
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OutcomeHistoricalSourceAccessError(
            "source-access authorization is not valid JSON"
        ) from exc
    if not isinstance(value, Mapping) or set(value) != _DOCUMENT_KEYS:
        raise OutcomeHistoricalSourceAccessError(
            "source-access authorization schema has missing or unexpected fields"
        )
    if value.get("schema_version") != SCHEMA or value.get("status") != STATUS:
        raise OutcomeHistoricalSourceAccessError(
            "source-access authorization schema or status changed"
        )
    authorization_id = value.get("authorization_id")
    if not isinstance(authorization_id, str) or not authorization_id.strip():
        raise OutcomeHistoricalSourceAccessError("source-access authorization ID is invalid")
    if authorization_semantic_sha256(value) != _sha(
        value.get("authorization_sha256"), "authorization semantic digest"
    ):
        raise OutcomeHistoricalSourceAccessError(
            "source-access authorization semantic digest differs"
        )
    if (
        value.get("research_only") is not True
        or value.get("betting_authorized") is not False
        or value.get("network_fetch_authorized") is not True
        or value.get("source_scope") != SOURCE_SCOPE
        or value.get("authorized_actions") != AUTHORIZED_ACTIONS
        or value.get("protected_boundaries") != PROTECTED_BOUNDARIES
    ):
        raise OutcomeHistoricalSourceAccessError(
            "source-access authorization widened or changed its safety scope"
        )
    runtime_digest = _sha(value.get("runtime_policy_sha256"), "runtime-policy digest")
    source_digest = _sha(value.get("source_bundle_sha256"), "source-bundle digest")
    plan_digest = _sha(value.get("request_plan_sha256"), "request-plan digest")
    if (runtime_digest, source_digest, plan_digest) != (
        expected_runtime,
        expected_source,
        expected_plan,
    ):
        raise OutcomeHistoricalSourceAccessError(
            "source-access authorization binds different runtime, source, or plan bytes"
        )
    authorized_at = _utc(value.get("authorized_at_utc"), "authorized_at_utc")
    valid_from = _utc(value.get("valid_from_utc"), "valid_from_utc")
    expires_at = _utc(value.get("expires_at_utc"), "expires_at_utc")
    if authorized_at > valid_from or valid_from > access_time:
        raise OutcomeHistoricalSourceAccessError(
            "source-access authorization is late or not yet active"
        )
    if access_time >= expires_at or valid_from >= expires_at:
        raise OutcomeHistoricalSourceAccessError("source-access authorization is expired")
    return VerifiedOutcomeHistoricalSourceAccess(
        authorization_id=authorization_id,
        authorization_path=path,
        authorization_file_sha256=expected_file,
        runtime_policy_sha256=runtime_digest,
        source_bundle_sha256=source_digest,
        request_plan_sha256=plan_digest,
        authorized_at_utc=str(value["authorized_at_utc"]),
        valid_from_utc=str(value["valid_from_utc"]),
        expires_at_utc=str(value["expires_at_utc"]),
    )
