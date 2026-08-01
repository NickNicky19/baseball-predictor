"""Exact external authorization gate for the bounded 2023 Statcast sample."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping


SCHEMA = "shared-pa-statcast-historical-source-access-v1"
STATUS = "AUTHORIZED_RESEARCH_ONLY_BOUNDED_2023_STATCAST_SAMPLE"
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
SOURCE_SCOPE: Mapping[str, Any] = {
    "authority": "baseball_savant_statcast_pitch_level_csv",
    "scheme": "https",
    "host": "baseballsavant.mlb.com",
    "method": "GET",
    "path": "/statcast_search/csv",
    "season": 2023,
    "game_types": ["R"],
    "bounded_sample_date": "2023-07-25",
    "request_count": 1,
    "output_path": "data/source/shared_pa_statcast_sample_2023-07-25_v1",
    "attempt_history_path": "config/shared_pa_statcast_sample_attempt_history_20260731_v1.json",
    "attempt_history_sha256": "eb28fbfc18ba777f796abe1db9735579f99a06907d5de856d696d4de5db0aaf5",
    "prior_attempts_consumed": 1,
    "remaining_lifetime_attempts": 3,
    "complete_standard_csv_schema_requested": True,
    "server_side_field_filter": None,
    "redirects_allowed": False,
}
AUTHORIZED_ACTIONS: Mapping[str, bool] = {
    "bounded_raw_statcast_sample_capture": True,
    "full_2023_statcast_capture": False,
    "feature_candidate_fitting": False,
    "market_scoring": False,
    "prediction_generation": False,
    "prospective_reconstruction": False,
    "economic_evaluation": False,
}
PROTECTED_BOUNDARIES: Mapping[str, bool] = {
    "may_2026_accessed": False,
    "selection_2024_accessed": False,
    "spent_hr_confirmation_2025_accessed": False,
    "market_prices_accessed": False,
    "prospective_evidence_accessed": False,
}
DOCUMENT_KEYS = {
    "schema_version", "authorization_id", "status", "authorized_at_utc",
    "valid_from_utc", "expires_at_utc", "research_only", "betting_authorized",
    "network_fetch_authorized", "source_scope", "authorized_actions",
    "protected_boundaries", "carrier_commit", "runtime_policy_sha256",
    "source_bundle_sha256", "source_contract_sha256", "request_plan_sha256",
    "authorization_sha256",
}


class StatcastHistoricalSourceAccessError(ValueError):
    """The sample authorization is missing, stale, or broader than registered."""


@dataclass(frozen=True)
class VerifiedStatcastHistoricalSourceAccess:
    authorization_id: str
    authorization_file_sha256: str
    carrier_commit: str
    runtime_policy_sha256: str
    source_bundle_sha256: str
    source_contract_sha256: str
    request_plan_sha256: str
    output_path: str
    attempt_history_sha256: str = ""
    prior_attempts_consumed: int = 0
    remaining_lifetime_attempts: int = 4


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode("utf-8")


def authorization_semantic_sha256(value: Mapping[str, Any]) -> str:
    unsigned = dict(value)
    unsigned.pop("authorization_sha256", None)
    return hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise StatcastHistoricalSourceAccessError(f"{label} must be a lowercase SHA-256")
    return value


def _utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise StatcastHistoricalSourceAccessError(f"{label} must be canonical UTC")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise StatcastHistoricalSourceAccessError(f"{label} is invalid") from exc
    canonical = parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    if parsed.tzinfo is None or value != canonical:
        raise StatcastHistoricalSourceAccessError(f"{label} is not canonical UTC")
    return parsed


def verify_statcast_historical_source_access(
    *, authorization_path: Path, expected_authorization_sha256: str,
    expected_carrier_commit: str, expected_runtime_policy_sha256: str,
    expected_source_bundle_sha256: str, expected_source_contract_sha256: str,
    expected_request_plan_sha256: str, access_time_utc: str,
) -> VerifiedStatcastHistoricalSourceAccess:
    expected = {
        "authorization_file": _sha(expected_authorization_sha256, "authorization-file digest"),
        "runtime_policy": _sha(expected_runtime_policy_sha256, "runtime-policy digest"),
        "source_bundle": _sha(expected_source_bundle_sha256, "source-bundle digest"),
        "source_contract": _sha(expected_source_contract_sha256, "source-contract digest"),
        "request_plan": _sha(expected_request_plan_sha256, "request-plan digest"),
    }
    if re.fullmatch(r"[0-9a-f]{40}", expected_carrier_commit or "") is None:
        raise StatcastHistoricalSourceAccessError("carrier commit is invalid")
    access_time = _utc(access_time_utc, "access_time_utc")
    path = authorization_path.resolve(strict=True)
    if not path.is_file() or path.is_symlink():
        raise StatcastHistoricalSourceAccessError("authorization must be a regular non-link file")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected["authorization_file"]:
        raise StatcastHistoricalSourceAccessError("authorization differs from external expected digest")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StatcastHistoricalSourceAccessError("authorization is not valid JSON") from exc
    if not isinstance(value, Mapping) or set(value) != DOCUMENT_KEYS:
        raise StatcastHistoricalSourceAccessError("authorization schema has missing or unexpected fields")
    if value.get("schema_version") != SCHEMA or value.get("status") != STATUS:
        raise StatcastHistoricalSourceAccessError("authorization schema or status differs")
    if authorization_semantic_sha256(value) != _sha(value.get("authorization_sha256"), "semantic digest"):
        raise StatcastHistoricalSourceAccessError("authorization semantic digest differs")
    if (
        value.get("research_only") is not True
        or value.get("betting_authorized") is not False
        or value.get("network_fetch_authorized") is not True
        or value.get("source_scope") != SOURCE_SCOPE
        or value.get("authorized_actions") != AUTHORIZED_ACTIONS
        or value.get("protected_boundaries") != PROTECTED_BOUNDARIES
    ):
        raise StatcastHistoricalSourceAccessError("authorization safety scope changed")
    if SOURCE_SCOPE["prior_attempts_consumed"] + SOURCE_SCOPE["remaining_lifetime_attempts"] != 4:
        raise StatcastHistoricalSourceAccessError("attempt history lifetime accounting is invalid")
    observed = {
        "runtime_policy": value.get("runtime_policy_sha256"),
        "source_bundle": value.get("source_bundle_sha256"),
        "source_contract": value.get("source_contract_sha256"),
        "request_plan": value.get("request_plan_sha256"),
    }
    if observed != {key: expected[key] for key in observed} or value.get("carrier_commit") != expected_carrier_commit:
        raise StatcastHistoricalSourceAccessError("authorization binds different carrier, runtime, source, contract, or plan bytes")
    authorized = _utc(value.get("authorized_at_utc"), "authorized_at_utc")
    valid_from = _utc(value.get("valid_from_utc"), "valid_from_utc")
    expires = _utc(value.get("expires_at_utc"), "expires_at_utc")
    if authorized > valid_from or valid_from > access_time or access_time >= expires:
        raise StatcastHistoricalSourceAccessError("authorization is late, inactive, or expired")
    identifier = value.get("authorization_id")
    if not isinstance(identifier, str) or not identifier.strip():
        raise StatcastHistoricalSourceAccessError("authorization ID is invalid")
    return VerifiedStatcastHistoricalSourceAccess(
        authorization_id=identifier,
        authorization_file_sha256=expected["authorization_file"],
        carrier_commit=expected_carrier_commit,
        runtime_policy_sha256=expected["runtime_policy"],
        source_bundle_sha256=expected["source_bundle"],
        source_contract_sha256=expected["source_contract"],
        request_plan_sha256=expected["request_plan"],
        output_path=str(SOURCE_SCOPE["output_path"]),
        attempt_history_sha256=str(SOURCE_SCOPE["attempt_history_sha256"]),
        prior_attempts_consumed=int(SOURCE_SCOPE["prior_attempts_consumed"]),
        remaining_lifetime_attempts=int(SOURCE_SCOPE["remaining_lifetime_attempts"]),
    )
