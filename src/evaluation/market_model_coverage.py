"""Pure, category-correct key coverage calculations for market diagnostics."""
from __future__ import annotations

import pandas as pd

from src.evaluation.identity_keys import MODEL_KEY, require_unique


def hitter_key_gap(
    market: pd.DataFrame, model: pd.DataFrame, official_game_date: str
) -> tuple[pd.DataFrame, pd.DataFrame, set[tuple], set[tuple], set[tuple]]:
    """Return the strict/model hits-key relationship for one canonical date.

    The model artifact also carries strikeout-pitcher rows.  Counting all
    categories when comparing it to a hitters-only market creates a false
    "20 hitters per game" finding (18 hitters plus two pitchers).  This helper
    makes the category boundary explicit and testable.
    """
    market_need = [*MODEL_KEY, "official_game_date"]
    missing_market = [c for c in market_need if c not in market.columns]
    if missing_market:
        raise ValueError(f"strict market missing {missing_market}")
    model_need = [*MODEL_KEY, "game_date", "sim_p_over"]
    missing_model = [c for c in model_need if c not in model.columns]
    if missing_model:
        raise ValueError(f"model artifact missing {missing_model}")

    scoped_market = market.copy()
    scoped_market["official_game_date"] = pd.to_datetime(
        scoped_market["official_game_date"], errors="coerce"
    ).dt.strftime("%Y-%m-%d")
    scoped_market = scoped_market[
        scoped_market["official_game_date"].eq(official_game_date)
    ].copy()
    if scoped_market.empty:
        raise ValueError(f"strict market has no rows on {official_game_date}")

    scoped_model = model.copy()
    scoped_model["game_date"] = pd.to_datetime(
        scoped_model["game_date"], errors="coerce"
    ).dt.strftime("%Y-%m-%d")
    scoped_model = scoped_model[scoped_model["game_date"].eq(official_game_date)].copy()
    require_unique(scoped_model, MODEL_KEY, "model coverage audit")
    hit_model = scoped_model[scoped_model.category.eq("hits")].copy()

    market_keys = set(map(tuple, scoped_market[MODEL_KEY].to_numpy()))
    model_keys = set(map(tuple, hit_model[MODEL_KEY].to_numpy()))
    return scoped_market, hit_model, market_keys, model_keys, market_keys - model_keys
