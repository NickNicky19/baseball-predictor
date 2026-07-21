from __future__ import annotations

import pytest

from src.features.ml.base import BaseFeatureEngineer
from src.features.ml.feature_pipeline import FeaturePipeline, FeaturePipelineError
from src.models.dataclasses import LeagueBaselines, StatcastProfile
from src.simulation.pa_simulator import HybridPASimulator, PASimulatorConfig


class _Engineer(BaseFeatureEngineer):
    def __init__(self, name: str, result=None, error: Exception | None = None):
        super().__init__(name)
        self.result = result
        self.error = error

    def compute(self, data):
        if self.error:
            raise self.error
        return self.result

    def get_feature_names(self):
        return list(self.result or {})


def test_feature_pipeline_rejects_partial_output_after_engineer_failure():
    pipeline = FeaturePipeline([
        _Engineer("valid", {"barrel_rate": 0.1}),
        _Engineer("broken", error=ValueError("old silent failure")),
    ])
    with pytest.raises(FeaturePipelineError, match="no partial feature set"):
        pipeline.compute({})


def test_feature_pipeline_rejects_order_dependent_duplicate_override():
    pipeline = FeaturePipeline([
        _Engineer("source", {"barrel_rate": 0.1}),
        _Engineer("override", {"barrel_rate": 0.5}),
    ])
    with pytest.raises(FeaturePipelineError, match="duplicate feature ownership"):
        pipeline.compute({})


def _fitted_config(**overrides):
    values = dict(
        use_fitted_kbb=True,
        kbb_k_intercept=-1.0, kbb_k_hitter_season=1.0,
        kbb_k_hitter_recent=0.0, kbb_k_pitcher=0.0,
        kbb_bb_intercept=-2.0, kbb_bb_hitter_season=1.0,
        kbb_bb_hitter_recent=0.0, kbb_bb_pitcher=0.0,
    )
    values.update(overrides)
    return PASimulatorConfig(**values)


def test_fitted_kbb_rejects_invalid_rate_instead_of_legacy_fallback():
    sim = HybridPASimulator(config=_fitted_config())
    profile = StatcastProfile(
        player_id=1, player_name="Mutation", k_rate=1.4, bb_rate=0.08
    )
    with pytest.raises(ValueError, match=r"k_rate must be finite and in \[0, 1\]"):
        sim.expected_outcome_probabilities(statcast=profile)


def test_fitted_kbb_rejects_missing_season_rate():
    sim = HybridPASimulator(config=_fitted_config())
    profile = StatcastProfile(player_id=1, player_name="Missing", bb_rate=0.08)
    with pytest.raises(ValueError, match="requires hitter season rates"):
        sim.expected_outcome_probabilities(statcast=profile)


def test_fitted_kbb_preserves_valid_missing_recent_substitution():
    sim = HybridPASimulator(config=_fitted_config())
    profile = StatcastProfile(
        player_id=1, player_name="Valid", k_rate=0.22, bb_rate=0.08
    )
    probs = sim.expected_outcome_probabilities(statcast=profile)
    assert abs(sum(probs.values()) - 1.0) < 1e-12


def test_corrected_pitcher_hr9_direction_is_monotonic_and_legacy_is_frozen():
    league = LeagueBaselines()
    corrected = HybridPASimulator(
        config=PASimulatorConfig.from_league(
            league, pitcher_hr9_effect_mode="corrected"
        ), league_baselines=league,
    )
    frozen = HybridPASimulator(
        config=PASimulatorConfig.from_league(
            league, pitcher_hr9_effect_mode="legacy_frozen"
        ), league_baselines=league,
    )
    low = corrected.expected_outcome_probabilities(pitcher_hr_per_9=0.5)["home_run"]
    high = corrected.expected_outcome_probabilities(pitcher_hr_per_9=2.0)["home_run"]
    assert high > low
    frozen_low = frozen.expected_outcome_probabilities(pitcher_hr_per_9=0.5)["home_run"]
    frozen_high = frozen.expected_outcome_probabilities(pitcher_hr_per_9=2.0)["home_run"]
    assert frozen_high < frozen_low


def test_unknown_pitcher_hr9_mode_fails_closed():
    with pytest.raises(ValueError, match="pitcher_hr9_effect_mode"):
        HybridPASimulator(config=PASimulatorConfig(pitcher_hr9_effect_mode="guess"))
