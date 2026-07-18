#!/usr/bin/env python3
"""Mutation harness for the raw-payout economic enrichment contract."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.evaluation.market_economic_artifact import (  # noqa: E402
    BASE_REQUIRED_FIELDS, validate_economic_enrichment,
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
        print("       The broken artifact passed; this guard is decoration.")


BASE = pd.DataFrame([dict(
    mlb_game_pk=900, player_id=600, category="hits", line=0.5,
    vendor_game_id="g~a", start_time="2026-06-01T23:05:00Z", player="alpha",
    player_key="alpha", market_date="2026-06-01", official_game_date="2026-06-01",
    entry_p_over=0.5, close_p_over=0.5, entry_overround=0.10,
    entry_over_age_min=5, entry_under_age_min=5, entry_age_min=5,
    settlement_present=True, is_starter=True, official_pa=4, base_rule_eligible=True,
)])
assert set(BASE_REQUIRED_FIELDS) == set(BASE.columns)
ENRICHED = BASE.assign(
    entry_over_quote_time="2026-06-01T19:00:00Z",
    entry_under_quote_time="2026-06-01T19:00:00Z",
    close_over_quote_time="2026-06-01T23:00:00Z",
    close_under_quote_time="2026-06-01T23:00:00Z",
    entry_over_odds_decimal=1.8181818181818181,
    entry_under_odds_decimal=1.8181818181818181,
    close_over_odds_decimal=1.8181818181818181,
    close_under_odds_decimal=1.8181818181818181,
)

print("=" * 88)
print("check_market_economic_artifact_offline")
print("=" * 88)
summary = validate_economic_enrichment(BASE, ENRICHED)
check("E1  valid enrichment preserves the one-row baseline", summary["rows"] == 1)
check("E1  valid enrichment reports raw payout fields", "raw_odds" in summary)

# Identity mutation: a regenerated universe must not silently replace baseline.
missing = ENRICHED.iloc[0:0].copy()
mutation("removing a baseline MARKET_KEY FAILS", lambda: validate_economic_enrichment(BASE, missing))

# Value mutation: the same key is not enough.  A changed old field is a new
# historical experiment and must never inherit the baseline's label.
changed_legacy = ENRICHED.copy()
changed_legacy.loc[0, "entry_p_over"] = 0.51
mutation("changing a pre-existing baseline value FAILS", lambda: validate_economic_enrichment(BASE, changed_legacy))

# Raw quote mutation: if odds and de-vig projection disagree, later economic
# analysis would be pricing a different selection than the capture baseline.
changed_raw = ENRICHED.copy()
changed_raw.loc[0, "entry_over_odds_decimal"] = 2.0
mutation("raw odds inconsistent with de-vig probability FAILS", lambda: validate_economic_enrichment(BASE, changed_raw))

invalid_odds = ENRICHED.copy()
invalid_odds.loc[0, "close_under_odds_decimal"] = 1.0
mutation("non-payout decimal odds FAIL", lambda: validate_economic_enrichment(BASE, invalid_odds))

invalid_time = ENRICHED.copy()
invalid_time.loc[0, "entry_under_quote_time"] = "2026-06-01T23:05:00Z"
mutation("a quote at first pitch FAILS (raw timing cannot be erased)",
         lambda: validate_economic_enrichment(BASE, invalid_time))

print("=" * 88)
print(f"{len(PASS)}/{len(PASS) + len(FAIL)}")
for item in FAIL:
    print(f"  FAILED: {item}")
sys.exit(1 if FAIL else 0)
