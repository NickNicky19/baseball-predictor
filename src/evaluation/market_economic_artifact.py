"""Validate an enriched strict-market artifact without replacing its baseline.

The verified June hits baseline intentionally measures model/market information
using de-vigged probabilities.  That is useful, but it cannot calculate the
economic value of an actual posted wager because it does not retain the exact
decimal payout quote.  This module validates a *separate* enriched artifact
before any later policy research may use it.

It is deliberately not a policy fitter, selection rule, or staking engine.
Its only claim is narrower: the enriched artifact has exactly the same market
universe and pre-existing values as the immutable baseline, plus internally
consistent, valid raw payout observations.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

from src.evaluation.identity_keys import MARKET_KEY, require_unique
from src.evaluation.market_eligibility import (
    RAW_DECIMAL_ODDS_FIELDS, RAW_QUOTE_TIMESTAMP_FIELDS,
)


BASE_REQUIRED_FIELDS = [
    *MARKET_KEY,
    "vendor_game_id", "start_time", "player", "player_key", "market_date",
    "official_game_date", "entry_p_over", "close_p_over", "entry_overround",
    "entry_over_age_min", "entry_under_age_min", "entry_age_min",
    "settlement_present", "is_starter", "official_pa",
    "base_rule_eligible",
]


def _require_columns(frame: pd.DataFrame, columns: Sequence[str], label: str) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"{label} missing required columns {missing}")


def _canonical(frame: pd.DataFrame, columns: Sequence[str]) -> pd.DataFrame:
    """Create an exact, type-normalized frame for baseline equality checks."""
    out = frame.loc[:, list(columns)].copy()
    for column in ("start_time",):
        out[column] = pd.to_datetime(out[column], utc=True, errors="raise")
    for column in ("market_date", "official_game_date"):
        out[column] = pd.to_datetime(out[column], errors="raise").dt.strftime("%Y-%m-%d")
    for column in ("line", "entry_p_over", "close_p_over", "entry_overround",
                   "entry_over_age_min", "entry_under_age_min", "entry_age_min",
                   "official_pa"):
        out[column] = pd.to_numeric(out[column], errors="raise")
    out["mlb_game_pk"] = pd.to_numeric(out.mlb_game_pk, errors="raise").astype("int64")
    out["player_id"] = pd.to_numeric(out.player_id, errors="raise").astype("int64")
    out["settlement_present"] = out.settlement_present.astype(bool)
    out["is_starter"] = out.is_starter.astype(bool)
    out["base_rule_eligible"] = out.base_rule_eligible.astype(bool)
    return out.sort_values(MARKET_KEY).reset_index(drop=True)


def _validate_raw_odds(enriched: pd.DataFrame) -> dict[str, float]:
    _require_columns(enriched, [*RAW_DECIMAL_ODDS_FIELDS, *RAW_QUOTE_TIMESTAMP_FIELDS],
                     "enriched artifact")
    numeric = enriched.loc[:, RAW_DECIMAL_ODDS_FIELDS].apply(pd.to_numeric, errors="coerce")
    invalid = (~np.isfinite(numeric.to_numpy()).all(axis=1)
               | (numeric <= 1.0).any(axis=1).to_numpy())
    if invalid.any():
        raise ValueError(
            f"enriched artifact has {int(invalid.sum())} invalid raw decimal quote row(s); "
            "all entry/close over/under odds must be finite and > 1\n"
            f"{enriched.loc[invalid, [*MARKET_KEY, *RAW_DECIMAL_ODDS_FIELDS]].head(20).to_string(index=False)}"
        )

    expected_entry = (1.0 / numeric.entry_over_odds_decimal) / (
        (1.0 / numeric.entry_over_odds_decimal)
        + (1.0 / numeric.entry_under_odds_decimal)
    )
    expected_close = (1.0 / numeric.close_over_odds_decimal) / (
        (1.0 / numeric.close_over_odds_decimal)
        + (1.0 / numeric.close_under_odds_decimal)
    )
    expected_round = (1.0 / numeric.entry_over_odds_decimal) + (
        1.0 / numeric.entry_under_odds_decimal
    ) - 1.0
    observed = enriched[["entry_p_over", "close_p_over", "entry_overround"]].apply(
        pd.to_numeric, errors="coerce"
    )
    checks = {
        "entry_p_over": (expected_entry, observed.entry_p_over),
        "close_p_over": (expected_close, observed.close_p_over),
        "entry_overround": (expected_round, observed.entry_overround),
    }
    for label, (expected, got) in checks.items():
        mismatch = ~np.isclose(expected.to_numpy(float), got.to_numpy(float),
                               rtol=0.0, atol=1e-12, equal_nan=False)
        if mismatch.any():
            sample = enriched.loc[mismatch, [*MARKET_KEY, *RAW_DECIMAL_ODDS_FIELDS,
                                              label]].copy()
            sample["expected"] = expected.loc[mismatch].to_numpy()
            raise ValueError(
                f"raw decimal odds disagree with {label} on {int(mismatch.sum())} row(s). "
                "The enriched artifact is not a faithful extension of the quote boundary.\n"
                f"{sample.head(20).to_string(index=False)}"
            )

    timestamps = enriched.loc[:, ["start_time", *RAW_QUOTE_TIMESTAMP_FIELDS]].copy()
    for column in timestamps.columns:
        timestamps[column] = pd.to_datetime(timestamps[column], utc=True, errors="coerce")
    invalid_time = timestamps.isna().any(axis=1) | (
        (timestamps.entry_over_quote_time >= timestamps.start_time)
        | (timestamps.entry_under_quote_time >= timestamps.start_time)
        | (timestamps.close_over_quote_time >= timestamps.start_time)
        | (timestamps.close_under_quote_time >= timestamps.start_time)
    )
    if invalid_time.any():
        raise ValueError(
            f"enriched artifact has {int(invalid_time.sum())} invalid raw quote-time row(s). "
            "All retained entry/close quotes must have a real timestamp before first pitch.\n"
            f"{enriched.loc[invalid_time, [*MARKET_KEY, *RAW_QUOTE_TIMESTAMP_FIELDS]].head(20).to_string(index=False)}"
        )

    return {
        "entry_over_odds_decimal_min": float(numeric.entry_over_odds_decimal.min()),
        "entry_over_odds_decimal_median": float(numeric.entry_over_odds_decimal.median()),
        "entry_over_odds_decimal_max": float(numeric.entry_over_odds_decimal.max()),
        "close_over_odds_decimal_min": float(numeric.close_over_odds_decimal.min()),
        "close_over_odds_decimal_median": float(numeric.close_over_odds_decimal.median()),
        "close_over_odds_decimal_max": float(numeric.close_over_odds_decimal.max()),
    }


def validate_economic_enrichment(
    baseline: pd.DataFrame, enriched: pd.DataFrame
) -> dict[str, Any]:
    """Fail closed unless ``enriched`` is the baseline plus valid raw odds.

    No tolerance is used for the shared baseline fields.  This is intentional:
    the enrichment is meant to preserve an already certified historical
    experiment, not provide an excuse to regenerate it with subtly different
    eligibility, crosswalk, or quote-selection logic.
    """
    _require_columns(baseline, BASE_REQUIRED_FIELDS, "baseline artifact")
    _require_columns(enriched, [*BASE_REQUIRED_FIELDS, *RAW_DECIMAL_ODDS_FIELDS,
                                *RAW_QUOTE_TIMESTAMP_FIELDS],
                     "enriched artifact")
    require_unique(baseline, MARKET_KEY, "baseline artifact")
    require_unique(enriched, MARKET_KEY, "enriched artifact")

    left = _canonical(baseline, BASE_REQUIRED_FIELDS)
    right = _canonical(enriched, BASE_REQUIRED_FIELDS)
    try:
        assert_frame_equal(left, right, check_dtype=True, check_exact=True)
    except AssertionError as exc:
        raise ValueError(
            "economic enrichment does not exactly reproduce the immutable baseline "
            "on its existing fields. It must be a NEW artifact, not a replacement.\n"
            f"{exc}"
        ) from exc

    odds_summary = _validate_raw_odds(enriched)
    return {
        "rows": int(len(enriched)),
        "markets": sorted(enriched.category.astype(str).unique().tolist()),
        "official_date_universe": sorted(
            pd.to_datetime(enriched.official_game_date).dt.strftime("%Y-%m-%d").unique().tolist()
        ),
        "raw_odds": odds_summary,
    }
