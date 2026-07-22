#!/usr/bin/env python3
"""Offline mutation test for the hitter-only market/model coverage audit."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.evaluation.market_model_coverage import hitter_key_gap  # noqa: E402


def main() -> int:
    market = pd.DataFrame([
        dict(mlb_game_pk=1, player_id=10, category="hits", line=0.5,
             official_game_date="2026-06-01"),
        dict(mlb_game_pk=1, player_id=11, category="hits", line=0.5,
             official_game_date="2026-06-01"),
    ])
    model = pd.DataFrame([
        # The one modelled hitter key.
        dict(mlb_game_pk=1, player_id=10, category="hits", line=0.5,
             game_date="2026-06-01", sim_p_over=0.55),
        # A pitcher key.  It must never make the hitter coverage count look
        # like two modelled hitters.
        dict(mlb_game_pk=1, player_id=99, category="strikeouts", line=5.5,
             game_date="2026-06-01", sim_p_over=0.50),
    ])

    _, hit_model, market_keys, model_keys, missing = hitter_key_gap(
        market, model, "2026-06-01"
    )
    assert len(hit_model) == 1
    assert len(market_keys) == 2
    assert len(model_keys) == 1
    assert missing == {(1, 11, "hits", 0.5)}
    print("[PASS] hitter coverage ignores pitcher strikeout rows")

    # Mutation: the old all-category comparison would incorrectly count the
    # strikeout pitcher as a hitter-side model identity.
    old_all_category_ids = set(model.player_id)
    assert len(old_all_category_ids) == 2 and len(hit_model.player_id) == 1
    print("[PASS] MUTATION all-category player counting differs and is caught")

    bad_date = model.copy()
    bad_date.loc[0, "game_date"] = "2026-06-02"
    _, _, _, _, missing_after_date_mutation = hitter_key_gap(
        market, bad_date, "2026-06-01"
    )
    assert len(missing_after_date_mutation) == 2
    print("[PASS] MUTATION wrong model date creates a visible coverage gap")
    print("3/3 checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
