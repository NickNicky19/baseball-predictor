#!/usr/bin/env python3
"""Mutation harness for the chronological payout-aware hits policy fitter."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.evaluation.hits_policy_fit import (  # noqa: E402
    BlockInterval,
    arm_policy_rows,
    build_policy_pairs,
    choose_threshold,
    confirmation_gate,
    date_block_interval,
    empirical_thresholds,
    policy_metrics,
    split_fit_dates,
)
from src.evaluation.hits_policy_source import (  # noqa: E402
    materialize_strict_at_age,
)


PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, condition: bool) -> None:
    (PASS if condition else FAIL).append(name)
    print(f"  [{'OK' if condition else '!!'}] {name}")


def mutation(name: str, fn) -> None:
    caught = False
    try:
        fn()
    except ValueError:
        caught = True
    check(f"MUTATION {name}", caught)
    if not caught:
        print("       The broken input passed; this guard is decoration.")


def source_row(
    vendor: str,
    start: str,
    *,
    game_pk: int = 900,
    player_id: int = 600,
    age: float = 5.0,
    date: str = "2026-04-01",
) -> dict:
    return {
        "mlb_game_pk": game_pk,
        "player_id": player_id,
        "category": "hits",
        "line": 0.5,
        "vendor_game_id": vendor,
        "start_time": start,
        "player": "alpha",
        "player_key": "alpha",
        "market_date": date,
        "official_game_date": date,
        "entry_p_over": 0.5,
        "close_p_over": 0.54,
        "entry_overround": 0.08,
        "entry_over_age_min": age,
        "entry_under_age_min": age,
        "entry_age_min": age,
        "settlement_present": True,
        "entry_over_quote_time": f"{date}T12:00:00Z",
        "entry_under_quote_time": f"{date}T12:00:00Z",
        "close_over_quote_time": f"{date}T19:00:00Z",
        "close_under_quote_time": f"{date}T19:00:00Z",
        "entry_over_odds_decimal": 1.85,
        "entry_under_odds_decimal": 1.85,
        "close_over_odds_decimal": 1.75,
        "close_under_odds_decimal": 1.95,
        "is_starter": True,
        "official_lineup_slot": 1,
        "starter_replaced_in_slot": False,
        "official_pa": 4,
        "base_rule_eligible": True,
    }


def model(date: str = "2026-04-01", p: float = 0.52) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "mlb_game_pk": 900,
                "player_id": 600,
                "category": "hits",
                "line": 0.5,
                "game_date": date,
                "sim_p_over": p,
            }
        ]
    )


def truth(date: str = "2026-04-01") -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "mlb_game_pk": 900,
                "player_id": 600,
                "category": "hits",
                "game_date": date,
                "actual_value": 1,
            }
        ]
    )


print("=" * 92)
print("check_hits_policy_fit_offline")
print("=" * 92)

# P1: the exact posted payout, not the de-vigged distance, decides whether a
# row can enter the non-negative-EV policy family.
pairs = pd.DataFrame(
    [
        {
            "official_game_date": "2026-04-01",
            "p_candidate": 0.52,
            "p_frozen": 0.50,
            "entry_p_over": 0.50,
            "close_p_over": 0.51,
            "entry_over_odds_decimal": 1.85,
            "entry_under_odds_decimal": 1.85,
            "actual_value": 1,
            "line": 0.5,
        }
    ]
)
rows = arm_policy_rows(pairs, "candidate")
check("P1  row has a positive de-vig market edge", float(rows.market_edge.iloc[0]) > 0)
check("P1  exact posted-price expected profit is negative", float(rows.expected_profit.iloc[0]) < 0)
check("P1  negative posted-price EV cannot pass the zero boundary", policy_metrics(rows, 0).selected_rows == 0)
check(
    "MUTATION selecting by de-vig edge would admit the negative-EV row — CAUGHT",
    int((rows.market_edge > 0).sum()) != policy_metrics(rows, 0).selected_rows,
)

# P2: threshold candidates may depend on prices/model outputs, but never on
# CLV, wins, or realised profit.
two_dates = pd.concat(
    [
        rows.assign(official_game_date="2026-04-01", expected_profit=0.01),
        rows.assign(official_game_date="2026-04-02", expected_profit=0.10),
    ],
    ignore_index=True,
)
changed_results = two_dates.assign(clv=[99.0, -99.0], realised_profit=[-1.0, 10.0], won=[0, 1])
check(
    "P2  threshold family is outcome/CLV blind",
    empirical_thresholds(two_dates) == empirical_thresholds(changed_results),
)
mutation(
    "confirmation rows cannot enter threshold selection",
    lambda: choose_threshold(
        two_dates,
        expected_dates=["2026-04-01"],
        bootstrap=10,
        seed=17,
    ),
)

# P3: injecting the sealed May holdout must fail before any fitter can use it.
selector, confirmation = split_fit_dates(
    ["2026-03-25", "2026-03-26", "2026-04-01", "2026-04-02"],
    "2026-05-01",
)
check("P3  deterministic fit split is chronological", max(selector) < min(confirmation))
mutation(
    "a May date in the fit universe HARD-FAILS",
    lambda: split_fit_dates(
        ["2026-03-25", "2026-03-26", "2026-04-01", "2026-05-01"],
        "2026-05-01",
    ),
)

# P4: freshness precedes duplicate exclusion.  A stale extra fragment must not
# erase the one fresh row on the same final MODEL_KEY.
dup_source = pd.DataFrame(
    [
        source_row("g1", "2026-04-01T20:00:00Z", age=5),
        source_row("g1", "2026-04-01T20:01:00Z", age=500),
    ]
)
strict, funnel = materialize_strict_at_age(dup_source, 90)
check("P4  fresh-first materialization retains the one scoreable row", len(strict) == 1)
check("P4  stale extra fragment creates no final duplicate", funnel["duplicate_rows_excluded"] == 0)
preemptive = dup_source[~dup_source.duplicated(
    ["mlb_game_pk", "player_id", "category", "line"], keep=False
)]
check(
    "MUTATION excluding duplicates before freshness erases the valid row — CAUGHT",
    len(preemptive) != len(strict),
)

# P5: exact arm coverage remains fail-closed.
base_source = pd.DataFrame([source_row("g1", "2026-04-01T20:00:00Z")])
valid_pairs, _ = build_policy_pairs(
    base_source,
    model(p=0.50),
    model(p=0.55),
    truth(),
    max_quote_age=90,
    allowed_dates=["2026-04-01"],
)
check("P5  exact source/model/truth fixture joins", len(valid_pairs) == 1)
mutation(
    "removing one candidate MODEL_KEY HARD-FAILS",
    lambda: build_policy_pairs(
        base_source,
        model(p=0.50),
        model(p=0.55).iloc[0:0],
        truth(),
        max_quote_age=90,
        allowed_dates=["2026-04-01"],
    ),
)
mutation(
    "a source date outside the declared fit role HARD-FAILS",
    lambda: build_policy_pairs(
        base_source,
        model(p=0.50),
        model(p=0.55),
        truth(),
        max_quote_age=90,
        allowed_dates=["2026-03-31"],
    ),
)

# P6: passing one bound cannot conceal failure of the other.
passed, _ = confirmation_gate(
    BlockInterval(0.11, 0.20, 100),
    BlockInterval(0.01, 0.04, 100),
    expected_draws=100,
    capture_bar=0.10,
)
check("P6  both strict lower-bound gates pass", passed)
failed_capture, _ = confirmation_gate(
    BlockInterval(0.10, 0.20, 100),
    BlockInterval(0.01, 0.04, 100),
    expected_draws=100,
    capture_bar=0.10,
)
check("MUTATION equality with the +0.10 bar does NOT pass", not failed_capture)
failed_pair, _ = confirmation_gate(
    BlockInterval(0.11, 0.20, 100),
    BlockInterval(0.00, 0.04, 100),
    expected_draws=100,
    capture_bar=0.10,
)
check("MUTATION equality with zero paired improvement does NOT pass", not failed_pair)
failed_draws, _ = confirmation_gate(
    BlockInterval(0.11, 0.20, 99),
    BlockInterval(0.01, 0.04, 100),
    expected_draws=100,
    capture_bar=0.10,
)
check("MUTATION an invalid bootstrap draw cannot disappear", not failed_draws)

# P7: a protocol date with zero strict rows remains an explicit zero-exposure
# block.  Silently dropping it would make every resample valid.
empty_block_interval = date_block_interval(
    two_dates.iloc[[0]],
    0.0,
    block_dates=["2026-04-01", "2026-04-02"],
    bootstrap=100,
    seed=17,
)
check(
    "P7  zero-exposure protocol dates remain in block resampling",
    0 < empty_block_interval.valid_draws < 100,
)
without_empty_block = date_block_interval(
    two_dates.iloc[[0]],
    0.0,
    block_dates=["2026-04-01"],
    bootstrap=100,
    seed=17,
)
check(
    "MUTATION silently dropping the zero-exposure date changes evidence — CAUGHT",
    without_empty_block.valid_draws != empty_block_interval.valid_draws,
)

print("=" * 92)
print(f"{len(PASS)}/{len(PASS) + len(FAIL)}")
for item in FAIL:
    print(f"  FAILED: {item}")
sys.exit(1 if FAIL else 0)
