"""Projected start/slot opportunity challenger for the locked shared-PA control.

The only numerical change is the game-level PA distribution.  The batter's
per-PA empirical-Bayes probabilities, the 2023 PA-volume artifact, and the
coherent Hits/HR/Total-Bases derivation remain unchanged.  A player receives
zero PA when absent from a projected scenario; conditional on a projected
slot, the corresponding locked 2023 slot distribution is consumed.

Outputs dated inside the already reserved 2026-07-23..2026-09-16 shared-PA
window are permanently non-qualifying smoke.  They may be displayed before
games, but cannot be retrofitted into that experiment or used for promotion.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from src.evaluation.projected_lineup_contract import ProjectedLineupContractError
from src.evaluation.projected_lineup_contract_v2 import validate_projection_v2
from src.evaluation.projected_lineup_empirical_joint import CANDIDATE_ID as LINEUP_CANDIDATE_ID
from src.evaluation.shared_pa_batter_skill import (
    BatterSkillSnapshotError,
    validate_batter_skill_snapshot,
)
from src.evaluation.shared_pa_forward_evidence import (
    CONTROL_ID,
    PA_VOLUME_ARTIFACT_SHA256,
    ForwardPAVolumeArtifact,
    derive_market_distributions,
    pa_distribution_sha256,
    sha256_value,
)


class ProjectedOpportunityCandidateError(ValueError):
    """Required prospective evidence is missing, late, contradictory, or unbound."""


SCHEMA_VERSION = "shared-pa-projected-opportunity-player-v1"
CANDIDATE_ID = "shared_pa_projected_opportunity_eb200_v1"
RESERVED_WINDOW_FIRST = date(2026, 7, 23)
RESERVED_WINDOW_LAST = date(2026, 9, 16)
FIRST_CONFIRMATION_DATE = date(2026, 9, 17)


@dataclass(frozen=True)
class ProjectedOpportunityProtocol:
    """A hash-verified predeclared protocol and its byte identity."""

    value: Mapping[str, Any]
    sha256: str


def load_projected_opportunity_protocol(
    *, root: Path, path: Path
) -> ProjectedOpportunityProtocol:
    """Load the protocol only when every bound source/data byte still matches."""
    project_root = root.resolve()
    protocol_path = path.resolve()
    if project_root not in protocol_path.parents:
        raise ProjectedOpportunityCandidateError("candidate protocol must be inside project root")
    raw = protocol_path.read_bytes()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProjectedOpportunityCandidateError("candidate protocol is invalid JSON") from exc
    if not isinstance(value, dict):
        raise ProjectedOpportunityCandidateError("candidate protocol must be an object")
    if (
        value.get("schema_version") != "shared-pa-projected-opportunity-protocol-v1"
        or value.get("status") != "PREDECLARED_RESEARCH_ONLY_PENDING_EXACT_RELEASE"
        or value.get("candidate_id") != CANDIDATE_ID
    ):
        raise ProjectedOpportunityCandidateError("candidate protocol identity changed")
    if value.get("markets") != ["hits", "home_runs_over_0_5", "total_bases"]:
        raise ProjectedOpportunityCandidateError("candidate market declaration changed")
    windows = value.get("prospective_windows")
    if not isinstance(windows, dict) or windows.get("reserved_smoke") != {
        "first_official_date": RESERVED_WINDOW_FIRST.isoformat(),
        "last_official_date": RESERVED_WINDOW_LAST.isoformat(),
        "confirmation_eligible": False,
        "outcome_scoring_authorized": False,
    } or windows.get("first_fresh_confirmation_date") != FIRST_CONFIRMATION_DATE.isoformat():
        raise ProjectedOpportunityCandidateError("candidate prospective-window boundary changed")
    protected = value.get("protected_boundaries")
    required_protected = {
        "may_2026_access_allowed": False,
        "reserved_window_retrofit_allowed": False,
        "missed_prospective_backfill_allowed": False,
        "pitcher_features_in_candidate": False,
        "historical_prices_executable": False,
        "production_probability_changes_allowed": False,
        "betting_authorized": False,
    }
    if not isinstance(protected, dict) or any(
        protected.get(key) != expected for key, expected in required_protected.items()
    ):
        raise ProjectedOpportunityCandidateError("candidate protected boundary changed")
    bindings = value.get("immutable_bindings")
    if not isinstance(bindings, list) or not bindings:
        raise ProjectedOpportunityCandidateError("candidate immutable bindings are missing")
    seen: set[str] = set()
    for binding in bindings:
        if not isinstance(binding, dict) or set(binding) != {"path", "sha256"}:
            raise ProjectedOpportunityCandidateError("candidate immutable binding schema changed")
        relative = binding.get("path")
        expected_sha = binding.get("sha256")
        if (
            not isinstance(relative, str)
            or relative in seen
            or not isinstance(expected_sha, str)
            or len(expected_sha) != 64
            or any(char not in "0123456789abcdef" for char in expected_sha)
        ):
            raise ProjectedOpportunityCandidateError("candidate immutable binding is invalid")
        seen.add(relative)
        target = (project_root / relative).resolve()
        if project_root not in target.parents or not target.is_file():
            raise ProjectedOpportunityCandidateError("candidate immutable binding target is invalid")
        if hashlib.sha256(target.read_bytes()).hexdigest() != expected_sha:
            raise ProjectedOpportunityCandidateError(
                f"candidate immutable binding differs: {relative}"
            )
    required_paths = {
        "config/projected_lineup_contract_v1.json",
        "config/projected_lineup_empirical_joint_v1.json",
        "config/shared_pa_forward_evidence_contract_v1.json",
        "config/shared_pa_forward_eb_control_v1.json",
        "config/shared_pa_forward_runtime_v1.json",
        "data/analysis/system_integrity_v2/pa_volume_chronology_v1/pa_distribution_fit_2023.json",
        "src/evaluation/projected_lineup_contract.py",
        "src/evaluation/projected_lineup_contract_v2.py",
        "src/evaluation/projected_lineup_empirical_joint.py",
        "src/evaluation/projected_lineup_history.py",
        "src/evaluation/shared_pa_batter_skill.py",
        "src/evaluation/shared_pa_forward_collector.py",
        "src/evaluation/shared_pa_forward_evidence.py",
        "src/evaluation/shared_pa_projected_opportunity_candidate.py",
        "src/evaluation/shared_pa_projected_opportunity_runner.py",
    }
    if seen != required_paths:
        raise ProjectedOpportunityCandidateError("candidate immutable binding set changed")
    return ProjectedOpportunityProtocol(
        value=value,
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def _canonical_date(value: Any) -> date:
    if not isinstance(value, str):
        raise ProjectedOpportunityCandidateError("official_game_date must be canonical YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ProjectedOpportunityCandidateError("official_game_date must be canonical YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise ProjectedOpportunityCandidateError("official_game_date must be canonical YYYY-MM-DD")
    if parsed.year == 2026 and parsed.month == 5:
        raise ProjectedOpportunityCandidateError("May 2026 is sealed")
    return parsed


def _utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise ProjectedOpportunityCandidateError(f"{label} must be timezone-aware ISO-8601")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProjectedOpportunityCandidateError(f"{label} must be timezone-aware ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProjectedOpportunityCandidateError(f"{label} must be timezone-aware ISO-8601")
    return parsed.astimezone(timezone.utc)


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ProjectedOpportunityCandidateError(f"{label} must be a positive integer")
    return value


def _probability(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProjectedOpportunityCandidateError(f"{label} must be numeric")
    parsed = float(value)
    if not math.isfinite(parsed) or not 0.0 <= parsed <= 1.0:
        raise ProjectedOpportunityCandidateError(f"{label} must be in [0,1]")
    return parsed


def _lineup_code_sha256() -> str:
    source = Path(__file__).with_name("projected_lineup_empirical_joint.py")
    return hashlib.sha256(source.read_bytes()).hexdigest()


def _candidate_code_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def mix_projected_pa_distribution(
    *,
    player_id: int,
    projection_marginals: Mapping[str, Any],
    pa_volume: ForwardPAVolumeArtifact,
) -> tuple[list[int], list[float], float, dict[str, float]]:
    """Mix no-start mass and slot-conditional PA distributions exactly."""
    player = _positive_int(player_id, "player_id")
    starts = projection_marginals.get("start_probability")
    slots = projection_marginals.get("slot_probability")
    if not isinstance(starts, Mapping) or not isinstance(slots, Mapping):
        raise ProjectedOpportunityCandidateError("projected lineup marginals are missing")
    player_key = str(player)
    if player_key not in starts:
        raise ProjectedOpportunityCandidateError(
            "player is absent from projected-lineup support; explicit abstention required"
        )
    start_probability = _probability(starts[player_key], "start_probability")
    slot_probability: dict[str, float] = {}
    for slot in range(1, 10):
        value = _probability(slots.get(f"{player}:{slot}", 0.0), f"slot_probability.{slot}")
        slot_probability[str(slot)] = value
    if not math.isclose(
        sum(slot_probability.values()), start_probability, rel_tol=0.0, abs_tol=1e-12
    ):
        raise ProjectedOpportunityCandidateError(
            "player slot probability does not equal projected start probability"
        )

    mixture: dict[int, float] = {0: 1.0 - start_probability}
    for slot in range(1, 10):
        slot_weight = slot_probability[str(slot)]
        for pa, probability in pa_volume.by_lineup_slot[slot].items():
            mixture[pa] = mixture.get(pa, 0.0) + slot_weight * probability
    support = sorted(mixture)
    mass = [mixture[value] for value in support]
    if any(not math.isfinite(value) or value < 0.0 for value in mass) or not math.isclose(
        sum(mass), 1.0, rel_tol=0.0, abs_tol=1e-12
    ):
        raise ProjectedOpportunityCandidateError("projected PA mixture is invalid")
    return support, mass, start_probability, slot_probability


def _build_unsigned(
    *,
    batter_skill_snapshot: Mapping[str, Any],
    projected_lineup_record: Mapping[str, Any],
    lineup_contract: Mapping[str, Any],
    loaded_forward_contract: Mapping[str, Any],
    candidate_protocol: ProjectedOpportunityProtocol,
    research_epoch_utc: str,
    prediction_generated_at_utc: str,
) -> dict[str, Any]:
    if not isinstance(candidate_protocol, ProjectedOpportunityProtocol):
        raise ProjectedOpportunityCandidateError("hash-verified candidate protocol is required")
    try:
        validate_batter_skill_snapshot(
            batter_skill_snapshot,
            loaded_forward_contract=loaded_forward_contract,
        )
        projection = validate_projection_v2(projected_lineup_record, lineup_contract)
    except (BatterSkillSnapshotError, ProjectedLineupContractError) as exc:
        raise ProjectedOpportunityCandidateError("input evidence failed its governing contract") from exc
    official_date_text = batter_skill_snapshot.get("official_game_date")
    official_date = _canonical_date(official_date_text)
    horizon = _utc(batter_skill_snapshot.get("target_horizon_utc"), "target_horizon_utc")
    epoch = _utc(research_epoch_utc, "research_epoch_utc")
    generated = _utc(prediction_generated_at_utc, "prediction_generated_at_utc")
    projection_receipt = _utc(
        projected_lineup_record.get("projection_receipt_utc"), "projection_receipt_utc"
    )
    skill_receipt = _utc(batter_skill_snapshot.get("stats_receipt_utc"), "stats_receipt_utc")
    if (
        epoch > projection_receipt
        or max(projection_receipt, skill_receipt) > generated
        or generated > horizon
    ):
        raise ProjectedOpportunityCandidateError(
            "candidate release, projection receipt, prediction generation, and T-4 horizon are out of order"
        )

    game_pk = _positive_int(batter_skill_snapshot.get("mlb_game_pk"), "mlb_game_pk")
    player_id = _positive_int(batter_skill_snapshot.get("player_id"), "player_id")
    side = batter_skill_snapshot.get("side")
    if side not in {"home", "away"}:
        raise ProjectedOpportunityCandidateError("side must be home or away")
    team_id = _positive_int(batter_skill_snapshot.get("team_id"), "team_id")
    if (
        projected_lineup_record.get("official_game_date") != official_date_text
        or projected_lineup_record.get("mlb_game_pk") != game_pk
        or projected_lineup_record.get("team_id") != team_id
        or _utc(projected_lineup_record.get("target_horizon_utc"), "lineup target_horizon_utc")
        != horizon
    ):
        raise ProjectedOpportunityCandidateError("projected lineup target identity differs")
    if (
        batter_skill_snapshot.get("projected_lineup_content_sha256")
        != projected_lineup_record.get("projection_content_sha256")
    ):
        raise ProjectedOpportunityCandidateError("batter skill and projected lineup lineage differs")
    if player_id not in projected_lineup_record.get("active_roster_player_ids", []):
        raise ProjectedOpportunityCandidateError("player is absent from the receipted active roster")
    if projected_lineup_record.get("fitted_candidate_id") != LINEUP_CANDIDATE_ID:
        raise ProjectedOpportunityCandidateError("projected lineup candidate identity changed")
    if projected_lineup_record.get("model_code_sha256") != _lineup_code_sha256():
        raise ProjectedOpportunityCandidateError("projected lineup executing code hash differs")

    pa_volume = loaded_forward_contract.get("pa_volume_artifact")
    if not isinstance(pa_volume, ForwardPAVolumeArtifact):
        raise ProjectedOpportunityCandidateError("verified PA-volume artifact is missing")
    if (
        loaded_forward_contract.get("pa_volume_sha256") != PA_VOLUME_ARTIFACT_SHA256
        or batter_skill_snapshot.get("control_id") != CONTROL_ID
    ):
        raise ProjectedOpportunityCandidateError("locked PA or probability-control identity differs")

    support, mass, start_probability, slot_probability = mix_projected_pa_distribution(
        player_id=player_id,
        projection_marginals=projection,
        pa_volume=pa_volume,
    )
    candidate_markets = derive_market_distributions(
        per_pa_probability=batter_skill_snapshot["per_pa_probability"], support=support, mass=mass
    )
    baseline_support = sorted(pa_volume.pooled)
    baseline_mass = [pa_volume.pooled[value] for value in baseline_support]
    baseline_markets = derive_market_distributions(
        per_pa_probability=batter_skill_snapshot["per_pa_probability"],
        support=baseline_support,
        mass=baseline_mass,
    )
    candidate_tails = candidate_markets["tails"]
    baseline_tails = baseline_markets["tails"]
    deltas = {
        key: float(candidate_tails[key]) - float(baseline_tails[key])
        for key in sorted(candidate_tails)
    }
    if RESERVED_WINDOW_FIRST <= official_date <= RESERVED_WINDOW_LAST:
        evidence_class = "nonqualifying_reserved_window_operational_smoke"
        confirmation_eligible = False
    elif official_date >= FIRST_CONFIRMATION_DATE:
        evidence_class = "future_untouched_candidate_observation"
        confirmation_eligible = True
    else:
        evidence_class = "nonqualifying_pre_protocol_research"
        confirmation_eligible = False

    return {
        "schema_version": SCHEMA_VERSION,
        "terminal_state": "candidate_complete",
        "research_only": True,
        "betting_authorized": False,
        "promotion_eligible": False,
        "candidate_id": CANDIDATE_ID,
        "candidate_protocol_sha256": candidate_protocol.sha256,
        "evidence_class": evidence_class,
        "confirmation_eligible": confirmation_eligible,
        "research_epoch_utc": research_epoch_utc,
        "prediction_generated_at_utc": prediction_generated_at_utc,
        "official_game_date": official_date_text,
        "mlb_game_pk": game_pk,
        "team_id": team_id,
        "side": side,
        "player_id": player_id,
        "target_horizon_utc": batter_skill_snapshot["target_horizon_utc"],
        "batter_skill_snapshot_sha256": batter_skill_snapshot[
            "batter_skill_snapshot_sha256"
        ],
        "projected_lineup_content_sha256": projected_lineup_record[
            "projection_content_sha256"
        ],
        "projected_lineup_fitted_artifact_sha256": projected_lineup_record[
            "fitted_artifact_sha256"
        ],
        "projected_lineup_code_sha256": projected_lineup_record["model_code_sha256"],
        "per_pa_control_id": batter_skill_snapshot["control_id"],
        "per_pa_control_config_sha256": batter_skill_snapshot["control_config_sha256"],
        "pa_volume_artifact_sha256": PA_VOLUME_ARTIFACT_SHA256,
        "baseline_pa_scope": "pooled_projected_lineup",
        "projected_start_probability": start_probability,
        "projected_slot_probability": slot_probability,
        "candidate_pa_support": support,
        "candidate_pa_mass": mass,
        "candidate_pa_distribution_sha256": pa_distribution_sha256(
            support=support, mass=mass
        ),
        "candidate_expected_pa": sum(pa * probability for pa, probability in zip(support, mass)),
        "per_pa_probability": dict(batter_skill_snapshot["per_pa_probability"]),
        "baseline_market_distributions": baseline_markets,
        "candidate_market_distributions": candidate_markets,
        "candidate_minus_baseline_tail_probability": deltas,
        "pitcher_block_status": "excluded_batter_only",
        "policy_status": "none_research_probability_only",
        "candidate_code_sha256": _candidate_code_sha256(),
    }


def build_projected_opportunity_candidate(
    *,
    batter_skill_snapshot: Mapping[str, Any],
    projected_lineup_record: Mapping[str, Any],
    lineup_contract: Mapping[str, Any],
    loaded_forward_contract: Mapping[str, Any],
    candidate_protocol: ProjectedOpportunityProtocol,
    research_epoch_utc: str,
    prediction_generated_at_utc: str,
) -> dict[str, Any]:
    """Build one immutable research candidate and independently replay it."""
    unsigned = _build_unsigned(
        batter_skill_snapshot=batter_skill_snapshot,
        projected_lineup_record=projected_lineup_record,
        lineup_contract=lineup_contract,
        loaded_forward_contract=loaded_forward_contract,
        candidate_protocol=candidate_protocol,
        research_epoch_utc=research_epoch_utc,
        prediction_generated_at_utc=prediction_generated_at_utc,
    )
    record = dict(unsigned)
    record["candidate_record_sha256"] = sha256_value(unsigned)
    validate_projected_opportunity_candidate(
        record=record,
        batter_skill_snapshot=batter_skill_snapshot,
        projected_lineup_record=projected_lineup_record,
        lineup_contract=lineup_contract,
        loaded_forward_contract=loaded_forward_contract,
        candidate_protocol=candidate_protocol,
    )
    return record


def validate_projected_opportunity_candidate(
    *,
    record: Mapping[str, Any],
    batter_skill_snapshot: Mapping[str, Any],
    projected_lineup_record: Mapping[str, Any],
    lineup_contract: Mapping[str, Any],
    loaded_forward_contract: Mapping[str, Any],
    candidate_protocol: ProjectedOpportunityProtocol,
) -> None:
    """Rebuild from governing inputs; hash-consistent semantic edits fail."""
    if not isinstance(record, Mapping) or "candidate_record_sha256" not in record:
        raise ProjectedOpportunityCandidateError("candidate record is incomplete")
    expected = _build_unsigned(
        batter_skill_snapshot=batter_skill_snapshot,
        projected_lineup_record=projected_lineup_record,
        lineup_contract=lineup_contract,
        loaded_forward_contract=loaded_forward_contract,
        candidate_protocol=candidate_protocol,
        research_epoch_utc=record.get("research_epoch_utc"),
        prediction_generated_at_utc=record.get("prediction_generated_at_utc"),
    )
    supplied = dict(record)
    supplied_hash = supplied.pop("candidate_record_sha256")
    if supplied != expected or supplied_hash != sha256_value(expected):
        raise ProjectedOpportunityCandidateError(
            "candidate record differs from independent probability replay"
        )
