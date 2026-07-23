from __future__ import annotations

import json

import pytest

from src.simulation.game_simulator import GameSimulator, GameSimulatorInput


def _valid_input(**updates) -> GameSimulatorInput:
    values = {
        "expected_pa": 4.1,
        "pitcher_k_pct": 22.0,
        "pitcher_bb_pct": 8.0,
        "lineup_slot": 3,
    }
    values.update(updates)
    return GameSimulatorInput(**values)


@pytest.mark.parametrize(
    "updates,match",
    [
        ({"expected_pa": float("nan")}, "finite"),
        ({"expected_pa": 0}, "positive"),
        ({"pitcher_k_pct": 101}, "within"),
        ({"pitcher_bb_pct": -1}, "within"),
        ({"pitcher_k_pct": 70, "pitcher_bb_pct": 40}, "cannot exceed"),
        ({"pitcher_k_pct": 99, "umpire_k_bias": 2}, "effective pitcher K"),
        ({"park_hr_factor": 0}, "positive"),
        ({"weather_hr_factor": float("inf")}, "finite"),
        ({"pitcher_hr_per_9": -0.1}, "nonnegative"),
        ({"lineup_slot": 0}, "1 to 9"),
        ({"lineup_slot": "3"}, "1 to 9"),
        ({"lineup_slot": True}, "1 to 9"),
    ],
)
def test_mutation_invalid_simulator_input_fails_closed(updates, match) -> None:
    with pytest.raises(ValueError, match=match):
        _valid_input(**updates)


def test_valid_simulator_input_remains_accepted() -> None:
    value = _valid_input(
        park_hr_factor=1.08,
        park_hits_factor=0.97,
        weather_hr_factor=1.02,
        recent_form_mult=1.05,
        bvp_ops_factor=1.10,
        bvp_hr_factor=1.20,
        pitcher_hr_per_9=1.3,
    )
    assert value.expected_pa == 4.1


def test_configured_missing_pa_distribution_cannot_fall_back(tmp_path) -> None:
    with pytest.raises(FileNotFoundError, match="configured PA distribution"):
        GameSimulator(config={"base_running": {"pa_distribution_path": str(tmp_path / "missing.json")}})


@pytest.mark.parametrize(
    "mutation,match",
    [
        ({"1": {"4": -0.1}}, "missing lineup slots|nonnegative"),
        ({str(slot): {"4": 1.0} for slot in range(1, 9)}, "missing lineup slots"),
        ({str(slot): {"4": float("nan")} for slot in range(1, 10)}, "finite"),
        ({**{str(slot): {"4": 1.0} for slot in range(1, 10)}, "10": {"4": 1.0}}, "lineup slot"),
    ],
)
def test_mutation_malformed_pa_distribution_fails_closed(tmp_path, mutation, match) -> None:
    path = tmp_path / "pa.json"
    path.write_text(json.dumps({"by_lineup_slot": mutation}), encoding="utf-8")
    with pytest.raises(ValueError, match=match):
        GameSimulator(config={"base_running": {"pa_distribution_path": str(path)}})


def test_valid_complete_pa_distribution_is_consumed(tmp_path) -> None:
    path = tmp_path / "pa.json"
    path.write_text(
        json.dumps({"by_lineup_slot": {str(slot): {"3": 1.0} for slot in range(1, 10)}}),
        encoding="utf-8",
    )
    simulator = GameSimulator(
        random_seed=7,
        config={"base_running": {"pa_distribution_path": str(path)}},
    )
    result = simulator.simulate_game(_valid_input())
    assert result.plate_appearances == 3


@pytest.mark.parametrize(
    "kwargs,match",
    [
        ({"base_state_dist": {(0, 0, 0): -1.0}}, "nonnegative"),
        ({"base_state_dist": {(0, 0, 2): 1.0}}, "invalid"),
        ({"base_state_dist": {(0, 0, 0): float("nan")}}, "finite"),
        ({"p_score_from_base": {"typo": 0.2}}, "unknown outcomes"),
        ({"p_score_from_base": {"single": 1.2}}, "within"),
        ({"base_state_mix_rate": 1.1}, "within"),
        ({"run_traffic_boost": -0.1}, "nonnegative"),
        (
            {"p_score_from_base": {"triple": 0.9}, "run_traffic_boost": 0.2},
            "require clipping",
        ),
    ],
)
def test_mutation_invalid_base_running_configuration_fails_closed(kwargs, match) -> None:
    with pytest.raises(ValueError, match=match):
        GameSimulator(**kwargs)
