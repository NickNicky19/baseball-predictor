"""Full retained-evidence producer for hierarchical source-bound side bundles."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping, Sequence

from src.evaluation.projected_lineup_contract import load_contract, validate_projection
from src.evaluation.shadow_capture_plan import CaptureTarget, ShadowCapturePlan
from src.evaluation.shared_pa_forward_collector import RawPregameResponse
from src.evaluation.shared_pa_forward_evidence import sha256_value
from src.evaluation.shared_pa_projected_opportunity_candidate_v2 import (
    ProjectedOpportunityProtocolV2,
)
from src.evaluation.shared_pa_projected_opportunity_evidence_v2 import (
    build_evidence_envelope_v2,
    replay_projected_context_v2,
)
from src.evaluation.shared_pa_projected_opportunity_runner_v2 import (
    DateBoundedStatsBatchV2,
    build_side_candidate_bundle_v2,
)
from src.evaluation.shared_pa_source_bound_candidate_v3 import CandidateProtocolV3
from src.evaluation.shared_pa_source_bound_candidate_v4 import (
    CandidateProtocolV4,
    build_source_bound_candidate_record_v4,
)


class SourceBoundRunnerV4Error(ValueError):
    """The hierarchical producer cannot replay every required raw input."""


SCHEMA_VERSION = "shared-pa-source-bound-opportunity-side-bundle-v4"


def _v4_abstention(value: Mapping[str, Any]) -> dict[str, Any]:
    unsigned = {
        "schema_version": "shared-pa-source-bound-opportunity-abstention-v4",
        "terminal_state": "candidate_abstention",
        "research_only": True,
        "betting_authorized": False,
        "promotion_eligible": False,
        "candidate_id": "shared_pa_candidate_v1",
        "mlb_game_pk": value.get("mlb_game_pk"),
        "team_id": value.get("team_id"),
        "side": value.get("side"),
        "player_id": value.get("player_id"),
        "target_horizon_utc": value.get("target_horizon_utc"),
        "reason": value.get("reason"),
        "upstream_v2_abstention_sha256": value.get("abstention_sha256"),
        "probability_substituted": False,
    }
    return {**unsigned, "abstention_sha256": sha256_value(unsigned)}


def build_side_candidate_bundle_v4(
    *, root: Path, v2_protocol: ProjectedOpportunityProtocolV2,
    parent_protocol: CandidateProtocolV3, hierarchical_protocol: CandidateProtocolV4,
    authority_arguments: Mapping[str, Any], plan: ShadowCapturePlan,
    target: CaptureTarget, side: str, team_id: int,
    schedule_response: RawPregameResponse,
    active_roster_receipt: Mapping[str, Any], active_roster_raw: bytes,
    history_records: Sequence[Mapping[str, Any]],
    history_raw_by_sha256: Mapping[str, bytes],
    history_coverage: Mapping[str, Any],
    history_schedule_raw_by_sha256: Mapping[str, bytes],
    opportunity_snapshot: Mapping[str, Any],
    projected_lineup_record: Mapping[str, Any],
    stats_batch: DateBoundedStatsBatchV2, prediction_generated_at_utc: str,
    loaded_forward_contract: Mapping[str, Any],
    runtime_release_receipt_path: Path | None = None,
) -> dict[str, Any]:
    """Replay the v2 raw boundary, then replace only its opportunity component."""
    upstream = build_side_candidate_bundle_v2(
        root=root, protocol=v2_protocol, plan=plan, target=target, side=side,
        team_id=team_id, schedule_response=schedule_response,
        active_roster_receipt=active_roster_receipt,
        active_roster_raw=active_roster_raw, history_records=history_records,
        history_raw_by_sha256=history_raw_by_sha256,
        history_coverage=history_coverage,
        history_schedule_raw_by_sha256=history_schedule_raw_by_sha256,
        opportunity_snapshot=opportunity_snapshot,
        projected_lineup_record=projected_lineup_record,
        stats_batch=stats_batch,
        prediction_generated_at_utc=prediction_generated_at_utc,
        loaded_forward_contract=loaded_forward_contract,
        runtime_release_receipt_path=runtime_release_receipt_path,
    )
    context = replay_projected_context_v2(
        root=root, plan=plan, target=target, side=side, team_id=team_id,
        schedule_response=schedule_response,
        active_roster_receipt=active_roster_receipt,
        active_roster_raw=active_roster_raw, history_records=history_records,
        history_raw_by_sha256=history_raw_by_sha256,
        history_coverage=history_coverage,
        history_schedule_raw_by_sha256=history_schedule_raw_by_sha256,
        opportunity_snapshot=opportunity_snapshot,
        projected_lineup_record=projected_lineup_record,
        prediction_generated_at_utc=prediction_generated_at_utc,
    )
    marginals = validate_projection(
        context["replayed_projection"],
        load_contract(root / "config/projected_lineup_contract_v1.json"),
    )
    records: list[dict[str, Any]] = []
    for upstream_record in upstream["candidate_records"]:
        player_id = int(upstream_record["player_id"])
        responses = stats_batch.responses.get(player_id)
        if responses is None:
            raise SourceBoundRunnerV4Error(
                "upstream candidate lacks the raw stats batch required for v4 replay"
            )
        envelope = build_evidence_envelope_v2(
            root=root, plan=plan, target=target, side=side, team_id=team_id,
            schedule_response=schedule_response,
            active_roster_receipt=active_roster_receipt,
            active_roster_raw=active_roster_raw,
            history_records=history_records,
            history_raw_by_sha256=history_raw_by_sha256,
            history_coverage=history_coverage,
            history_schedule_raw_by_sha256=history_schedule_raw_by_sha256,
            opportunity_snapshot=opportunity_snapshot,
            projected_lineup_record=projected_lineup_record,
            player_id=player_id, stats_responses=responses,
            prediction_generated_at_utc=prediction_generated_at_utc,
        )
        unsigned_envelope = dict(envelope)
        unsigned_envelope.pop("evidence_envelope_sha256")
        unsigned_envelope["lineup_state"] = "projected_probability_distribution"
        envelope = {
            **unsigned_envelope,
            "evidence_envelope_sha256": sha256_value(unsigned_envelope),
        }
        records.append(build_source_bound_candidate_record_v4(
            root=root, evidence_envelope=envelope,
            projection_marginals=marginals,
            authority_arguments=authority_arguments,
            parent_protocol=parent_protocol,
            hierarchical_protocol=hierarchical_protocol,
            source_manifest_sha256=upstream["source_manifest_sha256"],
            runtime_release_receipt_sha256=upstream[
                "runtime_release_receipt_sha256"
            ],
        ))
    abstentions = [_v4_abstention(value) for value in upstream["abstentions"]]
    terminal_state = (
        "candidate_side_complete" if not abstentions else
        "candidate_side_all_abstained" if not records else
        "candidate_side_partial_abstentions"
    )
    unsigned = {
        "schema_version": SCHEMA_VERSION,
        "terminal_state": terminal_state,
        "research_only": True,
        "betting_authorized": False,
        "promotion_eligible": False,
        "candidate_id": "shared_pa_candidate_v1",
        "candidate_protocol_sha256": hierarchical_protocol.sha256,
        "source_manifest_sha256": upstream["source_manifest_sha256"],
        "source_release_commit": upstream["source_release_commit"],
        "runtime_release_receipt_sha256": upstream[
            "runtime_release_receipt_sha256"
        ],
        "upstream_v2_side_bundle_sha256": upstream["side_bundle_sha256"],
        "producer_code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "plan_sha256": plan.plan_sha256,
        "target_id": target.target_id,
        "official_game_date": target.official_game_date,
        "mlb_game_pk": target.mlb_game_pk,
        "team_id": team_id,
        "side": side,
        "target_horizon_utc": target.entry_target_at_utc,
        "prediction_generated_at_utc": prediction_generated_at_utc,
        "lineup_state": "projected_probability_distribution",
        "projected_lineup_content_sha256": context["replayed_projection"][
            "projection_content_sha256"
        ],
        "support_player_ids": upstream["support_player_ids"],
        "candidate_records": records,
        "abstentions": abstentions,
        "coverage": {
            "projected_support_players": len(upstream["support_player_ids"]),
            "predicted_players": len(records),
            "abstained_players": len(abstentions),
        },
    }
    if len(records) + len(abstentions) != len(upstream["support_player_ids"]):
        raise SourceBoundRunnerV4Error("v4 side accounting is incomplete")
    return {**unsigned, "side_bundle_sha256": sha256_value(unsigned)}
