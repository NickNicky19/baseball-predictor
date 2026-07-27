"""Fail-closed consumption boundary for the future opportunity/matchup candidate."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from src.evaluation.forward_pitcher_context_v2 import ForwardPitcherContextV2
from src.evaluation.projected_lineup_contract import load_contract as load_lineup_contract
from src.evaluation.projected_lineup_contract_v2 import validate_projection_v2
from src.evaluation.prospective_batter_opportunity_v2 import validate_snapshot_v2
from src.evaluation.shadow_capture_plan import CaptureTarget


SCHEMA_VERSION = "shared-pa-opportunity-matchup-input-v1"
PROTOCOL_SCHEMA_VERSION = "shared-pa-opportunity-matchup-protocol-v1"


class SharedPAOpportunityMatchupError(ValueError):
    pass


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _sha(value: object, label: str) -> str:
    text = str(value).strip().lower()
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise SharedPAOpportunityMatchupError(f"{label} must be a lowercase SHA-256 digest")
    return text


def _utc(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise SharedPAOpportunityMatchupError(f"{label} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SharedPAOpportunityMatchupError(f"{label} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SharedPAOpportunityMatchupError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _date(value: object, label: str) -> date:
    if not isinstance(value, str):
        raise SharedPAOpportunityMatchupError(f"{label} must be YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise SharedPAOpportunityMatchupError(f"{label} must be YYYY-MM-DD") from exc
    if value != parsed.isoformat():
        raise SharedPAOpportunityMatchupError(f"{label} must be canonical YYYY-MM-DD")
    if parsed.year == 2026 and parsed.month == 5:
        raise SharedPAOpportunityMatchupError("May 2026 is sealed")
    return parsed


def _positive_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise SharedPAOpportunityMatchupError(f"{label} must be a positive integer")
    return value


def load_protocol(path: str | Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SharedPAOpportunityMatchupError("protocol is unreadable") from exc
    if not isinstance(value, Mapping) or value.get("schema_version") != PROTOCOL_SCHEMA_VERSION:
        raise SharedPAOpportunityMatchupError("protocol schema changed")
    if value.get("status") != "PREDECLARED_RESEARCH_ONLY_NOT_ACTIVE":
        raise SharedPAOpportunityMatchupError("protocol was activated without qualification")
    if value.get("required_input_blocks") != [
        "time_safe_batter_core", "receipt_bound_opportunity", "receipt_bound_projected_lineup", "receipt_bound_probable_pitcher_v2"
    ] or value.get("missing_input_policy") != "TERMINAL_ABSTENTION_NO_SUBSTITUTION":
        raise SharedPAOpportunityMatchupError("required input or missingness policy changed")
    protected = value.get("protected_boundaries")
    if not isinstance(protected, Mapping) or any(item is not False for item in protected.values()):
        raise SharedPAOpportunityMatchupError("a protected boundary was weakened")
    return dict(value)


def _batter_core(receipt: object, *, target: CaptureTarget, player_id: int) -> dict[str, Any]:
    required = {"schema_version", "source_kind", "player_id", "observed_at_utc", "max_source_game_date", "feature_payload_sha256", "feature_schema_sha256"}
    if not isinstance(receipt, Mapping) or set(receipt) != required:
        raise SharedPAOpportunityMatchupError("batter-core receipt schema changed")
    if receipt["schema_version"] != "time-safe-batter-core-receipt-v1" or receipt["source_kind"] != "internal_point_in_time_batter_history":
        raise SharedPAOpportunityMatchupError("batter-core receipt source is not approved")
    if _positive_int(receipt["player_id"], "batter-core player_id") != player_id:
        raise SharedPAOpportunityMatchupError("batter-core player identity differs")
    if _utc(receipt["observed_at_utc"], "batter-core observed_at_utc") > _utc(target.entry_target_at_utc, "target horizon"):
        raise SharedPAOpportunityMatchupError("batter-core receipt arrived after T-minus-4")
    if _date(receipt["max_source_game_date"], "max_source_game_date") >= _date(target.official_game_date, "target date"):
        raise SharedPAOpportunityMatchupError("batter-core receipt includes same-day or future data")
    _sha(receipt["feature_payload_sha256"], "feature_payload_sha256")
    _sha(receipt["feature_schema_sha256"], "feature_schema_sha256")
    return dict(receipt)


def terminal_abstention(*, target: CaptureTarget, player_id: int, batting_team_id: int, reason: str) -> dict[str, Any]:
    if not isinstance(reason, str) or not reason.strip():
        raise SharedPAOpportunityMatchupError("terminal abstention requires a reason")
    row = {
        "schema_version": SCHEMA_VERSION, "terminal_state": "terminal_missing_required_input", "research_only": True,
        "betting_authorized": False, "target_id": target.target_id, "official_game_date": target.official_game_date,
        "mlb_game_pk": target.mlb_game_pk, "player_id": _positive_int(player_id, "player_id"),
        "batting_team_id": _positive_int(batting_team_id, "batting_team_id"), "reason": reason,
        "feature_values": None, "prediction_authorized": False,
    }
    return {**row, "bundle_sha256": hashlib.sha256(_canonical_bytes(row)).hexdigest()}


def build_candidate_input_bundle(*, target: CaptureTarget, batting_side: str, batting_team_id: int, player_id: int,
                                 batter_core_receipt: Mapping[str, Any], opportunity_snapshot: Mapping[str, Any] | None,
                                 projected_lineup_record: Mapping[str, Any] | None, pitcher_context_record: Mapping[str, Any] | None,
                                 lineup_contract_path: str | Path) -> dict[str, Any]:
    """Validate every consumed block; missing or contradictory evidence abstains."""
    if batting_side not in {"home", "away"}:
        raise SharedPAOpportunityMatchupError("batting_side must be home or away")
    team_id = _positive_int(batting_team_id, "batting_team_id")
    batter_id = _positive_int(player_id, "player_id")
    core = _batter_core(batter_core_receipt, target=target, player_id=batter_id)
    if opportunity_snapshot is None or projected_lineup_record is None or pitcher_context_record is None:
        return terminal_abstention(target=target, player_id=batter_id, batting_team_id=team_id, reason="one or more required receipted blocks are absent")

    validate_snapshot_v2(opportunity_snapshot)
    if (opportunity_snapshot.get("official_game_date"), opportunity_snapshot.get("mlb_game_pk"), opportunity_snapshot.get("side"), opportunity_snapshot.get("team_id")) != (
        target.official_game_date, target.mlb_game_pk, batting_side, team_id
    ) or _utc(opportunity_snapshot.get("target_horizon_utc"), "opportunity horizon") != _utc(target.entry_target_at_utc, "target horizon"):
        raise SharedPAOpportunityMatchupError("opportunity snapshot target identity differs")
    opportunity_rows = [row for row in opportunity_snapshot["features"] if row.get("player_id") == batter_id]
    if len(opportunity_rows) != 1 or opportunity_rows[0].get("fit_eligible") is not True:
        raise SharedPAOpportunityMatchupError("player has no uniquely eligible opportunity row")

    lineup = validate_projection_v2(projected_lineup_record, load_lineup_contract(lineup_contract_path))
    if (projected_lineup_record.get("official_game_date"), projected_lineup_record.get("mlb_game_pk"), projected_lineup_record.get("team_id")) != (
        target.official_game_date, target.mlb_game_pk, team_id
    ) or _utc(projected_lineup_record.get("target_horizon_utc"), "lineup horizon") != _utc(target.entry_target_at_utc, "target horizon"):
        raise SharedPAOpportunityMatchupError("projected-lineup target identity differs")
    if batter_id not in projected_lineup_record["active_roster_player_ids"] or str(batter_id) not in lineup["start_probability"]:
        raise SharedPAOpportunityMatchupError("player is absent from the projected-lineup support")

    pitcher = ForwardPitcherContextV2.from_mapping(pitcher_context_record).bind_target(target)
    if not pitcher.candidate_input_eligible:
        return terminal_abstention(target=target, player_id=batter_id, batting_team_id=team_id, reason="probable-pitcher identity is unavailable")
    expected_team = pitcher.home_team_id if batting_side == "home" else pitcher.away_team_id
    opposing_team = pitcher.away_team_id if batting_side == "home" else pitcher.home_team_id
    opposing_pitcher = pitcher.away_probable_pitcher if batting_side == "home" else pitcher.home_probable_pitcher
    if expected_team != team_id or opposing_pitcher.player_id is None:
        raise SharedPAOpportunityMatchupError("pitcher receipt team-side identity differs")

    unsigned = {
        "schema_version": SCHEMA_VERSION, "terminal_state": "eligible_complete", "research_only": True,
        "betting_authorized": False, "prediction_authorized": False, "target_id": target.target_id,
        "official_game_date": target.official_game_date, "mlb_game_pk": target.mlb_game_pk, "player_id": batter_id,
        "batting_side": batting_side, "batting_team_id": team_id, "opposing_team_id": opposing_team,
        "input_hashes": {
            "batter_core": hashlib.sha256(_canonical_bytes(core)).hexdigest(),
            "opportunity": opportunity_snapshot["snapshot_sha256"],
            "projected_lineup": projected_lineup_record["projection_content_sha256"],
            "probable_pitcher": pitcher.context_sha256,
        },
        "feature_values": {
            "opportunity": dict(opportunity_rows[0]),
            "projected_start_probability": lineup["start_probability"][str(batter_id)],
            "projected_slot_probability": {key: value for key, value in lineup["slot_probability"].items() if key.startswith(f"{batter_id}:")},
            "opposing_probable_pitcher_id": opposing_pitcher.player_id,
        },
    }
    return {**unsigned, "bundle_sha256": hashlib.sha256(_canonical_bytes(unsigned)).hexdigest()}
