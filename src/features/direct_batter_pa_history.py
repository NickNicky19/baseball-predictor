"""Strictly-prior batter-only PA history features.

The transformer deliberately has no opponent or current-game context.  Every
source row must be from a completed regular-season game strictly before the
target date.  Missing history remains explicit; no baseball value is imputed.
"""
from __future__ import annotations

from collections import Counter
from math import log
from typing import Any

import numpy as np
import pandas as pd

from src.data.statcast_integrity import derive_batted_ball_evidence


OUTCOMES = (
    "strikeout", "walk", "single", "double", "triple", "home_run",
    "bip_out", "other_non_ab",
)
EVENT_TO_OUTCOME = {
    "strikeout": "strikeout", "strikeout_double_play": "strikeout",
    "walk": "walk", "intent_walk": "walk",
    "single": "single", "double": "double", "triple": "triple", "home_run": "home_run",
    "double_play": "bip_out", "field_error": "bip_out", "field_out": "bip_out",
    "fielders_choice": "bip_out", "fielders_choice_out": "bip_out",
    "force_out": "bip_out", "grounded_into_double_play": "bip_out",
    "other_out": "bip_out", "triple_play": "bip_out",
    "catcher_interf": "other_non_ab", "hit_by_pitch": "other_non_ab",
    "sac_bunt": "other_non_ab", "sac_fly": "other_non_ab",
    "sac_fly_double_play": "other_non_ab",
}
NON_PA_EVENTS = frozenset({"truncated_pa"})
SWINGS = frozenset({"swinging_strike", "swinging_strike_blocked", "foul", "foul_tip", "hit_into_play"})
WHIFFS = frozenset({"swinging_strike", "swinging_strike_blocked"})
REQUIRED_COLUMNS = frozenset({
    "game_date", "game_type", "batter", "events", "description", "type", "zone",
    "pitch_type", "release_speed", "pfx_x", "pfx_z", "plate_x", "plate_z",
    "launch_speed", "launch_angle", "launch_speed_angle",
})


def _finite(values: pd.Series) -> np.ndarray:
    return pd.to_numeric(values, errors="coerce").dropna().to_numpy(float)


def _moments(values: np.ndarray, prefix: str) -> dict[str, float | None]:
    if len(values) == 0:
        return {f"{prefix}_mean": None, f"{prefix}_sd": None}
    return {f"{prefix}_mean": float(values.mean()), f"{prefix}_sd": float(values.std(ddof=0))}


