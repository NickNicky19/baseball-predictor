"""Pure one-side runner for the projected-opportunity research candidate.

Persistence and network transport are intentionally outside this module.  One
call must account for every player receiving positive projected start mass.
Valid players receive immutable Hits/HR/Total-Bases records; unavailable or
invalid player stats receive explicit abstentions instead of substitutions.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from src.evaluation.projected_lineup_contract import ProjectedLineupContractError
from src.evaluation.projected_lineup_contract_v2 import validate_projection_v2
from src.evaluation.shared_pa_batter_skill import (
    BatterSkillSnapshotError,
    build_batter_skill_snapshot,
)
from src.evaluation.shared_pa_forward_collector import RawPregameResponse
from src.evaluation.shared_pa_forward_evidence import sha256_value
from src.evaluation.shared_pa_forward_runner import StatsBatch
from src.evaluation.shared_pa_projected_opportunity_candidate import (
    ProjectedOpportunityCandidateError,
    ProjectedOpportunityProtocol,
    build_projected_opportunity_candidate,
)


class ProjectedOpportunityRunnerError(ValueError):
    """The side population or prediction chronology cannot be proved."""


SCHEMA_VERSION = "shared-pa-projected-opportunity-side-bundle-v1"


def _utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise ProjectedOpportunityRunnerError(f"{label} must be timezone-aware ISO-8601")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProjectedOpportunityRunnerError(f"{label} must be timezone-aware ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProjectedOpportunityRunnerError(f"{label} must be timezone-aware ISO-8601")
    return parsed.astimezone(timezone.utc)


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ProjectedOpportunityRunnerError(f"{label} must be a positive integer")
    return value


def _abstention(
    *,
    projection: Mapping[str, Any],
    player_id: int,
    side: str,
    observed_at_utc: str,
    reason: str,
    protocol_sha256: str,
) -> dict[str, Any]:
    unsigned = {
        "schema_version": "shared-pa-projected-opportunity-abstention-v1",
        "terminal_state": "candidate_abstention",
        "research_only": True,
        "betting_authorized": False,
        "promotion_eligible": False,
        "official_game_date": projection["official_game_date"],
        "mlb_game_pk": projection["mlb_game_pk"],
        "team_id": projection["team_id"],
        "side": side,
        "player_id": player_id,
        "target_horizon_utc": projection["target_horizon_utc"],
        "observed_at_utc": observed_at_utc,
        "projected_lineup_content_sha256": projection["projection_content_sha256"],
        "candidate_protocol_sha256": protocol_sha256,
        "reason": reason,
        "probability_substituted": False,
    }
    return {**unsigned, "abstention_sha256": sha256_value(unsigned)}


def build_side_candidate_bundle(
    *,
    projected_lineup_record: Mapping[str, Any],
    lineup_contract: Mapping[str, Any],
    stats_batch: StatsBatch,
    loaded_forward_contract: Mapping[str, Any],
    candidate_protocol: ProjectedOpportunityProtocol,
    side: str,
    research_epoch_utc: str,
    prediction_generated_at_utc: str,
    collector_instance_id: str,
    collector_code_sha256: str,
    runtime_manifest_sha256: str,
) -> dict[str, Any]:
    """Build a complete, explicitly accounted research bundle for one side."""
    if not isinstance(stats_batch, StatsBatch):
        raise ProjectedOpportunityRunnerError("stats batch is required")
    if not isinstance(candidate_protocol, ProjectedOpportunityProtocol):
        raise ProjectedOpportunityRunnerError("hash-verified candidate protocol is required")
    if side not in {"home", "away"}:
        raise ProjectedOpportunityRunnerError("side must be home or away")
    try:
        marginals = validate_projection_v2(projected_lineup_record, lineup_contract)
    except ProjectedLineupContractError as exc:
        raise ProjectedOpportunityRunnerError("projected lineup failed its governing contract") from exc
    horizon = _utc(projected_lineup_record.get("target_horizon_utc"), "target_horizon_utc")
    generated = _utc(prediction_generated_at_utc, "prediction_generated_at_utc")
    if generated > horizon:
        raise ProjectedOpportunityRunnerError("prediction bundle cannot be generated after T-4")
    support = sorted(int(value) for value in marginals["start_probability"])
    if not support or any(value <= 0 for value in support):
        raise ProjectedOpportunityRunnerError("projected lineup player support is invalid")
    accounted = set(stats_batch.responses) | set(stats_batch.errors)
    if accounted != set(support):
        raise ProjectedOpportunityRunnerError(
            "stats batch must account for every and only projected-support player"
        )

    records: list[dict[str, Any]] = []
    abstentions: list[dict[str, Any]] = []
    for player_id in support:
        if player_id in stats_batch.errors:
            abstentions.append(_abstention(
                projection=projected_lineup_record,
                player_id=player_id,
                side=side,
                observed_at_utc=prediction_generated_at_utc,
                reason="official_stats_source_error",
                protocol_sha256=candidate_protocol.sha256,
            ))
            continue
        response = stats_batch.responses[player_id]
        if not isinstance(response, RawPregameResponse):
            raise ProjectedOpportunityRunnerError("stats batch response type changed")
        try:
            skill = build_batter_skill_snapshot(
                projected_lineup_record=projected_lineup_record,
                lineup_contract=lineup_contract,
                player_id=player_id,
                side=side,
                stats_response=response,
                loaded_forward_contract=loaded_forward_contract,
                collector_instance_id=collector_instance_id,
                collector_code_sha256=collector_code_sha256,
                runtime_manifest_sha256=runtime_manifest_sha256,
            )
            records.append(build_projected_opportunity_candidate(
                batter_skill_snapshot=skill,
                projected_lineup_record=projected_lineup_record,
                lineup_contract=lineup_contract,
                loaded_forward_contract=loaded_forward_contract,
                candidate_protocol=candidate_protocol,
                research_epoch_utc=research_epoch_utc,
                prediction_generated_at_utc=prediction_generated_at_utc,
            ))
        except BatterSkillSnapshotError as exc:
            abstentions.append(_abstention(
                projection=projected_lineup_record,
                player_id=player_id,
                side=side,
                observed_at_utc=prediction_generated_at_utc,
                reason=f"invalid_player_input_{type(exc).__name__}",
                protocol_sha256=candidate_protocol.sha256,
            ))
        except ProjectedOpportunityCandidateError as exc:
            raise ProjectedOpportunityRunnerError(
                "candidate-wide chronology, identity, or probability replay failed"
            ) from exc

    terminal_state = (
        "candidate_side_complete"
        if not abstentions
        else "candidate_side_all_abstained"
        if not records
        else "candidate_side_partial_abstentions"
    )
    unsigned = {
        "schema_version": SCHEMA_VERSION,
        "terminal_state": terminal_state,
        "research_only": True,
        "betting_authorized": False,
        "promotion_eligible": False,
        "official_game_date": projected_lineup_record["official_game_date"],
        "mlb_game_pk": _positive_int(projected_lineup_record.get("mlb_game_pk"), "mlb_game_pk"),
        "team_id": _positive_int(projected_lineup_record.get("team_id"), "team_id"),
        "side": side,
        "target_horizon_utc": projected_lineup_record["target_horizon_utc"],
        "prediction_generated_at_utc": prediction_generated_at_utc,
        "projected_lineup_content_sha256": projected_lineup_record[
            "projection_content_sha256"
        ],
        "candidate_protocol_sha256": candidate_protocol.sha256,
        "support_player_ids": support,
        "candidate_records": records,
        "abstentions": abstentions,
        "coverage": {
            "projected_support_players": len(support),
            "predicted_players": len(records),
            "abstained_players": len(abstentions),
        },
    }
    if len(records) + len(abstentions) != len(support):
        raise ProjectedOpportunityRunnerError("candidate side accounting is incomplete")
    return {**unsigned, "side_bundle_sha256": sha256_value(unsigned)}
