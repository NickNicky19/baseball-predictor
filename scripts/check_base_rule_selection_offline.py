#!/usr/bin/env python3
"""Mutation checks for the base-rule market-selection boundary.

The legacy evaluator's ``scoreable`` subset correctly uses vendor settlement
presence for its legacy experiment.  The separate base-rule artifact must not
reuse that filter: its scoring boundary is documented official starter/PA
eligibility.  This check proves that a fresh vendor-null quote remains
available for that later official-rule decision.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.evaluation.market_eligibility import (  # noqa: E402
    STATUS_ABSENT,
    STATUS_SCOREABLE,
    STATUS_STALE,
    fresh_quote_pairs,
    policy_source_quote_pairs,
    scoreable,
)
from src.evaluation.identity_keys import MARKET_KEY  # noqa: E402
from src.evaluation.official_hitter_eligibility import apply_base_hitter_rule  # noqa: E402
from scripts.build_base_rule_strict_hits import POLICY_SOURCE_KEY  # noqa: E402


def main() -> int:
    # Same price eligibility fields the shared query returns.  The important
    # row is ``vendor-null-but-fresh``: it must survive quote selection even
    # though it did not have vendor result presence, because the official base
    # rule is applied later by a different artifact.
    def quote_row(*, vendor_game_id: str, player: str, over_age: int,
                  under_age: int, status: str, is_starter: bool,
                  official_pa: int) -> dict:
        """One production-shaped two-sided quote-pair fixture row."""

        return dict(
            vendor_game_id=vendor_game_id,
            start_time="2026-06-01T23:00:00Z",
            player=player,
            entry_over_age_min=over_age,
            entry_under_age_min=under_age,
            entry_age_min=max(over_age, under_age),
            entry_over_odds_decimal=2.0,
            entry_under_odds_decimal=2.0,
            close_over_odds_decimal=2.0,
            close_under_odds_decimal=2.0,
            status=status,
            is_starter=is_starter,
            official_pa=official_pa,
        )

    frame = pd.DataFrame([
        quote_row(vendor_game_id="vendor-null-but-fresh", player="starter",
                  over_age=10, under_age=10, status=STATUS_ABSENT,
                  is_starter=True, official_pa=3),
        quote_row(vendor_game_id="vendor-settled-and-fresh", player="starter",
                  over_age=10, under_age=10, status=STATUS_SCOREABLE,
                  is_starter=True, official_pa=2),
        # The over alone is fresh.  The paired under is stale, so the de-vigged
        # price is not fresh and the selection must not enter the base artifact.
        quote_row(vendor_game_id="vendor-null-and-stale", player="starter",
                  over_age=10, under_age=91, status=STATUS_STALE,
                  is_starter=True, official_pa=4),
        quote_row(vendor_game_id="fresh-substitute", player="substitute",
                  over_age=4, under_age=4, status=STATUS_ABSENT,
                  is_starter=False, official_pa=3),
    ])

    fresh = fresh_quote_pairs(frame, max_quote_age=90)
    got = set(fresh.vendor_game_id)
    assert got == {
        "vendor-null-but-fresh", "vendor-settled-and-fresh", "fresh-substitute"
    }, got
    print("[PASS] fresh quote boundary ignores vendor result presence and excludes stale quotes")

    # Mutation: replacing the new boundary with legacy scoreable() silently
    # loses the vendor-null starter.  This is the discriminating bug the test
    # exists to catch, not an equivalent reimplementation.
    legacy = scoreable(frame)
    assert "vendor-null-but-fresh" not in set(legacy.vendor_game_id)
    print("[PASS] MUTATION legacy vendor-settlement filter loses a fresh starter â€” CAUGHT")

    rule_mask = apply_base_hitter_rule(fresh)
    kept = set(fresh.loc[rule_mask, "vendor_game_id"])
    assert kept == {"vendor-null-but-fresh", "vendor-settled-and-fresh"}, kept
    print("[PASS] official base rule admits fresh starters and removes a fresh substitute")

    uncensored = policy_source_quote_pairs(frame)
    assert set(uncensored.vendor_game_id) == set(frame.vendor_game_id)
    assert "vendor-null-and-stale" in set(uncensored.vendor_game_id)
    print("[PASS] policy source retains stale evidence so the cutoff is not self-censored")

    # Mutation: using the current 90-minute policy as the policy SOURCE makes
    # the inherited threshold impossible to challenge.
    assert "vendor-null-and-stale" not in set(fresh.vendor_game_id)
    print("[PASS] MUTATION applying the inherited cutoff before fitting loses evidence")

    twins = frame.iloc[[0, 2]].copy().reset_index(drop=True)
    twins["mlb_game_pk"] = 700001
    twins["player_id"] = 10
    twins["category"] = "hits"
    twins["line"] = 0.5
    source_twins = policy_source_quote_pairs(twins)
    after_freshness = fresh_quote_pairs(source_twins, max_quote_age=90)
    assert len(source_twins) == 2 and len(after_freshness) == 1
    assert not after_freshness.duplicated(MARKET_KEY).any()
    print("[PASS] freshness precedes duplicate resolution; stale twin creates no final collision")

    preemptive = source_twins[~source_twins.duplicated(MARKET_KEY, keep=False)]
    assert preemptive.empty
    print("[PASS] MUTATION preemptive duplicate exclusion discards a valid fresh row")

    name_variants = pd.DataFrame([
        dict(vendor_game_id="same-game", start_time="2026-04-07T23:05:00Z",
             player="jazz chisholm", player_key="jazz chisholm",
             category="hits", line=0.5),
        dict(vendor_game_id="same-game", start_time="2026-04-07T23:05:00Z",
             player="jazz chisholm jr.", player_key="jazz chisholm",
             category="hits", line=0.5),
    ])
    assert not name_variants.duplicated(POLICY_SOURCE_KEY).any()
    assert name_variants.duplicated(
        ["vendor_game_id", "start_time", "player_key", "category", "line"],
        keep=False,
    ).all()
    print("[PASS] exact vendor name is source identity; normalized name is mapping only")

    # Negative age is not merely stale.  The production helper must refuse it,
    # preserving ctrl4 rather than quietly categorising leakage.
    corrupt = frame.copy()
    corrupt.loc[0, "entry_under_age_min"] = -1
    caught = False
    try:
        fresh_quote_pairs(corrupt, max_quote_age=90)
    except ValueError as exc:
        caught = "NEGATIVE" in str(exc)
    assert caught
    print("[PASS] MUTATION negative entry age remains a hard leakage failure")
    print("9/9 checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
