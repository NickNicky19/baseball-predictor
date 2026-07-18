"""Fail-closed DraftKings HR-over-0.5 research market contract."""
from __future__ import annotations

from typing import Iterable

import numpy as np
import pandas as pd


BOOK = "draftkings"
VENDOR_MARKET = "player home runs"
MODEL_CATEGORY = "home_runs"
LINE = 0.5
SIDE = "over"
SOURCE_KEY = ["vendor_game_id", "start_time", "player", "line"]


def quote_sql(source: str, entry_hours: int) -> str:
    """Return the exact one-sided quote projection without numeric result truth."""
    if int(entry_hours) <= 0:
        raise ValueError("entry_hours must be positive")
    return f"""
    WITH pre AS (
      SELECT game_id, start_time, player, line, lower(side) AS side,
             ts, odds, result IS NOT NULL AS has_result,
             start_time - INTERVAL {int(entry_hours)} HOUR AS horizon
      FROM ({source})
      WHERE lower(book)='{BOOK}' AND lower(market)='{VENDOR_MARKET}'
        AND line={LINE} AND ts<start_time
    ), product_check AS (
      SELECT count(*) FILTER(WHERE side<>'{SIDE}') AS non_over_rows
      FROM pre
    ), selection AS (
      SELECT game_id AS vendor_game_id, start_time, player, line,
        max(horizon) AS horizon,
        bool_or(has_result) AS settlement_present,
        bool_or(has_result) AND bool_or(NOT has_result) AS mixed_settlement_snapshots,
        count(*) AS over_observations,
        max(ts) FILTER(WHERE side='{SIDE}' AND ts<=horizon) AS entry_quote_time,
        arg_max(odds,ts) FILTER(WHERE side='{SIDE}' AND ts<=horizon) AS entry_decimal_odds,
        max(ts) FILTER(WHERE side='{SIDE}') AS close_quote_time,
        arg_max(odds,ts) FILTER(WHERE side='{SIDE}') AS close_decimal_odds,
        min(ts) FILTER(WHERE side='{SIDE}' AND ts>horizon) AS first_post_horizon_time,
        arg_min(odds,ts) FILTER(WHERE side='{SIDE}' AND ts>horizon) AS first_post_horizon_odds
      FROM pre
      WHERE side='{SIDE}'
      GROUP BY game_id,start_time,player,line
    )
    SELECT '{BOOK}' AS sportsbook, '{VENDOR_MARKET}' AS vendor_market,
      '{SIDE}' AS selection_side,
      CAST(s.start_time AT TIME ZONE 'UTC' AT TIME ZONE 'America/New_York' AS DATE) AS market_date,
      s.*,
      date_diff('second',entry_quote_time,horizon)/60.0 AS entry_age_min,
      date_diff('second',horizon,first_post_horizon_time)/60.0 AS post_horizon_gap_min,
      CASE WHEN first_post_horizon_time IS NULL THEN NULL
           ELSE entry_decimal_odds=first_post_horizon_odds END AS same_price_after_horizon,
      p.non_over_rows
    FROM selection s CROSS JOIN product_check p
    """


def validate_quotes(frame: pd.DataFrame) -> None:
    required = [
        "sportsbook", "vendor_market", "selection_side", "market_date",
        *SOURCE_KEY, "horizon", "settlement_present", "over_observations",
        "entry_quote_time", "entry_decimal_odds", "close_quote_time",
        "close_decimal_odds", "entry_age_min", "non_over_rows",
    ]
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError(f"HR-over quote projection missing {missing}")
    if "result" in frame.columns:
        raise ValueError("numeric vendor result reached the HR-over contract")
    if not frame.sportsbook.astype(str).str.lower().eq(BOOK).all():
        raise ValueError("HR-over contract encountered another sportsbook")
    if not frame.vendor_market.astype(str).str.lower().eq(VENDOR_MARKET).all():
        raise ValueError("HR-over contract encountered another product")
    if not frame.selection_side.astype(str).str.lower().eq(SIDE).all():
        raise ValueError("HR-over contract encountered another selection side")
    if len(frame) and not frame.non_over_rows.eq(0).all():
        raise ValueError("DraftKings HR-over contract encountered a non-over side")
    line = pd.to_numeric(frame.line, errors="coerce")
    if line.isna().any() or not np.isclose(line.to_numpy(float), LINE).all():
        raise ValueError("HR-over contract encountered a line other than 0.5")
    if frame[SOURCE_KEY].isna().any().any() or frame.duplicated(SOURCE_KEY).any():
        raise ValueError("HR-over source key is null or duplicated")
    odds = frame[["entry_decimal_odds", "close_decimal_odds"]].apply(
        pd.to_numeric, errors="coerce"
    )
    if odds.isna().any().any() or (~np.isfinite(odds.to_numpy())).any() or (odds <= 1).any().any():
        raise ValueError("HR-over decimal odds must be finite and > 1")
    times = frame[["start_time", "horizon", "entry_quote_time", "close_quote_time"]].apply(
        pd.to_datetime, errors="coerce"
    )
    if times.isna().any().any():
        raise ValueError("HR-over quote projection has an invalid timestamp")
    if (times.entry_quote_time > times.horizon).any():
        raise ValueError("HR-over entry quote post-dates T-4h")
    if (times.close_quote_time >= times.start_time).any():
        raise ValueError("HR-over close does not precede first pitch")
    age = pd.to_numeric(frame.entry_age_min, errors="coerce")
    if age.isna().any() or (age < 0).any():
        raise ValueError("HR-over quote age is missing or negative")


def implied_break_even(decimal_odds: Iterable[float]) -> np.ndarray:
    odds = np.asarray(list(decimal_odds), dtype=float)
    if (~np.isfinite(odds)).any() or (odds <= 1.0).any():
        raise ValueError("decimal odds must be finite and > 1")
    return 1.0 / odds


def expected_profit_per_unit(model_probability: Iterable[float], decimal_odds: Iterable[float]) -> np.ndarray:
    probability = np.asarray(list(model_probability), dtype=float)
    odds = np.asarray(list(decimal_odds), dtype=float)
    if len(probability) != len(odds):
        raise ValueError("probability and odds lengths differ")
    if (~np.isfinite(probability)).any() or ((probability < 0) | (probability > 1)).any():
        raise ValueError("model probability must be finite in [0,1]")
    implied_break_even(odds)
    return probability * odds - 1.0


def raw_implied_probability_movement(entry_odds: Iterable[float], close_odds: Iterable[float]) -> np.ndarray:
    """Positive means the raw closing over price implies greater probability."""
    return implied_break_even(close_odds) - implied_break_even(entry_odds)


def validate_research_verdict(payload: dict) -> None:
    if payload.get("betting_authorized") is not False:
        raise ValueError("HR-over research artifact attempted to authorize betting")
    if payload.get("may_opened") is not False:
        raise ValueError("HR-over research artifact opened May")
    if payload.get("probability_label") != "RAW_BREAK_EVEN_INCLUDES_UNKNOWN_MARGIN":
        raise ValueError("one-sided implied probability was mislabeled fair/de-vigged")
