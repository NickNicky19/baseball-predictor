"""Fail-closed contract for the pre-2026 HR batted-ball mapping inputs.

This module validates evidence only.  It does not fit a model or alter live
prediction behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import pandas as pd


SCHEMA_VERSION = "hr-pre2026-batted-ball-input-audit-v1"
ALLOWED_SEASONS = (2023, 2024, 2025)
ROLLER_SCHEMA = "a4.1"
MIN_BIP = 5
IDENTITY = ["game_pk", "player_id"]
RATE_COLUMNS = [
    "roll15_barrel_rate",
    "roll15_hardhit_rate",
    "roll30_barrel_rate",
    "roll30_hardhit_rate",
]
BIP_COLUMNS = ["roll15_bip", "roll30_bip"]
ALLOWED_MAPPING_FEATURES = frozenset(RATE_COLUMNS + BIP_COLUMNS)
REQUIRED_COLUMNS = frozenset(
    [
        "season",
        "game_date",
        "game_pk",
        "player_id",
        "out_pa",
        "out_hits",
        "out_hr",
        "roller_schema",
        *RATE_COLUMNS,
        *BIP_COLUMNS,
    ]
)


@dataclass(frozen=True)
class ValidatedInput:
    frame: pd.DataFrame
    summary: dict[str, object]


def _numeric(frame: pd.DataFrame, column: str, *, integer: bool = False) -> pd.Series:
    values = pd.to_numeric(frame[column], errors="coerce")
    if values.isna().any():
        raise ValueError(f"{column} contains {int(values.isna().sum())} non-numeric/null values")
    if integer and not (values == values.round()).all():
        raise ValueError(f"{column} must contain integer values")
    return values


def validate_feature_allowlist(columns: Iterable[str]) -> tuple[str, ...]:
    selected = tuple(columns)
    if not selected:
        raise ValueError("mapping feature list must not be empty")
    unknown = sorted(set(selected) - ALLOWED_MAPPING_FEATURES)
    if unknown:
        raise ValueError(f"mapping feature list contains forbidden/non-pregame columns: {unknown}")
    if len(selected) != len(set(selected)):
        raise ValueError("mapping feature list contains duplicates")
    return selected


def validate_historical_input(
    frame: pd.DataFrame, *, expected_builder_schema: str | None = None
) -> ValidatedInput:
    """Validate the immutable 2023-2025 point-in-time hitter table."""

    missing = sorted(REQUIRED_COLUMNS - set(frame.columns))
    if missing:
        raise ValueError(f"historical input is missing required columns: {missing}")
    if frame.empty:
        raise ValueError("historical input is empty")

    df = frame.copy()
    if expected_builder_schema is not None:
        if "builder_schema" not in df.columns:
            raise ValueError("historical input is missing builder_schema")
        schemas = set(df["builder_schema"].dropna().astype(str).unique())
        if schemas != {expected_builder_schema} or df["builder_schema"].isna().any():
            raise ValueError(
                f"builder_schema must be exactly {expected_builder_schema}, got {sorted(schemas)}"
            )
    if df[IDENTITY + ["game_date", "season"]].isna().any().any():
        raise ValueError("historical input has null identity/date/season values")
    duplicate = df.duplicated(IDENTITY, keep=False)
    if duplicate.any():
        raise ValueError(f"historical input has {int(duplicate.sum())} duplicate player-game rows")

    seasons = _numeric(df, "season", integer=True).astype(int)
    observed = tuple(sorted(seasons.unique().tolist()))
    if observed != ALLOWED_SEASONS:
        raise ValueError(f"historical seasons must equal {ALLOWED_SEASONS}, got {observed}")

    dates = pd.to_datetime(df["game_date"], format="%Y-%m-%d", errors="coerce")
    if dates.isna().any():
        raise ValueError(f"game_date has {int(dates.isna().sum())} invalid values")
    if not (dates.dt.year.to_numpy() == seasons.to_numpy()).all():
        raise ValueError("game_date year does not match season")

    schemas = set(df["roller_schema"].dropna().astype(str).unique())
    if schemas != {ROLLER_SCHEMA} or df["roller_schema"].isna().any():
        raise ValueError(f"roller_schema must be exactly {ROLLER_SCHEMA}, got {sorted(schemas)}")

    out_pa = _numeric(df, "out_pa", integer=True)
    out_hits = _numeric(df, "out_hits", integer=True)
    out_hr = _numeric(df, "out_hr", integer=True)
    if (out_pa < 0).any() or (out_hits < 0).any() or (out_hr < 0).any():
        raise ValueError("official outcomes must be non-negative")
    if (out_hits > out_pa).any() or (out_hr > out_hits).any():
        raise ValueError("official outcome accounting violates HR <= hits <= PA")

    missing_by_feature: dict[str, int] = {}
    fallback_by_window: dict[str, dict[str, int]] = {}
    for window in (15, 30):
        bip_col = f"roll{window}_bip"
        bip = pd.to_numeric(df[bip_col], errors="coerce")
        nonnumeric_bip = df[bip_col].notna() & bip.isna()
        if nonnumeric_bip.any():
            raise ValueError(f"{bip_col} contains non-numeric values")
        if (bip.dropna() != bip.dropna().round()).any():
            raise ValueError(f"{bip_col} must contain integer values when present")
        if (bip.dropna() < 0).any():
            raise ValueError(f"{bip_col} contains negative counts")
        missing_by_feature[bip_col] = int(bip.isna().sum())
        barrel_col = f"roll{window}_barrel_rate"
        hardhit_col = f"roll{window}_hardhit_rate"
        barrel = pd.to_numeric(df[barrel_col], errors="coerce")
        hardhit = pd.to_numeric(df[hardhit_col], errors="coerce")
        if not barrel.isna().equals(hardhit.isna()):
            raise ValueError(f"roll{window} barrel/hard-hit availability must move as one pair")
        for suffix in ("barrel_rate", "hardhit_rate"):
            col = f"roll{window}_{suffix}"
            rate = pd.to_numeric(df[col], errors="coerce")
            bad = rate.notna() & ((rate < 0) | (rate > 1))
            if bad.any():
                raise ValueError(f"{col} has {int(bad.sum())} values outside [0, 1]")
            present_when_ineligible = rate.notna() & (bip.isna() | (bip < MIN_BIP))
            if present_when_ineligible.any():
                raise ValueError(
                    f"{col} is populated on {int(present_when_ineligible.sum())} rows below the {MIN_BIP}-BIP guard"
                )
            missing_by_feature[col] = int(rate.isna().sum())
        fallback_by_window[str(window)] = {
            "no_history_bip_missing": int(bip.isna().sum()),
            "below_min_bip": int((bip.notna() & (bip < MIN_BIP)).sum()),
            "quality_pair_unavailable_with_sufficient_bip": int(
                (bip.notna() & (bip >= MIN_BIP) & barrel.isna()).sum()
            ),
            "quality_pair_available": int(barrel.notna().sum()),
        }

    by_season: dict[str, dict[str, object]] = {}
    hr_binary = out_hr.gt(0).astype(int)
    for season in ALLOWED_SEASONS:
        mask = seasons.eq(season)
        by_season[str(season)] = {
            "rows": int(mask.sum()),
            "dates": int(dates[mask].nunique()),
            "date_min": dates[mask].min().date().isoformat(),
            "date_max": dates[mask].max().date().isoformat(),
            "hr_occurrences": int(hr_binary[mask].sum()),
            "hr_occurrence_rate": float(hr_binary[mask].mean()),
        }

    summary: dict[str, object] = {
        "rows": int(len(df)),
        "identity_unique": True,
        "seasons": list(ALLOWED_SEASONS),
        "date_min": dates.min().date().isoformat(),
        "date_max": dates.max().date().isoformat(),
        "roller_schema": ROLLER_SCHEMA,
        "official_target": "out_hr > 0",
        "hr_occurrences": int(hr_binary.sum()),
        "hr_occurrence_rate": float(hr_binary.mean()),
        "missing_by_feature": missing_by_feature,
        "fallback_by_window": fallback_by_window,
        "by_season": by_season,
    }
    return ValidatedInput(frame=df, summary=summary)


def validate_manifest_coverage(
    frame: pd.DataFrame,
    manifests: dict[int, dict[str, object]],
    *,
    expected_builder_schema: str = "a3.1",
) -> dict[str, object]:
    """Require every assembled date and row to be declared done by its manifest."""

    result: dict[str, object] = {}
    for season in ALLOWED_SEASONS:
        manifest = manifests.get(season)
        if not manifest:
            raise ValueError(f"missing training manifest for {season}")
        if int(manifest.get("season", -1)) != season:
            raise ValueError(f"manifest season mismatch for {season}")
        if manifest.get("builder_schema") != expected_builder_schema:
            raise ValueError(
                f"manifest {season} builder_schema is not {expected_builder_schema}"
            )
        records = manifest.get("dates")
        if not isinstance(records, dict):
            raise ValueError(f"manifest {season} dates must be a mapping")
        observed = frame.loc[pd.to_numeric(frame["season"]).eq(season)]
        observed_dates = set(observed["game_date"].astype(str))
        done_dates = {d for d, v in records.items() if isinstance(v, dict) and v.get("status") == "done"}
        missing_dates = sorted(observed_dates - done_dates)
        if missing_dates:
            raise ValueError(f"manifest {season} does not declare data dates done: {missing_dates[:5]}")
        declared_rows = sum(
            int(v.get("hitter_rows", 0))
            for v in records.values()
            if isinstance(v, dict) and v.get("status") == "done"
        )
        if declared_rows != len(observed):
            raise ValueError(
                f"manifest {season} declares {declared_rows} hitter rows, artifact has {len(observed)}"
            )
        result[str(season)] = {
            "manifest_dates": len(records),
            "done_dates": len(done_dates),
            "empty_dates": sum(
                1 for v in records.values() if isinstance(v, dict) and v.get("status") == "empty"
            ),
            "declared_hitter_rows": declared_rows,
        }
    return result
