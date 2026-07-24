from __future__ import annotations

import pandas as pd
import pytest

from src.data.savant import SavantClient
from src.data.statcast_batted_ball_contract import StatcastBattedBallContractError
from src.models.dataclasses import StatcastProfile
from src.simulation.pa_simulator import HybridPASimulator


def test_player_level_kwan_shaped_impossible_pair_is_quarantined_at_raw_parse(tmp_path):
    """Regression: a 50% barrel rate with 9.4% hard-hit can never be consumed."""
    frame = pd.DataFrame(
        [{
            "player_id": 680757,
            "player_name": "Steven Kwan",
            "pa": 100,
            "barrel_rate": 0.50,
            "hard_hit_percent": 9.4,
        }]
    )
    csv_path = tmp_path / "player_level.csv"
    frame.to_csv(csv_path, index=False)
    with pytest.raises(StatcastBattedBallContractError, match="impossible barrel_rate"):
        SavantClient(min_pa=1).build_hitter_profiles_from_csv(csv_path)


def test_valid_player_level_batted_ball_pair_preserves_input_rates():
    frame = pd.DataFrame(
        [{
            "player_id": 1,
            "player_name": "Valid",
            "pa": 100,
            "barrel_rate": 0.05,
            "hard_hit_percent": 40.0,
        }]
    )
    profile = SavantClient(min_pa=1)._profiles_from_player_level_csv(frame)[1]
    assert profile.barrel_rate == 0.05
    assert profile.hard_hit_rate == 0.40


def test_probability_consumption_rejects_rich_override_mutation():
    profile = StatcastProfile(
        player_id=1,
        player_name="Valid",
        sample_pa=100,
        barrel_rate=0.05,
        hard_hit_rate=0.40,
    )
    with pytest.raises(StatcastBattedBallContractError, match="impossible barrel_rate"):
        HybridPASimulator().expected_outcome_probabilities(
            statcast=profile,
            rich_features={"barrel_rate": 0.50, "hard_hit_rate": 0.094},
        )


def test_probability_consumption_rejects_partial_rich_override_mutation():
    with pytest.raises(StatcastBattedBallContractError, match="supplies only one"):
        HybridPASimulator().expected_outcome_probabilities(
            rich_features={"barrel_rate": 0.10},
        )
