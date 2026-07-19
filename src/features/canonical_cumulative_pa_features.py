"""Expanding point-in-time Statcast profiles without changing the v1 transformer.

The original 46-day transformer is hash-bound to prior evidence and therefore
must remain byte-for-byte immutable.  This module reuses its event taxonomy and
primitive helpers, and an offline parity test requires identical output when
both transformers receive the same date window.
"""
from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from src.features.canonical_pa_features import (
    BIP_OUT_EVENTS,
    HIT_EVENTS,
    NON_PA_TERMINAL_EVENTS,
    OTHER_NON_AB_EVENTS,
    PROFILE_FIELDS,
    STRIKEOUT_EVENTS,
    SWING_DESCRIPTIONS,
    WALK_EVENTS,
    WHIFF_DESCRIPTIONS,
    _mean,
    _rate,
    _validate_raw,
)


SCHEMA_VERSION = "shared-pa-canonical-cumulative-statcast-v1"


def expected_cache_groups(
    targets: pd.DataFrame,
    *,
    allowed_years: list[int],
) -> list[tuple[int, int]]:
    """Return the exact player-year source universe required by targets."""
    if set(targets.columns) != {"season", "player_id"}:
        raise ValueError("cumulative source-universe columns changed")
    years = [int(year) for year in allowed_years]
    if years != sorted(set(years)):
        raise ValueError("cumulative allowed source years must be unique and sorted")
    target_years = pd.to_numeric(targets["season"], errors="raise").astype(int)
    if not set(target_years).issubset(set(years)):
        raise ValueError("cumulative targets exceed allowed source years")
    groups: list[tuple[int, int]] = []
    work = targets.assign(_season=target_years)
    for raw_player_id, player in work.groupby("player_id", sort=True):
        maximum_target_year = int(player["_season"].max())
        groups.extend(
            (year, int(raw_player_id))
            for year in years
            if year <= maximum_target_year
        )
    return groups


def cumulative_profiles(
    frame: pd.DataFrame,
    *,
    target_dates: list[str],
    start_inclusive: str | date,
    entity_column: str,
    entity_id: int,
    prefix: str,
) -> list[dict[str, Any]]:
    """Build expanding profiles over ``[start_inclusive, target_date)``."""
    if len(target_dates) != len(set(target_dates)):
        raise ValueError("cumulative target dates are duplicated")
    if entity_column not in {"batter", "pitcher"}:
        raise ValueError("cumulative entity_column must be batter or pitcher")
    if not prefix or not prefix.replace("_", "").isalnum():
        raise ValueError("cumulative feature prefix is invalid")
    start = date.fromisoformat(start_inclusive) if isinstance(start_inclusive, str) else start_inclusive
    work = _validate_raw(frame)
    output: list[dict[str, Any]] = []
    for raw_target in target_dates:
        end = date.fromisoformat(raw_target)
        if end <= start:
            raise ValueError("cumulative target must follow its lower bound")
        output.append(
            _profile(
                work,
                start=start,
                end=end,
                entity_column=entity_column,
                entity_id=entity_id,
                prefix=prefix,
            )
        )
    return output


