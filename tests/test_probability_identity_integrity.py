from __future__ import annotations

import copy
import hashlib
import json

import pytest

import src.prediction.daily_predictor as daily_predictor_module
from src.models.dataclasses import LeagueBaselines
from src.prediction.correction_manager import CorrectionManager, CorrectionSettings
from src.prediction.daily_predictor import DailyPredictor
from src.prediction.prop_engine import PropEngine
from src.utils.model_version import model_version


def _state(**changes):
    payload = {
        "category_offsets": {},
        "league_overrides": {"xwoba": 0.321},
        "pa_config_overrides": {},
        "sample_sizes": {"total": 100},
        "confidence": 0.5,
        "version": "1.0",
        "notes": "test fixture",
    }
    payload.update(changes)
    return payload


def _manager(tmp_path, payload=None, **setting_changes):
    path = tmp_path / "correction.json"
    path.write_text(json.dumps(payload or _state()), encoding="utf-8")
    settings = CorrectionSettings(
        enabled=False,
        state_path=str(path),
        apply_model_parameters=setting_changes.get("apply_model_parameters", True),
        apply_projection_offsets=setting_changes.get("apply_projection_offsets", True),
    )
    return CorrectionManager(settings=settings), path


def test_pa_simulator_changes_fork_model_version():
    base = {"league_avg": {"xwoba": 0.320}}
    candidate = copy.deepcopy(base)
    candidate["pa_simulator"] = {"hr_intercept": -3.1}
    assert model_version(base) != model_version(candidate)


def test_correction_state_forks_model_version_without_changing_frozen_default():
    config = {"pa_simulator": {"hr_intercept": -3.28}}
    frozen = model_version(config)
    digest = "a" * 64
    corrected = model_version(config, correction_state_sha256=digest)
    assert corrected != frozen
    assert model_version(config) == frozen
    with pytest.raises(ValueError, match="SHA-256"):
        model_version(config, correction_state_sha256="not-a-hash")


def test_unknown_pa_probability_field_fails_closed():
    with pytest.raises(ValueError, match="unknown probability fields"):
        PropEngine(config={"pa_simulator": {"hr_intercet": -3.1}})


def test_league_reconfiguration_preserves_pa_probability_overrides():
    engine = PropEngine(config={"pa_simulator": {"hr_intercept": -2.75}})
    assert engine.pa_config.hr_intercept == -2.75
    engine.configure_simulation(
        league_baselines=LeagueBaselines(xwoba_on_contact=0.400)
    )
    assert engine.pa_config.hr_intercept == -2.75


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"unexpected": 1}, "schema mismatch"),
        ({"confidence": float("nan")}, "confidence must be finite"),
        ({"confidence": 1.1}, "confidence must be finite"),
        ({"league_overrides": {"not_a_field": 1.0}}, "unknown league"),
        ({"pa_config_overrides": {"not_a_field": 1.0}}, "unknown PA"),
        ({"category_offsets": {"not_a_market": 0.1}}, "unknown correction categories"),
        ({"sample_sizes": {"total": -1}}, "non-negative integer"),
    ],
)
def test_correction_state_mutations_fail_closed(tmp_path, mutation, message):
    payload = _state()
    payload.update(mutation)
    manager, _ = _manager(tmp_path, payload)
    with pytest.raises(ValueError, match=message):
        manager.require_active_state()


def test_missing_and_corrupt_requested_state_fail_closed(tmp_path):
    missing = CorrectionManager(
        settings=CorrectionSettings(state_path=str(tmp_path / "missing.json"))
    )
    with pytest.raises(FileNotFoundError, match="correction state not found"):
        missing.require_active_state()

    corrupt_path = tmp_path / "corrupt.json"
    corrupt_path.write_text("{", encoding="utf-8")
    corrupt = CorrectionManager(
        settings=CorrectionSettings(state_path=str(corrupt_path))
    )
    with pytest.raises(json.JSONDecodeError):
        corrupt.require_active_state()


def test_output_offset_state_is_rejected_as_probability_incoherent(tmp_path):
    manager, _ = _manager(
        tmp_path,
        _state(category_offsets={"hits": 0.1}, league_overrides={}),
    )
    with pytest.raises(ValueError, match="not probability-consistent"):
        manager.require_active_state()


def test_disabled_parameter_override_is_rejected_as_decorative(tmp_path):
    manager, _ = _manager(tmp_path, apply_model_parameters=False)
    with pytest.raises(ValueError, match="unconsumed correction provenance"):
        manager.require_active_state()


def test_correction_identity_binds_source_and_effective_state(tmp_path):
    manager, path = _manager(tmp_path)
    identity = manager.require_active_state()
    assert identity["source_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    expected = hashlib.sha256(
        json.dumps(_state(), sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert identity["effective_state_sha256"] == expected


def test_prediction_provenance_binds_correction_identity(tmp_path, monkeypatch):
    manager, _ = _manager(tmp_path)
    identity = manager.require_active_state()
    predictor = DailyPredictor.__new__(DailyPredictor)
    predictor.config = {"pa_simulator": {"hr_intercept": -3.28}}
    predictor.config_path = None
    predictor._config_supplied_directly = True
    predictor.correction_manager = manager
    monkeypatch.setattr(daily_predictor_module, "code_provenance", lambda root: {"test": True})

    provenance = predictor._feature_provenance(
        game_date="2026-07-22",
        hitter_categories=("hits", "home_runs", "total_bases"),
        include_pitchers=False,
        corrections_active=True,
        edges_requested=False,
        use_projected_lineups=False,
    )
    assert provenance["correction_identity"] == identity
    assert provenance["model_version"] == model_version(
        predictor.config,
        correction_state_sha256=identity["effective_state_sha256"],
    )


def test_valid_parameter_correction_remains_numerically_exact(tmp_path):
    manager, _ = _manager(tmp_path)
    manager.require_active_state()
    league = LeagueBaselines(xwoba=0.320)
    corrected, _ = manager.prepare(
        league,
        PropEngine(league_baselines=league).pa_config,
        PropEngine(league_baselines=league),
        force=True,
    )
    assert corrected.xwoba == 0.3205
