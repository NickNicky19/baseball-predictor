"""Regression and mutation tests for shared prospective PA evidence."""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.evaluation.shared_pa_forward_evidence import (
    CONTROL_ID,
    CONTROL_CONFIG_SHA256,
    PA_VOLUME_ARTIFACT_SHA256,
    PA_OUTCOMES,
    SCHEMA_VERSION,
    SharedPAForwardEvidenceError,
    derive_market_distributions,
    empirical_bayes_pa_probability,
    load_forward_contract,
    pa_distribution_sha256,
    sha256_value,
    snapshot_sha256,
    validate_player_snapshot,
)


PRIOR = {
    "strikeout": 40005 / 177226,
    "walk": 15128 / 177226,
    "single": 25128 / 177226,
    "double": 7971 / 177226,
    "triple": 690 / 177226,
    "home_run": 5698 / 177226,
    "bip_out": 78930 / 177226,
    "other_non_ab": 3676 / 177226,
}


def _snapshot(*, lineup_state: str = "projected") -> dict:
    start = datetime(2026, 7, 30, tzinfo=timezone.utc)
    horizon = start - timedelta(hours=4)
    counts = {
        "strikeout": 20,
        "walk": 10,
        "single": 16,
        "double": 5,
        "triple": 1,
        "home_run": 6,
        "bip_out": 39,
        "other_non_ab": 3,
    }
    per_pa = empirical_bayes_pa_probability(
        counts=counts, league_prior=PRIOR, prior_strength_pa=200.0
    )
    source_slot = 3
    effective_slot = source_slot if lineup_state == "confirmed" else None
    scope = f"confirmed_slot_{source_slot}" if lineup_state == "confirmed" else "pooled_projected_lineup"
    loaded = load_forward_contract(
        root=Path(__file__).resolve().parents[1],
        contract_path=Path(__file__).resolve().parents[1] / "config/shared_pa_forward_evidence_contract_v1.json",
    )
    artifact = loaded["pa_volume_artifact"]
    distribution = artifact.by_lineup_slot[source_slot] if lineup_state == "confirmed" else artifact.pooled
    support = sorted(distribution)
    mass = [distribution[value] for value in support]
    game_identity = {
        "mlb_game_pk": 123456,
        "official_game_date": "2026-07-29",
        "official_start_utc": start.isoformat(),
        "home_team_id": 111,
        "away_team_id": 112,
    }
    player_identity = {"mlb_game_pk": 123456, "side": "home", "player_id": 654321}
    record = {
        "schema_version": SCHEMA_VERSION,
        "terminal_state": "captured_complete",
        "research_only": True,
        "betting_authorized": False,
        "promotion_eligible": False,
        "collector_instance_id": "synthetic-forward-control",
        "monotonic_receipt_sequence": source_slot,
        "receipt_utc": horizon.isoformat(),
        "source_observation_utc": horizon.isoformat(),
        "collector_code_sha256": "a" * 64,
        "runtime_manifest_sha256": "b" * 64,
        "plan_sha256": "c" * 64,
        "target_id": "d" * 64,
        "mlb_game_pk": 123456,
        "official_game_date": "2026-07-29",
        "official_start_utc": start.isoformat(),
        "target_horizon_utc": horizon.isoformat(),
        "side": "home",
        "home_team_id": 111,
        "away_team_id": 112,
        "game_identity_sha256": sha256_value(game_identity),
        "lineup_state": lineup_state,
        "source_lineup_slot": source_slot,
        "effective_lineup_slot": effective_slot,
        "lineup_receipt_utc": horizon.isoformat(),
        "raw_lineup_payload_sha256": "f" * 64,
        "player_id": 654321,
        "player_identity_sha256": sha256_value(player_identity),
        "hard_player_key": "123456:home:654321:shared_pa",
        "stats_receipt_utc": horizon.isoformat(),
        "raw_stats_payload_sha256": "1" * 64,
        "stats_counts": counts,
        "stats_pa": sum(counts.values()),
        "stats_season": 2026,
        "control_id": CONTROL_ID,
        "control_config_sha256": CONTROL_CONFIG_SHA256,
        "prior_strength_pa": 200.0,
        "league_prior_probability": PRIOR,
        "per_pa_probability": per_pa,
        "feature_snapshot_sha256": "",
        "rate_fallback_labels": [],
        "pa_volume_candidate_id": "pa_volume_2023_only_v1",
        "pa_volume_artifact_sha256": PA_VOLUME_ARTIFACT_SHA256,
        "pa_distribution_scope": scope,
        "pa_support": support,
        "pa_mass": mass,
        "pa_distribution_sha256": pa_distribution_sha256(support=support, mass=mass),
        "expected_pa": sum(n * p for n, p in zip(support, mass)),
        "market_distributions": derive_market_distributions(
            per_pa_probability=per_pa, support=support, mass=mass
        ),
        "prediction_method": "exact_iid_pa_mixture_v1",
        "pitcher_block_status": "excluded_batter_only",
        "policy_status": "none_research_probability_only",
        "snapshot_sha256": "",
    }
    record["feature_snapshot_sha256"] = sha256_value({
        "counts": record["stats_counts"],
        "league_prior_probability": record["league_prior_probability"],
        "prior_strength_pa": record["prior_strength_pa"],
        "raw_stats_payload_sha256": record["raw_stats_payload_sha256"],
        "stats_receipt_utc": record["stats_receipt_utc"],
    })
    record["snapshot_sha256"] = snapshot_sha256(record)
    return record


