"""Coefficient-free joint projected-lineup research candidate.

This module does not guess player availability.  Eligibility comes only from a
receipt-bound target active roster.  It assigns equal mass to each strictly
prior, complete historical lineup whose nine players remain on that roster,
combining identical lineups before normalization.  If that evidence cannot
produce a complete joint distribution, the target is terminally unavailable.

The output is research-only and must pass prospective lineup evaluation before
any downstream Hits, HR, or Total Bases candidate may consume it.
"""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from src.evaluation.projected_lineup_contract import (
    quarantine_record,
    sha256_value,
    validate_projection,
)
from src.evaluation.projected_lineup_history import (
    HistoricalLineupFeatureError,
    _normalize_completed_lineups,
    build_feature_store,
)


class EmpiricalJointLineupError(ValueError):
    """The empirical joint lineup cannot be truthfully constructed."""


SCHEMA_VERSION = "projected-lineup-empirical-joint-artifact-v1"
CANDIDATE_ID = "projected_lineup_empirical_joint_v1"


def _utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise EmpiricalJointLineupError(f"{label} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EmpiricalJointLineupError(f"{label} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EmpiricalJointLineupError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _target_date(value: Any) -> date:
    if not isinstance(value, str):
        raise EmpiricalJointLineupError("official_game_date must be canonical YYYY-MM-DD")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise EmpiricalJointLineupError("official_game_date must be canonical YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise EmpiricalJointLineupError("official_game_date must be canonical YYYY-MM-DD")
    if value.startswith("2026-05-"):
        raise EmpiricalJointLineupError("May 2026 is sealed")
    return parsed


def _receipt_without_players(receipt: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "source_kind", "source_record_id", "received_at_utc",
        "payload_sha256", "input_surface_sha256", "players",
    }
    if not isinstance(receipt, Mapping) or set(receipt) != required:
        raise EmpiricalJointLineupError("active roster receipt schema changed")
    return {key: receipt[key] for key in sorted(required - {"players"})}


def _feature_store_receipt(
    feature_store: Mapping[str, Any],
    *,
    observed_at_utc: str,
) -> dict[str, Any]:
    if not isinstance(feature_store, Mapping):
        raise EmpiricalJointLineupError("historical feature store must be an object")
    record_id = feature_store.get("feature_store_sha256")
    history_hash = feature_store.get("history_input_sha256")
    if not isinstance(record_id, str) or not isinstance(history_hash, str):
        raise EmpiricalJointLineupError("historical feature store lacks bound hashes")
    unsigned = dict(feature_store)
    supplied = unsigned.pop("feature_store_sha256", None)
    if supplied != sha256_value(unsigned):
        raise EmpiricalJointLineupError("historical feature store content hash differs")
    return {
        "source_kind": "internal_historical_lineup_feature_store",
        "source_record_id": record_id,
        "received_at_utc": observed_at_utc,
        "payload_sha256": record_id,
        "input_surface_sha256": history_hash,
    }


def _joint_scenarios(
    normalized_history: Sequence[Mapping[str, Any]],
    *,
    roster_ids: set[int],
) -> list[dict[str, Any]]:
    games: dict[int, list[dict[str, int]]] = defaultdict(list)
    for row in normalized_history:
        games[int(row["mlb_game_pk"])].append(
            {"player_id": int(row["player_id"]), "slot": int(row["slot"])}
        )

    counts: Counter[tuple[tuple[int, int], ...]] = Counter()
    for lineup in games.values():
        ordered = sorted(lineup, key=lambda entry: entry["slot"])
        if len(ordered) != 9:
            raise EmpiricalJointLineupError("normalized history contains an incomplete lineup")
        if all(entry["player_id"] in roster_ids for entry in ordered):
            counts[tuple((entry["player_id"], entry["slot"]) for entry in ordered)] += 1

    total = sum(counts.values())
    if total == 0:
        return []
    scenarios: list[dict[str, Any]] = []
    for signature, count in sorted(counts.items()):
        scenarios.append({
            "probability": count / total,
            "lineup": [
                {"player_id": player_id, "slot": slot}
                for player_id, slot in signature
            ],
        })
    return scenarios


def build_empirical_joint_projection(
    *,
    official_game_date: str,
    mlb_game_pk: int,
    team_id: int,
    target_horizon_utc: str,
    projection_receipt_utc: str,
    active_roster_receipt: Mapping[str, Any],
    historical_feature_store: Mapping[str, Any],
    completed_lineups: Sequence[Mapping[str, Any]],
    contract: Mapping[str, Any],
) -> dict[str, Any]:
    """Build and validate one future-only empirical joint projection.

    Invalid evidence raises.  A structurally valid input with no eligible joint
    historical lineup returns an explicit terminal unavailable record.
    """
    target_date = _target_date(official_game_date)
    horizon = _utc(target_horizon_utc, "target_horizon_utc")
    observed = _utc(projection_receipt_utc, "projection_receipt_utc")
    if observed > horizon:
        return quarantine_record(
            official_game_date=official_game_date,
            mlb_game_pk=mlb_game_pk,
            team_id=team_id,
            target_horizon_utc=target_horizon_utc,
            observed_at_utc=projection_receipt_utc,
            reason="projection assembled after the decision horizon",
        )

    try:
        normalized = _normalize_completed_lineups(
            completed_lineups, target_date=target_date, team_id=team_id
        )
    except (ValueError, HistoricalLineupFeatureError) as exc:
        raise EmpiricalJointLineupError("historical lineup evidence is invalid") from exc

    if not isinstance(active_roster_receipt, Mapping):
        raise EmpiricalJointLineupError("active roster receipt must be an object")
    roster_players = active_roster_receipt.get("players")
    if not isinstance(roster_players, list):
        raise EmpiricalJointLineupError("active roster players are missing")
    roster_ids = {
        player.get("player_id")
        for player in roster_players
        if isinstance(player, Mapping)
    }
    if (
        len(roster_ids) != len(roster_players)
        or len(roster_ids) < 9
        or any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in roster_ids)
    ):
        raise EmpiricalJointLineupError("active roster player identities are invalid")

    try:
        replayed_feature_store = build_feature_store(
            official_game_date=official_game_date,
            team_id=team_id,
            active_roster_receipt=active_roster_receipt,
            completed_lineups=completed_lineups,
        )
    except HistoricalLineupFeatureError as exc:
        raise EmpiricalJointLineupError("historical feature store replay failed") from exc
    if historical_feature_store != replayed_feature_store:
        raise EmpiricalJointLineupError(
            "historical feature store differs from independent semantic replay"
        )

    scenarios = _joint_scenarios(normalized, roster_ids=roster_ids)
    if not scenarios:
        return quarantine_record(
            official_game_date=official_game_date,
            mlb_game_pk=mlb_game_pk,
            team_id=team_id,
            target_horizon_utc=target_horizon_utc,
            observed_at_utc=projection_receipt_utc,
            reason="no strictly-prior complete joint lineup is eligible under the receipted active roster",
        )

    fitted_artifact_sha256 = sha256_value({
        "schema_version": SCHEMA_VERSION,
        "candidate_id": CANDIDATE_ID,
        "historical_feature_store_sha256": historical_feature_store["feature_store_sha256"],
        "eligible_joint_scenarios": scenarios,
    })
    model_code_sha256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    record = {
        "schema_version": "projected-lineup-projection-v1",
        "terminal_state": "projected_complete",
        "research_only": True,
        "betting_authorized": False,
        "official_game_date": official_game_date,
        "mlb_game_pk": mlb_game_pk,
        "team_id": team_id,
        "target_horizon_utc": target_horizon_utc,
        "projection_receipt_utc": projection_receipt_utc,
        "input_receipts": {
            "active_roster": _receipt_without_players(active_roster_receipt),
            "historical_lineup_features": _feature_store_receipt(
                historical_feature_store, observed_at_utc=projection_receipt_utc
            ),
        },
        "active_roster_player_ids": sorted(roster_ids),
        "fitted_candidate_id": CANDIDATE_ID,
        "fitted_artifact_sha256": fitted_artifact_sha256,
        "model_code_sha256": model_code_sha256,
        "scenarios": scenarios,
    }
    record["projection_content_sha256"] = sha256_value(record)
    validate_projection(record, contract)
    return record
