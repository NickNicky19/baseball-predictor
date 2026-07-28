"""Offline retained-receipt inventory boundary for the full-game envelope.

This module verifies byte identity, declared source/protocol identity,
chronology, target identity, and explicit terminal missingness.  It deliberately
does not manufacture or fetch evidence and cannot authorize PA or starter PMFs.
The checked-in authority has no externally authorized inventory digest, so the
public boundary always returns a terminal blocker.
"""

from __future__ import annotations

import hashlib
import json
import re
import stat
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from src.evaluation.forward_pitcher_context_v2 import (
    ForwardPitcherContextV2,
    ForwardPitcherContextV2Error,
)
from src.evaluation.forward_pitcher_context import (
    ForwardPitcherContextError,
    games_from_raw_schedule_response,
)
from src.evaluation.pitcher_joint_opportunity_v1 import (
    PitcherJointOpportunityError,
    RawPitcherWorkloadReceipt,
    validate_evidence_authority_contract_bytes,
    validate_workload_history,
)
from src.evaluation.projected_lineup_contract import (
    ProjectedLineupContractError,
    load_contract as load_projected_lineup_contract,
    validate_projection,
)
from src.evaluation.shadow_capture_plan import (
    CaptureTarget,
    ShadowCapturePlan,
    ShadowCapturePlanError,
)
from src.evaluation.projected_lineup_official_roster import (
    OfficialRosterReceiptError,
    RawOfficialRosterResponse,
    parse_active_roster_receipt,
)
from src.evaluation.prospective_batter_opportunity import (
    ProspectiveBatterOpportunityError,
    RawOpportunityResponse,
    _validate_coverage,
    parse_opportunity_history,
    parse_prior_schedule_denominator,
)


AUTHORITY_SCHEMA = "full-game-opportunity-receipt-replay-authority-v1"
INVENTORY_SCHEMA = "full-game-opportunity-retained-receipt-inventory-v1"
ENTRY_SCHEMA = "full-game-opportunity-retained-receipt-entry-v1"
AUTHORITY_PATH = "config/full_game_opportunity_receipt_replay_v1.json"
EXPECTED_AUTHORITY_SHA256 = "fe6788d115ae1db76cb282e216545c706a8bb1ff7fefb7be99476373e2322cd3"
PR32 = "1ebeb255bf3ecf17adb82f644f9dc2ff11c7491c"
PR33 = "506ebb40203582d25ff01c0a8c2d1dec4c44ed7d"
PR35 = "0e6a40d5cd1b00d3b1f1128f97a12e7b897aaf55"
SOURCE_COMMITS = {
    "projected_lineup_pr32": PR32,
    "pitcher_source_truth_pr33": PR33,
    "pitcher_joint_pr35": PR35,
}
SHA_RE = re.compile(r"[0-9a-f]{64}\Z")
UTC_RE = re.compile(
    r"\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])"
    r"T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d{6})?Z\Z"
)
MONTH_NAME_MAY_RE = re.compile(
    r"(?i)(?:\bmay\b.{0,24}\b2026\b|\b2026\b.{0,24}\bmay\b)"
)
CALENDAR_DATE_RE = re.compile(
    r"(?<!\d)(?P<year>\d{4})[-_/.](?P<month>\d{1,2})"
    r"(?:[-_/.](?P<day>\d{1,2}))?(?!\d)"
)
US_DATE_RE = re.compile(
    r"(?<!\d)(?P<month>\d{1,2})[-_/.](?P<day>\d{1,2})"
    r"[-_/.](?P<year>\d{4})(?!\d)"
)
BASIC_DATE_RE = re.compile(
    r"(?<!\d)(?P<year>\d{4})(?P<month>\d{2})(?P<day>\d{2})(?!\d)"
)
ISO_WEEK_RE = re.compile(
    r"(?<!\d)(?P<year>\d{4})-?W(?P<week>\d{2})-?(?P<weekday>[1-7])(?!\d)",
    re.IGNORECASE,
)
ORDINAL_DATE_RE = re.compile(
    r"(?<!\d)(?P<year>\d{4})-?(?P<ordinal>\d{3})(?!\d)"
)

PR32_CANDIDATE_ID = "shared_pa_projected_opportunity_eb200_v2"
PR35_CANDIDATE_ID = "pitcher_joint_opportunity_v1"
PROJECTED_LINEUP_CONTRACT_PATH = "config/projected_lineup_contract_v1.json"
IMMUTABLE_SOURCE_SHA256 = {
    "pr32_protocol": "785f797b6785a7eaeb1ef8597b44a6e01ed6b52f9dd682736386d8924c0896ab",
    "pr32_source_manifest": "8e904425224a128c156452a91b22f8f9e89a221f7a6e9a3b9fedd34c8e932584",
    "pr35_protocol": "d349c81b0168b9924f387cbec6973ed5ea7e29d8194905992960e0605b75dbf8",
    "pr35_evidence_authority_receipt": "9c29e53656f70ce30bf1db4a415c814ab825f32288b3f482965d6cd5bc452a18",
}
NON_GAME_SOURCE_TYPES = frozenset(IMMUTABLE_SOURCE_SHA256)

