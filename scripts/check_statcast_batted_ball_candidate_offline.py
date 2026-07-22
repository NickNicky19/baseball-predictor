"""Offline mutation checks for the candidate batted-ball feature seam."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.savant import SavantClient
from src.data.statcast_batted_ball_rates import barrel_rate, hard_hit_rate
from src.data.statcast_distributions import StatcastDistributionBuilder
from src.features.feature_factory import FeatureFactory
from src.features.legacy_statcast_features import StatcastFeatureEngine


def _raw_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "batter": [101] * 8 + [202] * 8,
            "player_name": ["Alpha"] * 8 + ["Beta"] * 8,
            "events": ["single"] * 16,
            "launch_speed": [94.9, 95.0, 99.0, 100.0, 90.0, 91.0, 92.0, 93.0]
            + [101.0, 102.0, 103.0, 104.0, 96.0, 97.0, 98.0, 99.0],
            "launch_angle": [10.0, 12.0, 28.0, 29.0, 5.0, 6.0, 7.0, 8.0]
            + [28.0, 29.0, 30.0, 31.0, 15.0, 16.0, 17.0, 18.0],
            "launch_speed_angle": [1, 2, 6, 6, 1, 1, 1, 1]
            + [6, 6, 6, 6, 2, 2, 2, 2],
            "estimated_woba_using_speedangle": [0.3] * 16,
            "estimated_ba_using_speedangle": [0.25] * 16,
            "estimated_slg_using_speedangle": [0.4] * 16,
        }
    )


def _assert_close(actual: float, expected: float) -> None:
    assert math.isclose(actual, expected, rel_tol=0, abs_tol=1e-12), (
        actual,
        expected,
    )


def main() -> int:
    frame = _raw_frame()

    disabled = SavantClient(min_pa=1)
    disabled_profiles = disabled.build_hitter_profiles_from_statcast(frame)
    _assert_close(disabled_profiles[101].barrel_rate, 0.085)
    _assert_close(disabled_profiles[101].hard_hit_rate, 0.39)
    print("[OK] disabled core path retains the exact league fallbacks")

    disabled_dist = StatcastDistributionBuilder().build_from_statcast_df(frame)
    _assert_close(disabled_dist[101].barrel_rate, 0.085)
    print("[OK] disabled distribution path retains the exact barrel fallback")

    enabled = SavantClient(min_pa=1, derive_batted_ball_rates=True)
    enabled_profiles = enabled.build_hitter_profiles_from_statcast(frame)
    _assert_close(enabled_profiles[101].barrel_rate, 2 / 8)
    _assert_close(enabled_profiles[101].hard_hit_rate, 3 / 8)
    _assert_close(enabled_profiles[202].barrel_rate, 4 / 8)
    _assert_close(enabled_profiles[202].hard_hit_rate, 1.0)
    assert enabled_profiles[101].barrel_rate != enabled_profiles[202].barrel_rate
    print("[OK] enabled core path derives non-degenerate player rates")

    enabled_dist = StatcastDistributionBuilder(
        derive_batted_ball_rates=True
    ).build_from_statcast_df(frame)
    for player_id in (101, 202):
        _assert_close(
            enabled_dist[player_id].barrel_rate,
            enabled_profiles[player_id].barrel_rate,
        )
        _assert_close(
            enabled_dist[player_id].hard_hit_rate,
            enabled_profiles[player_id].hard_hit_rate,
        )
    print("[OK] core and distribution paths use one definition")

    boundary = pd.DataFrame({"launch_speed": [94.9, 95.0]})
    _assert_close(hard_hit_rate(boundary), 0.5)
    print("[OK] hard-hit boundary is exactly 95 mph")

    explicit = frame.iloc[:4].copy()
    explicit["barrel"] = [0, 0, 0, 0]
    _assert_close(barrel_rate(explicit), 0.0)
    print("[OK] explicit barrel classification overrides bucket derivation")

    fallback = frame.iloc[:4].drop(columns=["launch_speed_angle"]).copy()
    assert barrel_rate(fallback) is not None
    print("[OK] EV/launch-angle fallback remains available")

    engine = StatcastFeatureEngine(min_pa=1, derive_batted_ball_rates=True)
    engine_profiles = engine.build_profiles_from_statcast_df(frame)
    _assert_close(engine_profiles[101].barrel_rate, 2 / 8)
    _assert_close(engine_profiles[101].distribution.barrel_rate, 2 / 8)
    print("[OK] production feature engine threads the candidate flag")

    frozen_factory = FeatureFactory(config={})
    candidate_factory = FeatureFactory(
        config={"feature_factory": {"derive_batted_ball_rates": True}}
    )
    assert frozen_factory.derive_batted_ball_rates is False
    assert frozen_factory.statcast_engine.derive_batted_ball_rates is False
    assert candidate_factory.derive_batted_ball_rates is True
    assert candidate_factory.statcast_engine.derive_batted_ball_rates is True
    print("[OK] absent/false config is frozen; explicit true reaches runtime")

    print("9/9")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
