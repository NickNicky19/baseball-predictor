"""Fail-closed projected-lineup evidence contract.

This module deliberately validates *a model-produced joint lineup distribution*;
it does not invent one.  A future internal fitted projector may use it only after
its source adapter, fitted artifact, and chronological evaluation are separately
bound.  Nothing here feeds the frozen production predictor.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


class ProjectedLineupContractError(ValueError):
    """A projected-lineup record is late, incomplete, or not reproducible."""


SCHEMA_VERSION = "projected-lineup-projection-v1"
CONTRACT_SCHEMA_VERSION = "projected-lineup-contract-v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TERMINAL_STATES = {"projected_complete", "projected_unavailable", "projected_quarantined"}


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def sha256_value(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _sha(value: Any, label: str) -> str:
    text = str(value).strip().lower()
    if not _SHA256.fullmatch(text):
        raise ProjectedLineupContractError(f"{label} must be a lowercase SHA-256 digest")
    return text


def _utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise ProjectedLineupContractError(f"{label} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProjectedLineupContractError(f"{label} is not an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProjectedLineupContractError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ProjectedLineupContractError(f"{label} must be a positive integer")
    return value


def load_contract(path: str | Path) -> dict[str, Any]:
    """Load the fixed research boundary; no provider is activated here."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectedLineupContractError("projected-lineup contract is unreadable") from exc
    if not isinstance(raw, Mapping) or raw.get("schema_version") != CONTRACT_SCHEMA_VERSION:
        raise ProjectedLineupContractError("projected-lineup contract schema changed")
    if raw.get("status") != "RESEARCH_ONLY_NOT_ACTIVE_IN_PRODUCTION" or raw.get("decision_horizon") != "T-4h":
        raise ProjectedLineupContractError("research-only T-minus-4 boundary changed")
    if raw.get("permitted_source_kinds") != ["internal_historical_lineup_feature_store"]:
        raise ProjectedLineupContractError("an unapproved lineup source was enabled")
    if raw.get("required_input_receipts") != ["active_roster", "historical_lineup_features"]:
        raise ProjectedLineupContractError("required lineup input receipts changed")
    forbidden = raw.get("forbidden")
    if not isinstance(forbidden, Mapping) or any(value is not True for value in forbidden.values()):
        raise ProjectedLineupContractError("a protected projected-lineup boundary was weakened")
    return dict(raw)


def _validate_receipts(value: Any, horizon: datetime, contract: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    if not isinstance(value, Mapping):
        raise ProjectedLineupContractError("input_receipts must be an object")
    required = list(contract["required_input_receipts"])
    optional = list(contract["optional_input_receipts"])
    if set(value) - set(required) - set(optional) or any(key not in value for key in required):
        raise ProjectedLineupContractError("input receipt keys differ from the contract")
    parsed: dict[str, Mapping[str, Any]] = {}
    for name, raw in value.items():
        if not isinstance(raw, Mapping) or set(raw) != {"source_kind", "source_record_id", "received_at_utc", "payload_sha256", "input_surface_sha256"}:
            raise ProjectedLineupContractError(f"{name} receipt schema is invalid")
        if raw["source_kind"] not in contract["permitted_source_kinds"]:
            raise ProjectedLineupContractError(f"{name} receipt uses an unapproved source")
        if not isinstance(raw["source_record_id"], str) or not raw["source_record_id"].strip():
            raise ProjectedLineupContractError(f"{name} receipt lacks a source record identity")
        if _utc(raw["received_at_utc"], f"{name}.received_at_utc") > horizon:
            raise ProjectedLineupContractError(f"{name} receipt arrived after T-minus-4")
        _sha(raw["payload_sha256"], f"{name}.payload_sha256")
        _sha(raw["input_surface_sha256"], f"{name}.input_surface_sha256")
        parsed[name] = raw
    return parsed


def _validate_scenarios(value: Any, roster_ids: set[int]) -> tuple[list[dict[str, Any]], dict[int, float], dict[tuple[int, int], float]]:
    if not isinstance(value, list) or not value:
        raise ProjectedLineupContractError("scenarios must be a non-empty list")
    scenarios: list[dict[str, Any]] = []
    start_marginal: dict[int, float] = {}
    slot_marginal: dict[tuple[int, int], float] = {}
    total = 0.0
    seen_scenarios: set[str] = set()
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping) or set(raw) != {"probability", "lineup"}:
            raise ProjectedLineupContractError(f"scenario {index} schema is invalid")
        probability = raw["probability"]
        if isinstance(probability, bool) or not isinstance(probability, (int, float)) or not math.isfinite(float(probability)) or not 0.0 < float(probability) <= 1.0:
            raise ProjectedLineupContractError(f"scenario {index} probability is invalid")
        lineup = raw["lineup"]
        if not isinstance(lineup, list) or len(lineup) != 9:
            raise ProjectedLineupContractError(f"scenario {index} must contain exactly nine lineup entries")
        ids: list[int] = []
        slots: list[int] = []
        normalized: list[dict[str, int]] = []
        for entry in lineup:
            if not isinstance(entry, Mapping) or set(entry) != {"player_id", "slot"}:
                raise ProjectedLineupContractError(f"scenario {index} lineup entry schema is invalid")
            player_id = _positive_int(entry["player_id"], "scenario player_id")
            slot = _positive_int(entry["slot"], "scenario slot")
            if slot > 9:
                raise ProjectedLineupContractError("scenario slot must be between one and nine")
            if player_id not in roster_ids:
                raise ProjectedLineupContractError("scenario contains a player outside the receipted active roster")
            ids.append(player_id)
            slots.append(slot)
            normalized.append({"player_id": player_id, "slot": slot})
        if len(set(ids)) != 9 or set(slots) != set(range(1, 10)):
            raise ProjectedLineupContractError(f"scenario {index} violates unique-player or unique-slot constraints")
        signature = sha256_value(normalized)
        if signature in seen_scenarios:
            raise ProjectedLineupContractError("duplicate lineup scenarios must be combined before validation")
        seen_scenarios.add(signature)
        p = float(probability)
        total += p
        for entry in normalized:
            start_marginal[entry["player_id"]] = start_marginal.get(entry["player_id"], 0.0) + p
            key = (entry["player_id"], entry["slot"])
            slot_marginal[key] = slot_marginal.get(key, 0.0) + p
        scenarios.append({"probability": p, "lineup": normalized})
    if not math.isclose(total, 1.0, rel_tol=0.0, abs_tol=1e-12):
        raise ProjectedLineupContractError("scenario probability mass must sum to one")
    return scenarios, start_marginal, slot_marginal


