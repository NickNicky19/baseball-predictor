#!/usr/bin/env python
"""Mutation checks for the HR frozen-baseline diagnostic contract."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.hr_over_baseline_diagnostic import (
    MODEL_KEY,
    at_least_one_probability,
    paired_date_block_interval,
    realized_pa_probability,
    require_exact_keys,
    score_probability,
    settlement_conditioned_over_probability,
    validate_open_dates,
    validate_report,
)


def caught(fn) -> bool:
    try:
        fn()
    except ValueError:
        return True
    return False


def check(condition: bool, label: str) -> None:
    if not condition:
        raise AssertionError(label)
    print(f"  [OK] {label}")


def fixture() -> pd.DataFrame:
    rows = []
    for index, date in enumerate(("2026-04-01", "2026-04-02"), start=1):
        rows.append({
            "mlb_game_pk": index,
            "player_id": 100 + index,
            "category": "home_runs",
            "line": 0.5,
            "official_game_date": date,
            "official_won": index == 1,
            "entry_decimal_odds": 4.0,
            "close_decimal_odds": 3.8,
            "baseline": 0.20,
            "variant": 0.25 if index == 1 else 0.15,
        })
    return pd.DataFrame(rows)


def main() -> int:
    print("HR FROZEN BASELINE DIAGNOSTIC — OFFLINE MUTATIONS")
    weights = {2: 0.25, 4: 0.75}
    actual = at_least_one_probability(0.10, weights)
    expected = 1.0 - (0.25 * 0.9**2 + 0.75 * 0.9**4)
    check(np.isclose(actual, expected), "D1 exact at-least-one mixture identity")
    wrong = sum(weight * (1.0 - (1.0 - 0.10) ** pa) for pa, weight in weights.items()) / 2.0
    check(not np.isclose(actual, wrong), "MUTATION wrong normalization is caught")
    check(caught(lambda: at_least_one_probability(0.1, {2: 0.4, 4: 0.4})),
          "D2 PA weights that do not sum to one fail")
    check(np.isclose(realized_pa_probability(0.1, 3), 1 - 0.9**3),
          "D3 realized-PA oracle has one exact postgame state")
    contract = settlement_conditioned_over_probability(0.1, {1: 0.2, 4: 0.8})
    unconditional = at_least_one_probability(0.1, {1: 0.2, 4: 0.8})
    check(contract["conditional_over_probability"] > unconditional,
          "D3b voiding one-PA/no-HR raises the graded conditional probability")
    check(np.isclose(sum(contract[key] for key in (
        "unconditional_win_probability", "unconditional_loss_probability", "void_probability"
    )), 1.0), "D3c settlement states partition probability mass")
    for odds in (2.0, 4.0, 8.0):
        unconditional_ev = (
            contract["unconditional_win_probability"] * (odds - 1.0)
            - contract["unconditional_loss_probability"]
        )
        conditional_ev = contract["conditional_over_probability"] * odds - 1.0
        check(np.sign(unconditional_ev) == np.sign(conditional_ev),
              f"D3d conditional/unconditional EV signs agree at odds {odds:g}")

    rows = fixture()
    check(not caught(lambda: require_exact_keys(rows, rows.copy(), "fixture")),
          "D4 identical exact key sets pass")
    check(caught(lambda: require_exact_keys(rows.iloc[:-1], rows, "fixture")),
          "MUTATION dropping one feature key fails")
    duplicated = pd.concat([rows, rows.iloc[[0]]], ignore_index=True)
    check(caught(lambda: require_exact_keys(duplicated, rows, "fixture")),
          "MUTATION duplicating one feature key fails")
    check(caught(lambda: validate_open_dates(["2026-04-01", "2026-05-01"])),
          "MUTATION May leakage fails before scoring")

    baseline = score_probability(rows, "baseline")
    variant = score_probability(rows, "variant")
    interval = paired_date_block_interval(
        baseline, variant, ["2026-04-01", "2026-04-02"], draws=500, seed=7
    )
    check(interval["valid_draws"] == 500, "D5 paired date-block interval uses every valid draw")
    changed = variant.copy()
    changed.loc[0, MODEL_KEY[1]] = 999
    check(caught(lambda: paired_date_block_interval(baseline, changed, ["2026-04-01", "2026-04-02"])),
          "MUTATION value comparison cannot hide a changed identity")

    valid_report = {
        "status": "RESEARCH_ONLY",
        "betting_authorized": False,
        "may_opened": False,
        "historical_executability_verified": False,
        "variants": {"oracle_realized_pa": {"deployable": False}},
    }
    check(not caught(lambda: validate_report(valid_report)), "D6 fail-closed research report passes")
    authorized = {**valid_report, "betting_authorized": True}
    check(caught(lambda: validate_report(authorized)),
          "MUTATION betting authorization fails")
    deployable = {**valid_report, "variants": {"oracle_realized_pa": {"deployable": True}}}
    check(caught(lambda: validate_report(deployable)),
          "MUTATION postgame oracle mislabeled deployable fails")
    print("  18/18")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
