"""Contracts for an uncensored chronological hits-policy source.

The source retains quote-age evidence and vendor fragments so freshness can be
fitted without censoring the data by the inherited cutoff. It is not a strict
scoring universe. A candidate cutoff is applied first; only then are every row
on a duplicated final MARKET_KEY excluded symmetrically.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

from src.evaluation.identity_keys import MARKET_KEY
from src.evaluation.market_eligibility import RAW_DECIMAL_ODDS_FIELDS


POLICY_SOURCE_KIND = "hits_policy_source_fragment_grain_v1"
DEFERRED_FRESHNESS_RULE = "deferred_to_chronological_policy_fit_v1"
POLICY_SOURCE_KEY = [
    "vendor_game_id", "start_time", "player", "category", "line",
]
REQUIRED_FIELDS = [
    *MARKET_KEY, *POLICY_SOURCE_KEY, "player_key", "official_game_date",
    "entry_over_age_min", "entry_under_age_min", "entry_age_min",
    "entry_p_over", "close_p_over", "entry_overround",
    *RAW_DECIMAL_ODDS_FIELDS, "is_starter", "official_pa",
    "base_rule_eligible",
]


def validate_policy_source(frame: pd.DataFrame) -> None:
    """Fail closed unless ``frame`` is a valid uncensored source."""
    missing = [column for column in REQUIRED_FIELDS if column not in frame.columns]
    if missing:
        raise ValueError(f"hits policy source missing {missing}")
    if frame[POLICY_SOURCE_KEY].isna().any().any():
        raise ValueError("hits policy source has a null source key")
    if frame.duplicated(POLICY_SOURCE_KEY).any():
        raise ValueError("hits policy source has duplicate exact vendor source keys")

    ages = frame[["entry_over_age_min", "entry_under_age_min", "entry_age_min"]].apply(
        pd.to_numeric, errors="coerce"
    )
    if ages.isna().any().any() or (ages < 0).any().any():
        raise ValueError("hits policy source has missing or negative quote age")
    expected_age = ages[["entry_over_age_min", "entry_under_age_min"]].max(axis=1)
    if not np.array_equal(expected_age.to_numpy(), ages.entry_age_min.to_numpy()):
        raise ValueError("entry_age_min is not the maximum of the two side ages")

    odds = frame[RAW_DECIMAL_ODDS_FIELDS].apply(pd.to_numeric, errors="coerce")
    if (~np.isfinite(odds.to_numpy())).any() or (odds <= 1.0).any().any():
        raise ValueError("hits policy source has invalid posted decimal odds")
    if frame["is_starter"].isna().any() or not frame["is_starter"].astype(bool).all():
        raise ValueError("hits policy source contains a nonstarter or unknown starter role")
    pa = pd.to_numeric(frame.official_pa, errors="coerce")
    if pa.isna().any() or not pa.ge(1.0).all():
        raise ValueError("hits policy source contains a starter without a plate appearance")
    if frame["base_rule_eligible"].isna().any() or not frame[
        "base_rule_eligible"
    ].astype(bool).all():
        raise ValueError("hits policy source contains a base-rule-ineligible row")


def materialize_strict_at_age(
    source: pd.DataFrame, max_quote_age: float
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Apply freshness, then symmetrically exclude final-key collisions."""
    validate_policy_source(source)
    cutoff = float(max_quote_age)
    if not np.isfinite(cutoff) or cutoff < 0:
        raise ValueError("max_quote_age must be finite and non-negative")
    over_age = pd.to_numeric(source.entry_over_age_min, errors="raise")
    under_age = pd.to_numeric(source.entry_under_age_min, errors="raise")
    fresh = source[over_age.le(cutoff) & under_age.le(cutoff)].copy()
    duplicate_mask = fresh.duplicated(MARKET_KEY, keep=False)
    duplicate_rows = fresh[duplicate_mask].copy()
    strict = fresh[~duplicate_mask].copy()
    if strict[MARKET_KEY].isna().any().any() or strict.duplicated(MARKET_KEY).any():
        raise AssertionError("freshness materialization did not produce a strict universe")
    return strict, {
        "source_rows": int(len(source)),
        "fresh_rows": int(len(fresh)),
        "duplicate_rows_excluded": int(len(duplicate_rows)),
        "duplicate_keys_excluded": int(
            duplicate_rows.groupby(MARKET_KEY).ngroups if len(duplicate_rows) else 0
        ),
        "strict_rows": int(len(strict)),
    }


def assert_reproduces_strict_baseline(
    source: pd.DataFrame, baseline: pd.DataFrame, max_quote_age: float
) -> dict[str, Any]:
    """Prove the source recreates a previously certified strict artifact."""
    strict, counts = materialize_strict_at_age(source, max_quote_age)
    if baseline[MARKET_KEY].isna().any().any() or baseline.duplicated(MARKET_KEY).any():
        raise ValueError("comparison baseline is not strict on MARKET_KEY")
    shared = [column for column in baseline.columns if column in strict.columns]
    if set(MARKET_KEY) - set(shared):
        raise ValueError("source and baseline do not share MARKET_KEY")

    left = baseline[shared].sort_values(MARKET_KEY).reset_index(drop=True)
    right = strict[shared].sort_values(MARKET_KEY).reset_index(drop=True)
    try:
        assert_frame_equal(left, right, check_dtype=True, check_exact=True)
    except AssertionError as exc:
        raise ValueError(
            "uncensored policy source does not exactly reproduce the certified "
            f"strict baseline at max_quote_age={max_quote_age}\n{exc}"
        ) from exc
    counts["shared_columns_compared"] = len(shared)
    return counts
