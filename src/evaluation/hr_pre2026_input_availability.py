"""Outcome-blind Statcast availability contract for historical HR reconstruction."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable


SCHEMA = "hr-pre2026-input-availability-v1"
STATUS = "OUTCOME_BLIND_MODEL_INPUT_AVAILABILITY"
FORBIDDEN_SOURCE_COLUMNS = {
    "out_pa",
    "out_ab",
    "out_hits",
    "out_doubles",
    "out_triples",
    "out_hr",
    "out_rbi",
    "out_runs",
    "out_bb",
    "out_k",
}


def statcast_window(target_date: str, lookback_days: int) -> tuple[str, str]:
    """Return the exact inclusive start/exclusive end used by reconstruction.

    The preserved implementation asks Savant for ``end=target-1`` and computes
    ``start=end-lookback_days``. Because both fetch bounds are inclusive, this
    represents ``lookback_days + 1`` calendar dates.
    """
    if lookback_days <= 0:
        raise ValueError("lookback_days must be positive")
    target = date.fromisoformat(target_date)
    end_inclusive = target - timedelta(days=1)
    start_inclusive = end_inclusive - timedelta(days=lookback_days)
    return start_inclusive.isoformat(), target.isoformat()


def qualifying_event_count(
    event_dates: Iterable[str], *, target_date: str, lookback_days: int
) -> int:
    start, end_exclusive = statcast_window(target_date, lookback_days)
    return sum(start <= str(value) < end_exclusive for value in event_dates)


def regular_season_event_dates(
    event_dates: Iterable[str], game_types: Iterable[str]
) -> list[str]:
    """Return only regular-season rows from the immutable per-player cache."""
    dates = [str(value) for value in event_dates]
    types = [str(value) for value in game_types]
    if len(dates) != len(types):
        raise ValueError("event dates and game types must have identical lengths")
    return [value for value, game_type in zip(dates, types) if game_type == "R"]


def date_availability(
    active_player_ids: Iterable[int],
    event_dates_by_player: dict[int, Iterable[str]],
    *,
    target_date: str,
    lookback_days: int,
    min_events: int,
) -> dict[str, object]:
    if min_events <= 0:
        raise ValueError("min_events must be positive")
    players = [int(value) for value in active_player_ids]
    if not players or len(players) != len(set(players)):
        raise ValueError("active player ids must be non-empty and unique")
    start, end_exclusive = statcast_window(target_date, lookback_days)
    counts = {
        player_id: qualifying_event_count(
            event_dates_by_player.get(player_id, ()),
            target_date=target_date,
            lookback_days=lookback_days,
        )
        for player_id in players
    }
    advanced = sorted(player_id for player_id, count in counts.items() if count >= min_events)
    return {
        "target_date": target_date,
        "window_start_inclusive": start,
        "window_end_exclusive": end_exclusive,
        "active_player_rows": len(players),
        "advanced_active_players": len(advanced),
        "advanced_active_player_ids": advanced,
        "all_fallback": not advanced,
        "runner_requirement_satisfied": bool(advanced),
        "event_count_min": min(counts.values()),
        "event_count_median": _median(list(counts.values())),
        "event_count_max": max(counts.values()),
    }


def validate_source_columns(columns: Iterable[str]) -> None:
    found = FORBIDDEN_SOURCE_COLUMNS & {str(value) for value in columns}
    if found:
        raise ValueError(f"outcome columns are forbidden from availability selection: {sorted(found)}")


def _median(values: list[int]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return (ordered[middle - 1] + ordered[middle]) / 2.0
