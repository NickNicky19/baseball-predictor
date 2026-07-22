#!/usr/bin/env python3
"""Mutation checks for uncensored source -> strict age materialization."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.evaluation.hits_policy_source import (  # noqa: E402
    assert_reproduces_strict_baseline, materialize_strict_at_age,
    validate_policy_source,
)


def row(vendor: str, player: str, player_key: str, age: int, price: float) -> dict:
    return dict(
        mlb_game_pk=700001, player_id=10, category="hits", line=0.5,
        vendor_game_id=vendor, start_time="2026-04-07T23:05:00Z",
        player=player, player_key=player_key, official_game_date="2026-04-07",
        entry_over_age_min=age, entry_under_age_min=age, entry_age_min=age,
        entry_p_over=price, close_p_over=0.55, entry_overround=0.05,
        entry_over_odds_decimal=1.8, entry_under_odds_decimal=2.1,
        close_over_odds_decimal=1.9, close_under_odds_decimal=2.0,
        is_starter=True, official_pa=4, base_rule_eligible=True,
    )


def main() -> int:
    source = pd.DataFrame([
        row("fragment-fresh", "jazz chisholm", "jazz chisholm", 5, 0.54),
        row("fragment-stale", "jazz chisholm jr.", "jazz chisholm", 120, 0.61),
    ])
    validate_policy_source(source)
    print("[PASS] exact vendor representations coexist in the uncensored source")

    strict, counts = materialize_strict_at_age(source, 90)
    assert len(strict) == 1 and strict.vendor_game_id.iloc[0] == "fragment-fresh"
    assert counts["duplicate_rows_excluded"] == 0
    print("[PASS] freshness runs before duplicate resolution")

    preemptive = source[~source.duplicated(
        ["mlb_game_pk", "player_id", "category", "line"], keep=False
    )]
    assert preemptive.empty
    print("[PASS] MUTATION preemptive hard-key exclusion destroys the valid row")

    baseline = strict.copy()
    result = assert_reproduces_strict_baseline(source, baseline, 90)
    assert result["strict_rows"] == 1
    print("[PASS] uncensored source exactly reproduces a certified strict baseline")

    changed = baseline.copy()
    changed.loc[0, "entry_p_over"] += 0.01
    try:
        assert_reproduces_strict_baseline(source, changed, 90)
    except ValueError as exc:
        assert "does not exactly reproduce" in str(exc)
    else:
        raise AssertionError("changed price passed exact baseline reproduction")
    print("[PASS] MUTATION same key with changed price fails exact reproduction")

    corrupt = source.copy()
    corrupt.loc[0, "entry_under_age_min"] = -1
    try:
        validate_policy_source(corrupt)
    except ValueError as exc:
        assert "negative quote age" in str(exc)
    else:
        raise AssertionError("negative quote age reached policy fitting")
    print("[PASS] MUTATION negative quote age remains a hard failure")
    print("6/6 checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
