"""Receipt-replayed projected-opportunity candidate, version 2.

The only numerical change from the locked shared-PA control remains PA
opportunity.  Unlike v1, this entry point constructs its own v2 evidence
envelope from retained bytes and typed plan objects before calculating a
probability; a caller cannot supply a self-hashed projection or editable T-4.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from src.evaluation.projected_lineup_contract import load_contract, sha256_value, validate_projection
from src.evaluation.shadow_capture_plan import CaptureTarget, ShadowCapturePlan
from src.evaluation.shared_pa_forward_collector import RawPregameResponse
from src.evaluation.shared_pa_forward_evidence import (
    CONTROL_ID,
    PA_VOLUME_ARTIFACT_SHA256,
    ForwardPAVolumeArtifact,
    derive_market_distributions,
    empirical_bayes_pa_probability,
    pa_distribution_sha256,
)
from src.evaluation.shared_pa_projected_opportunity_candidate import (
    load_projected_opportunity_protocol,
    mix_projected_pa_distribution,
)
from src.evaluation.shared_pa_projected_opportunity_evidence_v2 import (
    RawDateBoundedStatsResponse,
    build_evidence_envelope_v2,
)
from src.evaluation.shared_pa_projected_opportunity_release_v2 import (
    ProjectedOpportunityReleaseV2Error,
    resolve_release_identity_v2,
)


class ProjectedOpportunityCandidateV2Error(ValueError):
    pass


SCHEMA_VERSION = "shared-pa-projected-opportunity-player-v2"
CANDIDATE_ID = "shared_pa_projected_opportunity_eb200_v2"
FIRST_CONFIRMATION_DATE = date(2026, 9, 17)
LAST_CONFIRMATION_DATE = date(2026, 9, 27)
RESERVED_FIRST = date(2026, 7, 23)
RESERVED_LAST = date(2026, 9, 16)
V1_EVALUATION_PROTOCOL_PATH = "config/shared_pa_projected_opportunity_forward_v1.json"
V1_EVALUATION_PROTOCOL_SHA256 = "43f2d79cd440750dad65c57126f5077d3d0558093b2cbaa8bc65254aadf6b966"
V2_RUNTIME_SOURCE_CLOSURE = frozenset({
    "src/evaluation/projected_lineup_contract.py",
    "src/evaluation/projected_lineup_contract_v2.py",
    "src/evaluation/projected_lineup_empirical_joint.py",
    "src/evaluation/projected_lineup_history.py",
    "src/evaluation/projected_lineup_official_roster.py",
    "src/evaluation/prospective_batter_opportunity.py",
    "src/evaluation/shadow_capture_plan.py",
    "src/evaluation/shared_pa_batter_skill.py",
    "src/evaluation/shared_pa_forward_collector.py",
    "src/evaluation/shared_pa_forward_evidence.py",
    "src/evaluation/shared_pa_projected_opportunity_candidate.py",
    "src/evaluation/shared_pa_projected_opportunity_candidate_v2.py",
    "src/evaluation/shared_pa_projected_opportunity_evidence_v2.py",
    "src/evaluation/shared_pa_projected_opportunity_release_v2.py",
    "src/evaluation/shared_pa_projected_opportunity_runner_v2.py",
})


def validate_prediction_release_chronology_v2(
    *, release_identity: Mapping[str, Any], prediction_generated_at_utc: str,
    target_horizon_utc: str,
) -> None:
    """Require an exact published release before prediction and prediction by T-4."""
    if release_identity.get("confirmation_release_eligible") is not True:
        return
    created_raw = release_identity.get("release_created_at_utc")
    try:
        created = datetime.fromisoformat(str(created_raw).replace("Z", "+00:00"))
        generated = datetime.fromisoformat(
            str(prediction_generated_at_utc).replace("Z", "+00:00")
        )
        horizon = datetime.fromisoformat(str(target_horizon_utc).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ProjectedOpportunityCandidateV2Error(
            "release-to-prediction chronology is invalid"
        ) from exc
    if any(value.tzinfo is None or value.utcoffset() is None for value in (created, generated, horizon)):
        raise ProjectedOpportunityCandidateV2Error(
            "release-to-prediction chronology contains a naive timestamp"
        )
    if created > generated or generated > horizon:
        raise ProjectedOpportunityCandidateV2Error(
            "exact release, prediction, and T-4 chronology is invalid"
        )


def validate_inherited_evaluation_contract_v2(value: Mapping[str, Any]) -> None:
    """Require the exact predeclared v1 market-by-market adjudication gates."""
    expected_baselines = {
        "all_markets": [
            "league_rate_2023_same_pa_volume",
            "time_safe_player_empirical_bayes_with_pooled_projected_pa_volume",
            "frozen_production_simulator_when_exact_pregame_probability_is_available",
        ],
        "home_runs_over_0_5_additional": [
            "valid_two_sided_market_implied_probability_when_receipt_verified"
        ],
        "missing_required_baseline": "gate_unassessable_no_promotion",
    }
    expected_evaluation = {
        "markets_scored_separately": True,
        "one_market_cannot_rescue_another": True,
        "probability_metrics": [
            "brier", "log_loss", "absolute_bias", "equal_frequency_decile_ece", "auc"
        ],
        "hr_tail_metric": "candidate-fixed upper-decile absolute calibration error",
        "distribution_metrics": ["ranked_probability_score"],
        "coverage": [
            "planned_rows", "predicted_rows", "gradeable_rows", "explicit_terminal_abstentions"
        ],
        "uncertainty": {
            "unit": "official_date_cluster",
            "method": "paired_percentile_bootstrap",
            "confidence": 0.95,
            "resamples": 10000,
            "seed": 20260722,
        },
        "material_proper_score_fraction": 0.01,
        "point_and_upper_confidence_bound_must_clear": True,
        "post_outcome_threshold_changes_allowed": False,
    }
    expected_windows = {
        "reserved_smoke": {
            "first_official_date": "2026-07-23",
            "last_official_date": "2026-09-16",
            "confirmation_eligible": False,
            "outcome_scoring_authorized": False,
        },
        "first_fresh_confirmation_date": "2026-09-17",
        "first_fresh_confirmation_last_official_date": "2026-09-27",
        "first_fresh_window_limitation": (
            "eleven calendar dates at the end of the 2026 regular season; insufficient evidence "
            "cannot be repaired by including postseason, extending backward, or lowering gates"
        ),
        "independent_forward_replication_after_first_window": True,
        "first_candidate_record_rule": (
            "target_horizon_utc must occur after an exact release-bound append-only evidence scope is published"
        ),
    }
    expected_settlement = {
        "pregame_predictions_immutable_before_outcomes": True,
        "official_final_lineup_pa_hits_hr_total_bases_captured_after_game": True,
        "settlement_requires_verified_market_line_and_rules": True,
        "valid_price_requires_two_sided_same_book_same_market_same_line_same_observation": True,
        "predictions_may_be_displayed_immediately": True,
        "display_label": "RESEARCH_ONLY_NOT_BETTING_AUTHORIZED",
    }
    if not isinstance(value, Mapping):
        raise ProjectedOpportunityCandidateV2Error("inherited v1 evaluation contract is missing")
    if (
        value.get("schema_version") != "shared-pa-projected-opportunity-protocol-v1"
        or value.get("candidate_id") != "shared_pa_projected_opportunity_eb200_v1"
        or value.get("markets") != ["hits", "home_runs_over_0_5", "total_bases"]
        or value.get("baselines") != expected_baselines
        or value.get("evaluation") != expected_evaluation
        or value.get("prospective_windows") != expected_windows
        or value.get("outcome_and_settlement") != expected_settlement
    ):
        raise ProjectedOpportunityCandidateV2Error(
            "inherited v1 separate-market evaluation, baseline, uncertainty, or window gate changed"
        )


@dataclass(frozen=True)
class ProjectedOpportunityProtocolV2:
    value: Mapping[str, Any]
    sha256: str
    root: Path
    source_path: Path


def load_protocol_v2(*, root: Path, path: Path) -> ProjectedOpportunityProtocolV2:
    repository = root.resolve()
    source = path.resolve()
    if repository not in source.parents:
        raise ProjectedOpportunityCandidateV2Error("v2 protocol must be inside project root")
    raw = source.read_bytes()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProjectedOpportunityCandidateV2Error("v2 protocol is invalid JSON") from exc
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != "shared-pa-projected-opportunity-protocol-v2"
        or value.get("candidate_id") != CANDIDATE_ID
        or value.get("status") not in {
            "RESEARCH_ONLY_EXACT_BYTES_RELEASE_PENDING",
            "EXACT_RELEASE_PUBLISHED",
        }
    ):
        raise ProjectedOpportunityCandidateV2Error("v2 protocol identity changed")
    if value.get("confirmation_window") != {
        "first_official_date": FIRST_CONFIRMATION_DATE.isoformat(),
        "last_official_date": LAST_CONFIRMATION_DATE.isoformat(),
        "postseason_eligible": False,
    }:
        raise ProjectedOpportunityCandidateV2Error("v2 confirmation window changed")
    if value.get("evaluation_contract") != {
        "path": V1_EVALUATION_PROTOCOL_PATH,
        "sha256": V1_EVALUATION_PROTOCOL_SHA256,
        "inheritance": "all_market_baseline_metric_uncertainty_coverage_and_window_gates",
    }:
        raise ProjectedOpportunityCandidateV2Error("v2 inherited evaluation contract binding changed")
    if value.get("release_contract") != {
        "source_manifest_path": (
            "reports/shared_pa_projected_opportunity_candidate_v2_hash_manifest.json"
        ),
        "runtime_release_receipt_schema": (
            "shared-pa-projected-opportunity-runtime-release-v2"
        ),
        "published_status_requires_external_runtime_receipt": True,
        "exact_clean_git_head_required": True,
    }:
        raise ProjectedOpportunityCandidateV2Error("v2 exact release contract changed")
    if value.get("decision_horizon") != "T-4h" or value.get("stats_contract") != {
        "source": "official_mlb_statsapi_dated_game_log_segmented_v2",
        "start_date": "target season January 1",
        "end_date": "calendar date strictly before target official date",
        "may_2026_segments": "skip 2026-05-01 through 2026-05-31 entirely",
        "required_semantic_rows": (
            "each split carries an in-range canonical date and unique official game id"
        ),
        "transport_receipt_required": True,
        "same_day_outcomes_allowed": False,
        "season_or_cutoff_fallback_allowed": False,
    }:
        raise ProjectedOpportunityCandidateV2Error("v2 stats chronology contract changed")
    if value.get("numerical_contract") != {
        "only_changed_quantity": "game_pa_distribution",
        "per_pa_control": "empirical_bayes_player_rate_pa_200_2023_control_v1",
        "pa_volume": "pa_volume_2023_only_v1",
        "pitcher_features": "excluded_batter_only",
        "manual_coefficients": False,
    }:
        raise ProjectedOpportunityCandidateV2Error("v2 numerical contract changed")
    if value.get("protected_boundaries") != {
        "may_2026_access_allowed": False,
        "outcomes_allowed": False,
        "prices_allowed_in_probability": False,
        "missed_receipt_backfill_allowed": False,
        "production_probability_changes_allowed": False,
        "betting_authorized": False,
    }:
        raise ProjectedOpportunityCandidateV2Error("v2 protected boundary changed")
    bindings = value.get("immutable_bindings")
    if not isinstance(bindings, list) or not bindings:
        raise ProjectedOpportunityCandidateV2Error("v2 immutable bindings are missing")
    seen: set[str] = set()
    bound_payloads: dict[str, dict[str, Any]] = {}
    for binding in bindings:
        if not isinstance(binding, dict) or set(binding) != {"path", "sha256"}:
            raise ProjectedOpportunityCandidateV2Error("v2 immutable binding schema changed")
        relative = binding["path"]
        expected = binding["sha256"]
        if not isinstance(relative, str) or relative in seen or not isinstance(expected, str):
            raise ProjectedOpportunityCandidateV2Error("v2 immutable binding identity changed")
        target = (repository / relative).resolve()
        if repository not in target.parents or not target.is_file():
            raise ProjectedOpportunityCandidateV2Error("v2 immutable binding target is invalid")
        if hashlib.sha256(target.read_bytes()).hexdigest() != expected:
            raise ProjectedOpportunityCandidateV2Error(f"v2 immutable binding differs: {relative}")
        if relative == V1_EVALUATION_PROTOCOL_PATH:
            try:
                inherited = json.loads(target.read_bytes())
            except json.JSONDecodeError as exc:
                raise ProjectedOpportunityCandidateV2Error(
                    "inherited v1 evaluation contract is invalid JSON"
                ) from exc
            if not isinstance(inherited, dict):
                raise ProjectedOpportunityCandidateV2Error(
                    "inherited v1 evaluation contract is not an object"
                )
            bound_payloads[relative] = inherited
        seen.add(relative)
    required = {
        "config/projected_lineup_contract_v1.json",
        V1_EVALUATION_PROTOCOL_PATH,
        "config/shared_pa_forward_evidence_contract_v1.json",
        "data/analysis/system_integrity_v2/pa_volume_chronology_v1/pa_distribution_fit_2023.json",
        *V2_RUNTIME_SOURCE_CLOSURE,
        "tests/test_shared_pa_projected_opportunity_candidate_v2.py",
        "tests/test_shared_pa_projected_opportunity_runner_v2.py",
        "tests/test_prospective_batter_opportunity.py",
    }
    if seen != required:
        raise ProjectedOpportunityCandidateV2Error("v2 immutable binding set changed")
    validate_inherited_evaluation_contract_v2(bound_payloads.get(V1_EVALUATION_PROTOCOL_PATH, {}))
    try:
        inherited = load_projected_opportunity_protocol(
            root=repository, path=repository / V1_EVALUATION_PROTOCOL_PATH
        )
    except ValueError as exc:
        raise ProjectedOpportunityCandidateV2Error(
            "inherited v1 runtime binding failed exact recursive validation"
        ) from exc
    if (
        inherited.sha256 != V1_EVALUATION_PROTOCOL_SHA256
        or inherited.value != bound_payloads[V1_EVALUATION_PROTOCOL_PATH]
    ):
        raise ProjectedOpportunityCandidateV2Error(
            "inherited v1 runtime protocol differs from exact replay"
        )
    return ProjectedOpportunityProtocolV2(
        value=value,
        sha256=hashlib.sha256(raw).hexdigest(),
        root=repository,
        source_path=source,
    )


def replay_protocol_v2(
    *, root: Path, protocol: ProjectedOpportunityProtocolV2
) -> ProjectedOpportunityProtocolV2:
    """Reload the protocol and every binding from the retained source path."""
    if not isinstance(protocol, ProjectedOpportunityProtocolV2):
        raise ProjectedOpportunityCandidateV2Error("hash-verified v2 protocol is required")
    repository = root.resolve()
    if protocol.root.resolve() != repository:
        raise ProjectedOpportunityCandidateV2Error("v2 protocol project root differs")
    replayed = load_protocol_v2(root=repository, path=protocol.source_path)
    if replayed.sha256 != protocol.sha256 or replayed.value != protocol.value:
        raise ProjectedOpportunityCandidateV2Error("v2 protocol differs from retained-byte replay")
    return replayed


def _candidate_hash(record: Mapping[str, Any]) -> str:
    unsigned = dict(record)
    unsigned.pop("candidate_record_sha256", None)
    return sha256_value(unsigned)


def classify_evidence_date(value: date) -> tuple[str, bool]:
    """Apply the closed, regular-season-only prospective window."""
    if RESERVED_FIRST <= value <= RESERVED_LAST:
        return "nonqualifying_reserved_window_operational_smoke", False
    if FIRST_CONFIRMATION_DATE <= value <= LAST_CONFIRMATION_DATE:
        return "future_untouched_candidate_observation", True
    return "nonqualifying_outside_locked_confirmation_window", False


def build_projected_opportunity_candidate_v2(
    *,
    root: Path,
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
    player_id: int,
    stats_responses: Sequence[RawDateBoundedStatsResponse],
    prediction_generated_at_utc: str,
    loaded_forward_contract: Mapping[str, Any],
    runtime_release_receipt_path: Path | None = None,
) -> dict[str, Any]:
    verified_protocol = replay_protocol_v2(root=root, protocol=protocol)
    try:
        release_identity = resolve_release_identity_v2(
            root=root,
            protocol_status=str(verified_protocol.value["status"]),
            protocol_sha256=verified_protocol.sha256,
            protocol_source_path=verified_protocol.source_path,
            runtime_release_receipt_path=runtime_release_receipt_path,
            decision_time_utc=prediction_generated_at_utc,
        )
    except ProjectedOpportunityReleaseV2Error as exc:
        raise ProjectedOpportunityCandidateV2Error(
            "v2 exact runtime release identity failed"
        ) from exc
    envelope = build_evidence_envelope_v2(
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
        player_id=player_id,
        stats_responses=stats_responses,
        prediction_generated_at_utc=prediction_generated_at_utc,
    )
    control = loaded_forward_contract.get("control")
    prior = loaded_forward_contract.get("league_prior_probability")
    pa_volume = loaded_forward_contract.get("pa_volume_artifact")
    if not isinstance(control, Mapping) or not isinstance(prior, Mapping) or not isinstance(pa_volume, ForwardPAVolumeArtifact):
        raise ProjectedOpportunityCandidateV2Error("locked forward control bundle is incomplete")
    if (
        loaded_forward_contract.get("pa_volume_sha256") != PA_VOLUME_ARTIFACT_SHA256
        or float(control.get("prior_strength_pa", -1)) != 200.0
    ):
        raise ProjectedOpportunityCandidateV2Error("locked control identity changed")
    per_pa = empirical_bayes_pa_probability(
        counts=envelope["stats_counts"],
        league_prior=prior,
        prior_strength_pa=200.0,
    )
    lineup_contract = load_contract(root / "config/projected_lineup_contract_v1.json")
    marginals = validate_projection(projected_lineup_record, lineup_contract)
    support, mass, start_probability, slot_probability = mix_projected_pa_distribution(
        player_id=player_id,
        projection_marginals=marginals,
        pa_volume=pa_volume,
    )
    markets = derive_market_distributions(
        per_pa_probability=per_pa, support=support, mass=mass
    )
    baseline_support = sorted(pa_volume.pooled)
    baseline_mass = [pa_volume.pooled[value] for value in baseline_support]
    baseline = derive_market_distributions(
        per_pa_probability=per_pa,
        support=baseline_support,
        mass=baseline_mass,
    )
    official_date = date.fromisoformat(envelope["official_game_date"])
    validate_prediction_release_chronology_v2(
        release_identity=release_identity,
        prediction_generated_at_utc=envelope["prediction_generated_at_utc"],
        target_horizon_utc=envelope["target_horizon_utc"],
    )
    evidence_class, confirmation_eligible = classify_evidence_date(official_date)
    release_status = str(verified_protocol.value["status"])
    if release_identity["confirmation_release_eligible"] is not True:
        evidence_class = "nonqualifying_exact_release_pending"
        confirmation_eligible = False
    unsigned = {
        "schema_version": SCHEMA_VERSION,
        "candidate_id": CANDIDATE_ID,
        "terminal_state": "candidate_complete",
        "research_only": True,
        "betting_authorized": False,
        "promotion_eligible": False,
        "candidate_protocol_sha256": verified_protocol.sha256,
        "candidate_protocol_status": release_status,
        "source_manifest_sha256": release_identity["source_manifest_sha256"],
        "source_release_commit": release_identity["source_commit"],
        "release_created_at_utc": release_identity["release_created_at_utc"],
        "runtime_release_receipt_sha256": release_identity[
            "runtime_release_receipt_sha256"
        ],
        "evidence_envelope_sha256": envelope["evidence_envelope_sha256"],
        "evidence_class": evidence_class,
        "confirmation_eligible": confirmation_eligible,
        "plan_sha256": envelope["plan_sha256"],
        "target_id": envelope["target_id"],
        "official_game_date": envelope["official_game_date"],
        "official_start_utc": envelope["official_start_utc"],
        "target_horizon_utc": envelope["target_horizon_utc"],
        "prediction_generated_at_utc": envelope["prediction_generated_at_utc"],
        "mlb_game_pk": envelope["mlb_game_pk"],
        "side": envelope["side"],
        "team_id": envelope["team_id"],
        "player_id": envelope["player_id"],
        "stats_source": envelope["stats_source"],
        "stats_cutoff_date": envelope["stats_cutoff_date"],
        "stats_raw_sha256s": envelope["stats_raw_sha256s"],
        "stats_transport_receipt_sha256s": envelope["stats_transport_receipt_sha256s"],
        "per_pa_control_id": CONTROL_ID,
        "per_pa_control_config_sha256": loaded_forward_contract["control_sha256"],
        "pa_volume_artifact_sha256": PA_VOLUME_ARTIFACT_SHA256,
        "projected_start_probability": start_probability,
        "projected_slot_probability": slot_probability,
        "candidate_pa_support": support,
        "candidate_pa_mass": mass,
        "candidate_pa_distribution_sha256": pa_distribution_sha256(support=support, mass=mass),
        "per_pa_probability": per_pa,
        "baseline_market_distributions": baseline,
        "candidate_market_distributions": markets,
        "pitcher_block_status": "excluded_batter_only",
        "policy_status": "none_research_probability_only",
        "candidate_code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    return {**unsigned, "candidate_record_sha256": _candidate_hash(unsigned)}


def replay_and_validate_candidate_record_v2(
    *,
    record: Mapping[str, Any],
    root: Path,
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
    player_id: int,
    stats_responses: Sequence[RawDateBoundedStatsResponse],
    prediction_generated_at_utc: str,
    loaded_forward_contract: Mapping[str, Any],
    runtime_release_receipt_path: Path | None = None,
) -> dict[str, Any]:
    """Recompute a candidate from retained evidence and require exact equality."""
    supplied = dict(record)
    if (
        supplied.get("schema_version") != SCHEMA_VERSION
        or supplied.get("candidate_id") != CANDIDATE_ID
        or supplied.get("candidate_record_sha256") != _candidate_hash(supplied)
    ):
        raise ProjectedOpportunityCandidateV2Error("candidate record identity or hash differs")
    replayed = build_projected_opportunity_candidate_v2(
        root=root,
        protocol=protocol,
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
        stats_responses=stats_responses,
        prediction_generated_at_utc=prediction_generated_at_utc,
        loaded_forward_contract=loaded_forward_contract,
        runtime_release_receipt_path=runtime_release_receipt_path,
    )
    if replayed != supplied:
        raise ProjectedOpportunityCandidateV2Error(
            "candidate record differs from retained-evidence replay"
        )
    return replayed