def _rate(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else float(numerator / denominator)


def batted_ball_composition(
    *, denominator: int | None, barrel_count: int | None,
    hard_hit_count: int | None, history_pa: int,
) -> dict[str, int | float | None]:
    """Return mutually exclusive, count-bearing batted-ball composition."""
    values = (denominator, barrel_count, hard_hit_count)
    if all(value is None for value in values):
        return {
            "history_hard_hit_non_barrel_count": None,
            "history_other_measured_bbe_count": None,
            "history_barrel_share_bbe": None,
            "history_hard_hit_non_barrel_share_bbe": None,
            "history_other_measured_bbe_share_bbe": None,
            "history_measured_bbe_per_pa": None,
        }
    if any(value is None for value in values):
        raise ValueError("partial batted-ball composition counts")
    if any(not isinstance(value, int) or value < 0 for value in values):
        raise ValueError("batted-ball composition counts must be nonnegative integers")
    if not isinstance(history_pa, int) or history_pa < 0:
        raise ValueError("history_pa must be a nonnegative integer")
    assert denominator is not None and barrel_count is not None and hard_hit_count is not None
    if denominator == 0 or barrel_count > hard_hit_count or hard_hit_count > denominator:
        raise ValueError("impossible batted-ball composition ordering")
    if denominator > history_pa:
        raise ValueError("measured BBE denominator exceeds prior PA exposure")
    hard_hit_non_barrel = hard_hit_count - barrel_count
    other = denominator - hard_hit_count
    return {
        "history_hard_hit_non_barrel_count": hard_hit_non_barrel,
        "history_other_measured_bbe_count": other,
        "history_barrel_share_bbe": barrel_count / denominator,
        "history_hard_hit_non_barrel_share_bbe": hard_hit_non_barrel / denominator,
        "history_other_measured_bbe_share_bbe": other / denominator,
        "history_measured_bbe_per_pa": denominator / history_pa if history_pa else None,
    }


def prepare_raw(frame: pd.DataFrame, *, player_id: int) -> pd.DataFrame:
    missing = sorted(REQUIRED_COLUMNS.difference(frame.columns))
    if missing:
        raise ValueError(f"direct batter source missing columns: {missing}")
    work = frame.loc[:, sorted(REQUIRED_COLUMNS)].copy()
    work["_date"] = pd.to_datetime(work["game_date"], format="%Y-%m-%d", errors="coerce")
    if work["_date"].isna().any():
        raise ValueError("direct batter source has malformed game_date")
    ids = pd.to_numeric(work["batter"], errors="coerce")
    if ids.isna().any() or not ids.astype(int).eq(int(player_id)).all():
        raise ValueError("direct batter source identity mismatch")
    game_type = work["game_type"].astype("string")
    if game_type.isna().any() or game_type.str.len().eq(0).any():
        raise ValueError("direct batter source has missing game_type")
    return work


def history_features(prepared: pd.DataFrame, *, player_id: int, target_date: str) -> dict[str, Any]:
    target = pd.Timestamp(target_date)
    if target.year not in {2023, 2024}:
        raise ValueError("direct batter selection may read only 2023 and 2024 targets")
    history = prepared.loc[(prepared["_date"] < target) & prepared["game_type"].astype(str).eq("R")].copy()
    if not history.empty and history["_date"].max() >= target:
        raise AssertionError("same-day or future Statcast row entered direct batter history")

    observed = history.loc[history["events"].notna(), ["_date", "events"]].copy()
    event_names = observed["events"].astype(str)
    unknown = sorted(set(event_names).difference(EVENT_TO_OUTCOME).difference(NON_PA_EVENTS))
    if unknown:
        raise ValueError(f"unmapped terminal Statcast events: {unknown}")
    terminal = observed.loc[~event_names.isin(NON_PA_EVENTS)].copy()
    terminal["outcome"] = terminal["events"].astype(str).map(EVENT_TO_OUTCOME)
    if terminal["outcome"].isna().any():
        raise AssertionError("terminal PA outcome mapping is incomplete")
    counts = terminal["outcome"].value_counts().to_dict()
    pa = int(len(terminal))
    result: dict[str, Any] = {
        "player_id": int(player_id),
        "target_date": target.date().isoformat(),
        "max_source_date": None if history.empty else history["_date"].max().date().isoformat(),
        "history_pitch_count": int(len(history)),
        "history_pa": pa,
    }
    for outcome in OUTCOMES:
        count = int(counts.get(outcome, 0))
        result[f"history_{outcome}_count"] = count
        result[f"history_{outcome}_rate"] = _rate(count, pa)
        dates = terminal.loc[terminal["outcome"].eq(outcome), "_date"]
        result[f"days_since_{outcome}"] = None if dates.empty else int((target - dates.max()).days)
    ages = (target - terminal["_date"]).dt.days.to_numpy(float) if pa else np.asarray([], dtype=float)
    result.update(_moments(ages, "history_pa_age_days"))
    result["days_since_pa"] = None if terminal.empty else int((target - terminal["_date"].max()).days)

    descriptions = history["description"].astype("string")
    swings = descriptions.isin(SWINGS)
    whiffs = descriptions.isin(WHIFFS)
    zone = pd.to_numeric(history["zone"], errors="coerce")
    outside = zone.isin([11, 12, 13, 14])
    in_zone = zone.between(1, 9)
    result.update({
        "history_swing_rate": _rate(int(swings.sum()), len(history)),
        "history_whiff_rate": _rate(int(whiffs.sum()), int(swings.sum())),
        "history_chase_rate": _rate(int((outside & swings).sum()), int(outside.sum())),
        "history_zone_rate": _rate(int(in_zone.sum()), int(zone.notna().sum())),
    })
    history_events = history["events"].astype("string")
    bip = history.loc[
        history["type"].astype("string").eq("X")
        & history_events.notna()
        & ~history_events.isin(NON_PA_EVENTS)
    ]
    ev = _finite(bip["launch_speed"])
    la = _finite(bip["launch_angle"])
    result["history_bip"] = int(len(bip))
    result.update(_moments(ev, "history_exit_velocity"))
    result.update(_moments(la, "history_launch_angle"))
    batted_ball_evidence = derive_batted_ball_evidence(bip)
    result["history_batted_ball_denominator"] = (
        None if batted_ball_evidence is None else batted_ball_evidence.measured_batted_balls
    )
    result["history_barrel_count"] = (
        None if batted_ball_evidence is None else batted_ball_evidence.barrel_count
    )
    result["history_hard_hit_count"] = (
        None if batted_ball_evidence is None else batted_ball_evidence.hard_hit_count
    )
    result["history_barrel_rate"] = (
        None if batted_ball_evidence is None else batted_ball_evidence.barrel_rate
    )
    result["history_hard_hit_rate"] = (
        None if batted_ball_evidence is None else batted_ball_evidence.hard_hit_rate
    )
    result.update(batted_ball_composition(
        denominator=result["history_batted_ball_denominator"],
        barrel_count=result["history_barrel_count"],
        hard_hit_count=result["history_hard_hit_count"],
        history_pa=pa,
    ))
    for column, name in (
        ("release_speed", "release_speed"), ("pfx_x", "pfx_x"), ("pfx_z", "pfx_z"),
        ("plate_x", "plate_x"), ("plate_z", "plate_z"),
    ):
        result.update(_moments(_finite(history[column]), f"history_{name}"))
    pitch_types = [value for value in history["pitch_type"].astype("string").dropna().astype(str) if value]
    frequency = Counter(pitch_types)
    total_types = sum(frequency.values())
    result["history_distinct_pitch_types"] = int(len(frequency))
    result["history_pitch_type_entropy"] = None if total_types == 0 else float(-sum((count / total_types) * log(count / total_types) for count in frequency.values()))
    if result["max_source_date"] is not None and result["max_source_date"] >= result["target_date"]:
        raise AssertionError("direct batter chronology certificate failed")
    return result
