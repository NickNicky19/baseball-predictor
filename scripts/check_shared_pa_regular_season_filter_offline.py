#!/usr/bin/env python3
"""Mutation checks for the candidate-only regular-season Statcast filter."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.features.canonical_pa_features import canonical_profile  # noqa: E402


def raw(game_types: list[str] | None = None) -> pd.DataFrame:
    rows = []
    for number, game_type in enumerate(game_types or ["R", "S"]):
        rows.append({
            "game_date": "2024-03-20",
            "batter": 1,
            "pitcher": 2,
            "events": "single",
            "description": "hit_into_play",
            "type": "X",
            "zone": 5,
            "launch_speed": 99.0,
            "launch_angle": 20.0,
            "launch_speed_angle": 6,
            "estimated_ba_using_speedangle": 0.5,
            "estimated_woba_using_speedangle": 0.6,
            "estimated_slg_using_speedangle": 0.7,
            "game_type": game_type,
        })
    return pd.DataFrame(rows)


def fails(fn: object) -> bool:
    try:
        fn()  # type: ignore[operator]
    except ValueError:
        return True
    return False


def main() -> int:
    unfiltered = canonical_profile(
        raw(), target_date="2024-03-21", entity_column="batter", entity_id=1, prefix="hitter"
    )
    regular = canonical_profile(
        raw(), target_date="2024-03-21", entity_column="batter", entity_id=1,
        prefix="hitter", allowed_game_types=frozenset({"R"}),
    )
    checks = [
        ("baseline remains unfiltered", unfiltered["hitter_pa"] == 2),
        ("regular policy excludes spring", regular["hitter_pa"] == 1),
        ("missing game type fails closed", fails(lambda: canonical_profile(
            raw().drop(columns=["game_type"]), target_date="2024-03-21", entity_column="batter",
            entity_id=1, prefix="hitter", allowed_game_types=frozenset({"R"}),
        ))),
        ("empty policy rejected", fails(lambda: canonical_profile(
            raw(), target_date="2024-03-21", entity_column="batter", entity_id=1,
            prefix="hitter", allowed_game_types=frozenset(),
        ))),
        ("missing game-type value fails closed", fails(lambda: canonical_profile(
            raw(["R", ""]), target_date="2024-03-21", entity_column="batter", entity_id=1,
            prefix="hitter", allowed_game_types=frozenset({"R"}),
        ))),
        ("same-day rows remain excluded", canonical_profile(
            raw(), target_date="2024-03-20", entity_column="batter", entity_id=1,
            prefix="hitter", allowed_game_types=frozenset({"R"}),
        )["hitter_pa"] == 0),
    ]
    failed = [name for name, passed in checks if not passed]
    if failed:
        raise SystemExit(f"regular-season filter checks failed: {failed}")
    print(f"REGULAR-SEASON FILTER VALID: {len(checks)}/{len(checks)} checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
