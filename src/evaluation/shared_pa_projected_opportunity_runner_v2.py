"""Fail-closed one-side consumer for the replayable v2 PA candidate.

This is the only v2 side-population boundary.  It replays the shared raw
pregame context, accounts for every player with positive projected start
mass, and either emits a v2 candidate or an explicit no-substitution
abstention.  It has no v1 candidate fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from src.evaluation.projected_lineup_contract import load_contract, validate_projection, sha256_value
from src.evaluation.shadow_capture_plan import CaptureTarget, ShadowCapturePlan
from src.evaluation.shared_pa_forward_collector import RawPregameResponse
from src.evaluation.shared_pa_projected_opportunity_candidate_v2 import (
    ProjectedOpportunityCandidateV2Error,
    ProjectedOpportunityProtocolV2,
    build_projected_opportunity_candidate_v2,
    replay_protocol_v2,
)
from src.evaluation.shared_pa_projected_opportunity_evidence_v2 import (
    ProjectedOpportunityEvidenceV2Error,
    RawDateBoundedStatsResponse,
    expected_stats_requests,
    replay_date_bounded_counts,
    replay_projected_context_v2,
)
from src.evaluation.shared_pa_projected_opportunity_release_v2 import (
    ProjectedOpportunityReleaseV2Error,
    resolve_release_identity_v2,
)


class ProjectedOpportunityRunnerV2Error(ValueError):
    """The v2 side population cannot be proved without substitution."""


SCHEMA_VERSION = "shared-pa-projected-opportunity-side-bundle-v2"


def _utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise ProjectedOpportunityRunnerV2Error(f"{label} must be timezone-aware ISO-8601")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProjectedOpportunityRunnerV2Error(
            f"{label} must be timezone-aware ISO-8601"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProjectedOpportunityRunnerV2Error(f"{label} must be timezone-aware ISO-8601")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class FailedStatsAttemptV2:
    player_id: int
    request_urls: tuple[str, ...]
    attempted_at_utc: str
    failed_at_utc: str
    error_class: str
    attempt_sha256: str

    def __post_init__(self) -> None:
        _positive_int(self.player_id, "failed stats player_id")
        if not isinstance(self.request_urls, tuple) or not self.request_urls or any(
            not isinstance(value, str) or not value for value in self.request_urls
        ):
            raise ProjectedOpportunityRunnerV2Error("failed stats request URLs are missing")
        attempted = _utc(self.attempted_at_utc, "failed stats attempted_at_utc")
        failed = _utc(self.failed_at_utc, "failed stats failed_at_utc")
        if failed < attempted:
            raise ProjectedOpportunityRunnerV2Error("failed stats attempt ends before it starts")
        if self.error_class not in {"network_error", "timeout", "http_error", "transport_error"}:
            raise ProjectedOpportunityRunnerV2Error("failed stats error class is not predeclared")
        if self.attempt_sha256 != sha256_value(self.unsigned()):
            raise ProjectedOpportunityRunnerV2Error("failed stats attempt hash differs")

    def unsigned(self) -> dict[str, Any]:
        return {
            "schema_version": "official-mlb-stats-failed-attempt-v2",
            "player_id": self.player_id,
            "request_urls": list(self.request_urls),
            "attempted_at_utc": self.attempted_at_utc,
            "failed_at_utc": self.failed_at_utc,
            "error_class": self.error_class,
        }

    def receipt(self) -> dict[str, Any]:
        return {**self.unsigned(), "attempt_sha256": self.attempt_sha256}

    @classmethod
    def capture(
        cls, *, player_id: int, request_urls: Sequence[str], attempted_at_utc: str,
        failed_at_utc: str, error_class: str,
    ) -> "FailedStatsAttemptV2":
        unsigned = {
            "schema_version": "official-mlb-stats-failed-attempt-v2",
            "player_id": player_id,
            "request_urls": list(request_urls),
            "attempted_at_utc": attempted_at_utc,
            "failed_at_utc": failed_at_utc,
            "error_class": error_class,
        }
        return cls(
            player_id=player_id,
            request_urls=tuple(request_urls),
            attempted_at_utc=attempted_at_utc,
            failed_at_utc=failed_at_utc,
            error_class=error_class,
            attempt_sha256=sha256_value(unsigned),
        )


@dataclass(frozen=True)
class DateBoundedStatsBatchV2:
    responses: Mapping[int, Sequence[RawDateBoundedStatsResponse]]
    errors: Mapping[int, FailedStatsAttemptV2]

    def __post_init__(self) -> None:
        response_ids = set(self.responses)
        error_ids = set(self.errors)
        if response_ids & error_ids:
            raise ProjectedOpportunityRunnerV2Error(
                "stats response and error identities overlap"
            )
        for player_id, responses in self.responses.items():
            _positive_int(player_id, "stats response player_id")
            if not isinstance(responses, (list, tuple)) or not responses or any(
                not isinstance(response, RawDateBoundedStatsResponse) for response in responses
            ):
                raise ProjectedOpportunityRunnerV2Error(
                    "stats responses must retain date-bounded raw bytes"
                )
        for player_id, attempt in self.errors.items():
            _positive_int(player_id, "stats error player_id")
            if not isinstance(attempt, FailedStatsAttemptV2) or attempt.player_id != player_id:
                raise ProjectedOpportunityRunnerV2Error(
                    "stats errors require a retained failed-attempt receipt"
                )


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ProjectedOpportunityRunnerV2Error(f"{label} must be a positive integer")
    return value


def _abstention(
    *,
    plan: ShadowCapturePlan,
    target: CaptureTarget,
    projection: Mapping[str, Any],
    protocol: ProjectedOpportunityProtocolV2,
    side: str,
    team_id: int,
    player_id: int,
    observed_at_utc: str,
    reason: str,
    source_attempt_sha256: str | None,
    source_attempt_receipt: Mapping[str, Any] | None,
    stats_evidence_sha256s: Sequence[str],
    release_identity: Mapping[str, Any],
) -> dict[str, Any]:
    unsigned = {
        "schema_version": "shared-pa-projected-opportunity-abstention-v2",
        "terminal_state": "candidate_abstention",
        "research_only": True,
        "betting_authorized": False,
        "promotion_eligible": False,
        "probability_substituted": False,
        "candidate_id": "shared_pa_projected_opportunity_eb200_v2",
        "candidate_protocol_sha256": protocol.sha256,
        "candidate_protocol_status": protocol.value["status"],
        "source_manifest_sha256": release_identity["source_manifest_sha256"],
        "source_release_commit": release_identity["source_commit"],
        "runtime_release_receipt_sha256": release_identity[
            "runtime_release_receipt_sha256"
        ],
        "plan_sha256": plan.plan_sha256,
        "target_id": target.target_id,
        "official_game_date": target.official_game_date,
        "mlb_game_pk": target.mlb_game_pk,
        "team_id": team_id,
        "side": side,
        "player_id": player_id,
        "target_horizon_utc": target.entry_target_at_utc,
        "observed_at_utc": observed_at_utc,
        "projected_lineup_content_sha256": projection["projection_content_sha256"],
        "reason": reason,
        "source_attempt_sha256": source_attempt_sha256,
        "source_attempt_receipt": (
            dict(source_attempt_receipt) if source_attempt_receipt is not None else None
        ),
        "stats_evidence_sha256s": list(stats_evidence_sha256s),
    }
    return {**unsigned, "abstention_sha256": sha256_value(unsigned)}


def build_side_candidate_bundle_v2(
    *,
    root: Any,
    protocol: ProjectedOpportunityProtocolV2,
    plan: ShadowCapturePlan,
    target: CaptureTarget,
    side: str,
    team_id: int,
    schedule_response: RawPregameResponse,
    active_roster_receipt: Mapping[str, Any],
    active_roster_raw: bytes,
    history_records: Sequence[Mapping[str, Any]],
    history_raw_by_sha256: Mapping[str, bytes],
    history_coverage: Mapping[str, Any],
    history_schedule_raw_by_sha256: Mapping[str, bytes],
    opportunity_snapshot: Mapping[str, Any],
    projected_lineup_record: Mapping[str, Any],
    stats_batch: DateBoundedStatsBatchV2,
    prediction_generated_at_utc: str,
    loaded_forward_contract: Mapping[str, Any],
    runtime_release_receipt_path: Any | None = None,
) -> dict[str, Any]:
    """Build one complete v2 side bundle from retained pregame evidence."""
    if not isinstance(stats_batch, DateBoundedStatsBatchV2):
        raise ProjectedOpportunityRunnerV2Error("date-bounded v2 stats batch is required")
    try:
        verified_protocol = replay_protocol_v2(root=root, protocol=protocol)
        release_identity = resolve_release_identity_v2(
            root=root,
            protocol_status=str(verified_protocol.value["status"]),
            protocol_sha256=verified_protocol.sha256,
            protocol_source_path=verified_protocol.source_path,
            runtime_release_receipt_path=runtime_release_receipt_path,
        )
        context = replay_projected_context_v2(
            root=root,
            plan=plan,
            target=target,
            side=side,
            team_id=team_id,
            schedule_response=schedule_response,
            active_roster_receipt=active_roster_receipt,
            active_roster_raw=active_roster_raw,
            history_records=history_records,
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
    except (
        ProjectedOpportunityCandidateV2Error,
        ProjectedOpportunityEvidenceV2Error,
        ProjectedOpportunityReleaseV2Error,
        ValueError,
    ) as exc:
        raise ProjectedOpportunityRunnerV2Error(
            "shared v2 plan, chronology, identity, or projection replay failed"
        ) from exc
    support = sorted(
        int(player_id)
        for player_id, probability in marginals["start_probability"].items()
        if float(probability) > 0.0
    )
    if not support:
        raise ProjectedOpportunityRunnerV2Error("projected start support is empty")
    accounted = set(stats_batch.responses) | set(stats_batch.errors)
    if accounted != set(support):
        raise ProjectedOpportunityRunnerV2Error(
            "stats batch must account for every and only projected-support player"
        )

    records: list[dict[str, Any]] = []
    abstentions: list[dict[str, Any]] = []
    for player_id in support:
        if player_id in stats_batch.errors:
            attempt = stats_batch.errors[player_id]
            expected_urls, _ = expected_stats_requests(
                player_id=player_id, target_date=context["target_date"]
            )
            if list(attempt.request_urls) != expected_urls or any(
                value > context["generated"] or value > context["horizon"]
                for value in (
                    _utc(attempt.attempted_at_utc, "failed stats attempted_at_utc"),
                    _utc(attempt.failed_at_utc, "failed stats failed_at_utc"),
                )
            ):
                raise ProjectedOpportunityRunnerV2Error(
                    "failed stats attempt identity or chronology differs"
                )
            abstentions.append(_abstention(
                plan=plan,
                target=target,
                projection=context["replayed_projection"],
                protocol=verified_protocol,
                side=side,
                team_id=team_id,
                player_id=player_id,
                observed_at_utc=prediction_generated_at_utc,
                reason="official_date_bounded_stats_source_error",
                source_attempt_sha256=attempt.attempt_sha256,
                source_attempt_receipt=attempt.receipt(),
                stats_evidence_sha256s=[],
                release_identity=release_identity,
            ))
            continue
        responses = stats_batch.responses[player_id]
        if any(
            _utc(response.request_sent_at_utc, "stats request_sent_at_utc")
            > context["generated"]
            or _utc(response.received_at_utc, "stats received_at_utc")
            > context["generated"]
            for response in responses
        ):
            raise ProjectedOpportunityRunnerV2Error(
                "retained stats request or response postdates prediction generation"
            )
        try:
            replay_date_bounded_counts(
                responses,
                player_id=player_id,
                target_date=context["target_date"],
                horizon=context["horizon"],
            )
        except ProjectedOpportunityEvidenceV2Error:
            abstentions.append(_abstention(
                plan=plan,
                target=target,
                projection=context["replayed_projection"],
                protocol=verified_protocol,
                side=side,
                team_id=team_id,
                player_id=player_id,
                observed_at_utc=prediction_generated_at_utc,
                reason="invalid_date_bounded_stats_evidence",
                source_attempt_sha256=None,
                source_attempt_receipt=None,
                stats_evidence_sha256s=[
                    response.transport_receipt_sha256 for response in responses
                ],
                release_identity=release_identity,
            ))
            continue
        try:
            records.append(build_projected_opportunity_candidate_v2(
                root=root,
                protocol=verified_protocol,
                plan=plan,
                target=target,
                side=side,
                team_id=team_id,
                schedule_response=schedule_response,
                active_roster_receipt=active_roster_receipt,
                active_roster_raw=active_roster_raw,
                history_records=history_records,
                history_raw_by_sha256=history_raw_by_sha256,
                history_coverage=history_coverage,
                history_schedule_raw_by_sha256=history_schedule_raw_by_sha256,
                opportunity_snapshot=opportunity_snapshot,
                projected_lineup_record=projected_lineup_record,
                player_id=player_id,
                stats_responses=responses,
                prediction_generated_at_utc=prediction_generated_at_utc,
                loaded_forward_contract=loaded_forward_contract,
                runtime_release_receipt_path=runtime_release_receipt_path,
            ))
        except (ProjectedOpportunityCandidateV2Error, ProjectedOpportunityEvidenceV2Error) as exc:
            raise ProjectedOpportunityRunnerV2Error(
                "v2 candidate replay failed after shared evidence validation"
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
        "candidate_id": "shared_pa_projected_opportunity_eb200_v2",
        "candidate_protocol_sha256": verified_protocol.sha256,
        "candidate_protocol_status": verified_protocol.value["status"],
        "source_manifest_sha256": release_identity["source_manifest_sha256"],
        "source_release_commit": release_identity["source_commit"],
        "runtime_release_receipt_sha256": release_identity[
            "runtime_release_receipt_sha256"
        ],
        "plan_sha256": plan.plan_sha256,
        "target_id": target.target_id,
        "official_game_date": target.official_game_date,
        "mlb_game_pk": target.mlb_game_pk,
        "team_id": team_id,
        "side": side,
        "target_horizon_utc": target.entry_target_at_utc,
        "prediction_generated_at_utc": prediction_generated_at_utc,
        "projected_lineup_content_sha256": context["replayed_projection"][
            "projection_content_sha256"
        ],
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
        raise ProjectedOpportunityRunnerV2Error("candidate side accounting is incomplete")
    return {**unsigned, "side_bundle_sha256": sha256_value(unsigned)}
