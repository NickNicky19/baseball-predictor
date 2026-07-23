"""Tests for the fail-closed correction consumption boundary."""

import json

import pytest

from src.learning.bias_corrector import BiasCorrectionState, BiasCorrector
from src.models.dataclasses import LeagueBaselines, PropCategory, PropProjection
from src.prediction.correction_manager import CorrectionManager, CorrectionSettings


def test_correction_manager_disabled_by_default():
    manager = CorrectionManager(settings=CorrectionSettings(enabled=False))
    assert not manager.is_active()


HASH = "a" * 64


def promoted_state(**updates):
    values = dict(
        league_overrides={"xwoba": 0.330},
        sample_sizes={"total": 400},
        confidence=1.0,
        training_cutoff="2024-12-31",
        source_sha256=HASH,
        protocol_sha256=HASH,
        code_sha256=HASH,
        config_sha256=HASH,
        test_sha256=HASH,
        market_scope=("hits",),
        promotion_status="PROMOTED",
    )
    values.update(updates)
    return BiasCorrectionState(**values)


def test_mutation_output_only_projection_offset_is_quarantined():
    corrector = BiasCorrector(promoted_state(category_offsets={"hrr": 0.5}))
    manager = CorrectionManager(corrector=corrector)

    original = PropProjection(
        player_id=1,
        player_name="Test",
        category="hrr",
        game_date="2026-07-01",
        projected_value=2.0,
        confidence=0.7,
    )
    with pytest.raises(ValueError, match="output-only category offsets"):
        manager.apply_projections([original], force=True)


def test_valid_promoted_parameter_state_can_apply_without_output_rewrite():
    manager = CorrectionManager(corrector=BiasCorrector(promoted_state()))
    original = LeagueBaselines(xwoba=0.320)
    corrected = manager.corrector.apply_league_baselines(original)
    assert corrected.xwoba == 0.330


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"version": "1.0"}, "legacy or unknown"),
        ({"confidence": float("nan")}, "confidence"),
        ({"source_sha256": "not-a-hash"}, "source_sha256"),
        ({"training_cutoff": "2026/07/01"}, "training_cutoff"),
        ({"promotion_status": "FORGED"}, "promotion_status"),
        ({"league_overrides": {"season": 2025}}, "non-rate field"),
        ({"pa_config_overrides": {"use_fitted_kbb": 1.0}}, "nonnumeric field"),
    ],
)
def test_mutation_persisted_state_fails_closed(tmp_path, mutation, message):
    payload = promoted_state().to_dict()
    payload.update(mutation)
    path = tmp_path / "state.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    manager = CorrectionManager(settings=CorrectionSettings(state_path=str(path)))
    with pytest.raises(ValueError, match=message):
        manager.load_state_if_exists(required=True)


def test_research_only_state_cannot_enable_runtime():
    state = promoted_state(promotion_status="RESEARCH_ONLY")
    manager = CorrectionManager(corrector=BiasCorrector(state))
    with pytest.raises(ValueError, match="research-only"):
        manager.enable()


def test_mutation_same_day_training_cutoff_fails_runtime_context():
    manager = CorrectionManager(corrector=BiasCorrector(promoted_state()))
    with pytest.raises(ValueError, match="strictly before"):
        manager.validate_runtime_context(
            target_date="2024-12-31", requested_markets=("hits",)
        )


def test_mutation_unpromoted_market_fails_runtime_context():
    manager = CorrectionManager(corrector=BiasCorrector(promoted_state()))
    with pytest.raises(ValueError, match="not promoted"):
        manager.validate_runtime_context(
            target_date="2025-01-01", requested_markets=("home_runs",)
        )


def test_valid_later_in_scope_runtime_context_passes():
    manager = CorrectionManager(corrector=BiasCorrector(promoted_state()))
    manager.validate_runtime_context(
        target_date="2025-01-01", requested_markets=("hits",)
    )


def test_league_blend():
    league = LeagueBaselines(xwoba=0.320)
    corrector = BiasCorrector()
    corrector.state.league_overrides = {"xwoba": 0.330}
    corrector.state.confidence = 0.5
    blended = corrector.apply_league_baselines(league)
    assert blended.xwoba == 0.325
