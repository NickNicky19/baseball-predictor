"""Strict-unique, uncensored HR-over source used only for reconstruction."""
from __future__ import annotations

from typing import Any, Mapping

import numpy as np
import pandas as pd

from src.evaluation.hr_over_contract import BOOK, LINE, MODEL_CATEGORY, SIDE, VENDOR_MARKET
from src.evaluation.hr_over_mapped_source import MARKET_KEY, OPEN_MONTHS, RAW_SOURCE_KEY
from src.evaluation.strict_market_artifact import (
    HR_OVER_05_COMPLETE_PATH_RULE,
    ONE_SIDED_FRESHNESS_DEFERRED_RULE,
)


PROTOCOL_SCHEMA = "draftkings-hr-over-reconstruct-source-protocol-v1"
REQUIRED_COLUMNS = [
    *MARKET_KEY,
    *RAW_SOURCE_KEY,
    "official_game_date",
    "sportsbook",
    "vendor_market",
    "selection_side",
    "market_month",
    "entry_age_min",
    "entry_decimal_odds",
    "close_decimal_odds",
    "settlement_present",
    "mapping_status",
]


def validate_protocol(protocol: Mapping[str, Any]) -> None:
    if protocol.get("schema_version") != PROTOCOL_SCHEMA:
        raise ValueError("wrong HR-over reconstruction protocol schema")
    if protocol.get("status") != "LOCKED_BEFORE_STRICT_UNIQUE_HR_RECONSTRUCT_RESULTS":
        raise ValueError("HR-over reconstruction protocol was not locked before results")
    scope = protocol.get("scope", {})
    if scope != {
        "sportsbook": BOOK,
        "vendor_market": VENDOR_MARKET,
        "category": MODEL_CATEGORY,
        "line": LINE,
        "selection_side": SIDE,
        "open_market_months": OPEN_MONTHS,
        "may_opened": False,
    }:
        raise ValueError("HR-over reconstruction protocol scope changed")
    if protocol.get("selection_rule") != HR_OVER_05_COMPLETE_PATH_RULE:
        raise ValueError("HR-over reconstruction selection rule changed")
    if protocol.get("price_freshness_rule") != ONE_SIDED_FRESHNESS_DEFERRED_RULE:
        raise ValueError("HR-over reconstruction freshness rule changed")
    if protocol.get("max_quote_age") is not None:
        raise ValueError("HR-over reconstruction protocol selected a freshness cutoff")
    if protocol.get("settlement_presence_filter_applied") is not False:
        raise ValueError("HR-over reconstruction protocol selected on settlement presence")
    if protocol.get("betting_authorized") is not False:
        raise ValueError("HR-over reconstruction protocol attempted authorization")


def validate_mapped_source(frame: pd.DataFrame) -> pd.DataFrame:
    missing = [column for column in REQUIRED_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"mapped HR-over source missing {missing}")
    forbidden = {"result", "actual", "actual_value", "sim_p_over"}.intersection(frame.columns)
    if forbidden:
        raise ValueError(f"truth/model columns entered HR reconstruction source: {sorted(forbidden)}")
    if frame.empty:
        raise ValueError("mapped HR-over source is empty")
    clean = frame.copy()
    if clean[MARKET_KEY].isna().any().any():
        raise ValueError("mapped HR-over source has a null MARKET_KEY")
    if not clean.category.astype(str).eq(MODEL_CATEGORY).all():
        raise ValueError("another category entered the HR reconstruction source")
    line = pd.to_numeric(clean.line, errors="coerce")
    if line.isna().any() or not np.isclose(line.to_numpy(float), LINE).all():
        raise ValueError("another line entered the HR reconstruction source")
    if not clean.sportsbook.astype(str).str.lower().eq(BOOK).all():
        raise ValueError("another sportsbook entered the HR reconstruction source")
    if not clean.vendor_market.astype(str).str.lower().eq(VENDOR_MARKET).all():
        raise ValueError("another market entered the HR reconstruction source")
    if not clean.selection_side.astype(str).str.lower().eq(SIDE).all():
        raise ValueError("another side entered the HR reconstruction source")
    if not clean.mapping_status.astype(str).eq("hard_mapped").all():
        raise ValueError("an unmapped row entered the HR reconstruction source")
    if clean[RAW_SOURCE_KEY].isna().any().any() or clean.duplicated(RAW_SOURCE_KEY).any():
        raise ValueError("mapped HR source raw identity is null or duplicated")
    clean["official_game_date"] = pd.to_datetime(
        clean.official_game_date, errors="coerce"
    ).dt.strftime("%Y-%m-%d")
    if clean.official_game_date.isna().any() or clean.official_game_date.str.startswith("2026-05").any():
        raise ValueError("invalid or May official date entered HR reconstruction source")
    if sorted(clean.market_month.astype(str).unique().tolist()) != OPEN_MONTHS:
        raise ValueError("HR reconstruction source open months changed")
    age = pd.to_numeric(clean.entry_age_min, errors="coerce")
    if age.isna().any() or (age < 0).any():
        raise ValueError("HR reconstruction source quote age is missing or negative")
    return clean


def strict_unique(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    source = validate_mapped_source(frame)
    duplicate_mask = source.duplicated(MARKET_KEY, keep=False)
    excluded = source[duplicate_mask].copy()
    unique = source[~duplicate_mask].copy()
    validate_derivation(source, unique, excluded)
    return unique, excluded


def _key_set(frame: pd.DataFrame) -> set[tuple[Any, ...]]:
    return set(map(tuple, frame[MARKET_KEY].to_numpy()))


def _row_set(frame: pd.DataFrame) -> set[tuple[Any, ...]]:
    return set(map(tuple, frame[RAW_SOURCE_KEY].astype(str).to_numpy()))


def validate_derivation(
    source: pd.DataFrame,
    unique: pd.DataFrame,
    excluded: pd.DataFrame,
) -> None:
    source_duplicate_mask = source.duplicated(MARKET_KEY, keep=False)
    expected_excluded = source[source_duplicate_mask]
    expected_unique = source[~source_duplicate_mask]
    duplicate_keys = _key_set(expected_excluded)

    if unique[MARKET_KEY].isna().any().any() or unique.duplicated(MARKET_KEY).any():
        raise ValueError("strict HR reconstruction source is null or duplicated")
    if _key_set(unique).intersection(duplicate_keys):
        raise ValueError("one row of an ambiguous HR MARKET_KEY survived")
    if _row_set(unique) != _row_set(expected_unique):
        raise ValueError("strict HR derivation dropped or added a unique raw selection")
    if _row_set(excluded) != _row_set(expected_excluded):
        raise ValueError("strict HR derivation did not exclude every ambiguous raw row")
    if len(unique) + len(excluded) != len(source):
        raise ValueError("strict HR derivation accounting does not sum")

    expected_old = _row_set(expected_unique[pd.to_numeric(expected_unique.entry_age_min) > 90])
    if not expected_old.issubset(_row_set(unique)):
        raise ValueError("an inherited 90-minute freshness cutoff censored HR reconstruction")
    expected_absent = _row_set(expected_unique[~expected_unique.settlement_present.astype(bool)])
    if not expected_absent.issubset(_row_set(unique)):
        raise ValueError("vendor settlement presence censored HR reconstruction")
