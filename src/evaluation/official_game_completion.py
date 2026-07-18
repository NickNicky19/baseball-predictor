"""Official postgame facts needed for fail-closed sportsbook grading."""
from __future__ import annotations

from typing import Any

import pandas as pd


GAME_KEY = ["mlb_game_pk"]
GAME_COMPLETION_COLUMNS = [
    "mlb_game_pk",
    "official_game_date",
    "coded_game_state",
    "scheduled_innings",
    "final_inning",
    "regular_game_completed",
]


def game_completion_row(
    game_pk: int, official_game_date: str, feed: dict[str, Any]
) -> pd.DataFrame:
    """Extract only official, completed-game facts from an MLB live feed."""
    feed_date = feed.get("gameData", {}).get("datetime", {}).get("officialDate")
    if str(feed_date) != str(official_game_date):
        raise ValueError(
            f"game_pk {game_pk}: feed officialDate {feed_date!r} differs from "
            f"declared {official_game_date!r}"
        )
    coded = feed.get("gameData", {}).get("status", {}).get("codedGameState")
    linescore = feed.get("liveData", {}).get("linescore", {})
    scheduled = linescore.get("scheduledInnings")
    final_inning = linescore.get("currentInning")
    if coded != "F":
        raise ValueError(f"game_pk {game_pk}: official feed is not coded final")
    try:
        scheduled_int = int(scheduled)
        final_int = int(final_inning)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"game_pk {game_pk}: missing official inning facts") from exc
    if scheduled_int <= 0 or final_int <= 0:
        raise ValueError(f"game_pk {game_pk}: invalid official inning facts")
    out = pd.DataFrame([{
        "mlb_game_pk": int(game_pk),
        "official_game_date": str(official_game_date),
        "coded_game_state": str(coded),
        "scheduled_innings": scheduled_int,
        "final_inning": final_int,
        "regular_game_completed": bool(scheduled_int == 9 and final_int >= 9),
    }], columns=GAME_COMPLETION_COLUMNS)
    validate_game_completion(out)
    return out


def validate_game_completion(frame: pd.DataFrame) -> None:
    missing = [column for column in GAME_COMPLETION_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"official game completion missing {missing}")
    if frame[GAME_KEY].isna().any().any() or frame.duplicated(GAME_KEY).any():
        raise ValueError("official game completion has null or duplicate game identity")
    dates = pd.to_datetime(frame.official_game_date, errors="coerce")
    if dates.isna().any():
        raise ValueError("official game completion has invalid date")
    if not frame.coded_game_state.astype(str).eq("F").all():
        raise ValueError("official game completion contains a non-final game")
    scheduled = pd.to_numeric(frame.scheduled_innings, errors="coerce")
    final = pd.to_numeric(frame.final_inning, errors="coerce")
    if scheduled.isna().any() or final.isna().any() or (scheduled <= 0).any() or (final <= 0).any():
        raise ValueError("official game completion has invalid inning facts")
    expected = scheduled.eq(9) & final.ge(9)
    observed = frame.regular_game_completed.astype(bool)
    if not observed.equals(expected):
        raise ValueError("regular-game completion label contradicts official innings")