# Exact source surfaces required by the already-inert cross-envelope.  Raw
# companions are first-class requirements; a semantic receipt without its raw
# bytes is not replay authority.
REQUIREMENTS: dict[str, dict[str, Any]] = {
    "pr32_protocol": {"schema": "shared-pa-projected-opportunity-protocol-v2", "source": "repository_source", "protocol": "pr32_exact_source", "repeatable": False, "raw": False},
    "pr32_source_manifest": {"schema": "shared-pa-projected-opportunity-candidate-hash-manifest-v2", "source": "repository_source", "protocol": "pr32_exact_source", "repeatable": False, "raw": False},
    "pr32_runtime_release_receipt": {"schema": "shared-pa-projected-opportunity-runtime-release-v2", "source": "external_release_observer", "protocol": "pr32_exact_release", "repeatable": False, "raw": False},
    "shared_plan": {"schema": "shadow-capture-plan-v1", "source": "official_mlb_schedule_t4", "protocol": "shared_t4_plan", "repeatable": False, "raw": False},
    "batter_side_bundle": {"schema": "shared-pa-projected-opportunity-side-bundle-v2", "source": "pr32_runner", "protocol": "pr32_exact_release", "repeatable": False, "raw": False},
    "batter_candidate_record": {"schema": "shared-pa-projected-opportunity-player-v2", "source": "pr32_runner", "protocol": "pr32_exact_release", "repeatable": False, "raw": False},
    "batter_projected_lineup": {"schema": "projected-lineup-projection-v1", "source": "projected_lineup_empirical_joint_v1", "protocol": "pr32_exact_release", "repeatable": False, "raw": False},
    "batter_active_roster_receipt": {"schema": "official_mlb_active_roster_t4", "source": "mlb_statsapi_active_roster", "protocol": "projected_lineup_roster_v1", "repeatable": False, "raw": False},
    "batter_active_roster_raw": {"schema": "raw-official-mlb-active-roster-json-v1", "source": "mlb_statsapi_active_roster", "protocol": "projected_lineup_roster_v1", "repeatable": False, "raw": True},
    "batter_history_coverage_receipt": {"schema": "prospective-batter-opportunity-coverage-v1", "source": "strict_prior_lineup_history", "protocol": "prospective_batter_opportunity_v1", "repeatable": False, "raw": False},
    "batter_history_schedule_receipt": {"schema": "prospective-batter-opportunity-schedule-v1", "source": "mlb_statsapi_schedule", "protocol": "prospective_batter_opportunity_v1", "repeatable": True, "raw": False},
    "batter_history_schedule_raw": {"schema": "raw-official-mlb-schedule-json-v1", "source": "mlb_statsapi_schedule", "protocol": "prospective_batter_opportunity_v1", "repeatable": True, "raw": True},
    "batter_history_game_receipt": {"schema": "prospective-batter-opportunity-history-v1", "source": "mlb_statsapi_final_feed", "protocol": "prospective_batter_opportunity_v1", "repeatable": True, "raw": False},
    "batter_history_game_raw": {"schema": "raw-official-mlb-final-feed-json-v1", "source": "mlb_statsapi_final_feed", "protocol": "prospective_batter_opportunity_v1", "repeatable": True, "raw": True},
    "batter_stats_transport_receipt": {"schema": "official-mlb-dated-game-log-transport-v1", "source": "mlb_statsapi_dated_game_log", "protocol": "pr32_stats_v2", "repeatable": True, "raw": False},
    "batter_stats_raw": {"schema": "raw-official-mlb-dated-game-log-json-v1", "source": "mlb_statsapi_dated_game_log", "protocol": "pr32_stats_v2", "repeatable": True, "raw": True},
    "batter_stats_terminal_attempt": {"schema": "official-mlb-stats-failed-attempt-v2", "source": "mlb_statsapi_dated_game_log", "protocol": "pr32_stats_v2", "repeatable": True, "raw": False},
    "pr35_protocol": {"schema": "pitcher-joint-opportunity-protocol-v1", "source": "repository_source", "protocol": "pr35_exact_source", "repeatable": False, "raw": False},
    "pr35_evidence_authority_receipt": {"schema": "pitcher-joint-opportunity-evidence-authority-v1", "source": "external_evidence_authority", "protocol": "pr35_exact_release", "repeatable": False, "raw": False},
    "pr35_runtime_release_receipt": {"schema": "pitcher-joint-opportunity-runtime-release-v1", "source": "external_release_observer", "protocol": "pr35_exact_release", "repeatable": False, "raw": False},
    "pr35_model_authorization_receipt": {"schema": "pitcher-joint-opportunity-model-authorization-v1", "source": "external_model_authority", "protocol": "pr35_exact_release", "repeatable": False, "raw": False},
    "starter_plan_source_receipt": {"schema": "aws-pitcher-receipt-plan-receipt-v1", "source": "mlb_statsapi_schedule", "protocol": "aws_pitcher_receipt_plan_v1", "repeatable": False, "raw": False},
    "starter_plan_source_raw": {"schema": "raw-official-mlb-schedule-json-v1", "source": "mlb_statsapi_schedule", "protocol": "aws_pitcher_receipt_plan_v1", "repeatable": False, "raw": True},
    "starter_ledger_manifest": {"schema": "forward-pitcher-context-ledger-v1", "source": "forward_pitcher_context_ledger", "protocol": "forward_pitcher_context_ledger_v1", "repeatable": False, "raw": False},
    "starter_ledger_terminal_record": {"schema": "forward-pitcher-context-ledger-entry-v1", "source": "forward_pitcher_context_ledger", "protocol": "forward_pitcher_context_ledger_v1", "repeatable": False, "raw": False},
    "starter_context_receipt": {"schema": "forward-pitcher-context-v2", "source": "mlb_statsapi_schedule", "protocol": "forward_pitcher_context_v2", "repeatable": False, "raw": False},
    "starter_context_raw": {"schema": "raw-official-mlb-schedule-json-v1", "source": "mlb_statsapi_schedule", "protocol": "forward_pitcher_context_v2", "repeatable": False, "raw": True},
    "starter_workload_transport_receipt": {"schema": "official-mlb-pitcher-game-log-transport-v1", "source": "mlb_statsapi_live_feed", "protocol": "pitcher_joint_opportunity_v1", "repeatable": True, "raw": False},
    "starter_workload_raw": {"schema": "raw-official-mlb-live-feed-json-v1", "source": "mlb_statsapi_live_feed", "protocol": "pitcher_joint_opportunity_v1", "repeatable": True, "raw": True},
    "starter_workload_receipt_manifest": {"schema": "pitcher-pit-workload-receipt-manifest-v1", "source": "pitcher_joint_opportunity_v1", "protocol": "pitcher_joint_opportunity_v1", "repeatable": False, "raw": False},
    "starter_workload_feature_artifact": {"schema": "pitcher-pit-workload-history-v1", "source": "pitcher_joint_opportunity_v1", "protocol": "pitcher_joint_opportunity_v1", "repeatable": False, "raw": False},
}

AUTHORITY_KEYS = {
    "schema_version", "status", "source_commits", "required_receipt_types",
    "available_legitimate_receipt_types", "authorized_inventory_sha256",
    "semantic_replay_authorized", "probability_consumption_authorized",
    "model_fitting_authorized", "network_fetch_authorized",
    "historical_or_prospective_backfill_authorized", "research_only",
    "betting_authorized", "may_2026_access_allowed",
}
INVENTORY_KEYS = {
    "schema_version", "source_commits", "target", "entries", "research_only",
    "betting_authorized", "probability_consumption_authorized",
    "model_fitting_authorized", "inventory_sha256",
}
TARGET_KEYS = {
    "official_game_date", "mlb_game_pk", "official_start_utc", "target_horizon_utc",
    "target_id", "batter_team_id", "pitching_team_id",
}
ENTRY_KEYS = {
    "schema_version", "receipt_type", "source_schema_version", "source_name",
    "protocol", "availability", "relative_path", "file_sha256", "raw_payload_sha256",
    "observed_at_utc", "terminal_reason", "entry_sha256",
}
OPTIONAL_TYPES = {"batter_stats_terminal_attempt"}
RAW_PAIRS = {
    "batter_active_roster_receipt": ("batter_active_roster_raw", "payload_sha256"),
    "batter_history_schedule_receipt": ("batter_history_schedule_raw", "source_payload_sha256"),
    "batter_history_game_receipt": ("batter_history_game_raw", "source_payload_sha256"),
    "batter_stats_transport_receipt": ("batter_stats_raw", "payload_sha256"),
    "starter_plan_source_receipt": ("starter_plan_source_raw", "source_payload_sha256"),
    "starter_context_receipt": ("starter_context_raw", "source_payload_sha256"),
    "starter_workload_transport_receipt": ("starter_workload_raw", "payload_sha256"),
}
RAW_TYPES = {pair[0] for pair in RAW_PAIRS.values()}


