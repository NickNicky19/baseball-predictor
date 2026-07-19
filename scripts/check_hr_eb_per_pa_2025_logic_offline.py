#!/usr/bin/env python3
"""Synthetic chronology and decision checks before 2025 outcomes are opened."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.hr_eb_per_pa_confirmation import build_predictions, decide, validate_source


DATES = [f"2025-07-{day:02d}" for day in range(1, 13)]


def row(season: int, date: str, game: int, player: int, pa: int, hr: int) -> dict:
    return {
        "builder_schema": "a3.2", "season": season, "game_date": date,
        "game_pk": game, "player_id": player, "out_pa": pa, "out_hr": hr,
    }


def synthetic() -> pd.DataFrame:
    rows = [
        row(2023, "2023-04-01", 1, 10, 100, 5),
        row(2024, "2024-04-01", 2, 10, 100, 5),
        row(2024, "2024-04-01", 2, 20, 100, 1),
    ]
    for index, date in enumerate(DATES, start=10):
        rows.extend([row(2025, date, index, 10, 4, index % 2), row(2025, date, index, 20, 4, 0)])
    return validate_source(pd.DataFrame(rows))


def decision_fixture() -> dict:
    metric = {
        "candidate": {"pa_weighted_roc_auc": .60, "pa_weighted_calibration_intercept": 0.0, "pa_weighted_calibration_slope": 1.0},
        "rolling_league": {"pa_weighted_roc_auc": .50},
        "rolling_raw_player": {"pa_weighted_roc_auc": .55, "pa_weighted_calibration_intercept": .1, "pa_weighted_calibration_slope": .8},
    }
    deltas = {
        "rolling_league": {"pa_weighted_binary_log_loss": -.01, "pa_weighted_binary_brier": -.001},
        "rolling_raw_player": {"pa_weighted_binary_log_loss": -.01, "pa_weighted_binary_brier": -.001},
    }
    return {
        "bootstrap_draws": 20000, "prediction_rows": 24, "coverage": {"eligible_rows": 24},
        "periods": {"full": {"metrics": metric}, "early": {"deltas": deltas}, "late": {"deltas": deltas}},
        "comparisons": {
            name: {"intervals": {
                "candidate_minus_baseline_log_loss_95": [-.02, -.001],
                "candidate_minus_baseline_brier_95": [-.002, -.0001], "valid_draws": 20000,
            }} for name in ("rolling_league", "rolling_raw_player")
        },
    }


def main() -> int:
    source = synthetic()
    before, _ = build_predictions(source, DATES, 200)
    changed = source.copy()
    changed.loc[changed["game_date"].eq(DATES[-1]), "out_hr"] = 4
    after, _ = build_predictions(changed, DATES, 200)
    first = before[before["game_date"].eq(DATES[0])].reset_index(drop=True)
    first_after = after[after["game_date"].eq(DATES[0])].reset_index(drop=True)
    pd.testing.assert_frame_equal(first, first_after)
    same_date = before[before["game_date"].eq(DATES[0])]
    if same_date["prior_league_pa"].nunique() != 1 or same_date["prior_league_hr"].nunique() != 1:
        raise AssertionError("same-date outcome leaked between predictions")
    if not (before["prior_league_pa"].diff().dropna() >= 0).all():
        raise AssertionError("rolling history moved backward")
    passing = decision_fixture()
    if not decide(passing)["per_pa_component_confirmed"]:
        raise AssertionError("complete passing fixture did not pass")
    failures = []
    for mutation in ("interval", "half", "auc", "calibration", "draws", "coverage"):
        bad = copy.deepcopy(passing)
        if mutation == "interval": bad["comparisons"]["rolling_league"]["intervals"]["candidate_minus_baseline_log_loss_95"][1] = .001
        elif mutation == "half": bad["periods"]["late"]["deltas"]["rolling_raw_player"]["pa_weighted_binary_brier"] = .001
        elif mutation == "auc": bad["periods"]["full"]["metrics"]["candidate"]["pa_weighted_roc_auc"] = .49
        elif mutation == "calibration":
            bad["periods"]["full"]["metrics"]["candidate"]["pa_weighted_calibration_intercept"] = .2
            bad["periods"]["full"]["metrics"]["candidate"]["pa_weighted_calibration_slope"] = .5
        elif mutation == "draws": bad["comparisons"]["rolling_raw_player"]["intervals"]["valid_draws"] = 19999
        else: bad["prediction_rows"] = 23
        if decide(bad)["per_pa_component_confirmed"]:
            failures.append(mutation)
    if failures:
        raise AssertionError(f"decision mutations survived: {failures}")
    print("HR EB PER-PA 2025 LOGIC VALID: chronology and 6/6 decision mutations rejected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
