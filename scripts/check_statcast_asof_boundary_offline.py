#!/usr/bin/env python3
"""Mutation check for the pregame Statcast as-of boundary.

The provider's end date is inclusive. A target-date row is therefore a direct
sentinel for the historical leakage this contract prevents.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.savant import SavantClient  # noqa: E402
from src.features.legacy_statcast_features import StatcastFeatureEngine  # noqa: E402
from src.models.dataclasses import GameContext, HitterGameContext, PlayerIdentity  # noqa: E402


TARGET = "2026-04-30"
PRIOR = "2026-04-29"


def fixture() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for game_date, xba, launch_speed in (
        (PRIOR, 0.20, 86.0),
        (TARGET, 0.90, 106.0),
    ):
        for i in range(8):
            rows.append(
                {
                    "game_date": game_date,
                    "batter": 123,
                    "player_name": "Boundary Hitter",
                    "events": "single",
                    "estimated_ba_using_speedangle": xba,
                    "estimated_woba_using_speedangle": xba,
                    "estimated_slg_using_speedangle": xba,
                    "launch_speed": launch_speed,
                    "launch_angle": 12.0 + i,
                    "barrel": 0,
                    "hard_hit": int(launch_speed >= 95.0),
                    "description": "hit_into_play",
                }
            )
    return pd.DataFrame(rows)


def provider(frame: pd.DataFrame, calls: list[str]) -> SavantClient:
    savant = SavantClient(lookback_days=45, min_pa=1)

    def fetch_statcast_range(
        end_date: str | None = None, lookback_days: int | None = None
    ) -> pd.DataFrame:
        del lookback_days
        assert end_date is not None
        calls.append(end_date)
        return frame.loc[frame["game_date"] <= end_date].copy()

    savant.fetch_statcast_range = fetch_statcast_range  # type: ignore[method-assign]
    return savant


def main() -> int:
    frame = fixture()
    calls: list[str] = []
    engine = StatcastFeatureEngine(lookback_days=45, min_pa=1)
    engine.savant = provider(frame, calls)

    profiles = engine.build_profiles_for_date(TARGET)
    profile = profiles[123]

    checks = [
        (calls == [PRIOR], "target date is converted to the preceding inclusive end date"),
        (profile.sample_pa == 8, "only prior-date observations reach the profile"),
        (abs(float(profile.xba) - 0.20) < 1e-12, "target-date xBA sentinel is excluded"),
    ]

    # Deliberate mutation: restore the old inclusive target-date call. The
    # player's xBA must change materially, proving the harness sees the bug.
    mutation_calls: list[str] = []
    mutated_savant = provider(frame, mutation_calls)
    contaminated = engine.build_profiles_from_statcast_df(
        mutated_savant.fetch_statcast_range(end_date=TARGET)
    )[123]
    mutation_caught = (
        mutation_calls == [TARGET]
        and contaminated.sample_pa == 16
        and abs(float(contaminated.xba) - float(profile.xba)) > 0.25
    )
    checks.append((mutation_caught, "MUTATION: inclusive target date contaminates the profile"))

    # Statcast's player_name field can carry the pitcher's display name even
    # when the profile is correctly keyed by batter MLB ID. Display metadata
    # must come from the canonical hitter identity, without changing features.
    hitter = HitterGameContext(
        player=PlayerIdentity(mlb_id=123, name="Canonical Hitter", team="TST"),
        game=GameContext(
            game_pk=1, game_date=TARGET, venue="Test Park", is_home=True,
            opponent="OPP",
        ),
        lineup_slot=1,
    )
    wrong_name = profiles[123]
    wrong_name.player_name = "Opposing Pitcher"
    resolved = engine.resolve_profile_for_hitter(hitter, {123: wrong_name})
    checks.extend([
        (
            resolved.player_name == "Canonical Hitter",
            "Statcast display name is replaced by canonical hitter metadata",
        ),
        (
            resolved.player_id == wrong_name.player_id
            and resolved.sample_pa == wrong_name.sample_pa
            and resolved.xba == wrong_name.xba,
            "display-name repair does not change prediction features",
        ),
    ])
    mismatched = profiles[123]
    mismatched.player_id = 999
    try:
        engine.resolve_profile_for_hitter(hitter, {123: mismatched})
        identity_failure = False
    except ValueError:
        identity_failure = True
    checks.append((identity_failure, "MUTATION: profile/key MLB-ID mismatch fails closed"))

    for ok, label in checks:
        print(f"  [{'OK' if ok else 'FAIL'}] {label}")
    passed = sum(bool(ok) for ok, _ in checks)
    print(f"\n  {passed}/{len(checks)}")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
