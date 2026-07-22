"""Hard-keyed, outcome-blind source construction for DraftKings HR over 0.5."""
from __future__ import annotations

import hashlib
import json
import unicodedata
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from src.evaluation.hr_over_contract import (
    BOOK,
    LINE,
    MODEL_CATEGORY,
    SIDE,
    VENDOR_MARKET,
    validate_quotes,
)


PROTOCOL_SCHEMA = "draftkings-hr-over-mapped-source-protocol-v2"
REPORT_SCHEMA = "draftkings-hr-over-mapped-source-report-v1"
RAW_SOURCE_KEY = ["vendor_game_id", "start_time", "player", "line"]
FRAGMENT_KEY = ["vendor_game_id", "start_time"]
CONSUMER_KEY = ["vendor_game_id", "start_time", "player_key"]
MARKET_KEY = ["mlb_game_pk", "player_id", "category", "line"]
OPEN_MONTHS = ["2026-03", "2026-04", "2026-06"]


def sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_json(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root is not an object: {path}")
    return payload


def normalize_player_name(value: object) -> str:
    """Match the locked crosswalk-v3 name normalization exactly."""
    text = "".join(
        character
        for character in unicodedata.normalize("NFKD", str(value))
        if not unicodedata.combining(character)
    )
    text = text.lower().strip()
    for character in ".'`-":
        text = text.replace(character, "")
    return " ".join(
        part
        for part in text.split()
        if part not in ("jr", "sr", "ii", "iii", "iv", "v")
    )


def validate_protocol(protocol: Mapping[str, Any]) -> None:
    if protocol.get("schema_version") != PROTOCOL_SCHEMA:
        raise ValueError("wrong HR-over mapped-source protocol schema")
    if protocol.get("status") != "LOCKED_AFTER_V1_SOURCE_KEY_FALSIFICATION_BEFORE_MAPPED_RESULTS":
        raise ValueError("HR-over mapped-source protocol was not locked before results")
    scope = protocol.get("scope")
    expected_scope = {
        "sportsbook": BOOK,
        "vendor_market": VENDOR_MARKET,
        "category": MODEL_CATEGORY,
        "line": LINE,
        "selection_side": SIDE,
        "entry_horizon_hours": 4,
        "open_market_months": OPEN_MONTHS,
        "may_opened": False,
    }
    if scope != expected_scope:
        raise ValueError("HR-over mapped-source scope changed")
    if protocol.get("betting_authorized") is not False:
        raise ValueError("HR-over identity protocol attempted to authorize betting")
    invariants = protocol.get("protected_invariants", {})
    if invariants.get("official_outcomes_used") is not False:
        raise ValueError("official outcomes entered the identity-only protocol")
    if invariants.get("model_probabilities_used") is not False:
        raise ValueError("model probabilities entered the identity-only protocol")
    if invariants.get("freshness_cutoff_selected") is not False:
        raise ValueError("a freshness cutoff entered the uncensored HR source")


def resolve_input(root: Path, spec: Mapping[str, Any]) -> Path:
    path = Path(str(spec.get("path", "")))
    if not path.is_absolute():
        path = root / path
    expected = str(spec.get("sha256", "")).lower()
    if len(expected) != 64:
        raise ValueError(f"invalid expected SHA-256 for {path}")
    if not path.is_file():
        raise FileNotFoundError(path)
    actual = sha256(path)
    if actual != expected:
        raise ValueError(f"input hash mismatch for {path}: {actual} != {expected}")
    return path


def _utc(series: pd.Series, label: str) -> pd.Series:
    values = pd.to_datetime(series, errors="coerce", utc=True)
    if values.isna().any():
        raise ValueError(f"invalid UTC timestamp in {label}")
    return values


def _utc_nullable(series: pd.Series, label: str) -> pd.Series:
    non_null = series.notna()
    values = pd.to_datetime(series, errors="coerce", utc=True)
    if values[non_null].isna().any():
        raise ValueError(f"invalid UTC timestamp in {label}")
    return values


def validate_quote_source(frame: pd.DataFrame, expected_months: list[str] | None = None) -> pd.DataFrame:
    required = [
        "sportsbook", "vendor_market", "selection_side", "market_date",
        "market_month", "vendor_game_id", "start_time", "player", "line",
        "horizon", "settlement_present", "status", "entry_quote_time",
        "entry_decimal_odds", "close_quote_time", "close_decimal_odds",
        "entry_age_min", "entry_raw_break_even_probability",
        "close_raw_break_even_probability", "raw_implied_probability_movement",
    ]
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError(f"HR-over quote source missing {missing}")
    if "result" in frame.columns:
        raise ValueError("numeric vendor result reached the mapped HR source")
    if frame.empty:
        raise ValueError("HR-over quote source is empty")
    result = frame.copy()
    result["start_time"] = _utc(result.start_time, "quote start_time")
    result["horizon"] = _utc(result.horizon, "quote horizon")
    for column in [
        "entry_quote_time",
        "close_quote_time",
        "first_post_horizon_time",
    ]:
        if column in result.columns:
            result[column] = _utc_nullable(result[column], f"quote {column}")
    result["market_date"] = pd.to_datetime(
        result.market_date, errors="coerce"
    ).dt.strftime("%Y-%m-%d")
    if result.market_date.isna().any():
        raise ValueError("invalid HR-over market_date")
    result["market_month"] = result.market_month.astype(str)
    months = sorted(result.market_month.unique().tolist())
    expected = OPEN_MONTHS if expected_months is None else sorted(expected_months)
    if months != sorted(expected):
        raise ValueError(f"HR-over quote months changed: {months} != {sorted(expected)}")
    if any(month == "2026-05" for month in months) or result.market_date.str.startswith("2026-05").any():
        raise ValueError("May entered the HR-over mapped source")
    if not result.sportsbook.astype(str).str.lower().eq(BOOK).all():
        raise ValueError("another sportsbook entered the HR-over mapped source")
    if not result.vendor_market.astype(str).str.lower().eq(VENDOR_MARKET).all():
        raise ValueError("another market entered the HR-over mapped source")
    if not result.selection_side.astype(str).str.lower().eq(SIDE).all():
        raise ValueError("another side entered the HR-over mapped source")
    line = pd.to_numeric(result.line, errors="coerce")
    if line.isna().any() or not np.isclose(line.to_numpy(float), LINE).all():
        raise ValueError("a line other than HR 0.5 entered the mapped source")
    result["line"] = line.astype(float)
    result["player_key"] = result.player.map(normalize_player_name)
    if result.player_key.eq("").any():
        raise ValueError("HR-over player normalized to an empty key")
    if result[RAW_SOURCE_KEY].isna().any().any() or result.duplicated(RAW_SOURCE_KEY).any():
        raise ValueError("HR-over raw source key is null or duplicated")
    return result


def complete_price_paths(frame: pd.DataFrame) -> pd.DataFrame:
    complete = frame[
        frame.entry_quote_time.notna()
        & frame.entry_decimal_odds.notna()
        & frame.close_quote_time.notna()
        & frame.close_decimal_odds.notna()
    ].copy()
    if complete.empty:
        raise ValueError("no complete HR-over T-4h and close price paths")
    validate_quotes(complete)
    allowed_statuses = {"price_path_observed", "vendor_settlement_absent"}
    statuses = set(complete.status.astype(str).unique())
    if not statuses.issubset(allowed_statuses):
        raise ValueError(f"complete price paths carry impossible statuses: {sorted(statuses)}")
    return complete


def validate_crosswalk_frames(
    players: pd.DataFrame,
    fragments: pd.DataFrame,
    expected_months: list[str],
    report: Mapping[str, Any] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    player_required = [*CONSUMER_KEY, "mlb_game_pk", "player_id"]
    fragment_required = [
        *FRAGMENT_KEY, "slate_date", "outcome", "mlb_game_pk", "official_date"
    ]
    missing_players = [column for column in player_required if column not in players.columns]
    missing_fragments = [column for column in fragment_required if column not in fragments.columns]
    if missing_players or missing_fragments:
        raise ValueError(
            f"crosswalk schema missing players={missing_players}, fragments={missing_fragments}"
        )
    if report is not None:
        months = sorted(str(value) for value in report.get("months", []))
        if months != sorted(expected_months):
            raise ValueError(f"crosswalk report months changed: {months}")
        accepted = [str(value) for value in report.get("accepted_date_universe", [])]
        if any(value.startswith("2026-05") for value in accepted):
            raise ValueError("May entered a crosswalk accepted-date universe")

    clean_players = players.copy()
    clean_fragments = fragments.copy()
    clean_players["start_time"] = _utc(clean_players.start_time, "crosswalk player start_time")
    clean_fragments["start_time"] = _utc(clean_fragments.start_time, "crosswalk fragment start_time")
    clean_players["player_key"] = clean_players.player_key.astype(str)
    if clean_players[CONSUMER_KEY].isna().any().any():
        raise ValueError("crosswalk consumer key is null")
    if clean_players.duplicated(CONSUMER_KEY).any():
        raise ValueError("crosswalk consumer key is duplicated")
    if clean_fragments[FRAGMENT_KEY].isna().any().any():
        raise ValueError("crosswalk fragment key is null")
    if clean_fragments.duplicated(FRAGMENT_KEY).any():
        raise ValueError("crosswalk fragment key is duplicated")

    mapped_fragments = clean_fragments[clean_fragments.outcome.eq("mapped")].copy()
    if mapped_fragments[["mlb_game_pk", "official_date"]].isna().any().any():
        raise ValueError("a hard-mapped fragment lacks canonical game/date identity")
    mapped_fragments["official_date"] = pd.to_datetime(
        mapped_fragments.official_date, errors="coerce"
    ).dt.strftime("%Y-%m-%d")
    if mapped_fragments.official_date.isna().any():
        raise ValueError("a hard-mapped fragment has an invalid official date")
    if mapped_fragments.official_date.str.startswith("2026-05").any():
        raise ValueError("May entered hard-mapped fragment identity")

    check = clean_players.merge(
        mapped_fragments[[*FRAGMENT_KEY, "mlb_game_pk"]].rename(
            columns={"mlb_game_pk": "fragment_game_pk"}
        ),
        on=FRAGMENT_KEY,
        how="left",
        validate="many_to_one",
    )
    if check.fragment_game_pk.isna().any():
        raise ValueError("a crosswalk player row does not belong to a hard-mapped fragment")
    if not pd.to_numeric(check.mlb_game_pk).eq(
        pd.to_numeric(check.fragment_game_pk)
    ).all():
        raise ValueError("crosswalk player and fragment disagree on mlb_game_pk")
    return clean_players, clean_fragments


def map_price_paths(
    complete: pd.DataFrame,
    players: pd.DataFrame,
    fragments: pd.DataFrame,
) -> pd.DataFrame:
    """Return every complete price path with one exhaustive factual map status."""
    before = len(complete)
    fragment_projection = fragments[[
        *FRAGMENT_KEY, "slate_date", "outcome", "mlb_game_pk", "official_date"
    ]].rename(columns={
        "outcome": "fragment_outcome",
        "mlb_game_pk": "fragment_game_pk",
        "official_date": "official_game_date",
    })
    joined = complete.merge(
        fragment_projection,
        on=FRAGMENT_KEY,
        how="left",
        validate="many_to_one",
        indicator="_fragment_join",
    )
    player_projection = players[[*CONSUMER_KEY, "mlb_game_pk", "player_id"]]
    joined = joined.merge(
        player_projection,
        on=CONSUMER_KEY,
        how="left",
        validate="many_to_one",
        indicator="_player_join",
    )
    if len(joined) != before:
        raise AssertionError("identity joins changed the complete price-path row count")

    fragment_present = joined._fragment_join.eq("both")
    if fragment_present.any():
        slate_dates = pd.to_datetime(
            joined.loc[fragment_present, "slate_date"], errors="coerce"
        ).dt.strftime("%Y-%m-%d")
        if slate_dates.isna().any() or not slate_dates.eq(
            joined.loc[fragment_present, "market_date"].astype(str)
        ).all():
            raise ValueError("quote market_date and crosswalk fragment slate_date disagree")

    fragment_missing = joined._fragment_join.ne("both")
    fragment_unmapped = ~fragment_missing & joined.fragment_outcome.ne("mapped")
    player_unmapped = (
        ~fragment_missing
        & ~fragment_unmapped
        & joined._player_join.ne("both")
    )
    hard_mapped = ~(fragment_missing | fragment_unmapped | player_unmapped)
    joined["mapping_status"] = np.select(
        [fragment_missing, fragment_unmapped, player_unmapped],
        ["fragment_missing", "fragment_not_hard_mapped", "player_key_unmapped"],
        default="hard_mapped",
    )
    if int(joined.mapping_status.value_counts().sum()) != len(joined):
        raise AssertionError("HR-over mapping statuses are not exhaustive")

    if hard_mapped.any():
        mapped = joined.loc[hard_mapped]
        if mapped[["mlb_game_pk", "player_id", "fragment_game_pk", "official_game_date"]].isna().any().any():
            raise ValueError("a hard-mapped HR row lacks canonical identity")
        if not pd.to_numeric(mapped.mlb_game_pk).eq(
            pd.to_numeric(mapped.fragment_game_pk)
        ).all():
            raise ValueError("HR player mapping and fragment mapping disagree on game_pk")
        official_dates = pd.to_datetime(
            mapped.official_game_date, errors="coerce"
        ).dt.strftime("%Y-%m-%d")
        if official_dates.isna().any() or official_dates.str.startswith("2026-05").any():
            raise ValueError("invalid or May official date entered mapped HR source")
        joined.loc[hard_mapped, "official_game_date"] = official_dates

    joined["category"] = MODEL_CATEGORY
    joined["duplicate_market_key_group_size"] = 0
    if hard_mapped.any():
        sizes = joined.loc[hard_mapped].groupby(MARKET_KEY, dropna=False)[
            "vendor_game_id"
        ].transform("size")
        joined.loc[hard_mapped, "duplicate_market_key_group_size"] = sizes.to_numpy()
    joined["duplicate_market_key"] = joined.duplicate_market_key_group_size.gt(1)
    return joined.drop(columns=["_fragment_join", "_player_join"])


def assert_duplicate_rows_preserved(source: pd.DataFrame, mapped: pd.DataFrame) -> None:
    if len(source) != len(mapped):
        raise AssertionError("mapping dropped a complete HR-over price path")
    duplicate_rows = mapped[mapped.duplicate_market_key]
    if not duplicate_rows.empty:
        counts = duplicate_rows.groupby(MARKET_KEY, dropna=False).size()
        declared = duplicate_rows.groupby(MARKET_KEY, dropna=False)[
            "duplicate_market_key_group_size"
        ].first()
        if not counts.eq(declared).all():
            raise AssertionError("duplicate HR MARKET_KEY rows were not fully preserved")


def month_funnel(mapped: pd.DataFrame) -> list[dict[str, Any]]:
    scopes: list[tuple[str, pd.DataFrame]] = [("ALL", mapped)]
    scopes.extend((str(month), group) for month, group in mapped.groupby("market_month", sort=True))
    rows: list[dict[str, Any]] = []
    for scope, group in scopes:
        hard = group[group.mapping_status.eq("hard_mapped")]
        duplicate = hard[hard.duplicate_market_key]
        rows.append({
            "scope": scope,
            "complete_price_paths": int(len(group)),
            "vendor_settlement_present": int(group.settlement_present.astype(bool).sum()),
            "vendor_settlement_absent": int((~group.settlement_present.astype(bool)).sum()),
            "fragment_missing": int(group.mapping_status.eq("fragment_missing").sum()),
            "fragment_not_hard_mapped": int(group.mapping_status.eq("fragment_not_hard_mapped").sum()),
            "player_key_unmapped": int(group.mapping_status.eq("player_key_unmapped").sum()),
            "hard_mapped": int(len(hard)),
            "duplicate_market_key_rows": int(len(duplicate)),
            "duplicate_market_keys": int(duplicate.groupby(MARKET_KEY, dropna=False).ngroups),
            "unique_hard_mapped_market_keys": int(hard.groupby(MARKET_KEY, dropna=False).ngroups),
        })
    for row in rows:
        accounted = (
            row["fragment_missing"]
            + row["fragment_not_hard_mapped"]
            + row["player_key_unmapped"]
            + row["hard_mapped"]
        )
        if accounted != row["complete_price_paths"]:
            raise AssertionError("HR-over mapping funnel does not sum")
    return rows