def _profile(
    prepared: pd.DataFrame,
    *,
    start: date,
    end: date,
    entity_column: str,
    entity_id: int,
    prefix: str,
) -> dict[str, Any]:
    ids = pd.to_numeric(prepared[entity_column], errors="coerce")
    work = prepared.loc[
        ids.eq(int(entity_id))
        & prepared["_canonical_game_date"].ge(start)
        & prepared["_canonical_game_date"].lt(end)
    ].copy()

    event = work["events"].astype("string")
    observed_terminal = work.loc[event.notna()].copy()
    terminal = observed_terminal.loc[
        ~observed_terminal["events"].astype(str).isin(NON_PA_TERMINAL_EVENTS)
    ].copy()
    terminal_event = terminal["events"].astype(str)
    known = set().union(
        STRIKEOUT_EVENTS,
        WALK_EVENTS,
        OTHER_NON_AB_EVENTS,
        *(values for values in HIT_EVENTS.values()),
    )
    observed_event = observed_terminal["events"].astype(str)
    unknown = sorted(
        set(observed_event.unique()) - known - set(BIP_OUT_EVENTS) - set(NON_PA_TERMINAL_EVENTS)
    )
    if unknown:
        raise ValueError(f"unmapped terminal Statcast events: {unknown}")

    pa = len(terminal)
    counts = {
        "k_rate": int(terminal_event.isin(STRIKEOUT_EVENTS).sum()),
        "bb_rate": int(terminal_event.isin(WALK_EVENTS).sum()),
        "single_rate": int(terminal_event.isin(HIT_EVENTS["single"]).sum()),
        "double_rate": int(terminal_event.isin(HIT_EVENTS["double"]).sum()),
        "triple_rate": int(terminal_event.isin(HIT_EVENTS["triple"]).sum()),
        "home_run_rate": int(terminal_event.isin(HIT_EVENTS["home_run"]).sum()),
        "bip_out_rate": int(terminal_event.isin(BIP_OUT_EVENTS).sum()),
        "other_non_ab_rate": int(terminal_event.isin(OTHER_NON_AB_EVENTS).sum()),
    }
    if sum(counts.values()) != pa:
        raise ValueError("cumulative terminal event accounting does not sum to PA")

    bip = work.loc[work["type"].astype("string").eq("X")].copy()
    descriptions = work["description"].astype("string")
    swings = descriptions.isin(SWING_DESCRIPTIONS)
    whiffs = descriptions.isin(WHIFF_DESCRIPTIONS)
    zone = pd.to_numeric(work["zone"], errors="coerce")
    outside = zone.isin([11, 12, 13, 14])
    in_zone = zone.between(1, 9)
    exit_velocity = pd.to_numeric(bip["launch_speed"], errors="coerce")
    speed_angle = pd.to_numeric(bip["launch_speed_angle"], errors="coerce")
    measured_ev = exit_velocity.notna()
    classified_barrel = speed_angle.notna()

    values: dict[str, Any] = {
        "pitch_count": int(len(work)),
        "pa": int(pa),
        "bip": int(len(bip)),
        **{name: _rate(value, pa) for name, value in counts.items()},
        "xwoba": _mean(bip, "estimated_woba_using_speedangle"),
        "xba": _mean(bip, "estimated_ba_using_speedangle"),
        "xslg": _mean(bip, "estimated_slg_using_speedangle"),
        "avg_exit_velocity": _mean(bip, "launch_speed"),
        "avg_launch_angle": _mean(bip, "launch_angle"),
        "barrel_rate": _rate(int(speed_angle.eq(6).sum()), int(classified_barrel.sum())),
        "hard_hit_rate": _rate(int(exit_velocity[measured_ev].ge(95.0).sum()), int(measured_ev.sum())),
        "whiff_rate": _rate(int(whiffs.sum()), int(swings.sum())),
        "chase_rate": _rate(int((outside & swings).sum()), int(outside.sum())),
        "contact_rate": _rate(int((swings & ~whiffs).sum()), int(swings.sum())),
        "swing_rate": _rate(int(swings.sum()), int(len(work))),
        "zone_rate": _rate(int(in_zone.sum()), int(zone.notna().sum())),
    }
    if set(values) != set(PROFILE_FIELDS):
        raise AssertionError("cumulative profile field contract drifted")
    for name, value in values.items():
        if value is not None and isinstance(value, float) and not np.isfinite(value):
            raise ValueError(f"cumulative feature is non-finite: {name}")
        if name.endswith("_rate") and value is not None and not 0.0 <= value <= 1.0:
            raise ValueError(f"cumulative rate outside [0,1]: {name}")
    return {
        "schema_version": SCHEMA_VERSION,
        "target_date": end.isoformat(),
        "window_start_inclusive": start.isoformat(),
        "window_end_exclusive": end.isoformat(),
        "entity_column": entity_column,
        "entity_id": int(entity_id),
        **{f"{prefix}_{name}": value for name, value in values.items()},
    }
