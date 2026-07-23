from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.prediction.prop_engine import PropEngine
from src.simulation.pa_simulator import INERT_PA_CONFIG_FIELDS, PASimulatorConfig


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"hr_quality": float("nan")}, "finite"),
        ({"hr_quality": "0.2"}, "real number"),
        ({"hr_quality": True}, "real number"),
        ({"k_min": 0.5, "k_max": 0.4}, "k_min <= k_max"),
        ({"xbh_double_share": 0.98, "xbh_triple_share": 0.02}, "sum to less"),
        ({"rolling_quality_weight": 1.1}, "within"),
        ({"xba_shrinkage_pa": 0.0}, "positive"),
        ({"kbb_k_intercept": -1.0}, "forbidden while fitted mode is off"),
        ({"use_fitted_kbb": True}, "requires every fitted coefficient"),
    ],
)
def test_mutation_invalid_pa_config_fails_at_construction(overrides, message):
    with pytest.raises(ValueError, match=message):
        PASimulatorConfig(**overrides)


def test_mutation_unknown_daily_config_key_fails_closed():
    with pytest.raises(ValueError, match="unknown keys"):
        PropEngine(config={"pa_simulator": {"hr_quailty": 0.2}})


def test_mutation_inert_daily_config_override_fails_closed():
    with pytest.raises(ValueError, match="inert legacy fields"):
        PropEngine(config={"pa_simulator": {"single_base_weight": 0.9}})


def test_valid_fitted_config_is_preserved():
    values = {
        "use_fitted_kbb": True,
        "kbb_k_intercept": -1.0,
        "kbb_k_hitter_season": 1.0,
        "kbb_k_hitter_recent": 0.0,
        "kbb_k_pitcher": 0.0,
        "kbb_bb_intercept": -2.0,
        "kbb_bb_hitter_season": 1.0,
        "kbb_bb_hitter_recent": 0.0,
        "kbb_bb_pitcher": 0.0,
    }
    config = PASimulatorConfig(**values)
    assert config.use_fitted_kbb is True
    assert config.kbb_k_intercept == -1.0


def test_every_repository_pa_simulator_config_has_only_consumable_keys():
    paths = []
    valid = set(PASimulatorConfig.__dataclass_fields__) | {
        "kbb_artifact_path", "kbb_artifact_sha256"
    }
    for path in sorted((ROOT / "config").glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if payload.get("pa_simulator"):
            paths.append(path)
            keys = {
                key for key in payload["pa_simulator"]
                if not key.startswith("_")
            }
            assert not keys - valid
            assert not keys & INERT_PA_CONFIG_FIELDS
    assert [path.name for path in paths] == [
        "config.kbb.hits_contact_adapter.json",
        "config.kbb.hits_contact_baseline.json",
        "config.kbb.json",
    ]


def test_configured_missing_kbb_artifact_remains_a_hard_source_failure():
    payload = json.loads(
        (ROOT / "config" / "config.kbb.json").read_text(encoding="utf-8-sig")
    )
    with pytest.raises(ValueError, match="K/BB artifact does not exist"):
        PropEngine(config=payload)
