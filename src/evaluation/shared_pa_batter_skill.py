"""Lineup-independent, point-in-time batter per-PA skill snapshots.

The original shared-PA collector could build batter probabilities only when an
official projected lineup exposed exactly nine player IDs at T-4.  That made a
projected-lineup opportunity model numerically unusable on the very sides it
was intended to cover.  This boundary derives the unchanged empirical-Bayes
per-PA outcome simplex for players supported by a separately receipt-validated
projected-lineup distribution.  It consumes no target-game lineup or outcome.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timezone
from typing import Any, Mapping

from src.evaluation.projected_lineup_contract import ProjectedLineupContractError
from src.evaluation.projected_lineup_contract_v2 import validate_projection_v2
from src.evaluation.shared_pa_forward_collector import (
    RawPregameResponse,
    SharedPAForwardCollectorError,
    pa_counts_from_hitting_stats,
)
from src.evaluation.shared_pa_forward_evidence import (
    CONTROL_ID,
    PA_OUTCOMES,
    empirical_bayes_pa_probability,
    sha256_value,
)


class BatterSkillSnapshotError(ValueError):
    """The proposed skill snapshot is late, contradictory, or not replayable."""


SCHEMA_VERSION = "shared-pa-batter-skill-snapshot-v1"
_SHA_FIELDS = ("collector_code_sha256", "runtime_manifest_sha256")


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise BatterSkillSnapshotError(f"{label} must be a positive integer")
    return value


def _utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise BatterSkillSnapshotError(f"{label} must be timezone-aware ISO-8601")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BatterSkillSnapshotError(f"{label} must be timezone-aware ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise BatterSkillSnapshotError(f"{label} must be timezone-aware ISO-8601")
    return parsed.astimezone(timezone.utc)


def _canonical_date(value: Any) -> date:
    if not isinstance(value, str):
        raise BatterSkillSnapshotError("official_game_date must be canonical YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise BatterSkillSnapshotError("official_game_date must be canonical YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise BatterSkillSnapshotError("official_game_date must be canonical YYYY-MM-DD")
    if parsed.year == 2026 and parsed.month == 5:
        raise BatterSkillSnapshotError("May 2026 is sealed")
    return parsed


def _sha(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise BatterSkillSnapshotError(f"{label} must be a lowercase SHA-256")
    return value


def _snapshot_hash(record: Mapping[str, Any]) -> str:
    unsigned = dict(record)
    unsigned.pop("batter_skill_snapshot_sha256", None)
    return sha256_value(unsigned)


def validate_batter_skill_snapshot(
    record: Mapping[str, Any], *, loaded_forward_contract: Mapping[str, Any]
) -> None:
    """Recompute all material probabilities and reject schema drift."""
    required = {
        "schema_version", "terminal_state", "research_only", "betting_authorized",
        "promotion_eligible", "collector_instance_id", "receipt_utc",
        "source_observation_utc", "collector_code_sha256", "runtime_manifest_sha256",
        "official_game_date", "mlb_game_pk", "team_id", "side", "player_id",
        "target_horizon_utc", "projected_lineup_content_sha256",
        "raw_stats_payload_sha256", "stats_receipt_utc", "stats_counts", "stats_pa",
        "stats_season", "control_id", "control_config_sha256", "prior_strength_pa",
        "league_prior_probability", "per_pa_probability", "rate_fallback_labels",
        "feature_snapshot_sha256", "pitcher_block_status", "policy_status",
        "batter_skill_snapshot_sha256",
    }
    if not isinstance(record, Mapping) or set(record) != required:
        raise BatterSkillSnapshotError("batter skill snapshot schema changed")
    if (
        record.get("schema_version") != SCHEMA_VERSION
        or record.get("terminal_state") != "captured_complete"
        or record.get("research_only") is not True
        or record.get("betting_authorized") is not False
        or record.get("promotion_eligible") is not False
        or record.get("pitcher_block_status") != "excluded_batter_only"
        or record.get("policy_status") != "none_research_probability_only"
    ):
        raise BatterSkillSnapshotError("batter skill safety state changed")
    _canonical_date(record.get("official_game_date"))
    for label in ("mlb_game_pk", "team_id", "player_id"):
        _positive_int(record.get(label), label)
    if record.get("side") not in {"home", "away"}:
        raise BatterSkillSnapshotError("side must be home or away")
    horizon = _utc(record.get("target_horizon_utc"), "target_horizon_utc")
    receipt = _utc(record.get("stats_receipt_utc"), "stats_receipt_utc")
    if receipt > horizon or _utc(record.get("receipt_utc"), "receipt_utc") != receipt:
        raise BatterSkillSnapshotError("batter skill evidence arrived after T-4")
    if _utc(record.get("source_observation_utc"), "source_observation_utc") != receipt:
        raise BatterSkillSnapshotError("batter skill observation and receipt times differ")
    for label in (*_SHA_FIELDS, "projected_lineup_content_sha256", "raw_stats_payload_sha256"):
        _sha(record.get(label), label)
    counts = record.get("stats_counts")
    if (
        not isinstance(counts, Mapping)
        or set(counts) != set(PA_OUTCOMES)
        or any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in counts.values())
    ):
        raise BatterSkillSnapshotError("batter PA counts are invalid")
    if record.get("stats_pa") != sum(counts.values()):
        raise BatterSkillSnapshotError("batter PA denominator differs from its counts")
    if record.get("stats_season") != int(str(record["official_game_date"])[:4]):
        raise BatterSkillSnapshotError("batter stats season differs from target season")
    control = loaded_forward_contract.get("control")
    prior = loaded_forward_contract.get("league_prior_probability")
    if not isinstance(control, Mapping) or not isinstance(prior, Mapping):
        raise BatterSkillSnapshotError("loaded forward control is incomplete")
    if (
        record.get("control_id") != CONTROL_ID
        or record.get("control_config_sha256") != loaded_forward_contract.get("control_sha256")
        or record.get("prior_strength_pa") != 200.0
        or record.get("league_prior_probability") != prior
    ):
        raise BatterSkillSnapshotError("locked empirical-Bayes control differs")
    expected = empirical_bayes_pa_probability(
        counts=counts,
        league_prior=prior,
        prior_strength_pa=float(control["prior_strength_pa"]),
    )
    supplied = record.get("per_pa_probability")
    if not isinstance(supplied, Mapping) or set(supplied) != set(PA_OUTCOMES) or any(
        not math.isclose(float(supplied[name]), expected[name], rel_tol=0.0, abs_tol=1e-15)
        for name in PA_OUTCOMES
    ):
        raise BatterSkillSnapshotError("batter per-PA probability differs from replay")
    expected_feature = sha256_value({
        "counts": dict(counts),
        "league_prior_probability": dict(prior),
        "prior_strength_pa": 200.0,
        "raw_stats_payload_sha256": record["raw_stats_payload_sha256"],
        "stats_receipt_utc": record["stats_receipt_utc"],
    })
    if record.get("feature_snapshot_sha256") != expected_feature:
        raise BatterSkillSnapshotError("batter feature snapshot hash differs")
    expected_fallback = ["league_prior_only"] if sum(counts.values()) == 0 else []
    if record.get("rate_fallback_labels") != expected_fallback:
        raise BatterSkillSnapshotError("batter rate fallback label differs")
    if record.get("batter_skill_snapshot_sha256") != _snapshot_hash(record):
        raise BatterSkillSnapshotError("batter skill snapshot hash differs")


def build_batter_skill_snapshot(
    *,
    projected_lineup_record: Mapping[str, Any],
    lineup_contract: Mapping[str, Any],
    player_id: int,
    side: str,
    stats_response: RawPregameResponse,
    loaded_forward_contract: Mapping[str, Any],
    collector_instance_id: str,
    collector_code_sha256: str,
    runtime_manifest_sha256: str,
) -> dict[str, Any]:
    """Build one time-safe batter skill snapshot from a supported player ID."""
    try:
        marginals = validate_projection_v2(projected_lineup_record, lineup_contract)
    except ProjectedLineupContractError as exc:
        raise BatterSkillSnapshotError("projected lineup failed its governing contract") from exc
    player = _positive_int(player_id, "player_id")
    if str(player) not in marginals["start_probability"]:
        raise BatterSkillSnapshotError("player is absent from projected-lineup support")
    if side not in {"home", "away"}:
        raise BatterSkillSnapshotError("side must be home or away")
    official_date = projected_lineup_record.get("official_game_date")
    _canonical_date(official_date)
    horizon = _utc(projected_lineup_record.get("target_horizon_utc"), "target_horizon_utc")
    stats_receipt = _utc(stats_response.received_at_utc, "stats_receipt_utc")
    if stats_receipt > horizon:
        raise BatterSkillSnapshotError("batter skill evidence arrived after T-4")
    try:
        counts = pa_counts_from_hitting_stats(response=stats_response, player_id=player)
    except SharedPAForwardCollectorError as exc:
        raise BatterSkillSnapshotError("official batter stats failed validation") from exc
    control = loaded_forward_contract.get("control")
    prior = loaded_forward_contract.get("league_prior_probability")
    if not isinstance(control, Mapping) or not isinstance(prior, Mapping):
        raise BatterSkillSnapshotError("loaded forward control is incomplete")
    per_pa = empirical_bayes_pa_probability(
        counts=counts,
        league_prior=prior,
        prior_strength_pa=float(control["prior_strength_pa"]),
    )
    feature_payload = {
        "counts": counts,
        "league_prior_probability": dict(prior),
        "prior_strength_pa": 200.0,
        "raw_stats_payload_sha256": stats_response.sha256,
        "stats_receipt_utc": stats_response.received_at_utc,
    }
    for label, value in (
        ("collector_code_sha256", collector_code_sha256),
        ("runtime_manifest_sha256", runtime_manifest_sha256),
    ):
        _sha(value, label)
    record = {
        "schema_version": SCHEMA_VERSION,
        "terminal_state": "captured_complete",
        "research_only": True,
        "betting_authorized": False,
        "promotion_eligible": False,
        "collector_instance_id": str(collector_instance_id),
        "receipt_utc": stats_response.received_at_utc,
        "source_observation_utc": stats_response.received_at_utc,
        "collector_code_sha256": collector_code_sha256,
        "runtime_manifest_sha256": runtime_manifest_sha256,
        "official_game_date": official_date,
        "mlb_game_pk": _positive_int(projected_lineup_record.get("mlb_game_pk"), "mlb_game_pk"),
        "team_id": _positive_int(projected_lineup_record.get("team_id"), "team_id"),
        "side": side,
        "player_id": player,
        "target_horizon_utc": projected_lineup_record["target_horizon_utc"],
        "projected_lineup_content_sha256": projected_lineup_record["projection_content_sha256"],
        "raw_stats_payload_sha256": stats_response.sha256,
        "stats_receipt_utc": stats_response.received_at_utc,
        "stats_counts": counts,
        "stats_pa": sum(counts.values()),
        "stats_season": int(str(official_date)[:4]),
        "control_id": CONTROL_ID,
        "control_config_sha256": loaded_forward_contract["control_sha256"],
        "prior_strength_pa": 200.0,
        "league_prior_probability": dict(prior),
        "per_pa_probability": per_pa,
        "rate_fallback_labels": ["league_prior_only"] if sum(counts.values()) == 0 else [],
        "feature_snapshot_sha256": sha256_value(feature_payload),
        "pitcher_block_status": "excluded_batter_only",
        "policy_status": "none_research_probability_only",
        "batter_skill_snapshot_sha256": "",
    }
    record["batter_skill_snapshot_sha256"] = _snapshot_hash(record)
    validate_batter_skill_snapshot(record, loaded_forward_contract=loaded_forward_contract)
    return record
