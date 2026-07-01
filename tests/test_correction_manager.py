"""Tests for correction integration (Phase 5)."""

from src.learning.bias_corrector import BiasCorrector
from src.models.dataclasses import LeagueBaselines, PropCategory, PropProjection
from src.prediction.correction_manager import CorrectionManager, CorrectionSettings


def test_correction_manager_disabled_by_default():
    manager = CorrectionManager(settings=CorrectionSettings(enabled=False))
    assert not manager.is_active()


def test_apply_projection_offset():
    corrector = BiasCorrector()
    corrector.state.category_offsets = {"hrr": 0.5}
    corrector.state.confidence = 1.0
    manager = CorrectionManager(corrector=corrector)

    original = PropProjection(
        player_id=1,
        player_name="Test",
        category="hrr",
        game_date="2026-07-01",
        projected_value=2.0,
        confidence=0.7,
    )
    corrected = manager.apply_projections([original], force=True)[0]
    assert corrected.projected_value == 2.5


def test_league_blend():
    league = LeagueBaselines(xwoba=0.320)
    corrector = BiasCorrector()
    corrector.state.league_overrides = {"xwoba": 0.330}
    corrector.state.confidence = 0.5
    blended = corrector.apply_league_baselines(league)
    assert blended.xwoba == 0.325