def _rehash(record: dict) -> None:
    record["feature_snapshot_sha256"] = sha256_value({
        "counts": record["stats_counts"],
        "league_prior_probability": record["league_prior_probability"],
        "prior_strength_pa": record["prior_strength_pa"],
        "raw_stats_payload_sha256": record["raw_stats_payload_sha256"],
        "stats_receipt_utc": record["stats_receipt_utc"],
    })
    record["snapshot_sha256"] = snapshot_sha256(record)


@pytest.mark.parametrize("lineup_state", ["projected", "confirmed"])
def test_valid_snapshot_round_trip(lineup_state: str) -> None:
    validate_player_snapshot(_snapshot(lineup_state=lineup_state))


def test_projected_lineup_cannot_consume_projected_slot() -> None:
    record = _snapshot()
    record["effective_lineup_slot"] = record["source_lineup_slot"]
    record["pa_distribution_scope"] = "confirmed_slot_3"
    _rehash(record)
    with pytest.raises(SharedPAForwardEvidenceError, match="projected lineup"):
        validate_player_snapshot(record)


def test_empirical_bayes_probability_mutation_fails() -> None:
    record = _snapshot()
    record["per_pa_probability"]["home_run"] += 0.001
    record["per_pa_probability"]["bip_out"] -= 0.001
    record["market_distributions"] = derive_market_distributions(
        per_pa_probability=record["per_pa_probability"],
        support=record["pa_support"],
        mass=record["pa_mass"],
    )
    _rehash(record)
    with pytest.raises(SharedPAForwardEvidenceError, match="empirical-Bayes"):
        validate_player_snapshot(record)


def test_count_denominator_mutation_fails() -> None:
    record = _snapshot()
    record["stats_pa"] += 1
    _rehash(record)
    with pytest.raises(SharedPAForwardEvidenceError, match="stats_pa"):
        validate_player_snapshot(record)


def test_market_tail_mutation_fails() -> None:
    record = _snapshot()
    record["market_distributions"]["tails"]["home_runs_over_0.5"] += 0.01
    _rehash(record)
    with pytest.raises(SharedPAForwardEvidenceError, match="market distributions"):
        validate_player_snapshot(record)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("official_game_date", "2026-05-15", "May 2026"),
        ("model_price", -110, "schema differs"),
        ("pitcher_block_status", "actual_starter", "pitcher or policy"),
        ("hard_player_key", "123456:away:654321:shared_pa", "hard_player_key"),
        ("pa_volume_candidate_id", "unhashed_fallback", "PA-volume artifact"),
    ],
)
def test_governance_identity_and_probability_mutations_fail(
    field: str, value: object, message: str
) -> None:
    record = _snapshot()
    record[field] = value
    _rehash(record)
    with pytest.raises(SharedPAForwardEvidenceError, match=message):
        validate_player_snapshot(record)


def test_post_horizon_stats_receipt_fails() -> None:
    record = _snapshot()
    record["stats_receipt_utc"] = record["official_start_utc"]
    _rehash(record)
    with pytest.raises(SharedPAForwardEvidenceError, match="post-horizon"):
        validate_player_snapshot(record)


def test_zero_history_requires_explicit_prior_fallback() -> None:
    record = _snapshot()
    record["stats_counts"] = {name: 0 for name in PA_OUTCOMES}
    record["stats_pa"] = 0
    record["per_pa_probability"] = dict(PRIOR)
    record["market_distributions"] = derive_market_distributions(
        per_pa_probability=record["per_pa_probability"],
        support=record["pa_support"],
        mass=record["pa_mass"],
    )
    _rehash(record)
    with pytest.raises(SharedPAForwardEvidenceError, match="league-prior-only"):
        validate_player_snapshot(record)


def test_full_game_markets_share_one_pa_distribution() -> None:
    record = _snapshot()
    tails = record["market_distributions"]["tails"]
    assert tails["home_runs_over_0.5"] <= tails["hits_over_0.5"]
    assert tails["total_bases_over_0.5"] == pytest.approx(tails["hits_over_0.5"], abs=1e-12)
    assert tails["hits_over_1.5"] <= tails["hits_over_0.5"]


def test_locked_contract_binds_control_and_pa_volume_artifacts() -> None:
    root = __import__("pathlib").Path(__file__).resolve().parents[1]
    loaded = load_forward_contract(
        root=root,
        contract_path=root / "config/shared_pa_forward_evidence_contract_v1.json",
    )
    assert loaded["control"]["fit_season"] == 2023
    assert loaded["control"]["selection_season"] == 2024
    assert loaded["pa_volume_artifact"].candidate_id == "pa_volume_2023_only_v1"
