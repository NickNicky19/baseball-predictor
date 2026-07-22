"""Tests for correction integration (Phase 5)."""

from src.learning.bias_corrector import BiasCorrector
from src.models.dataclasses import (
    LeagueBaselines,
    MonteCarloResult,
    OutcomeProbabilities,
    PropProjection,
)
from src.prediction.correction_manager import CorrectionManager, CorrectionSettings


def test_correction_manager_disabled_by_default():
    manager = CorrectionManager(settings=CorrectionSettings(enabled=False))
    assert not manager.is_active()


def test_apply_projection_offset():
    corrector = BiasCorrector()
    corrector.state.category_offsets = {"hrr": 0.5}
    corrector.state.confidence = 1.0
    simulation = MonteCarloResult(
        n_sims=10,
        category="hrr",
        mean=2.0,
        median=2.0,
        p10=0.0,
        p90=4.0,
    )
    outcome_probs = OutcomeProbabilities(
        strikeout=0.2,
        walk=0.08,
        home_run=0.03,
        single=0.15,
        double=0.05,
        triple=0.01,
        out_on_bip=0.48,
    )
    original = PropProjection(
        player_id=1,
        player_name="Test",
        category="hrr",
        game_date="2026-07-01",
        projected_value=2.0,
        confidence=0.7,
        simulation=simulation,
        outcome_probs=outcome_probs,
        mlb_game_pk=123,
        input_health_flags=("verified",),
    )
    corrected = corrector.apply_projections([original])[0]
    assert corrected.projected_value == 2.5
    assert corrected.simulation is simulation
    assert corrected.outcome_probs is outcome_probs
    assert corrected.mlb_game_pk == 123
    assert corrected.input_health_flags == ("verified",)


def test_league_blend():
    league = LeagueBaselines(xwoba=0.320)
    corrector = BiasCorrector()
    corrector.state.league_overrides = {"xwoba": 0.330}
    corrector.state.confidence = 0.5
    blended = corrector.apply_league_baselines(league)
    assert blended.xwoba == 0.325
