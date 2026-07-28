"""Fail-closed contract for the 2023 direct-batter PA feature view.

This module deliberately separates label-free, strictly-prior batter history
from PA outcome targets.  It is a research data boundary, not a fitted model.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from scripts.validate_direct_batter_pa_panel import (
    validate_batted_ball_lineage,
    validate_feature_denominator_lineage,
)
from src.features.direct_batter_pa_history import OUTCOMES


IDENTITY = ["season", "game_date", "game_pk", "player_id"]
LINEAGE = ["max_source_date"]
TARGETS = [f"target_{name}" for name in OUTCOMES]
SOURCE_ONLY = [
    "lineup_slot",
    "out_pa",
    "out_ab",
    "out_hits",
    "out_doubles",
    "out_triples",
    "out_hr",
    "out_bb",
    "out_k",
    *TARGETS,
    "target_date",
    *LINEAGE,
]
PROHIBITED_FEATURE_PREFIXES = (
    "out_",
    "target_",
    "lineup_",
    "opp_sp_",
    "weather_",
    "park_",
    "umpire_",
)
CONTRACT_SHA256 = "54355ea512df8d92ef16c68430079cfa54c88ed6315d017017527ff30a0da20c"
FEATURE_COLUMNS = (
    "history_pitch_count", "history_pa", "history_strikeout_count",
    "history_strikeout_rate", "days_since_strikeout", "history_walk_count",
    "history_walk_rate", "days_since_walk", "history_single_count",
    "history_single_rate", "days_since_single", "history_double_count",
    "history_double_rate", "days_since_double", "history_triple_count",
    "history_triple_rate", "days_since_triple", "history_home_run_count",
    "history_home_run_rate", "days_since_home_run", "history_bip_out_count",
    "history_bip_out_rate", "days_since_bip_out", "history_other_non_ab_count",
    "history_other_non_ab_rate", "days_since_other_non_ab",
    "history_pa_age_days_count", "history_pa_age_days_missing_count",
    "history_pa_age_days_mean", "history_pa_age_days_sd", "days_since_pa",
    "history_description_denominator", "history_description_missing_count",
    "history_swing_count", "history_swing_denominator", "history_swing_rate",
    "history_whiff_count", "history_whiff_denominator", "history_whiff_rate",
    "history_chase_count", "history_chase_denominator", "history_chase_rate",
    "history_zone_count", "history_zone_denominator", "history_zone_missing_count",
    "history_zone_rate", "history_bip", "history_exit_velocity_count",
    "history_exit_velocity_missing_count", "history_exit_velocity_mean",
    "history_exit_velocity_sd", "history_launch_angle_count",
    "history_launch_angle_missing_count", "history_launch_angle_mean",
    "history_launch_angle_sd", "history_batted_ball_denominator",
    "history_barrel_count", "history_hard_hit_count", "history_barrel_rate",
    "history_hard_hit_rate", "history_hard_hit_non_barrel_count",
    "history_other_measured_bbe_count", "history_barrel_share_bbe",
    "history_hard_hit_non_barrel_share_bbe", "history_other_measured_bbe_share_bbe",
    "history_measured_bbe_per_pa", "history_release_speed_count",
    "history_release_speed_missing_count", "history_release_speed_mean",
    "history_release_speed_sd", "history_pfx_x_count", "history_pfx_x_missing_count",
    "history_pfx_x_mean", "history_pfx_x_sd", "history_pfx_z_count",
    "history_pfx_z_missing_count", "history_pfx_z_mean", "history_pfx_z_sd",
    "history_plate_x_count", "history_plate_x_missing_count", "history_plate_x_mean",
    "history_plate_x_sd", "history_plate_z_count", "history_plate_z_missing_count",
    "history_plate_z_mean", "history_plate_z_sd", "history_pitch_type_denominator",
    "history_pitch_type_missing_count", "history_distinct_pitch_types",
    "history_pitch_type_entropy",
)
FEATURE_COLUMNS_SHA256 = "f125c184e324e8b708ff75b11626ea35b4120e7b1ee90eb5ff2c7a956ef5501a"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_contract(path: Path) -> dict[str, Any]:
    if sha256_file(path) != CONTRACT_SHA256:
        raise ValueError("feature-view contract bytes differ from the fixed release digest")
    contract = json.loads(path.read_text(encoding="utf-8"))
    if contract.get("schema_version") != "direct-batter-pa-feature-view-contract-v1":
        raise ValueError("feature-view contract schema changed")
    if contract.get("status") != "LOCKED_LABEL_FREE_2023_RESEARCH_INPUT":
        raise ValueError("feature-view contract is not locked")
    if contract.get("identity_columns") != IDENTITY:
        raise ValueError("feature-view identity contract changed")
    if contract.get("lineage_columns") != LINEAGE:
        raise ValueError("feature-view lineage contract changed")
    if contract.get("target_columns") != TARGETS:
        raise ValueError("feature-view target contract changed")
    features = contract.get("feature_columns")
    if features != list(FEATURE_COLUMNS):
        raise ValueError("feature-view positive allowlist differs from the fixed ordered release schema")
    if hashlib.sha256(("\n".join(features) + "\n").encode("utf-8")).hexdigest() != FEATURE_COLUMNS_SHA256:
        raise ValueError("feature-view ordered allowlist digest differs from the fixed release digest")
    return contract


def expected_panel_columns(contract: dict[str, Any]) -> list[str]:
    return [*IDENTITY, *SOURCE_ONLY, *contract["feature_columns"]]


def _canonical_identity(frame: pd.DataFrame, *, context: str) -> pd.DataFrame:
    result = frame.copy()
    for column in ("season", "game_pk", "player_id"):
        values = pd.to_numeric(result[column], errors="coerce")
        if values.isna().any() or not values.eq(values.round()).all():
            raise ValueError(f"{context} {column} identity is invalid")
        result[column] = values.astype("int64")
    dates = pd.to_datetime(result["game_date"], format="%Y-%m-%d", errors="coerce")
    if dates.isna().any() or not dates.dt.strftime("%Y-%m-%d").eq(result["game_date"].astype(str)).all():
        raise ValueError(f"{context} game_date is not canonical ISO")
    if not dates.dt.year.eq(result["season"]).all() or set(result["season"].unique()) != {2023}:
        raise ValueError(f"{context} is outside the locked 2023 development year")
    if result.duplicated(IDENTITY).any():
        raise ValueError(f"{context} identity is duplicated")
    result["game_date"] = dates.dt.strftime("%Y-%m-%d")
    return result


def _canonical_nullable_iso_dates(values: pd.Series, *, context: str) -> pd.Series:
    present = values.notna()
    raw = values.loc[present]
    if not raw.map(lambda value: isinstance(value, str)).all():
        raise ValueError(f"{context} contains a non-string non-null date")
    if not raw.str.fullmatch(r"\d{4}-\d{2}-\d{2}").all():
        raise ValueError(f"{context} is not canonical nullable ISO")
    parsed = pd.to_datetime(raw, format="%Y-%m-%d", errors="coerce")
    if parsed.isna().any() or not parsed.dt.strftime("%Y-%m-%d").eq(raw).all():
        raise ValueError(f"{context} contains an invalid calendar date")
    result = pd.Series(pd.NaT, index=values.index, dtype="datetime64[ns]")
    result.loc[present] = parsed
    return result


def validate_all_feature_numerics(frame: pd.DataFrame) -> None:
    for column in FEATURE_COLUMNS:
        values = pd.to_numeric(frame[column], errors="raise")
        present = values.notna()
        if not np.isfinite(values.loc[present].to_numpy(dtype=float)).all():
            raise ValueError(f"{column} contains a non-finite value")
        is_count = (
            column.endswith(("_count", "_denominator", "_missing_count"))
            or column in {"history_pa", "history_bip", "history_pitch_count", "history_distinct_pitch_types"}
        )
        if is_count and ((values.loc[present] < 0).any() or not values.loc[present].mod(1).eq(0).all()):
            raise ValueError(f"{column} count/support is negative or nonintegral")
    distinct = pd.to_numeric(frame["history_distinct_pitch_types"], errors="raise")
    support = pd.to_numeric(frame["history_pitch_type_denominator"], errors="raise")
    if (distinct > support).any():
        raise ValueError("distinct pitch types exceed pitch-type support")


def validate_history_outcome_lineage(frame: pd.DataFrame) -> None:
    history_pa = pd.to_numeric(frame["history_pa"], errors="raise")
    if history_pa.isna().any() or (history_pa < 0).any() or not history_pa.mod(1).eq(0).all():
        raise ValueError("history PA denominator is invalid")
    counts: list[pd.Series] = []
    for outcome in OUTCOMES:
        count_name = f"history_{outcome}_count"
        rate_name = f"history_{outcome}_rate"
        recency_name = f"days_since_{outcome}"
        values = frame[[count_name, rate_name, recency_name]].apply(pd.to_numeric, errors="coerce")
        count = values[count_name]
        if count.isna().any() or (count < 0).any() or not count.mod(1).eq(0).all():
            raise ValueError(f"{outcome} history count is invalid")
        if (count > history_pa).any():
            raise ValueError(f"{outcome} history count exceeds PA denominator")
        positive_pa = history_pa.gt(0)
        if values.loc[~positive_pa, rate_name].notna().any():
            raise ValueError(f"{outcome} history rate exists with zero PA denominator")
        if values.loc[positive_pa, rate_name].isna().any():
            raise ValueError(f"{outcome} history rate is missing with positive PA denominator")
        expected = count.loc[positive_pa] / history_pa.loc[positive_pa]
        if not values.loc[positive_pa, rate_name].sub(expected).abs().le(1e-12).all():
            raise ValueError(f"{outcome} history count/rate serialization mismatch")
        has_outcome = count.gt(0)
        if values.loc[has_outcome, recency_name].isna().any():
            raise ValueError(f"{outcome} recency missing with positive count")
        if values.loc[~has_outcome, recency_name].notna().any():
            raise ValueError(f"{outcome} recency exists with zero count")
        if (values.loc[has_outcome, recency_name] < 1).any():
            raise ValueError(f"{outcome} recency is not strictly prior")
        counts.append(count)
    if not pd.concat(counts, axis=1).sum(axis=1).eq(history_pa).all():
        raise ValueError("history PA outcome counts do not sum to history PA")


def validate_batted_ball_composition(frame: pd.DataFrame) -> None:
    columns = [
        "history_batted_ball_denominator", "history_barrel_count", "history_hard_hit_count",
        "history_hard_hit_non_barrel_count", "history_other_measured_bbe_count",
        "history_barrel_share_bbe", "history_hard_hit_non_barrel_share_bbe",
        "history_other_measured_bbe_share_bbe", "history_measured_bbe_per_pa",
        "history_exit_velocity_count", "history_pa",
    ]
    values = frame[columns].apply(pd.to_numeric, errors="raise")
    measured_columns = columns[:9]
    partial = values[measured_columns].notna().any(axis=1) & ~values[measured_columns].notna().all(axis=1)
    if partial.any():
        raise ValueError("partial measured batted-ball composition")
    present = values["history_batted_ball_denominator"].notna()
    if not values.loc[present, "history_batted_ball_denominator"].eq(
        values.loc[present, "history_exit_velocity_count"]
    ).all():
        raise ValueError("batted-ball denominator differs from measured exit-velocity support")
    if not values.loc[~present, "history_exit_velocity_count"].eq(0).all():
        raise ValueError("batted-ball denominator missing despite measured exit velocity")
    denominator = values.loc[present, "history_batted_ball_denominator"]
    barrel = values.loc[present, "history_barrel_count"]
    hard = values.loc[present, "history_hard_hit_count"]
    hard_non_barrel = values.loc[present, "history_hard_hit_non_barrel_count"]
    other = values.loc[present, "history_other_measured_bbe_count"]
    if not hard_non_barrel.eq(hard - barrel).all() or not other.eq(denominator - hard).all():
        raise ValueError("batted-ball mutually exclusive counts are incoherent")
    for actual, numerator in (
        ("history_barrel_share_bbe", barrel),
        ("history_hard_hit_non_barrel_share_bbe", hard_non_barrel),
        ("history_other_measured_bbe_share_bbe", other),
    ):
        if not values.loc[present, actual].sub(numerator / denominator).abs().le(1e-12).all():
            raise ValueError(f"{actual} differs from count/denominator")
    history_pa = values.loc[present, "history_pa"]
    if (history_pa <= 0).any() or not values.loc[present, "history_measured_bbe_per_pa"].sub(
        denominator / history_pa
    ).abs().le(1e-12).all():
        raise ValueError("measured BBE per PA differs from count/PA denominator")


def validate_target_frame(
    targets: pd.DataFrame, *, feature_identity: pd.DataFrame | None = None,
) -> pd.DataFrame:
    if list(targets.columns) != [*IDENTITY, *TARGETS]:
        raise ValueError("target artifact schema/order changed")
    if feature_identity is not None and len(feature_identity) != len(targets):
        raise ValueError("feature and target identities are not in exact one-to-one order")
    normalized = _canonical_identity(targets, context="target artifact")
    values = normalized[TARGETS].apply(pd.to_numeric, errors="raise")
    if values.isna().any().any() or (values < 0).any().any():
        raise ValueError("target counts are missing or negative")
    if not np.isfinite(values.to_numpy(dtype=float)).all():
        raise ValueError("target counts are non-finite")
    if not np.equal(values, np.floor(values)).all(axis=None):
        raise ValueError("target counts are nonintegral")
    normalized[TARGETS] = values.astype("int64")
    if feature_identity is not None:
        canonical_features = _canonical_identity(feature_identity, context="feature identity")
        if len(canonical_features) != len(normalized) or not canonical_features[IDENTITY].equals(
            normalized[IDENTITY]
        ):
            raise ValueError("feature and target identities are not in exact one-to-one order")
    return normalized


def validate_feature_frame(frame: pd.DataFrame, contract: dict[str, Any]) -> None:
    expected = [*IDENTITY, *LINEAGE, *contract["feature_columns"]]
    if list(frame.columns) != expected:
        raise ValueError("feature-view columns or order differ from the exact positive schema")
    _canonical_identity(frame[IDENTITY], context="feature view")
    target_date = _canonical_nullable_iso_dates(frame["game_date"], context="game_date")
    source_date = _canonical_nullable_iso_dates(frame["max_source_date"], context="max_source_date")
    validate_all_feature_numerics(frame)
    history_pa = pd.to_numeric(frame["history_pa"], errors="raise")
    if source_date.notna().any() and source_date.loc[source_date.notna()].ge(
        target_date.loc[source_date.notna()]
    ).any():
        raise ValueError("feature view contains same-day or future history")
    if source_date.loc[history_pa.gt(0)].isna().any():
        raise ValueError("positive history is missing max_source_date")
    if source_date.loc[history_pa.eq(0)].notna().any():
        raise ValueError("zero history has a fabricated max_source_date")
    validate_history_outcome_lineage(frame)
    validate_batted_ball_lineage(frame)
    validate_batted_ball_composition(frame)
    validate_feature_denominator_lineage(frame)
    if pd.to_numeric(frame["history_pitch_count"], errors="raise").lt(history_pa).any():
        raise ValueError("history pitch count is below history PA")
    same_day_groups = frame.duplicated(["season", "game_date", "player_id"], keep=False)
    if same_day_groups.any():
        feature_and_lineage = [*LINEAGE, *contract["feature_columns"]]
        varying = frame.loc[same_day_groups].groupby(
            ["season", "game_date", "player_id"], dropna=False
        )[feature_and_lineage].nunique(dropna=False)
        if varying.gt(1).any(axis=None):
            raise ValueError("doubleheader rows do not share conservative prior-date features")


def split_panel(panel: pd.DataFrame, contract: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame]:
    expected = expected_panel_columns(contract)
    if list(panel.columns) != expected:
        extras = [column for column in panel.columns if column not in expected]
        missing = [column for column in expected if column not in panel.columns]
        raise ValueError(
            "source panel differs from exact schema or order: "
            f"extras={extras[:5]} missing={missing[:5]}"
        )
    canonical = _canonical_identity(panel, context="source panel")
    if not canonical[IDENTITY].equals(panel[IDENTITY].reset_index(drop=True)):
        panel = panel.copy()
        panel.loc[:, IDENTITY] = canonical[IDENTITY]
    feature_view = panel.loc[:, [*IDENTITY, *LINEAGE, *contract["feature_columns"]]].copy()
    targets = panel.loc[:, [*IDENTITY, *TARGETS]].copy()
    validate_feature_frame(feature_view, contract)
    targets = validate_target_frame(targets, feature_identity=feature_view[IDENTITY])
    return feature_view, targets


def deterministic_csv_gzip(frame: pd.DataFrame) -> bytes:
    text = frame.to_csv(index=False, lineterminator="\n", float_format="%.17g")
    buffer = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buffer, mtime=0, compresslevel=9) as handle:
        handle.write(text.encode("utf-8"))
    return buffer.getvalue()
