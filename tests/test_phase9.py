"""Phase 9 tests — purification, enrichment, validation infrastructure."""

from __future__ import annotations

import pandas as pd

from src.evaluation import BacktestEngine, CalibrationEngine, CalibrationConfig
from src.evaluation.backtest_engine import OutcomeRecord
from src.evaluation.calibration_tracker import CalibrationTracker
from src.evaluation.champion_challenger import ChampionChallengerTester
from src.evaluation.park_factor_estimator import ParkFactorEstimator
from src.evaluation.roi_simulator import ROISimulator
from src.evaluation.slot_pa_estimator import SlotPAEstimator
from src.evaluation.tunable_parameters import build_parameter_inventory
from src.evaluation.walk_forward_validator import WalkForwardValidator
from src.models.dataclasses import (
    LeagueBaselines,
    MonteCarloResult,
    PropProjection,
    StatcastDistributionProfile,
    StatcastProfile,
)
from src.simulation.pa_simulator import HybridPASimulator, PASimulatorConfig


def test_parameter_inventory_includes_pa_config_fields():
    report = build_parameter_inventory()
    assert report.summary.get("data_derived", 0) > 0
    pa_names = {p.name for p in report.parameters if p.location == "PASimulatorConfig"}
    assert "hr_hitter_power" in pa_names
    assert "hr_intercept" in pa_names


def test_pa_config_from_league_derives_intercepts():
    league = LeagueBaselines(k_pct=22.5, bb_pct=8.5, hr_per_9=1.1, contact_rate=0.755, barrel_rate=0.085)
    cfg = PASimulatorConfig.from_league(league)
    sim = HybridPASimulator(config=cfg, league_baselines=league)
    rates = sim.expected_rates()
    assert 0.05 < rates["k_prob"] < 0.50
    assert 0.002 < rates["hr_prob_on_bip"] < 0.15


def test_hr_prob_uses_distribution_profile():
    league = LeagueBaselines()
    cfg = PASimulatorConfig.from_league(league)
    sim = HybridPASimulator(config=cfg, league_baselines=league, random_seed=1)
    dist = StatcastDistributionProfile(
        exit_velocity_mean=95.0,
        launch_angle_mean=18.0,
        sweet_spot_rate=0.40,
        barrel_rate=0.12,
        hard_hit_rate=0.48,
        sample_bip=80,
    )
    statcast = StatcastProfile(
        player_id=1,
        player_name="Power",
        sample_pa=200,
        xwoba=0.360,
        barrel_rate=0.12,
        hard_hit_rate=0.48,
        distribution=dist,
    )
    rates_plain = sim.expected_rates(statcast=StatcastProfile(player_id=1, player_name="Avg", sample_pa=100))
    rates_power = sim.expected_rates(statcast=statcast)
    assert rates_power["hr_prob_on_bip"] >= rates_plain["hr_prob_on_bip"]


def test_park_factor_estimator_shrinks_toward_one():
    pairs = pd.DataFrame(
        {
            "venue": ["Coors Field"] * 10 + ["Oracle Park"] * 10,
            "category": ["home_runs"] * 20,
            "actual_value": [0.35] * 10 + [0.15] * 10,
        }
    )
    estimator = ParkFactorEstimator()
    factors = estimator.estimate_from_pairs(pairs, league_hr_rate=0.25)
    assert factors["Coors Field"].hr_factor > 1.0
    assert factors["Oracle Park"].hr_factor < 1.0


def test_slot_pa_estimator_empirical_prior():
    estimator = SlotPAEstimator()
    factors = estimator.empirical_prior()
    assert factors["1"] > factors["9"]


def test_calibration_engine_hr_coefficient_adjustment():
    engine = BacktestEngine()
    projections = [
        PropProjection(1, "A", "home_runs", "2026-06-25", 0.8, 0.7, mlb_game_pk=700001),
        PropProjection(2, "B", "home_runs", "2026-06-25", 0.7, 0.7, mlb_game_pk=700002),
    ]
    outcomes = [
        OutcomeRecord(1, "A", "2026-06-25", "home_runs", 0.3, mlb_game_pk=700001),
        OutcomeRecord(2, "B", "2026-06-25", "home_runs", 0.2, mlb_game_pk=700002),
    ]
    report = engine.evaluate_predictions(projections, outcomes)
    cal = CalibrationEngine(
        pa_config=PASimulatorConfig.from_league(LeagueBaselines()),
        calibration_config=CalibrationConfig(min_samples_per_category=1, coefficient_shrinkage=0.5),
    )
    updated = cal.calibrate_hr_coefficients(report)
    assert updated.hr_hitter_power < cal.pa_config.hr_hitter_power


