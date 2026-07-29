"""Externally anchored authorization for the 2023 PA-volume source fetch.

Runtime identity does not authorize network access.  This module verifies the
separate, immutable human/source-access decision that must exist before a
caller may contact the two allowlisted official MLB Stats API resources.
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


SCHEMA = "pa-volume-historical-source-access-authorization-v1"
STATUS = "AUTHORIZED_RESEARCH_ONLY_2023_OFFICIAL_MLB_STATS_API"
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
            "source_kind": "official_mlb_2023_regular_season_schedule",
            "path": "/api/v1/schedule",
        },
        {
            "source_kind": "official_mlb_2023_final_feed",
            "path_template": "/api/v1.1/game/{game_pk}/feed/live",
        },
    ],
    "feed_game_pks_from_authorized_schedule_only": True,
    "final_feed_status": {
        "abstract_game_state": "Final",
        "coded_game_state": "F",
    },
    "redirects_allowed": False,
}

AUTHORIZED_ACTIONS: Mapping[str, bool] = {
    "raw_historical_schedule_capture": True,
    "raw_historical_final_feed_capture": True,
    "feature_construction": False,
    "model_fitting": False,
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
    "authorization_sha256",
}


class HistoricalSourceAccessError(ValueError):
    """Historical source access is missing, unanchored, late, or too broad."""


@dataclass(frozen=True)
class VerifiedHistoricalSourceAccess:
    authorization_id: str
    authorization_path: Path
    authorization_file_sha256: str
    runtime_policy_sha256: str
    source_bundle_sha256: str
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
        raise HistoricalSourceAccessError(f"{label} must be a lowercase SHA-256")
    return value


def _utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise HistoricalSourceAccessError(f"{label} must be canonical UTC")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise HistoricalSourceAccessError(f"{label} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise HistoricalSourceAccessError(f"{label} must be UTC")
    canonical = parsed.astimezone(timezone.utc).isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")
    if value != canonical:
        raise HistoricalSourceAccessError(f"{label} is not canonical UTC")
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
        raise HistoricalSourceAccessError("source-access authorization is missing")
    cursor = candidate.absolute()
    while True:
        if cursor.exists() and _is_link_or_reparse(cursor):
            raise HistoricalSourceAccessError(
                "source-access authorization has a symlink or reparse ancestor"
            )
        if cursor == cursor.parent:
            break
        cursor = cursor.parent
    resolved = candidate.resolve(strict=True)
    if not resolved.is_file() or _is_link_or_reparse(resolved):
        raise HistoricalSourceAccessError(
            "source-access authorization must be a regular file"
        )
    return resolved


def verify_historical_source_access_authorization(
    *,
    authorization_path: str | Path,
    expected_authorization_sha256: str,
    expected_runtime_policy_sha256: str,
    expected_source_bundle_sha256: str,
    access_time_utc: str,
) -> VerifiedHistoricalSourceAccess:
    """Verify exact source scope and timing before any network caller executes."""
    expected_file = _sha(
        expected_authorization_sha256, "external authorization digest"
    )
    expected_runtime = _sha(
        expected_runtime_policy_sha256, "expected runtime-policy digest"
    )
    expected_source = _sha(
        expected_source_bundle_sha256, "expected source-bundle digest"
    )
    access_time = _utc(access_time_utc, "access_time_utc")
    path = _authorization_file(authorization_path)
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_file:
        raise HistoricalSourceAccessError(
            "source-access authorization differs from the external expected digest"
        )
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HistoricalSourceAccessError(
            "source-access authorization is not valid JSON"
        ) from exc
    if not isinstance(value, Mapping) or set(value) != _DOCUMENT_KEYS:
        raise HistoricalSourceAccessError(
            "source-access authorization schema has missing or unexpected fields"
        )
    if value.get("schema_version") != SCHEMA or value.get("status") != STATUS:
        raise HistoricalSourceAccessError(
            "source-access authorization schema or status changed"
        )
    authorization_id = value.get("authorization_id")
    if not isinstance(authorization_id, str) or not authorization_id.strip():
        raise HistoricalSourceAccessError("source-access authorization ID is invalid")
    supplied_semantic = _sha(
        value.get("authorization_sha256"), "authorization semantic digest"
    )
    if authorization_semantic_sha256(value) != supplied_semantic:
        raise HistoricalSourceAccessError(
            "source-access authorization semantic digest differs"
        )
    if (
        value.get("research_only") is not True
        or value.get("betting_authorized") is not False
        or value.get("network_fetch_authorized") is not True
    ):
        raise HistoricalSourceAccessError(
            "source-access authorization safety state changed"
        )
    if value.get("source_scope") != SOURCE_SCOPE:
        raise HistoricalSourceAccessError(
            "source-access authorization widened or changed source scope"
        )
    if value.get("authorized_actions") != AUTHORIZED_ACTIONS:
        raise HistoricalSourceAccessError(
            "source-access authorization widened or changed allowed actions"
        )
    if value.get("protected_boundaries") != PROTECTED_BOUNDARIES:
        raise HistoricalSourceAccessError(
            "source-access authorization violates May or another protected boundary"
        )
    runtime_digest = _sha(value.get("runtime_policy_sha256"), "runtime-policy digest")
    source_digest = _sha(value.get("source_bundle_sha256"), "source-bundle digest")
    if runtime_digest != expected_runtime or source_digest != expected_source:
        raise HistoricalSourceAccessError(
            "source-access authorization binds different runtime or source bytes"
        )
    authorized_at = _utc(value.get("authorized_at_utc"), "authorized_at_utc")
    valid_from = _utc(value.get("valid_from_utc"), "valid_from_utc")
    expires_at = _utc(value.get("expires_at_utc"), "expires_at_utc")
    if authorized_at > valid_from or valid_from > access_time:
        raise HistoricalSourceAccessError(
            "source-access authorization is late or not yet active"
        )
    if access_time >= expires_at or valid_from >= expires_at:
        raise HistoricalSourceAccessError("source-access authorization is expired")
    return VerifiedHistoricalSourceAccess(
        authorization_id=authorization_id,
        authorization_path=path,
        authorization_file_sha256=expected_file,
        runtime_policy_sha256=runtime_digest,
        source_bundle_sha256=source_digest,
        authorized_at_utc=str(value["authorized_at_utc"]),
        valid_from_utc=str(value["valid_from_utc"]),
        expires_at_utc=str(value["expires_at_utc"]),
    )
