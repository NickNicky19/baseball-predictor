#!/usr/bin/env python3
"""Mutation checks for the DraftKings HR-over-0.5 research contract."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.evaluation.hr_over_contract import (  # noqa: E402
    expected_profit_per_unit,
    raw_implied_probability_movement,
    validate_quotes,
    validate_research_verdict,
)


def fixture() -> pd.DataFrame:
    return pd.DataFrame([{
        "sportsbook": "draftkings",
        "vendor_market": "player home runs",
        "selection_side": "over",
        "market_date": "2026-04-01",
        "vendor_game_id": "m~a",
        "start_time": "2026-04-01 23:00:00",
        "player": "sample hitter",
        "line": 0.5,
        "horizon": "2026-04-01 19:00:00",
        "settlement_present": True,
        "over_observations": 4,
        "entry_quote_time": "2026-04-01 18:55:00",
        "entry_decimal_odds": 5.0,
        "close_quote_time": "2026-04-01 22:59:00",
        "close_decimal_odds": 4.0,
        "entry_age_min": 5.0,
        "non_over_rows": 0,
    }])


def must_fail(label: str, mutation) -> None:
    frame = fixture()
    mutation(frame)
    try:
        validate_quotes(frame)
    except ValueError:
        print(f"  [OK] MUTATION {label} -> hard fail")
        return
    raise AssertionError(f"mutation did not fail: {label}")


def main() -> int:
    print("HR-OVER CONTRACT — OFFLINE MUTATION CHECK")
    validate_quotes(fixture())
    print("  [OK] exact DraftKings HR over 0.5 fixture passes")
    must_fail("another sportsbook", lambda f: f.__setitem__("sportsbook", "other"))
    must_fail("an under side", lambda f: f.__setitem__("selection_side", "under"))
    must_fail("line 1.5", lambda f: f.__setitem__("line", 1.5))
    must_fail("numeric vendor result reaches contract", lambda f: f.__setitem__("result", 1.0))
    must_fail("negative quote age", lambda f: f.__setitem__("entry_age_min", -1.0))
    must_fail(
        "post-start close",
        lambda f: f.__setitem__("close_quote_time", "2026-04-01 23:00:00"),
    )
    ev = expected_profit_per_unit([0.25], [5.0])
    if not np.allclose(ev, [0.25]):
        raise AssertionError("exact posted-price EV formula changed")
    print("  [OK] exact decimal-payout EV is model_p * odds - 1")
    movement = raw_implied_probability_movement([5.0], [4.0])
    if not np.allclose(movement, [0.05]):
        raise AssertionError("raw over-price movement sign changed")
    print("  [OK] shorter closing odds produce positive raw implied movement")
    good = {
        "betting_authorized": False,
        "may_opened": False,
        "probability_label": "RAW_BREAK_EVEN_INCLUDES_UNKNOWN_MARGIN",
    }
    validate_research_verdict(good)
    print("  [OK] research verdict remains fail-closed")
    fake_fair = dict(good, probability_label="FAIR_DEVIGGED")
    try:
        validate_research_verdict(fake_fair)
    except ValueError:
        print("  [OK] MUTATION one-sided implied price cannot be called fair/de-vigged")
    else:
        raise AssertionError("fake fair label passed")
    authorized = dict(good, betting_authorized=True)
    try:
        validate_research_verdict(authorized)
    except ValueError:
        print("  [OK] MUTATION research artifact cannot authorize betting")
    else:
        raise AssertionError("authorization mutation passed")
    print("  11/11")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