class ReceiptReplayError(ValueError):
    pass


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_value(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or not SHA_RE.fullmatch(value):
        raise ReceiptReplayError(f"{label} must be a lowercase SHA-256")
    return value


def _utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not UTC_RE.fullmatch(value):
        raise ReceiptReplayError(f"{label} must be canonical UTC")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ReceiptReplayError(f"{label} must be canonical UTC") from exc
    canonical = parsed.strftime("%Y-%m-%dT%H:%M:%S")
    if parsed.microsecond:
        canonical += f".{parsed.microsecond:06d}"
    canonical += "Z"
    if value != canonical:
        raise ReceiptReplayError(f"{label} must be canonical UTC")
    return parsed


def _canonical_date(value: Any, label: str) -> date:
    if not isinstance(value, str):
        raise ReceiptReplayError(f"{label} must be a canonical date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ReceiptReplayError(f"{label} must be a canonical date") from exc
    if parsed.isoformat() != value or (parsed.year == 2026 and parsed.month == 5):
        raise ReceiptReplayError(f"{label} is invalid or sealed")
    return parsed


def _reject_may(value: Any, label: str) -> None:
    if isinstance(value, Mapping):
        for child in value.values():
            _reject_may(child, label)
    elif isinstance(value, list):
        for child in value:
            _reject_may(child, label)
    elif isinstance(value, str):
        if MONTH_NAME_MAY_RE.search(value):
            raise ReceiptReplayError(f"{label} references sealed May 2026")
        for match in CALENDAR_DATE_RE.finditer(value):
            if int(match.group("year")) == 2026 and int(match.group("month")) == 5:
                raise ReceiptReplayError(f"{label} references sealed May 2026")
        for match in US_DATE_RE.finditer(value):
            if int(match.group("year")) == 2026 and int(match.group("month")) == 5:
                raise ReceiptReplayError(f"{label} references sealed May 2026")
        for match in BASIC_DATE_RE.finditer(value):
            if int(match.group("year")) == 2026 and int(match.group("month")) == 5:
                raise ReceiptReplayError(f"{label} references sealed May 2026")
        for match in ISO_WEEK_RE.finditer(value):
            try:
                parsed = date.fromisocalendar(
                    int(match.group("year")),
                    int(match.group("week")),
                    int(match.group("weekday")),
                )
            except ValueError:
                continue
            if parsed.year == 2026 and parsed.month == 5:
                raise ReceiptReplayError(f"{label} references sealed May 2026")
        for match in ORDINAL_DATE_RE.finditer(value):
            try:
                parsed = date(int(match.group("year")), 1, 1) + timedelta(
                    days=int(match.group("ordinal")) - 1
                )
            except ValueError:
                continue
            if (
                parsed.year == int(match.group("year"))
                and parsed.year == 2026
                and parsed.month == 5
            ):
                raise ReceiptReplayError(f"{label} references sealed May 2026")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, child in pairs:
        if key in result:
            raise ReceiptReplayError(f"duplicate JSON key {key!r}")
        result[key] = child
    return result


def _json_object(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw, object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReceiptReplayError(f"{label} is invalid JSON") from exc
    if not isinstance(value, dict):
        raise ReceiptReplayError(f"{label} must be an object")
    return value


def _is_redirected(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    attrs = getattr(info, "st_file_attributes", 0)
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(attrs & reparse)


def _safe_file(root: Path, relative: Any, label: str) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ReceiptReplayError(f"{label} path is unsafe")
    _reject_may(relative, label)
    rel = Path(relative)
    if rel.is_absolute() or any(part in {"", ".", ".."} for part in rel.parts):
        raise ReceiptReplayError(f"{label} path is unsafe")
    absolute_root = root.absolute()
    current = absolute_root
    for part in rel.parts:
        if _is_redirected(current):
            raise ReceiptReplayError(f"{label} path is redirected")
        current = current / part
    if _is_redirected(current):
        raise ReceiptReplayError(f"{label} path is redirected")
    try:
        resolved_root = absolute_root.resolve(strict=True)
        resolved = current.resolve(strict=True)
        resolved.relative_to(resolved_root)
    except (OSError, ValueError) as exc:
        raise ReceiptReplayError(f"{label} path escapes or is unavailable") from exc
    if not resolved.is_file():
        raise ReceiptReplayError(f"{label} file is unavailable")
    return resolved


def _self_hash(value: Mapping[str, Any], field: str, label: str) -> str:
    unsigned = dict(value)
    supplied = _sha(unsigned.pop(field, None), field)
    if sha256_value(unsigned) != supplied:
        raise ReceiptReplayError(f"{label} self-hash differs")
    return supplied


def validate_authority_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != AUTHORITY_KEYS:
        raise ReceiptReplayError("receipt replay authority schema changed")
    if value["schema_version"] != AUTHORITY_SCHEMA or value["source_commits"] != SOURCE_COMMITS:
        raise ReceiptReplayError("receipt replay source authority differs")
    if value["required_receipt_types"] != list(REQUIREMENTS):
        raise ReceiptReplayError("receipt replay requirement ordering or identity differs")
    expected = {
        "status": "BLOCKED_LEGITIMATE_RETAINED_RECEIPTS_NOT_DELIVERED",
        "available_legitimate_receipt_types": [],
        "authorized_inventory_sha256": None,
        "semantic_replay_authorized": False,
        "probability_consumption_authorized": False,
        "model_fitting_authorized": False,
        "network_fetch_authorized": False,
        "historical_or_prospective_backfill_authorized": False,
        "research_only": True,
        "betting_authorized": False,
        "may_2026_access_allowed": False,
    }
    if any(value.get(key) != expected_value for key, expected_value in expected.items()):
        raise ReceiptReplayError("receipt replay terminal authority was weakened")
    return dict(value)


def load_authority(root: Path) -> dict[str, Any]:
    path = _safe_file(root, AUTHORITY_PATH, "receipt replay authority")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != EXPECTED_AUTHORITY_SHA256:
        raise ReceiptReplayError("receipt replay authority bytes differ from the certified source")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReceiptReplayError("receipt replay authority is invalid JSON") from exc
    return validate_authority_payload(value)


def _validate_target(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != TARGET_KEYS:
        raise ReceiptReplayError("receipt inventory target schema changed")
    target_date = _canonical_date(value["official_game_date"], "official_game_date")
    start = _utc(value["official_start_utc"], "official_start_utc")
    horizon = _utc(value["target_horizon_utc"], "target_horizon_utc")
    if start - horizon != timedelta(hours=4):
        raise ReceiptReplayError("target horizon is not exactly T-4")
    if start.date() not in {target_date, target_date + timedelta(days=1)}:
        raise ReceiptReplayError(
            "official start UTC is not on the official game date or its UTC rollover"
        )
    for field in ("mlb_game_pk", "batter_team_id", "pitching_team_id"):
        if isinstance(value[field], bool) or not isinstance(value[field], int) or value[field] <= 0:
            raise ReceiptReplayError(f"{field} must be a positive integer")
    if value["batter_team_id"] == value["pitching_team_id"]:
        raise ReceiptReplayError("batter and pitching teams must differ")
    try:
        schedule_target = CaptureTarget(
            mlb_game_pk=value["mlb_game_pk"],
            official_game_date=target_date.isoformat(),
            official_start_time_utc=value["official_start_utc"],
            entry_target_at_utc=value["target_horizon_utc"],
            entry_hours=4,
        )
    except ShadowCapturePlanError as exc:
        raise ReceiptReplayError("receipt target schedule identity is invalid") from exc
    if _sha(value["target_id"], "target_id") != schedule_target.target_id:
        raise ReceiptReplayError("target_id differs from immutable schedule identity")
    return dict(value)


SEMANTIC_EXACT_FIELDS: dict[str, frozenset[str]] = {
    "pr32_runtime_release_receipt": frozenset({
        "schema_version", "candidate_id", "source_commit", "source_tree_clean",
        "source_manifest_path", "source_manifest_sha256", "candidate_protocol_path",
        "candidate_protocol_sha256", "candidate_protocol_status", "created_at_utc",
        "research_only", "betting_authorized", "release_receipt_sha256",
    }),
    "shared_plan": frozenset({
        "schema_version", "plan_sha256", "official_game_date", "entry_hours",
        "policy_sha256", "schedule_snapshot_sha256", "targets",
    }),
    "batter_side_bundle": frozenset({
        "schema_version", "terminal_state", "research_only", "betting_authorized",
        "promotion_eligible", "candidate_id", "candidate_protocol_sha256",
        "candidate_protocol_status", "source_manifest_sha256", "source_release_commit",
        "runtime_release_receipt_sha256", "plan_sha256", "target_id",
        "official_game_date", "mlb_game_pk", "team_id", "side",
        "target_horizon_utc", "prediction_generated_at_utc",
        "projected_lineup_content_sha256", "support_player_ids", "candidate_records",
        "abstentions", "coverage", "side_bundle_sha256",
    }),
    "batter_candidate_record": frozenset({
        "schema_version", "candidate_id", "terminal_state", "research_only",
        "betting_authorized", "promotion_eligible", "candidate_protocol_sha256",
        "candidate_protocol_status", "source_manifest_sha256", "source_release_commit",
        "runtime_release_receipt_sha256", "evidence_envelope_sha256", "evidence_class",
        "confirmation_eligible", "plan_sha256", "target_id", "official_game_date",
        "official_start_utc", "target_horizon_utc", "prediction_generated_at_utc",
        "mlb_game_pk", "side", "team_id", "player_id", "stats_source",
        "stats_cutoff_date", "stats_raw_sha256s", "stats_transport_receipt_sha256s",
        "per_pa_control_id", "per_pa_control_config_sha256", "pa_volume_artifact_sha256",
        "projected_start_probability", "projected_slot_probability",
        "candidate_pa_support", "candidate_pa_mass", "candidate_pa_distribution_sha256",
        "per_pa_probability", "baseline_market_distributions",
        "candidate_market_distributions", "pitcher_block_status", "policy_status",
        "candidate_code_sha256", "candidate_record_sha256",
    }),
    "batter_active_roster_receipt": frozenset({
        "source_kind", "source_record_id", "received_at_utc", "payload_sha256",
        "input_surface_sha256", "players",
    }),
    "batter_history_schedule_receipt": frozenset({
        "schema_version", "source_kind", "team_id", "requested_start_date",
        "requested_end_date", "target_official_game_date", "target_horizon_utc",
        "source_received_at_utc", "source_request_url", "source_http_status",
        "source_content_type", "source_payload_sha256", "input_surface_sha256",
        "games", "research_only", "betting_authorized", "schedule_receipt_sha256",
    }),
    "batter_history_game_receipt": frozenset({
        "schema_version", "source_kind", "mlb_game_pk", "official_game_date", "side",
        "team_id", "capture_plan", "capture_plan_sha256", "source_received_at_utc",
        "source_request_url", "source_http_status", "source_content_type",
        "source_payload_sha256", "transport_payload_sha256", "transport_payload_size",
        "transport_payload_retained", "input_surface_sha256", "players",
        "research_only", "betting_authorized", "history_record_sha256",
    }),
    "batter_stats_transport_receipt": frozenset({
        "schema_version", "method", "request_url", "request_sent_at_utc",
        "received_at_utc", "http_status", "content_type", "request_sha256",
        "payload_sha256", "payload_size",
    }),
    "batter_stats_terminal_attempt": frozenset({
        "schema_version", "player_id", "request_urls", "attempted_at_utc",
        "failed_at_utc", "error_class", "attempt_sha256",
    }),
    "pr35_evidence_authority_receipt": frozenset({
        "schema_version", "candidate_id", "status",
        "authorized_evidence_authority_receipt_sha256", "binding",
        "required_binding_fields", "caller_selected_root_allowed",
        "direct_typed_receipt_consumption_allowed",
        "synthetic_replay_probability_consumption_allowed",
        "historical_or_missed_receipt_backfill_allowed", "research_only",
        "betting_authorized",
    }),
    "pr35_runtime_release_receipt": frozenset({
        "schema_version", "candidate_id", "protocol_sha256", "source_release_sha256",
        "test_evidence_sha256", "research_only", "betting_authorized", "release_sha256",
    }),
    "pr35_model_authorization_receipt": frozenset({
        "schema_version", "candidate_id", "qualification_state", "protocol_sha256",
        "artifact_sha256", "training_data_sha256", "fit_code_sha256",
        "fit_tests_sha256", "qualification_report_sha256", "research_only",
        "betting_authorized", "authorization_sha256",
    }),
    "starter_plan_source_receipt": frozenset({
        "schema_version", "official_game_date", "plan_sha256", "runtime_sha256",
        "source_name", "source_payload_sha256", "received_at_utc", "targets",
        "research_only", "betting_authorized", "model_or_market_accessed",
        "receipt_sha256",
    }),
    "starter_ledger_manifest": frozenset({
        "schema_version", "plan_sha256", "runtime_sha256", "target_ids", "records",
        "last_chain_sha256", "entry_chain_order", "research_only", "betting_authorized",
    }),
    "starter_ledger_terminal_record": frozenset({
        "schema_version", "target_id", "plan_sha256", "terminal_state",
        "observed_at_utc", "detail", "raw_payload_sha256", "raw_payload_path",
        "context_sha256", "context_path", "previous_chain_sha256", "chain_sha256",
    }),
    "starter_context_receipt": frozenset({
        "schema_version", "context_sha256", "target_id", "plan_sha256",
        "captured_at_utc", "source_name", "source_payload_sha256", "mlb_game_pk",
        "official_game_date", "official_start_time_utc", "game_type", "home_team_id",
        "home_team_name", "away_team_id", "away_team_name", "home_probable_pitcher",
        "away_probable_pitcher", "candidate_input_eligible",
    }),
    "starter_workload_transport_receipt": frozenset({
        "schema_version", "method", "request_url", "request_sent_at_utc",
        "received_at_utc", "http_status", "content_type", "request_sha256",
        "payload_sha256", "payload_size",
    }),
    "starter_workload_receipt_manifest": frozenset({
        "schema_version", "transport_receipt_sha256s", "manifest_sha256",
        "research_only", "betting_authorized",
    }),
}

SEMANTIC_REQUIRED_FIELDS: dict[str, frozenset[str]] = {
    "pr32_protocol": frozenset({"schema_version", "status", "candidate_id", "decision_horizon", "protected_boundaries"}),
    "pr32_source_manifest": frozenset({"schema_version", "status", "candidate_id", "files", "protected_boundaries"}),
    "batter_history_coverage_receipt": frozenset({
        "schema_version", "collection_epoch_date", "target_official_game_date", "team_id",
        "schedule_receipts", "schedule_receipt_sha256s", "schedule_raw_sha256s",
        "expected_prior_game_pks", "nonfinal_prior_game_pks", "captured_prior_game_pks",
        "terminal_missing_prior_game_pks", "expected_game_count", "captured_game_count",
        "terminal_missing_game_count", "complete_coverage", "denominator_receipt_bound",
        "research_only", "betting_authorized", "coverage_sha256",
    }),
    "pr35_protocol": frozenset({"schema_version", "candidate_id", "status", "research_boundary", "candidate_application"}),
    "starter_workload_feature_artifact": frozenset({
        "schema_version", "source_kind", "target_id", "official_game_date", "mlb_game_pk",
        "pitcher_id", "pitching_team_id", "target_horizon_utc", "assembled_at_utc",
        "max_source_game_date", "lineage", "prior_appearances", "workload_sha256",
    }),
}

SELF_HASH_FIELDS = {
    "pr32_runtime_release_receipt": "release_receipt_sha256",
    "batter_side_bundle": "side_bundle_sha256",
    "batter_candidate_record": "candidate_record_sha256",
    "batter_history_coverage_receipt": "coverage_sha256",
    "batter_history_schedule_receipt": "schedule_receipt_sha256",
    "batter_history_game_receipt": "history_record_sha256",
    "batter_stats_terminal_attempt": "attempt_sha256",
    "pr35_runtime_release_receipt": "release_sha256",
    "pr35_model_authorization_receipt": "authorization_sha256",
    "starter_plan_source_receipt": "receipt_sha256",
    "starter_ledger_terminal_record": "chain_sha256",
    "starter_workload_receipt_manifest": "manifest_sha256",
    "starter_workload_feature_artifact": "workload_sha256",
}


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ReceiptReplayError(f"{label} must be a positive integer")
    return value


def _validate_semantic_payload(
    *, root: Path, receipt_type: str, raw: bytes, payload: Mapping[str, Any],
    target: Mapping[str, Any], horizon: datetime,
) -> None:
    expected_fields = SEMANTIC_EXACT_FIELDS.get(receipt_type)
    if expected_fields is not None and set(payload) != expected_fields:
        raise ReceiptReplayError(f"{receipt_type} typed field set differs")
    required_fields = SEMANTIC_REQUIRED_FIELDS.get(receipt_type, frozenset())
    if not required_fields <= set(payload):
        raise ReceiptReplayError(f"{receipt_type} is missing mandatory semantic fields")

    immutable_digest = IMMUTABLE_SOURCE_SHA256.get(receipt_type)
    if immutable_digest is not None and hashlib.sha256(raw).hexdigest() != immutable_digest:
        raise ReceiptReplayError(f"{receipt_type} differs from its exact source commit bytes")

    self_hash_field = SELF_HASH_FIELDS.get(receipt_type)
    if self_hash_field is not None:
        _self_hash(payload, self_hash_field, receipt_type)

    if receipt_type == "shared_plan":
        try:
            plan = ShadowCapturePlan.from_mapping(payload)
        except ShadowCapturePlanError as exc:
            raise ReceiptReplayError("shared plan semantic replay failed") from exc
        matches = [candidate for candidate in plan.targets if candidate.target_id == target["target_id"]]
        if (
            plan.official_game_date != target["official_game_date"]
            or plan.entry_hours != 4
            or len(matches) != 1
            or matches[0].mlb_game_pk != target["mlb_game_pk"]
            or matches[0].official_start_time_utc != target["official_start_utc"]
            or matches[0].entry_target_at_utc != target["target_horizon_utc"]
        ):
            raise ReceiptReplayError("shared plan target identity differs")
    elif receipt_type == "batter_projected_lineup":
        try:
            contract = load_projected_lineup_contract(root / PROJECTED_LINEUP_CONTRACT_PATH)
            validate_projection(payload, contract)
        except (OSError, ProjectedLineupContractError) as exc:
            raise ReceiptReplayError("projected lineup semantic replay failed") from exc
    elif receipt_type == "starter_context_receipt":
        try:
            context = ForwardPitcherContextV2.from_mapping(payload)
        except ForwardPitcherContextV2Error as exc:
            raise ReceiptReplayError("starter context semantic replay failed") from exc
        if (
            context.target_id != target["target_id"]
            or context.mlb_game_pk != target["mlb_game_pk"]
            or context.official_game_date != target["official_game_date"]
            or context.official_start_time_utc != target["official_start_utc"]
            or context.captured_at_utc != payload["captured_at_utc"]
        ):
            raise ReceiptReplayError("starter context target identity differs")
    elif receipt_type == "pr35_evidence_authority_receipt":
        try:
            validate_evidence_authority_contract_bytes(raw)
        except PitcherJointOpportunityError as exc:
            raise ReceiptReplayError("PR35 evidence authority semantic replay failed") from exc

    static_values = {
        "pr32_protocol": {"candidate_id": PR32_CANDIDATE_ID, "decision_horizon": "T-4h"},
        "pr32_source_manifest": {"candidate_id": PR32_CANDIDATE_ID},
        "pr32_runtime_release_receipt": {"candidate_id": PR32_CANDIDATE_ID, "source_commit": PR32, "research_only": True, "betting_authorized": False},
        "batter_side_bundle": {"candidate_id": PR32_CANDIDATE_ID, "source_release_commit": PR32, "research_only": True, "betting_authorized": False, "promotion_eligible": False},
        "batter_candidate_record": {"candidate_id": PR32_CANDIDATE_ID, "source_release_commit": PR32, "research_only": True, "betting_authorized": False, "promotion_eligible": False},
        "batter_history_schedule_receipt": {"research_only": True, "betting_authorized": False},
        "batter_history_game_receipt": {"research_only": True, "betting_authorized": False},
        "pr35_protocol": {"candidate_id": PR35_CANDIDATE_ID},
        "pr35_evidence_authority_receipt": {"candidate_id": PR35_CANDIDATE_ID, "research_only": True, "betting_authorized": False},
        "pr35_runtime_release_receipt": {"candidate_id": PR35_CANDIDATE_ID, "research_only": True, "betting_authorized": False},
        "pr35_model_authorization_receipt": {"candidate_id": PR35_CANDIDATE_ID, "research_only": True, "betting_authorized": False},
        "starter_plan_source_receipt": {"source_name": "mlb_statsapi_schedule", "research_only": True, "betting_authorized": False, "model_or_market_accessed": False},
        "starter_ledger_manifest": {"research_only": True, "betting_authorized": False},
        "starter_workload_receipt_manifest": {"research_only": True, "betting_authorized": False},
    }
    for field, expected in static_values.get(receipt_type, {}).items():
        if payload.get(field) != expected:
            raise ReceiptReplayError(f"{receipt_type} semantic {field} differs")

    target_fields = {
        "target_id": target["target_id"],
        "official_game_date": target["official_game_date"],
        "target_official_game_date": target["official_game_date"],
        "mlb_game_pk": target["mlb_game_pk"],
        "official_start_utc": target["official_start_utc"],
        "official_start_time_utc": target["official_start_utc"],
        "target_horizon_utc": target["target_horizon_utc"],
    }
    for field, expected in target_fields.items():
        if (
            field in payload
            and not (receipt_type == "batter_history_game_receipt" and field == "official_game_date")
            and payload[field] != expected
        ):
            raise ReceiptReplayError(f"{receipt_type} target identity differs")
    if receipt_type == "batter_history_game_receipt":
        history_date = _canonical_date(payload["official_game_date"], "history official_game_date")
        if history_date >= _canonical_date(target["official_game_date"], "target official_game_date"):
            raise ReceiptReplayError("batter history is not strictly prior to the target game")

    for field in ("team_id",):
        if field in payload and payload[field] != target["batter_team_id"]:
            raise ReceiptReplayError(f"{receipt_type} batter team identity differs")
    if "pitching_team_id" in payload and payload["pitching_team_id"] != target["pitching_team_id"]:
        raise ReceiptReplayError(f"{receipt_type} pitching team identity differs")
    if receipt_type == "starter_context_receipt" and {
        payload["home_team_id"], payload["away_team_id"]
    } != {target["batter_team_id"], target["pitching_team_id"]}:
        raise ReceiptReplayError("starter context team identities differ")

    for field in (
        "received_at_utc", "source_received_at_utc", "captured_at_utc",
        "observed_at_utc", "request_sent_at_utc", "assembled_at_utc",
        "prediction_generated_at_utc", "created_at_utc", "attempted_at_utc",
        "failed_at_utc",
    ):
        if field in payload and _utc(payload[field], f"{receipt_type}.{field}") > horizon:
            raise ReceiptReplayError(f"{receipt_type} chronology exceeds T-4")

    for field in (
        "mlb_game_pk", "team_id", "pitching_team_id", "player_id", "pitcher_id",
        "home_team_id", "away_team_id", "payload_size", "transport_payload_size",
    ):
        if field in payload:
            _positive_int(payload[field], f"{receipt_type}.{field}")

    if receipt_type in {"batter_stats_transport_receipt", "starter_workload_transport_receipt"}:
        if payload["method"] != "GET" or payload["http_status"] != 200:
            raise ReceiptReplayError(f"{receipt_type} transport identity differs")
        if payload["content_type"].split(";", 1)[0].strip().lower() != "application/json":
            raise ReceiptReplayError(f"{receipt_type} transport content type differs")
        if _utc(payload["received_at_utc"], f"{receipt_type}.received_at_utc") < _utc(
            payload["request_sent_at_utc"], f"{receipt_type}.request_sent_at_utc"
        ):
            raise ReceiptReplayError(f"{receipt_type} response predates its request")


def _validate_entry(root: Path, value: Any, target: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != ENTRY_KEYS:
        raise ReceiptReplayError("retained receipt entry schema changed")
    _self_hash(value, "entry_sha256", "retained receipt entry")
    if value["schema_version"] != ENTRY_SCHEMA or value["receipt_type"] not in REQUIREMENTS:
        raise ReceiptReplayError("retained receipt type is unknown")
    requirement = REQUIREMENTS[value["receipt_type"]]
    if (
        value["source_schema_version"] != requirement["schema"]
        or value["source_name"] != requirement["source"]
        or value["protocol"] != requirement["protocol"]
    ):
        raise ReceiptReplayError("retained receipt schema, source, or protocol differs")
    horizon = _utc(target["target_horizon_utc"], "target_horizon_utc")
    observed = _utc(value["observed_at_utc"], "receipt observed_at_utc")
    availability = value["availability"]
    if availability == "terminal_missing":
        if (
            value["relative_path"] is not None
            or value["file_sha256"] is not None
            or value["raw_payload_sha256"] is not None
        ):
            raise ReceiptReplayError("terminal missingness cannot claim retained bytes")
        if value["terminal_reason"] not in {"source_error_before_horizon", "missed_after_horizon", "not_delivered_to_audit"}:
            raise ReceiptReplayError("terminal missingness reason is not explicit")
        if value["terminal_reason"] == "missed_after_horizon" and observed <= horizon:
            raise ReceiptReplayError("missed receipt was declared before the horizon")
        if value["terminal_reason"] == "source_error_before_horizon" and observed > horizon:
            raise ReceiptReplayError("source error was declared after the horizon")
        return dict(value)
    if availability != "retained" or value["terminal_reason"] is not None:
        raise ReceiptReplayError("receipt availability state is invalid")
    if observed > horizon:
        raise ReceiptReplayError("retained receipt arrived after T-4")
    path = _safe_file(root, value["relative_path"], value["receipt_type"])
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != _sha(value["file_sha256"], "receipt file_sha256"):
        raise ReceiptReplayError("retained receipt bytes differ from their hash")
    if value["receipt_type"] not in NON_GAME_SOURCE_TYPES:
        try:
            decoded = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ReceiptReplayError("retained receipt is not UTF-8") from exc
        _reject_may(decoded, value["receipt_type"])
    if requirement["raw"]:
        if _sha(value["raw_payload_sha256"], "raw_payload_sha256") != hashlib.sha256(raw).hexdigest():
            raise ReceiptReplayError("raw companion identity differs from retained bytes")
    else:
        payload = _json_object(raw, "retained semantic receipt")
        observed_schema = payload.get("schema_version", payload.get("source_kind"))
        if observed_schema != requirement["schema"]:
            raise ReceiptReplayError("retained payload schema differs")
        if value["receipt_type"] in RAW_PAIRS:
            payload_field = RAW_PAIRS[value["receipt_type"]][1]
            if _sha(value["raw_payload_sha256"], "raw_payload_sha256") != _sha(
                payload.get(payload_field), payload_field
            ):
                raise ReceiptReplayError("semantic receipt does not bind its raw companion")
        elif value["raw_payload_sha256"] is not None:
            raise ReceiptReplayError("non-transport receipt claims an unrelated raw payload")
        _validate_semantic_payload(
            root=root,
            receipt_type=value["receipt_type"],
            raw=raw,
            payload=payload,
            target=target,
            horizon=horizon,
        )
    return dict(value)


def _retained_bytes(root: Path, row: Mapping[str, Any]) -> bytes:
    return _safe_file(root, row["relative_path"], row["receipt_type"]).read_bytes()


def _validate_transport_replay(
    *, receipt_type: str, receipt: Mapping[str, Any], raw: bytes
) -> None:
    payload_sha = hashlib.sha256(raw).hexdigest()
    if receipt["payload_sha256"] != payload_sha or receipt["payload_size"] != len(raw):
        raise ReceiptReplayError(f"{receipt_type} raw payload identity differs")
    request_identity = sha256_value({
        "method": "GET",
        "request_url": receipt["request_url"],
        "request_sent_at_utc": receipt["request_sent_at_utc"],
    })
    if receipt["request_sha256"] != request_identity:
        raise ReceiptReplayError(f"{receipt_type} request identity differs")


def _validate_schedule_raw_target(raw: bytes, target: Mapping[str, Any], label: str) -> None:
    payload = _json_object(raw, label)
    try:
        games = games_from_raw_schedule_response(payload, allow_empty_date=True)
    except (ForwardPitcherContextError, ValueError) as exc:
        raise ReceiptReplayError(f"{label} schedule replay failed") from exc
    matches = [game for game in games if game.get("gamePk") == target["mlb_game_pk"]]
    if len(matches) != 1:
        raise ReceiptReplayError(f"{label} has no unique target game")
    game = matches[0]
    try:
        home_id = _positive_int(game["teams"]["home"]["team"]["id"], f"{label}.home_team_id")
        away_id = _positive_int(game["teams"]["away"]["team"]["id"], f"{label}.away_team_id")
    except (KeyError, TypeError) as exc:
        raise ReceiptReplayError(f"{label} team identity is unavailable") from exc
    if (
        game.get("officialDate") != target["official_game_date"]
        or _utc(game.get("gameDate"), f"{label}.gameDate")
        != _utc(target["official_start_utc"], "target official_start_utc")
        or {home_id, away_id} != {target["batter_team_id"], target["pitching_team_id"]}
    ):
        raise ReceiptReplayError(f"{label} target/date/team identity differs")


def _validate_paired_semantics(
    root: Path, rows: list[Mapping[str, Any]], target: Mapping[str, Any]
) -> None:
    retained: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        if row["availability"] == "retained":
            retained.setdefault(row["receipt_type"], []).append(row)

    raw_by_type_and_hash: dict[tuple[str, str], bytes] = {}
    semantic_payloads: dict[str, list[tuple[Mapping[str, Any], dict[str, Any]]]] = {}
    for receipt_type, typed_rows in retained.items():
        for row in typed_rows:
            raw = _retained_bytes(root, row)
            if REQUIREMENTS[receipt_type]["raw"]:
                raw_by_type_and_hash[(receipt_type, row["raw_payload_sha256"])] = raw
            else:
                semantic_payloads.setdefault(receipt_type, []).append(
                    (row, _json_object(raw, receipt_type))
                )

    for semantic_type, (raw_type, _) in RAW_PAIRS.items():
        for row, receipt in semantic_payloads.get(semantic_type, []):
            raw = raw_by_type_and_hash.get((raw_type, row["raw_payload_sha256"]))
            if raw is None:
                raise ReceiptReplayError("semantic receipt raw companion is unavailable")
            if semantic_type == "batter_active_roster_receipt":
                try:
                    replayed = parse_active_roster_receipt(
                        response=RawOfficialRosterResponse(raw, receipt["received_at_utc"]),
                        requested_date=target["official_game_date"],
                        team_id=target["batter_team_id"],
                        target_horizon_utc=target["target_horizon_utc"],
                    )
                except OfficialRosterReceiptError as exc:
                    raise ReceiptReplayError("active-roster raw replay failed") from exc
                if replayed != receipt:
                    raise ReceiptReplayError("active-roster receipt differs from raw replay")
            elif semantic_type == "batter_history_schedule_receipt":
                try:
                    replayed = parse_prior_schedule_denominator(
                        response=RawOpportunityResponse(
                            raw,
                            receipt["source_received_at_utc"],
                            receipt["source_request_url"],
                            receipt["source_http_status"],
                            receipt["source_content_type"],
                        ),
                        requested_start_date=receipt["requested_start_date"],
                        requested_end_date=receipt["requested_end_date"],
                        target_official_game_date=target["official_game_date"],
                        target_horizon_utc=target["target_horizon_utc"],
                        team_id=target["batter_team_id"],
                    )
                except ProspectiveBatterOpportunityError as exc:
                    raise ReceiptReplayError("history-schedule raw replay failed") from exc
                if replayed != receipt:
                    raise ReceiptReplayError("history-schedule receipt differs from raw replay")
            elif semantic_type == "batter_history_game_receipt":
                try:
                    replayed = parse_opportunity_history(
                        response=RawOpportunityResponse(
                            raw,
                            receipt["source_received_at_utc"],
                            receipt["source_request_url"],
                            receipt["source_http_status"],
                            receipt["source_content_type"],
                            receipt["transport_payload_sha256"],
                            receipt["transport_payload_size"],
                        ),
                        expected_game_pk=receipt["mlb_game_pk"],
                        expected_official_date=receipt["official_game_date"],
                        side=receipt["side"],
                        expected_team_id=receipt["team_id"],
                        capture_plan=receipt["capture_plan"],
                    )
                except ProspectiveBatterOpportunityError as exc:
                    raise ReceiptReplayError("history-game raw replay failed") from exc
                if replayed != receipt:
                    raise ReceiptReplayError("history-game receipt differs from raw replay")
            elif semantic_type in {
                "batter_stats_transport_receipt", "starter_workload_transport_receipt"
            }:
                _validate_transport_replay(
                    receipt_type=semantic_type, receipt=receipt, raw=raw
                )
            elif semantic_type in {"starter_plan_source_receipt", "starter_context_receipt"}:
                _validate_schedule_raw_target(raw, target, semantic_type)

    coverage_rows = semantic_payloads.get("batter_history_coverage_receipt", [])
    schedule_rows = semantic_payloads.get("batter_history_schedule_receipt", [])
    history_rows = semantic_payloads.get("batter_history_game_receipt", [])
    if coverage_rows:
        if len(coverage_rows) != 1:
            raise ReceiptReplayError("history coverage receipt cardinality differs")
        coverage = coverage_rows[0][1]
        schedule_raw_by_sha256 = {
            row["raw_payload_sha256"]: raw_by_type_and_hash[
                ("batter_history_schedule_raw", row["raw_payload_sha256"])
            ]
            for row, _ in schedule_rows
        }
        try:
            _validate_coverage(
                coverage,
                target_date=_canonical_date(
                    target["official_game_date"], "target official_game_date"
                ),
                target_horizon_utc=target["target_horizon_utc"],
                team_id=target["batter_team_id"],
                schedule_raw_by_sha256=schedule_raw_by_sha256,
            )
        except ProspectiveBatterOpportunityError as exc:
            raise ReceiptReplayError("history coverage raw replay failed") from exc
        retained_history_game_pks = sorted(
            receipt["mlb_game_pk"] for _, receipt in history_rows
        )
        if retained_history_game_pks != coverage["captured_prior_game_pks"]:
            raise ReceiptReplayError(
                "history coverage captured games differ from retained history receipts"
            )

    shared_plans = semantic_payloads.get("shared_plan", [])
    plan_payload = shared_plans[0][1] if len(shared_plans) == 1 else None
    if plan_payload is not None:
        plan = ShadowCapturePlan.from_mapping(plan_payload)
        for receipt_type in ("starter_plan_source_receipt", "starter_ledger_manifest", "starter_context_receipt"):
            for _, receipt in semantic_payloads.get(receipt_type, []):
                if receipt.get("plan_sha256") != plan.plan_sha256:
                    raise ReceiptReplayError(f"{receipt_type} plan identity differs")

    workload_rows = semantic_payloads.get("starter_workload_transport_receipt", [])
    feature_rows = semantic_payloads.get("starter_workload_feature_artifact", [])
    context_rows = semantic_payloads.get("starter_context_receipt", [])
    if workload_rows and feature_rows and context_rows:
        context = ForwardPitcherContextV2.from_mapping(context_rows[0][1])
        probable = (
            context.home_probable_pitcher
            if context.home_team_id == target["pitching_team_id"]
            else context.away_probable_pitcher
        )
        if probable.status != "resolved" or probable.player_id is None:
            raise ReceiptReplayError("workload feature has no receipt-proven probable pitcher")
        target_value = CaptureTarget(
            mlb_game_pk=target["mlb_game_pk"],
            official_game_date=target["official_game_date"],
            official_start_time_utc=target["official_start_utc"],
            entry_target_at_utc=target["target_horizon_utc"],
            entry_hours=4,
        )
        raw_receipts: list[RawPitcherWorkloadReceipt] = []
        transport_hashes: list[str] = []
        for row, receipt in workload_rows:
            raw = raw_by_type_and_hash[("starter_workload_raw", row["raw_payload_sha256"])]
            transport_sha = sha256_value(receipt)
            raw_receipts.append(RawPitcherWorkloadReceipt(
                body=raw,
                request_url=receipt["request_url"],
                request_sent_at_utc=receipt["request_sent_at_utc"],
                received_at_utc=receipt["received_at_utc"],
                http_status=receipt["http_status"],
                content_type=receipt["content_type"],
                request_sha256=receipt["request_sha256"],
                payload_sha256=receipt["payload_sha256"],
                payload_size=receipt["payload_size"],
                transport_receipt_sha256=transport_sha,
            ))
            transport_hashes.append(transport_sha)
        manifests = semantic_payloads.get("starter_workload_receipt_manifest", [])
        if len(manifests) != 1 or manifests[0][1]["transport_receipt_sha256s"] != transport_hashes:
            raise ReceiptReplayError("workload receipt manifest differs from retained transports")
        try:
            validate_workload_history(
                feature_rows[0][1],
                target=target_value,
                pitcher_id=probable.player_id,
                pitching_team_id=target["pitching_team_id"],
                protocol_sha256=IMMUTABLE_SOURCE_SHA256["pr35_protocol"],
                raw_receipts=raw_receipts,
            )
        except PitcherJointOpportunityError as exc:
            raise ReceiptReplayError("workload feature differs from raw PR35 replay") from exc


def validate_inventory_structure(root: Path, value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != INVENTORY_KEYS:
        raise ReceiptReplayError("retained receipt inventory schema changed")
    _self_hash(value, "inventory_sha256", "retained receipt inventory")
    if value["schema_version"] != INVENTORY_SCHEMA or value["source_commits"] != SOURCE_COMMITS:
        raise ReceiptReplayError("retained receipt inventory source identity differs")
    if (
        value["research_only"] is not True
        or value["betting_authorized"] is not False
        or value["probability_consumption_authorized"] is not False
        or value["model_fitting_authorized"] is not False
    ):
        raise ReceiptReplayError("retained receipt inventory authorization was widened")
    _reject_may(value, "retained receipt inventory")
    target = _validate_target(value["target"])
    if not isinstance(value["entries"], list):
        raise ReceiptReplayError("retained receipt entries must be a list")
    rows = [_validate_entry(root, row, target) for row in value["entries"]]
    counts = {name: 0 for name in REQUIREMENTS}
    for row in rows:
        counts[row["receipt_type"]] += 1
    for name, requirement in REQUIREMENTS.items():
        if (
            (name not in OPTIONAL_TYPES and counts[name] < 1)
            or (not requirement["repeatable"] and counts[name] != 1)
        ):
            raise ReceiptReplayError("retained receipt inventory coverage is incomplete or duplicated")
    if counts["batter_stats_transport_receipt"] + counts["batter_stats_terminal_attempt"] < 1:
        raise ReceiptReplayError("batter stats terminal evidence is absent")
    for semantic_type, (raw_type, _) in RAW_PAIRS.items():
        semantic_hashes = sorted(
            row["raw_payload_sha256"] for row in rows
            if row["receipt_type"] == semantic_type and row["availability"] == "retained"
        )
        raw_hashes = sorted(
            row["raw_payload_sha256"] for row in rows
            if row["receipt_type"] == raw_type and row["availability"] == "retained"
        )
        if semantic_hashes != raw_hashes:
            raise ReceiptReplayError("semantic receipt/raw companion coverage differs")
    _validate_paired_semantics(root, rows, target)
    return dict(value)


def audit_inventory(*, root: Path, inventory_path: Path, expected_inventory_sha256: str) -> dict[str, Any]:
    """Validate candidate bytes, then preserve the external-authority blocker."""
    authority = load_authority(root)
    try:
        relative_inventory = inventory_path.absolute().relative_to(root.absolute()).as_posix()
    except ValueError as exc:
        raise ReceiptReplayError("receipt inventory path is outside the evidence root") from exc
    path = _safe_file(root, relative_inventory, "receipt inventory")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != _sha(expected_inventory_sha256, "expected inventory SHA-256"):
        raise ReceiptReplayError("retained receipt inventory differs from external expected bytes")
    inventory = _json_object(raw, "retained receipt inventory")
    validate_inventory_structure(root, inventory)
    if authority["authorized_inventory_sha256"] is None:
        return {
            "schema_version": "full-game-opportunity-receipt-replay-audit-v1",
            "terminal_state": "BLOCKED_UNBOUND_EXTERNAL_RECEIPT_AUTHORITY",
            "inventory_sha256": expected_inventory_sha256,
            "required_receipt_type_count": len(REQUIREMENTS),
            "semantic_replay_authorized": False,
            "probability_consumption_authorized": False,
            "model_fitting_authorized": False,
            "research_only": True,
            "betting_authorized": False,
        }
    raise ReceiptReplayError("checked-in authority unexpectedly authorizes receipt replay")


def terminal_blocker(root: Path) -> dict[str, Any]:
    authority = load_authority(root)
    return {
        "schema_version": "full-game-opportunity-receipt-replay-audit-v1",
        "terminal_state": authority["status"],
        "missing_receipt_types": list(REQUIREMENTS),
        "authorized_inventory_sha256": None,
        "semantic_replay_authorized": False,
        "probability_consumption_authorized": False,
        "model_fitting_authorized": False,
        "research_only": True,
        "betting_authorized": False,
    }