def test_walk_forward_validator_runs_folds():
    pairs = pd.DataFrame(
        {
            "player_id": [1, 1, 2, 2, 1, 2],
            "game_pk": [701001, 701002, 701003, 701004, 701005, 701006],
            "player_name": ["A", "A", "B", "B", "A", "B"],
            "game_date": ["2026-06-20", "2026-06-22", "2026-06-20", "2026-06-25", "2026-06-28", "2026-06-28"],
            "category": ["hrr"] * 6,
            "actual_value": [2.0, 2.1, 1.5, 1.4, 2.2, 1.6],
            "predicted_value": [2.0, 2.0, 1.5, 1.5, 2.0, 1.5],
        }
    )

    def predict_fn(train: pd.DataFrame) -> list[PropProjection]:
        return [
            PropProjection(
                int(row["player_id"]),
                str(row["player_name"]),
                "hrr",
                str(row["game_date"]),
                float(row.get("predicted_value", 2.0)),
                0.7,
                mlb_game_pk=int(row["game_pk"]),
            )
            for _, row in train.iterrows()
        ]

    wf = WalkForwardValidator().validate(pairs, predict_fn, n_folds=2, min_fold_samples=1)
    assert len(wf.folds) >= 1


def test_champion_challenger_promotes_better_model():
    pairs = pd.DataFrame(
        {
            "player_id": [1] * 8,
            "player_name": ["A"] * 8,
            "game_date": [f"2026-06-{15+i}" for i in range(8)],
            "category": ["hrr"] * 8,
            "actual_value": [2.0, 2.1, 1.9, 2.0, 2.2, 2.0, 1.8, 2.1],
            "predicted_value": [2.0] * 8,
        }
    )

    def champion_fn(train: pd.DataFrame) -> list[PropProjection]:
        return _projections_from_pairs(train, bias=0.5)

    def better_fn(train: pd.DataFrame) -> list[PropProjection]:
        return _projections_from_pairs(train, bias=0.0)

    report = ChampionChallengerTester(improvement_threshold=0.01).compare(
        pairs,
        champion_fn,
        {"better": better_fn},
        n_folds=2,
    )
    assert report.challengers[0].name == "better"


def test_calibration_tracker_brier_score():
    mc = MonteCarloResult(
        n_sims=100,
        category="hrr",
        mean=2.0,
        median=2.0,
        p10=1.0,
        p90=3.0,
        p_ge_threshold={2.0: 0.65},
    )
    projections = [
        PropProjection(1, "A", "hrr", "2026-06-25", 2.0, 0.7, simulation=mc),
    ]
    outcomes = [OutcomeRecord(1, "A", "2026-06-25", "hrr", 2.0)]
    report = CalibrationTracker().evaluate_category(projections, outcomes, "hrr", threshold=1.5)
    # P(H+R+RBI >= 2), not the accidental float-line lookup P(... >= 1.5).
    assert report.brier_score == 0.1225


def test_roi_simulator_flat_stake():
    from src.models.dataclasses import EdgeRecommendation, EdgeResult

    plays = [
        EdgeResult(
            player_name="A",
            category="hrr",
            line=1.5,
            projected_value=2.0,
            implied_prob_over=0.52,
            model_prob_over=0.60,
            edge_pct=6.0,
            recommendation=EdgeRecommendation.LEAN_OVER,
            confidence=0.7,
        )
    ]
    outcomes = {("A", "hrr"): 2.0}
    result = ROISimulator(allow_legacy_research=True).simulate(plays, outcomes)
    assert result.overall is not None
    assert result.overall.wins >= 0
    assert result.status == "RESEARCH_ONLY"


def _projections_from_pairs(train: pd.DataFrame, bias: float) -> list[PropProjection]:
    return [
        PropProjection(
            int(row["player_id"]),
            str(row["player_name"]),
            "hrr",
            str(row["game_date"]),
            float(row.get("predicted_value", 2.0)) + bias,
            0.7,
        )
        for _, row in train.iterrows()
    ]
