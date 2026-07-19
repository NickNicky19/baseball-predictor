#!/usr/bin/env python3
"""Offline boundary and mutation checks for open-2026 source binding."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.open_2026_benchmark_sources import (  # noqa: E402
    cache_key, crosscheck_certified_hitter_outcomes, require_open_dates,
    validate_probability_artifact,
)


def must_fail(label: str, fn) -> None:
    try:
        fn()
    except ValueError:
        print(f"[OK] MUTATION {label} fails")
        return
    raise AssertionError(f"mutation unexpectedly passed: {label}")


def main() -> int:
    rows = []
    for category, line in (("hits", 0.5), ("hits", 1.5), ("home_runs", 0.5),
                           ("hrr", 1.5), ("hrr", 2.5), ("strikeouts", 4.5)):
        rows.append({"mlb_game_pk": 1, "player_id": 2, "game_date": "2026-03-25",
                     "category": category, "line": line, "sim_p_over": 0.5})
    model = pd.DataFrame(rows)
    assert validate_probability_artifact(model) == ["2026-03-25"]
    assert cache_key("https://statsapi.mlb.com/api/v1.1/game/823244/feed/live") == "17599b4c17538e857a2d172d67f19651fd40c157fd66f60e58a9b53ef2a00db8"
    must_fail("May date", lambda: require_open_dates(["2026-05-01"]))
    duplicate = pd.concat([model, model.iloc[[0]]], ignore_index=True)
    must_fail("duplicate MODEL_KEY", lambda: validate_probability_artifact(duplicate))
    missing_category = model[model["category"] != "hrr"]
    must_fail("production category removed", lambda: validate_probability_artifact(missing_category))
    invented_tb = pd.concat([model, pd.DataFrame([{**rows[0], "category": "total_bases"}])], ignore_index=True)
    must_fail("Total Bases relabeled production", lambda: validate_probability_artifact(invented_tb))
    bad_probability = model.copy()
    bad_probability.loc[0, "sim_p_over"] = 1.1
    must_fail("probability above one", lambda: validate_probability_artifact(bad_probability))

    official = pd.DataFrame([{"mlb_game_pk": 1, "player_id": 2, "game_date": "2026-03-25", "pa": 4,
                              "hits": 1, "home_runs": 0}])
    reconstructed = pd.DataFrame([
        {"mlb_game_pk": 1, "player_id": 2, "game_date": "2026-03-25", "category": "hits", "actual_value": 1},
        {"mlb_game_pk": 1, "player_id": 2, "game_date": "2026-03-25", "category": "home_runs", "actual_value": 0},
    ])
    crosscheck_certified_hitter_outcomes(official, reconstructed, model)
    wrong = copy.deepcopy(reconstructed)
    wrong.loc[wrong["category"] == "hits", "actual_value"] = 2
    must_fail("official Hits mismatch", lambda: crosscheck_certified_hitter_outcomes(official, wrong, model))
    print("8/8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
