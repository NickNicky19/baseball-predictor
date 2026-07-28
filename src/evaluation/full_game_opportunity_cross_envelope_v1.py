"""Inert fail-closed binding between batter PA and starter-removal evidence.

This module does not combine probabilities.  It only proves that two already
constructed research distributions refer to the same T-4 game state and exact
candidate releases.  The checked-in authority is deliberately unbound, so the
public consumer always abstains.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import stat
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = "full-game-opportunity-cross-envelope-v1"
AUTHORITY_SCHEMA_VERSION = "full-game-opportunity-cross-envelope-authority-v1"
OUTPUT_SCHEMA_VERSION = "full-game-opportunity-cross-envelope-output-v1"
UNBOUND = "UNBOUND_EXTERNAL_TRUST_REQUIRED"

PR32_COMMIT = "1ebeb255bf3ecf17adb82f644f9dc2ff11c7491c"
PR32_CANDIDATE_ID = "shared_pa_projected_opportunity_eb200_v2"
PR33_COMMIT = "506ebb40203582d25ff01c0a8c2d1dec4c44ed7d"
PR35_COMMIT = "0e6a40d5cd1b00d3b1f1128f97a12e7b897aaf55"
PR35_CANDIDATE_ID = "pitcher_joint_opportunity_v1"
PR35_PROTOCOL_SHA256 = "d349c81b0168b9924f387cbec6973ed5ea7e29d8194905992960e0605b75dbf8"
AUTHORITY_RELATIVE_PATH = "config/full_game_opportunity_cross_envelope_v1_authority.json"
# Filled from the exact checked-in authority bytes; mutation must fail closed.
EXPECTED_AUTHORITY_SHA256 = "b1523651debd3cd2c4f10ded2fe4ce2bdbf8f2d299143576678f535d1409e7d1"

_TOP_KEYS = {
    "schema_version", "research_only", "betting_authorized",
    "production_probability_consumption_authorized", "authority_state",
    "target", "release_bindings", "batter", "starter",
    "bullpen_transition_boundary", "protected_boundaries", "evidence_references",
    "envelope_sha256",
}
_TARGET_KEYS = {
    "official_game_date", "mlb_game_pk", "official_start_utc", "target_horizon_utc",
    "batter_side", "batting_team_id", "pitching_side", "pitching_team_id",
    "batter_opponent_team_id", "pitcher_opponent_team_id",
}
_RELEASE_KEYS = {
    "pr32_source_commit", "pr32_candidate_id", "pr32_protocol_sha256",
    "pr32_source_manifest_sha256", "pr32_runtime_release_receipt_sha256",
    "pr33_source_commit", "pr35_source_commit", "pr35_candidate_id",
    "pr35_protocol_sha256", "pr35_evidence_authority_receipt_sha256",
    "pr35_runtime_release_receipt_sha256", "pr35_model_authorization_receipt_sha256",
    "cross_envelope_authority_receipt_sha256",
}
_BATTER_KEYS = {
    "side_bundle_sha256", "candidate_record_sha256", "plan_sha256", "target_id",
    "official_game_date", "mlb_game_pk", "official_start_utc", "target_horizon_utc",
    "prediction_generated_at_utc", "side", "team_id", "player_id",
    "projected_lineup_content_sha256", "active_roster_receipt_sha256",
    "history_receipt_manifest_sha256", "stats_transport_receipt_sha256s",
    "source_kinds", "actual_target_lineup_consumed", "fallback_used",
    "pa_pmf", "pa_pmf_sha256",
}
_STARTER_KEYS = {
    "target_id", "official_game_date", "mlb_game_pk", "official_start_utc",
    "target_horizon_utc", "pitching_side", "pitching_team_id", "opposing_team_id",
    "probable_pitcher_mlb_id", "starter_identity_source", "plan_sha256",
    "plan_source_receipt_sha256", "ledger_manifest_sha256",
    "ledger_terminal_record_sha256", "context_sha256", "context_raw_payload_sha256",
    "plan_received_at_utc", "context_received_at_utc", "workload_assembled_at_utc",
    "workload_observation_cutoff_utc", "workload_max_source_game_date",
    "workload_raw_receipt_manifest_sha256", "workload_feature_artifact_sha256",
    "workload_parser_code_sha256", "workload_source_schema_sha256",
    "workload_feature_code_sha256", "source_kinds", "may_2026_interval_excluded",
    "may_2026_accessed", "actual_postgame_starter_consumed", "fallback_used",
    "starter_removal_pmf", "starter_removal_pmf_sha256",
}
_BULLPEN_KEYS = {
    "status", "transition_source", "starter_removal_pmf_sha256",
    "bullpen_quality_model", "bullpen_identity_receipts",
    "actual_postgame_relievers_consumed", "fallback_used",
    "probability_consumption_authorized",
}
_PROTECTED = {
    "may_2026_accessed": False,
    "actual_postgame_starter_consumed": False,
    "actual_postgame_relievers_consumed": False,
    "post_start_data_consumed": False,
    "historical_or_prospective_backfill_used": False,
    "fabricated_fallback_used": False,
    "batter_and_pitcher_probabilities_combined": False,
    "production_probability_changed": False,
    "betting_authorized": False,
}

_REFERENCE_KEYS = {"role", "relative_path", "sha256"}
_SINGLE_REFERENCE_ROLES = {
    "pr32_protocol", "pr32_source_manifest", "pr32_runtime_release_receipt",
    "pr35_protocol", "pr35_evidence_authority_receipt",
    "pr35_runtime_release_receipt", "pr35_model_authorization_receipt",
    "batter_side_bundle", "batter_candidate_record", "shared_plan",
    "batter_projected_lineup", "batter_active_roster_receipt",
    "batter_history_receipt_manifest", "starter_plan_source_receipt",
    "starter_ledger_manifest", "starter_ledger_terminal_record",
    "starter_context", "starter_context_raw_payload",
    "starter_workload_raw_receipt_manifest", "starter_workload_feature_artifact",
    "starter_workload_parser_code", "starter_workload_source_schema",
    "starter_workload_feature_code",
}
_MULTI_REFERENCE_ROLES = {"batter_stats_transport_receipt"}
_DATA_REFERENCE_ROLES = {
    "batter_side_bundle", "batter_candidate_record", "shared_plan",
    "batter_projected_lineup", "batter_active_roster_receipt",
    "batter_history_receipt_manifest", "batter_stats_transport_receipt",
    "starter_plan_source_receipt", "starter_ledger_manifest",
    "starter_ledger_terminal_record", "starter_context",
    "starter_context_raw_payload", "starter_workload_raw_receipt_manifest",
    "starter_workload_feature_artifact",
}
_JSON_REFERENCE_ROLES = (
    (_SINGLE_REFERENCE_ROLES | _MULTI_REFERENCE_ROLES)
    - {
        "starter_workload_parser_code", "starter_workload_feature_code",
    }
)


class FullGameOpportunityEnvelopeError(ValueError):
    pass


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_value(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_regular_file(root: Path, relative: Any, label: str) -> Path:
    """Resolve a retained file without trusting links in any ancestor."""
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise FullGameOpportunityEnvelopeError(f"{label} path is unsafe")
    pure = Path(relative)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise FullGameOpportunityEnvelopeError(f"{label} path is unsafe")
    absolute_root = root.absolute()
    current = absolute_root
    while True:
        if _is_link_or_reparse(current):
            raise FullGameOpportunityEnvelopeError(f"{label} ancestor is redirected")
        if current.parent == current:
            break
        current = current.parent
    if not absolute_root.is_dir():
        raise FullGameOpportunityEnvelopeError("evidence root is unavailable")
    candidate = absolute_root.joinpath(*pure.parts)
    current = absolute_root
    for part in pure.parts:
        current = current / part
        if _is_link_or_reparse(current):
            raise FullGameOpportunityEnvelopeError(f"{label} path is redirected")
    try:
        resolved_root = absolute_root.resolve(strict=True)
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(resolved_root)
    except (OSError, ValueError) as exc:
        raise FullGameOpportunityEnvelopeError(f"{label} path escapes or is unavailable") from exc
    if not resolved.is_file():
        raise FullGameOpportunityEnvelopeError(f"{label} file is unavailable")
    return resolved


_MAY_PATTERNS = (
    re.compile(r"(?i)(?:^|[^0-9])2026[-_/.]?05(?:[-_/.]?[0-3]?[0-9])?(?:[^0-9]|$)"),
    re.compile(r"(?i)(?:^|[^0-9])05[-_/.][0-3]?[0-9][-_/.]2026(?:[^0-9]|$)"),
    re.compile(r"(?i)(?:^|[^a-z])may(?:\s+|[-_/.])(?:[0-3]?[0-9](?:,|\s|[-_/.])*)?2026(?:[^0-9]|$)"),
)


def _reject_may_2026_anywhere(value: Any, label: str) -> None:
    """Reject May from retained data values and path-like keys, not declarations."""
    if isinstance(value, Mapping):
        for item in value.values():
            _reject_may_2026_anywhere(item, label)
    elif isinstance(value, list):
        for item in value:
            _reject_may_2026_anywhere(item, label)
    elif isinstance(value, str) and any(pattern.search(value) for pattern in _MAY_PATTERNS):
        raise FullGameOpportunityEnvelopeError(f"{label} references sealed May 2026")


def _read_json_bytes(path: Path, label: str) -> tuple[bytes, Any]:
    raw = path.read_bytes()
    try:
        decoded = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FullGameOpportunityEnvelopeError(f"{label} is not valid JSON") from exc
    return raw, decoded


def _self_hash(payload: Mapping[str, Any], field: str, label: str) -> str:
    if field not in payload:
        raise FullGameOpportunityEnvelopeError(f"{label} semantic digest is absent")
    observed = _sha(payload[field], f"{label}.{field}")
    unsigned = dict(payload)
    unsigned.pop(field)
    if observed != sha256_value(unsigned):
        raise FullGameOpportunityEnvelopeError(f"{label} semantic self-hash differs")
    return observed


def _declared_digest(envelope: Mapping[str, Any], section: str, field: str) -> str:
    return _sha(envelope[section][field], f"{section}.{field}")


def _validate_reference_digest_binding(
    role: str,
    *,
    raw_sha256: str,
    payload: Any | None,
    envelope: Mapping[str, Any],
) -> str | None:
    """Bind raw or canonical semantic bytes to the envelope-declared identity."""
    direct = {
        "pr32_protocol": ("release_bindings", "pr32_protocol_sha256"),
        "pr32_source_manifest": ("release_bindings", "pr32_source_manifest_sha256"),
        "pr35_protocol": ("release_bindings", "pr35_protocol_sha256"),
        "pr35_evidence_authority_receipt": ("release_bindings", "pr35_evidence_authority_receipt_sha256"),
        "starter_ledger_manifest": ("starter", "ledger_manifest_sha256"),
        "starter_context_raw_payload": ("starter", "context_raw_payload_sha256"),
        "starter_workload_parser_code": ("starter", "workload_parser_code_sha256"),
        "starter_workload_source_schema": ("starter", "workload_source_schema_sha256"),
        "starter_workload_feature_code": ("starter", "workload_feature_code_sha256"),
    }
    if role in direct:
        section, field = direct[role]
        if raw_sha256 != _declared_digest(envelope, section, field):
            raise FullGameOpportunityEnvelopeError(f"{role} raw-byte digest differs from envelope")
        return raw_sha256
    if payload is None:
        return None
    if role in {
        "batter_active_roster_receipt", "batter_history_receipt_manifest",
        "batter_stats_transport_receipt", "starter_workload_raw_receipt_manifest",
    }:
        semantic = sha256_value(payload)
        if role == "batter_active_roster_receipt":
            expected = _declared_digest(envelope, "batter", "active_roster_receipt_sha256")
        elif role == "batter_history_receipt_manifest":
            expected = _declared_digest(envelope, "batter", "history_receipt_manifest_sha256")
        elif role == "starter_workload_raw_receipt_manifest":
            expected = _declared_digest(envelope, "starter", "workload_raw_receipt_manifest_sha256")
        else:
            return semantic
        if semantic != expected:
            raise FullGameOpportunityEnvelopeError(f"{role} canonical digest differs from envelope")
        return semantic
    if not isinstance(payload, Mapping):
        raise FullGameOpportunityEnvelopeError(f"{role} must be a JSON object")
    self_fields = {
        "pr32_runtime_release_receipt": ("release_receipt_sha256", "release_bindings", "pr32_runtime_release_receipt_sha256"),
        "pr35_runtime_release_receipt": ("release_sha256", "release_bindings", "pr35_runtime_release_receipt_sha256"),
        "pr35_model_authorization_receipt": ("authorization_sha256", "release_bindings", "pr35_model_authorization_receipt_sha256"),
        "batter_side_bundle": ("side_bundle_sha256", "batter", "side_bundle_sha256"),
        "batter_candidate_record": ("candidate_record_sha256", "batter", "candidate_record_sha256"),
        "shared_plan": ("plan_sha256", "batter", "plan_sha256"),
        "batter_projected_lineup": ("projection_content_sha256", "batter", "projected_lineup_content_sha256"),
        "starter_plan_source_receipt": ("receipt_sha256", "starter", "plan_source_receipt_sha256"),
        "starter_ledger_terminal_record": ("chain_sha256", "starter", "ledger_terminal_record_sha256"),
        "starter_context": ("context_sha256", "starter", "context_sha256"),
        "starter_workload_feature_artifact": ("workload_sha256", "starter", "workload_feature_artifact_sha256"),
    }
    if role in self_fields:
        self_field, section, field = self_fields[role]
        semantic = _self_hash(payload, self_field, role)
        if semantic != _declared_digest(envelope, section, field):
            raise FullGameOpportunityEnvelopeError(f"{role} semantic digest differs from envelope")
        if role == "shared_plan" and semantic != _declared_digest(envelope, "starter", "plan_sha256"):
            raise FullGameOpportunityEnvelopeError("shared plan digest differs across batter and starter")
        return semantic
    return None


def _semantic_precheck(role: str, payload: Mapping[str, Any], envelope: Mapping[str, Any]) -> None:
    """Check only semantics whose shipped formats are locally defined.

    This intentionally is not presented as complete replay.  The PR32 retained
    raw-evidence bundle format is absent from this source tree, so the caller
    can never turn these checks into probability authority.
    """
    target = envelope["target"]
    batter = envelope["batter"]
    starter = envelope["starter"]
    common = {
        "official_game_date": target["official_game_date"],
        "mlb_game_pk": target["mlb_game_pk"],
    }
    for field, expected in common.items():
        if field in payload and payload[field] != expected:
            raise FullGameOpportunityEnvelopeError(f"{role} semantic target identity differs")
    if "target_horizon_utc" in payload and payload["target_horizon_utc"] != target["target_horizon_utc"]:
        raise FullGameOpportunityEnvelopeError(f"{role} semantic chronology differs")
    if role == "batter_side_bundle":
        expected = {
            "schema_version": "shared-pa-projected-opportunity-side-bundle-v2",
            "candidate_id": PR32_CANDIDATE_ID,
            "source_release_commit": PR32_COMMIT,
            "plan_sha256": batter["plan_sha256"],
            "target_id": batter["target_id"],
            "team_id": batter["team_id"],
            "side": batter["side"],
            "research_only": True,
            "betting_authorized": False,
        }
        if any(payload.get(key) != expected_value for key, expected_value in expected.items()):
            raise FullGameOpportunityEnvelopeError("batter side bundle semantic identity differs")
    elif role == "batter_candidate_record":
        expected = {
            "schema_version": "shared-pa-projected-opportunity-player-v2",
            "candidate_id": PR32_CANDIDATE_ID,
            "source_release_commit": PR32_COMMIT,
            "plan_sha256": batter["plan_sha256"],
            "target_id": batter["target_id"],
            "player_id": batter["player_id"],
            "team_id": batter["team_id"],
            "side": batter["side"],
            "stats_source": "official_mlb_statsapi_dated_game_log_segmented_v2",
            "research_only": True,
            "betting_authorized": False,
        }
        if any(payload.get(key) != expected_value for key, expected_value in expected.items()):
            raise FullGameOpportunityEnvelopeError("batter candidate semantic identity differs")
    elif role == "shared_plan":
        if (
            payload.get("schema_version") != "shadow-capture-plan-v1"
            or payload.get("official_game_date") != target["official_game_date"]
            or payload.get("plan_sha256") != batter["plan_sha256"]
            or payload.get("entry_hours") != 4
        ):
            raise FullGameOpportunityEnvelopeError("shared plan semantic identity differs")
    elif role == "starter_plan_source_receipt":
        if (
            payload.get("schema_version") != "aws-pitcher-receipt-plan-receipt-v1"
            or payload.get("official_game_date") != target["official_game_date"]
            or payload.get("plan_sha256") != starter["plan_sha256"]
            or payload.get("source_name") != "mlb_statsapi_schedule"
            or payload.get("research_only") is not True
            or payload.get("betting_authorized") is not False
            or payload.get("model_or_market_accessed") is not False
        ):
            raise FullGameOpportunityEnvelopeError("starter plan receipt semantic identity differs")
    elif role == "pr32_runtime_release_receipt":
        expected = {
            "schema_version": "shared-pa-projected-opportunity-runtime-release-v2",
            "candidate_id": PR32_CANDIDATE_ID,
            "source_commit": PR32_COMMIT,
            "source_manifest_sha256": envelope["release_bindings"]["pr32_source_manifest_sha256"],
            "candidate_protocol_sha256": envelope["release_bindings"]["pr32_protocol_sha256"],
            "research_only": True,
            "betting_authorized": False,
        }
        if any(payload.get(key) != expected_value for key, expected_value in expected.items()):
            raise FullGameOpportunityEnvelopeError("PR32 runtime receipt semantic release identity differs")
    elif role == "pr35_runtime_release_receipt":
        expected = {
            "schema_version": "pitcher-joint-opportunity-runtime-release-v1",
            "candidate_id": PR35_CANDIDATE_ID,
            "protocol_sha256": PR35_PROTOCOL_SHA256,
            "research_only": True,
            "betting_authorized": False,
        }
        if any(payload.get(key) != expected_value for key, expected_value in expected.items()):
            raise FullGameOpportunityEnvelopeError("PR35 runtime receipt semantic release identity differs")
    elif role == "pr35_model_authorization_receipt":
        expected = {
            "schema_version": "pitcher-joint-opportunity-model-authorization-v1",
            "candidate_id": PR35_CANDIDATE_ID,
            "protocol_sha256": PR35_PROTOCOL_SHA256,
            "research_only": True,
            "betting_authorized": False,
        }
        if any(payload.get(key) != expected_value for key, expected_value in expected.items()):
            raise FullGameOpportunityEnvelopeError("PR35 model authorization semantic identity differs")
    elif role == "pr35_evidence_authority_receipt":
        if (
            payload.get("schema_version") != "pitcher-joint-opportunity-evidence-authority-v1"
            or payload.get("candidate_id") != PR35_CANDIDATE_ID
            or payload.get("status") != "UNBOUND_NO_APPROVED_ARCHIVE_ERA"
            or payload.get("authorized_evidence_authority_receipt_sha256") is not None
        ):
            raise FullGameOpportunityEnvelopeError("PR35 evidence authority is not the exact unbound contract")
    elif role == "starter_context":
        expected = {
            "schema_version": "forward-pitcher-context-v2",
            "target_id": starter["target_id"],
            "plan_sha256": starter["plan_sha256"],
            "source_name": "mlb_statsapi_schedule",
            "mlb_game_pk": target["mlb_game_pk"],
            "official_game_date": target["official_game_date"],
            "official_start_time_utc": target["official_start_utc"],
        }
        if any(payload.get(key) != expected_value for key, expected_value in expected.items()):
            raise FullGameOpportunityEnvelopeError("starter context semantic target/source identity differs")
    elif role == "starter_ledger_terminal_record":
        expected = {
            "target_id": starter["target_id"],
            "plan_sha256": starter["plan_sha256"],
            "terminal_state": "captured",
            "context_sha256": starter["context_sha256"],
            "raw_payload_sha256": starter["context_raw_payload_sha256"],
        }
        if any(payload.get(key) != expected_value for key, expected_value in expected.items()):
            raise FullGameOpportunityEnvelopeError(
                "starter ledger terminal semantic identity/lineage differs"
            )
    elif role == "starter_workload_feature_artifact":
        lineage = payload.get("lineage")
        if (
            payload.get("schema_version") != "pitcher-pit-workload-history-v1"
            or payload.get("source_kind") != "official_mlb_pitching_game_log_point_in_time"
            or payload.get("target_id") != starter["target_id"]
            or payload.get("official_game_date") != target["official_game_date"]
            or payload.get("mlb_game_pk") != target["mlb_game_pk"]
            or payload.get("pitcher_id") != starter["probable_pitcher_mlb_id"]
            or payload.get("pitching_team_id") != starter["pitching_team_id"]
            or payload.get("target_horizon_utc") != target["target_horizon_utc"]
            or not isinstance(lineage, Mapping)
            or lineage.get("raw_receipt_manifest_sha256") != starter["workload_raw_receipt_manifest_sha256"]
            or lineage.get("parser_code_sha256") != starter["workload_parser_code_sha256"]
            or lineage.get("source_schema_sha256") != starter["workload_source_schema_sha256"]
            or lineage.get("feature_code_sha256") != starter["workload_feature_code_sha256"]
            or lineage.get("protocol_sha256") != PR35_PROTOCOL_SHA256
        ):
            raise FullGameOpportunityEnvelopeError("starter workload semantic identity/lineage differs")
    for timestamp_field in (
        "received_at_utc", "request_sent_at_utc", "captured_at_utc",
        "assembled_at_utc", "prediction_generated_at_utc",
    ):
        if timestamp_field in payload and _utc(payload[timestamp_field], f"{role}.{timestamp_field}") > _utc(target["target_horizon_utc"], "target_horizon_utc"):
            raise FullGameOpportunityEnvelopeError(f"{role} semantic chronology differs")


def _validate_referenced_evidence(
    envelope: Mapping[str, Any], *, evidence_root: Path | None
) -> None:
    if evidence_root is None:
        raise FullGameOpportunityEnvelopeError(
            "hash-only evidence is forbidden; retained evidence root is required"
        )
    references = envelope["evidence_references"]
    if not isinstance(references, list) or not references:
        raise FullGameOpportunityEnvelopeError("retained evidence references are required")
    by_role: dict[str, list[Mapping[str, Any]]] = {}
    stats_semantic_digests: list[str] = []
    seen_paths: set[str] = set()
    for index, value in enumerate(references):
        reference = _exact(value, _REFERENCE_KEYS, f"evidence_references[{index}]")
        role = reference["role"]
        if role not in _SINGLE_REFERENCE_ROLES | _MULTI_REFERENCE_ROLES:
            raise FullGameOpportunityEnvelopeError("evidence role is not predeclared")
        path_text = reference["relative_path"]
        if path_text in seen_paths:
            raise FullGameOpportunityEnvelopeError("evidence path is duplicated")
        seen_paths.add(path_text)
        if role in _DATA_REFERENCE_ROLES:
            _reject_may_2026_anywhere(path_text, f"{role} path")
        _sha(reference["sha256"], f"{role}.sha256")
        path = _safe_regular_file(Path(evidence_root), path_text, role)
        raw_sha256 = sha256_file(path)
        if raw_sha256 != reference["sha256"]:
            raise FullGameOpportunityEnvelopeError(f"{role} retained bytes differ")
        by_role.setdefault(str(role), []).append(reference)
        payload: Any | None = None
        if role in _JSON_REFERENCE_ROLES:
            _, payload = _read_json_bytes(path, str(role))
        if role in _DATA_REFERENCE_ROLES and payload is not None:
            _reject_may_2026_anywhere(payload, str(role))
        if isinstance(payload, Mapping):
            _semantic_precheck(str(role), payload, envelope)
        semantic = _validate_reference_digest_binding(
            str(role), raw_sha256=raw_sha256, payload=payload, envelope=envelope
        )
        if role == "batter_stats_transport_receipt":
            assert semantic is not None
            stats_semantic_digests.append(semantic)
    for role in _SINGLE_REFERENCE_ROLES:
        rows = by_role.get(role, [])
        if len(rows) != 1:
            raise FullGameOpportunityEnvelopeError(f"{role} retained file cardinality differs")
    stats_rows = by_role.get("batter_stats_transport_receipt", [])
    if len(stats_rows) != len(envelope["batter"]["stats_transport_receipt_sha256s"]):
        raise FullGameOpportunityEnvelopeError(
            "batter_stats_transport_receipt retained file cardinality differs"
        )
    if stats_semantic_digests != envelope["batter"]["stats_transport_receipt_sha256s"]:
        raise FullGameOpportunityEnvelopeError(
            "batter stats retained receipt digest set/order differs from envelope"
        )
    # PR32's raw schedule/roster/history/stats replay objects are not delivered
    # in this checkout.  A partial semantic check must never authorize PMFs.
    raise FullGameOpportunityEnvelopeError(
        "external PR32 retained-evidence replay contract is unavailable; authority remains unbound"
    )


def _exact(value: Any, keys: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise FullGameOpportunityEnvelopeError(f"{label} surface changed")
    return value


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise FullGameOpportunityEnvelopeError(f"{label} is not canonical SHA-256")
    return value


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise FullGameOpportunityEnvelopeError(f"{label} must be a positive integer")
    return value


def _utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise FullGameOpportunityEnvelopeError(f"{label} must be canonical UTC")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise FullGameOpportunityEnvelopeError(f"{label} is invalid") from exc
    if parsed.tzinfo != timezone.utc or parsed.isoformat().replace("+00:00", "Z") != value:
        raise FullGameOpportunityEnvelopeError(f"{label} must be canonical UTC")
    return parsed


def _date(value: Any, label: str) -> date:
    if not isinstance(value, str):
        raise FullGameOpportunityEnvelopeError(f"{label} must be canonical date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise FullGameOpportunityEnvelopeError(f"{label} is invalid") from exc
    if parsed.isoformat() != value:
        raise FullGameOpportunityEnvelopeError(f"{label} must be canonical date")
    return parsed


def _side(value: Any, label: str) -> str:
    if value not in {"home", "away"}:
        raise FullGameOpportunityEnvelopeError(f"{label} must be home or away")
    return str(value)


def _pmf(rows: Any, *, label: str, state_keys: tuple[str, ...]) -> list[dict[str, Any]]:
    if not isinstance(rows, list) or not rows:
        raise FullGameOpportunityEnvelopeError(f"{label} must be a nonempty list")
    expected_keys = set(state_keys) | {"probability"}
    seen: set[tuple[int, ...]] = set()
    output: list[dict[str, Any]] = []
    total = 0.0
    for index, row in enumerate(rows):
        _exact(row, expected_keys, f"{label}[{index}]")
        state: list[int] = []
        for key in state_keys:
            value = row[key]
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise FullGameOpportunityEnvelopeError(f"{label}.{key} is invalid")
            state.append(value)
        key_tuple = tuple(state)
        if key_tuple in seen:
            raise FullGameOpportunityEnvelopeError(f"{label} has duplicate state identity")
        seen.add(key_tuple)
        probability = row["probability"]
        if isinstance(probability, bool) or not isinstance(probability, (int, float)):
            raise FullGameOpportunityEnvelopeError(f"{label} probability is invalid")
        probability = float(probability)
        if not math.isfinite(probability) or probability < 0.0 or probability > 1.0:
            raise FullGameOpportunityEnvelopeError(f"{label} probability is out of range")
        total += probability
        output.append({**{name: state[position] for position, name in enumerate(state_keys)}, "probability": probability})
    if abs(total - 1.0) > 1e-12:
        raise FullGameOpportunityEnvelopeError(f"{label} probability mass is not one; no normalization allowed")
    if output != sorted(output, key=lambda row: tuple(row[name] for name in state_keys)):
        raise FullGameOpportunityEnvelopeError(f"{label} states must be uniquely sorted")
    return output


def validate_cross_envelope(
    value: Mapping[str, Any], *, evidence_root: Path | None = None
) -> dict[str, Any]:
    """Fail-closed audit boundary; this unbound release never returns PMFs."""
    top = _exact(value, _TOP_KEYS, "cross envelope")
    if (
        top["schema_version"] != SCHEMA_VERSION
        or top["research_only"] is not True
        or top["betting_authorized"] is not False
        or top["production_probability_consumption_authorized"] is not False
        or top["authority_state"] != UNBOUND
    ):
        raise FullGameOpportunityEnvelopeError("cross envelope authority boundary changed")

    target = _exact(top["target"], _TARGET_KEYS, "target")
    # This precedes date/time parsing so supported aliases cannot degrade into a
    # generic terminal parse error instead of the sealed-May rejection.
    _reject_may_2026_anywhere(target, "target")
    _reject_may_2026_anywhere(top["batter"], "batter envelope")
    _reject_may_2026_anywhere(top["starter"], "starter envelope")
    game_date = _date(target["official_game_date"], "official_game_date")
    if game_date.year == 2026 and game_date.month == 5:
        raise FullGameOpportunityEnvelopeError("May 2026 is sealed")
    game_pk = _positive_int(target["mlb_game_pk"], "mlb_game_pk")
    start = _utc(target["official_start_utc"], "official_start_utc")
    horizon = _utc(target["target_horizon_utc"], "target_horizon_utc")
    if horizon != start - timedelta(hours=4):
        raise FullGameOpportunityEnvelopeError("target horizon is not exact T-4")
    batter_side = _side(target["batter_side"], "batter_side")
    pitching_side = _side(target["pitching_side"], "pitching_side")
    if batter_side == pitching_side:
        raise FullGameOpportunityEnvelopeError("batter and pitcher sides must oppose")
    batting_team = _positive_int(target["batting_team_id"], "batting_team_id")
    pitching_team = _positive_int(target["pitching_team_id"], "pitching_team_id")
    if batting_team == pitching_team:
        raise FullGameOpportunityEnvelopeError("batting and pitching teams must differ")
    if target["batter_opponent_team_id"] != pitching_team or target["pitcher_opponent_team_id"] != batting_team:
        raise FullGameOpportunityEnvelopeError("cross-team opponent identity differs")

    releases = _exact(top["release_bindings"], _RELEASE_KEYS, "release bindings")
    expected_release_values = {
        "pr32_source_commit": PR32_COMMIT,
        "pr32_candidate_id": PR32_CANDIDATE_ID,
        "pr33_source_commit": PR33_COMMIT,
        "pr35_source_commit": PR35_COMMIT,
        "pr35_candidate_id": PR35_CANDIDATE_ID,
        "pr35_protocol_sha256": PR35_PROTOCOL_SHA256,
        "cross_envelope_authority_receipt_sha256": None,
    }
    if any(releases[key] != expected for key, expected in expected_release_values.items()):
        raise FullGameOpportunityEnvelopeError("exact release identity differs")
    for key in _RELEASE_KEYS - set(expected_release_values):
        _sha(releases[key], f"release_bindings.{key}")

    batter = _exact(top["batter"], _BATTER_KEYS, "batter bundle")
    starter = _exact(top["starter"], _STARTER_KEYS, "starter bundle")
    shared_batter = {
        "official_game_date": game_date.isoformat(), "mlb_game_pk": game_pk,
        "official_start_utc": target["official_start_utc"],
        "target_horizon_utc": target["target_horizon_utc"],
        "side": batter_side, "team_id": batting_team,
    }
    if any(batter[key] != expected for key, expected in shared_batter.items()):
        raise FullGameOpportunityEnvelopeError("batter bundle target identity differs")
    if batter["target_id"] != f"{game_pk}:{batter_side}:T-4":
        raise FullGameOpportunityEnvelopeError("batter target identity is not canonical")
    shared_starter = {
        "official_game_date": game_date.isoformat(), "mlb_game_pk": game_pk,
        "official_start_utc": target["official_start_utc"],
        "target_horizon_utc": target["target_horizon_utc"],
        "pitching_side": pitching_side, "pitching_team_id": pitching_team,
        "opposing_team_id": batting_team,
    }
    if any(starter[key] != expected for key, expected in shared_starter.items()):
        raise FullGameOpportunityEnvelopeError("starter bundle target identity differs")
    if starter["target_id"] != f"{game_pk}:{pitching_side}:T-4":
        raise FullGameOpportunityEnvelopeError("starter target identity is not canonical")
    if batter["plan_sha256"] != starter["plan_sha256"]:
        raise FullGameOpportunityEnvelopeError("batter and starter plans differ")

    for key in (
        "side_bundle_sha256", "candidate_record_sha256", "plan_sha256",
        "projected_lineup_content_sha256", "active_roster_receipt_sha256",
        "history_receipt_manifest_sha256", "pa_pmf_sha256",
    ):
        _sha(batter[key], f"batter.{key}")
    if not isinstance(batter["stats_transport_receipt_sha256s"], list) or not batter["stats_transport_receipt_sha256s"]:
        raise FullGameOpportunityEnvelopeError("batter stats receipt manifest is missing")
    if len(set(batter["stats_transport_receipt_sha256s"])) != len(batter["stats_transport_receipt_sha256s"]):
        raise FullGameOpportunityEnvelopeError("batter stats receipt identity is duplicated")
    for value_sha in batter["stats_transport_receipt_sha256s"]:
        _sha(value_sha, "batter stats receipt")
    expected_batter_sources = {
        "schedule": "official_mlb_schedule_t4",
        "active_roster": "official_mlb_active_roster_t4",
        "lineup_history": "official_mlb_final_lineups_strictly_prior",
        "batter_stats": "official_mlb_statsapi_dated_game_log_segmented_v2",
    }
    if batter["source_kinds"] != expected_batter_sources:
        raise FullGameOpportunityEnvelopeError("batter source identity changed")
    if batter["actual_target_lineup_consumed"] is not False or batter["fallback_used"] is not False:
        raise FullGameOpportunityEnvelopeError("actual target lineup or batter fallback is forbidden")
    generated = _utc(batter["prediction_generated_at_utc"], "prediction_generated_at_utc")
    if generated > horizon or generated >= start:
        raise FullGameOpportunityEnvelopeError("batter prediction used post-horizon or post-start data")
    _positive_int(batter["player_id"], "batter.player_id")
    pa_pmf = _pmf(batter["pa_pmf"], label="batter.pa_pmf", state_keys=("pa",))
    if sha256_value(pa_pmf) != batter["pa_pmf_sha256"]:
        raise FullGameOpportunityEnvelopeError("batter PA PMF hash differs")

    for key in (
        "plan_sha256", "plan_source_receipt_sha256", "ledger_manifest_sha256",
        "ledger_terminal_record_sha256", "context_sha256", "context_raw_payload_sha256",
        "workload_raw_receipt_manifest_sha256", "workload_feature_artifact_sha256",
        "workload_parser_code_sha256", "workload_source_schema_sha256",
        "workload_feature_code_sha256", "starter_removal_pmf_sha256",
    ):
        _sha(starter[key], f"starter.{key}")
    _positive_int(starter["probable_pitcher_mlb_id"], "probable_pitcher_mlb_id")
    if starter["starter_identity_source"] != "receipt_proven_probable_starter_t4":
        raise FullGameOpportunityEnvelopeError("starter identity is not receipt-proven at T-4")
    expected_starter_sources = {
        "plan": "mlb_statsapi_schedule",
        "context": "forward_pitcher_context_v2_t4",
        "workload": "official_mlb_pitching_game_feed_strictly_prior",
    }
    if starter["source_kinds"] != expected_starter_sources:
        raise FullGameOpportunityEnvelopeError("starter source identity changed")
    if (
        starter["actual_postgame_starter_consumed"] is not False
        or starter["fallback_used"] is not False
        or starter["may_2026_interval_excluded"] is not True
        or starter["may_2026_accessed"] is not False
    ):
        raise FullGameOpportunityEnvelopeError("starter fallback, actual identity, or May boundary changed")
    for label in (
        "plan_received_at_utc", "context_received_at_utc", "workload_assembled_at_utc",
        "workload_observation_cutoff_utc",
    ):
        observed = _utc(starter[label], label)
        if observed > horizon or observed >= start:
            raise FullGameOpportunityEnvelopeError("starter input postdates T-4 or game start")
    max_source_date = _date(starter["workload_max_source_game_date"], "workload_max_source_game_date")
    if max_source_date.year == 2026 and max_source_date.month == 5:
        raise FullGameOpportunityEnvelopeError("workload history references sealed May 2026")
    if max_source_date >= game_date:
        raise FullGameOpportunityEnvelopeError("workload history is not strictly prior")
    starter_pmf = _pmf(
        starter["starter_removal_pmf"],
        label="starter.starter_removal_pmf",
        state_keys=("batters_faced", "outs_recorded"),
    )
    for row in starter_pmf:
        if row["outs_recorded"] > 3 * row["batters_faced"]:
            raise FullGameOpportunityEnvelopeError("starter removal state is structurally impossible")
    if sha256_value(starter_pmf) != starter["starter_removal_pmf_sha256"]:
        raise FullGameOpportunityEnvelopeError("starter removal PMF hash differs")

    bullpen = _exact(top["bullpen_transition_boundary"], _BULLPEN_KEYS, "bullpen transition boundary")
    if bullpen != {
        "status": "TRANSITION_ONLY_QUALITY_NOT_MODELED",
        "transition_source": "starter_removal_pmf",
        "starter_removal_pmf_sha256": starter["starter_removal_pmf_sha256"],
        "bullpen_quality_model": None,
        "bullpen_identity_receipts": [],
        "actual_postgame_relievers_consumed": False,
        "fallback_used": False,
        "probability_consumption_authorized": False,
    }:
        raise FullGameOpportunityEnvelopeError("bullpen transition-only boundary changed")
    if top["protected_boundaries"] != _PROTECTED:
        raise FullGameOpportunityEnvelopeError("protected boundaries changed")
    unsigned = {key: top[key] for key in top if key != "envelope_sha256"}
    if top["envelope_sha256"] != sha256_value(unsigned):
        raise FullGameOpportunityEnvelopeError("cross-envelope hash differs")
    _validate_referenced_evidence(top, evidence_root=evidence_root)
    raise FullGameOpportunityEnvelopeError("unreachable unbound evidence state")


def _is_link_or_reparse(path: Path) -> bool:
    try:
        mode = path.lstat().st_mode
        attrs = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(mode) or bool(attrs & reparse)


def load_authority(root: Path) -> dict[str, Any]:
    path = root / AUTHORITY_RELATIVE_PATH
    current = path.absolute()
    while True:
        if _is_link_or_reparse(current):
            raise FullGameOpportunityEnvelopeError("authority path is redirected")
        if current.parent == current:
            break
        current = current.parent
    if not path.is_file() or sha256_file(path) != EXPECTED_AUTHORITY_SHA256:
        raise FullGameOpportunityEnvelopeError("authority bytes changed")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FullGameOpportunityEnvelopeError("authority is unreadable") from exc
    required = {
        "schema_version", "status", "authorized_authority_receipt_sha256",
        "public_probability_consumption_authorized", "research_only", "betting_authorized",
        "may_2026_access_allowed", "historical_or_prospective_backfill_allowed",
        "actual_postgame_starter_allowed", "actual_postgame_reliever_allowed",
        "probability_combination_allowed",
    }
    _exact(value, required, "authority")
    if value != {
        "schema_version": AUTHORITY_SCHEMA_VERSION,
        "status": UNBOUND,
        "authorized_authority_receipt_sha256": None,
        "public_probability_consumption_authorized": False,
        "research_only": True,
        "betting_authorized": False,
        "may_2026_access_allowed": False,
        "historical_or_prospective_backfill_allowed": False,
        "actual_postgame_starter_allowed": False,
        "actual_postgame_reliever_allowed": False,
        "probability_combination_allowed": False,
    }:
        raise FullGameOpportunityEnvelopeError("authority is not the exact inert contract")
    return value


def consume_cross_envelope(*, root: Path, envelope: Mapping[str, Any]) -> dict[str, Any]:
    """Public boundary: the delivered unbound release always abstains."""
    authority = load_authority(root)
    if authority["status"] == UNBOUND:
        return {
            "schema_version": OUTPUT_SCHEMA_VERSION,
            "terminal_state": "abstained",
            "reason_code": "EXTERNAL_AUTHORITY_UNBOUND",
            "research_only": True,
            "betting_authorized": False,
            "production_probability_consumption_authorized": False,
            "batter_pa_pmf": None,
            "starter_removal_pmf": None,
            "bullpen_quality_model": None,
            "probabilities_combined": False,
        }
    raise FullGameOpportunityEnvelopeError("bound authority requires a new reviewed implementation")