def validate_projection(record: Mapping[str, Any], contract: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and derive marginals. Invalid inputs raise; callers must quarantine."""
    required = {
        "schema_version", "terminal_state", "research_only", "betting_authorized",
        "official_game_date", "mlb_game_pk", "team_id", "target_horizon_utc",
        "projection_receipt_utc", "input_receipts", "active_roster_player_ids",
        "fitted_candidate_id", "fitted_artifact_sha256", "model_code_sha256", "scenarios",
        "projection_content_sha256",
    }
    if set(record) != required or record.get("schema_version") != SCHEMA_VERSION:
        raise ProjectedLineupContractError("projected-lineup projection schema changed")
    if record.get("terminal_state") != "projected_complete" or record.get("research_only") is not True or record.get("betting_authorized") is not False:
        raise ProjectedLineupContractError("projection is not explicitly research-only complete evidence")
    game_date = record.get("official_game_date")
    if not isinstance(game_date, str) or game_date.startswith("2026-05-"):
        raise ProjectedLineupContractError("May 2026 is sealed from projected-lineup records")
    _positive_int(record.get("mlb_game_pk"), "mlb_game_pk")
    _positive_int(record.get("team_id"), "team_id")
    horizon = _utc(record.get("target_horizon_utc"), "target_horizon_utc")
    if _utc(record.get("projection_receipt_utc"), "projection_receipt_utc") > horizon:
        raise ProjectedLineupContractError("projection receipt arrived after T-minus-4")
    receipts = _validate_receipts(record.get("input_receipts"), horizon, contract)
    roster = record.get("active_roster_player_ids")
    if not isinstance(roster, list) or len(roster) < 9:
        raise ProjectedLineupContractError("active roster must contain at least nine players")
    roster_ids = {_positive_int(value, "active_roster_player_id") for value in roster}
    if len(roster_ids) != len(roster):
        raise ProjectedLineupContractError("active roster contains duplicate player IDs")
    if not isinstance(record.get("fitted_candidate_id"), str) or not record["fitted_candidate_id"].strip():
        raise ProjectedLineupContractError("fitted candidate identity is required")
    _sha(record.get("fitted_artifact_sha256"), "fitted_artifact_sha256")
    _sha(record.get("model_code_sha256"), "model_code_sha256")
    scenarios, starts, slots = _validate_scenarios(record.get("scenarios"), roster_ids)
    unsigned = dict(record)
    supplied_hash = unsigned.pop("projection_content_sha256")
    if supplied_hash != sha256_value(unsigned):
        raise ProjectedLineupContractError("projection content hash does not bind its consumed inputs and scenarios")
    return {
        "input_receipt_hash": sha256_value(receipts),
        "scenario_hash": sha256_value(scenarios),
        "start_probability": {str(key): value for key, value in sorted(starts.items())},
        "slot_probability": {
            f"{player_id}:{slot}": value for (player_id, slot), value in sorted(slots.items())
        },
        "projection_content_sha256": supplied_hash,
    }


def quarantine_record(*, official_game_date: str, mlb_game_pk: int, team_id: int, target_horizon_utc: str, observed_at_utc: str, reason: str) -> dict[str, Any]:
    """Create an explicit terminal non-projection; it has no substitute probabilities."""
    if official_game_date.startswith("2026-05-"):
        raise ProjectedLineupContractError("May 2026 is sealed from projected-lineup records")
    horizon = _utc(target_horizon_utc, "target_horizon_utc")
    observed = _utc(observed_at_utc, "observed_at_utc")
    if observed > horizon:
        state = "projected_unavailable"
    else:
        state = "projected_quarantined"
    if not isinstance(reason, str) or not reason.strip():
        raise ProjectedLineupContractError("quarantine reason is required")
    return {
        "schema_version": SCHEMA_VERSION,
        "terminal_state": state,
        "research_only": True,
        "betting_authorized": False,
        "official_game_date": official_game_date,
        "mlb_game_pk": _positive_int(mlb_game_pk, "mlb_game_pk"),
        "team_id": _positive_int(team_id, "team_id"),
        "target_horizon_utc": horizon.isoformat(timespec="microseconds").replace("+00:00", "Z"),
        "observed_at_utc": observed.isoformat(timespec="microseconds").replace("+00:00", "Z"),
        "reason": reason,
    }
