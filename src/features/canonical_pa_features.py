"""Canonical point-in-time Statcast features for the shared PA challenger.

The same transformer is used for historical cached pitches and for a live
pregame Statcast pull.  It does not impute league values or apply hand-picked
shrinkage.  Missing history remains missing and is accompanied by factual
sample counts so the fitted model can learn how much evidence exists.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd


SCHEMA_VERSION = "shared-pa-canonical-statcast-v1"
FETCH_LOOKBACK_DAYS = 45
# The existing Savant request uses start=end-45 with both dates inclusive and
# end=target-1, so the exact half-open feature window is [target-46, target).
CALENDAR_DAYS_IN_WINDOW = FETCH_LOOKBACK_DAYS + 1

SWING_DESCRIPTIONS = frozenset({
    "swinging_strike", "swinging_strike_blocked", "foul", "foul_tip",
    "hit_into_play",
})
WHIFF_DESCRIPTIONS = frozenset({
    "swinging_strike", "swinging_strike_blocked",
})
STRIKEOUT_EVENTS = frozenset({"strikeout", "strikeout_double_play"})
WALK_EVENTS = frozenset({"walk", "intent_walk"})
HIT_EVENTS = {
    "single": frozenset({"single"}),
    "double": frozenset({"double"}),
    "triple": frozenset({"triple"}),
    "home_run": frozenset({"home_run"}),
}
OTHER_NON_AB_EVENTS = frozenset({
    "catcher_interf", "hit_by_pitch", "sac_bunt", "sac_fly",
    "sac_fly_double_play",
})
NON_PA_TERMINAL_EVENTS = frozenset({"truncated_pa"})
BIP_OUT_EVENTS = frozenset({
    "double_play", "field_error", "field_out", "fielders_choice",
    "fielders_choice_out", "force_out", "grounded_into_double_play",
    "other_out", "triple_play",
})

REQUIRED_RAW_COLUMNS = frozenset({
    "game_date", "batter", "pitcher", "events", "description", "type",
    "zone", "launch_speed", "launch_angle", "launch_speed_angle",
    "estimated_ba_using_speedangle", "estimated_woba_using_speedangle",
    "estimated_slg_using_speedangle",
})

PROFILE_FIELDS = (
    "pitch_count", "pa", "bip", "k_rate", "bb_rate", "single_rate",
    "double_rate", "triple_rate", "home_run_rate", "bip_out_rate",
    "other_non_ab_rate", "xwoba", "xba", "xslg", "avg_exit_velocity",
    "avg_launch_angle", "barrel_rate", "hard_hit_rate", "whiff_rate",
    "chase_rate", "contact_rate", "swing_rate", "zone_rate",
)


def window_bounds(target_date: str | date) -> tuple[date, date]:
    target = date.fromisoformat(target_date) if isinstance(target_date, str) else target_date
    return target - timedelta(days=CALENDAR_DAYS_IN_WINDOW), target


def _rate(numerator: int | float, denominator: int | float) -> float | None:
    return None if denominator <= 0 else float(numerator / denominator)


def _mean(frame: pd.DataFrame, column: str) -> float | None:
    values = pd.to_numeric(frame[column], errors="coerce").dropna()
    return None if values.empty else float(values.mean())


def _validate_raw(frame: pd.DataFrame) -> pd.DataFrame:
    missing = sorted(REQUIRED_RAW_COLUMNS - set(frame.columns))
    if missing:
        raise ValueError(f"canonical Statcast source columns missing: {missing}")
    output = frame.copy()
    original_non_null = output["game_date"].notna()
    parsed = pd.to_datetime(output["game_date"], format="%Y-%m-%d", errors="coerce")
    if parsed[original_non_null].isna().any():
        raise ValueError("canonical Statcast source contains malformed game_date")
    output["_canonical_game_date"] = parsed.dt.date
    return output


def canonical_profile(
    frame: pd.DataFrame,
    *,
    target_date: str | date,
    entity_column: str,
    entity_id: int,
    prefix: str,
    allowed_game_types: frozenset[str] | None = None,
) -> dict[str, Any]:
    """Aggregate one hitter or pitcher using only pitches before target_date.

    ``entity_column`` must be ``batter`` or ``pitcher``.  All event-rate
    classes are exhaustive: any previously unseen terminal event fails closed
    rather than being silently relabeled.
    """
    if entity_column not in {"batter", "pitcher"}:
        raise ValueError("canonical entity_column must be batter or pitcher")
    if not prefix or not prefix.replace("_", "").isalnum():
        raise ValueError("canonical feature prefix is invalid")
    work = _validate_raw(frame)
    return _canonical_profile_prepared(
        work,
        target_date=target_date,
        entity_column=entity_column,
        entity_id=entity_id,
        prefix=prefix,
        allowed_game_types=allowed_game_types,
    )


def canonical_profiles(
    frame: pd.DataFrame,
    *,
    target_dates: list[str],
    entity_column: str,
    entity_id: int,
    prefix: str,
    allowed_game_types: frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    """Build multiple dates while parsing and validating one raw frame once."""
    if len(target_dates) != len(set(target_dates)):
        raise ValueError("canonical target dates are duplicated")
    work = _validate_raw(frame)
    return [
        _canonical_profile_prepared(
            work,
            target_date=target,
            entity_column=entity_column,
            entity_id=entity_id,
            prefix=prefix,
            allowed_game_types=allowed_game_types,
        )
        for target in target_dates
    ]


def _canonical_profile_prepared(
    work: pd.DataFrame,
    *,
    target_date: str | date,
    entity_column: str,
    entity_id: int,
    prefix: str,
    allowed_game_types: frozenset[str] | None = None,
) -> dict[str, Any]:
    if entity_column not in {"batter", "pitcher"}:
        raise ValueError("canonical entity_column must be batter or pitcher")
    if not prefix or not prefix.replace("_", "").isalnum():
        raise ValueError("canonical feature prefix is invalid")
    start, end = window_bounds(target_date)
    ids = pd.to_numeric(work[entity_column], errors="coerce")
    work = work.loc[
        ids.eq(int(entity_id))
        & work["_canonical_game_date"].ge(start)
        & work["_canonical_game_date"].lt(end)
    ].copy()
    if allowed_game_types is not None:
        if not allowed_game_types or not all(
            isinstance(value, str) and value for value in allowed_game_types
        ):
            raise ValueError("allowed_game_types must contain nonempty strings")
        if "game_type" not in work.columns:
            raise ValueError("game_type is required when a game-type policy is enabled")
        game_types = work["game_type"].astype("string")
        if game_types.isna().any() or game_types.str.len().eq(0).any():
            raise ValueError("game_type is missing when a game-type policy is enabled")
        work = work.loc[game_types.isin(sorted(allowed_game_types))].copy()

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
    # Every remaining terminal event is an AB/BIP out only when it is a known
    # Statcast out event.  This explicit list makes source-schema drift visible.
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
        raise ValueError("canonical terminal event accounting does not sum to PA")

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
        raise AssertionError("canonical profile field contract drifted")
    for name, value in values.items():
        if value is not None and isinstance(value, float) and not np.isfinite(value):
            raise ValueError(f"canonical feature is non-finite: {name}")
        if name.endswith("_rate") and value is not None and not 0.0 <= value <= 1.0:
            raise ValueError(f"canonical rate outside [0,1]: {name}")
    return {
        "schema_version": SCHEMA_VERSION,
        "target_date": end.isoformat(),
        "window_start_inclusive": start.isoformat(),
        "window_end_exclusive": end.isoformat(),
        "entity_column": entity_column,
        "entity_id": int(entity_id),
        **{f"{prefix}_{name}": value for name, value in values.items()},
    }
