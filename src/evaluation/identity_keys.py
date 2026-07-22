"""Authoritative identity keys and guards for historical evaluation.

Dates and display names are context, not identity: a player can appear twice on
one date and a vendor can reuse a matchup identifier.  Historical scoring must
therefore use MLB's stable game primary key end to end.
"""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd


MODEL_KEY = ["mlb_game_pk", "player_id", "category", "line"]
OUTCOME_KEY = ["mlb_game_pk", "player_id", "category"]
MARKET_KEY = MODEL_KEY


def require_columns_and_non_null(
    df: pd.DataFrame, keys: Sequence[str], label: str
) -> None:
    """Fail before a historical join when its identity is incomplete."""
    missing = [key for key in keys if key not in df.columns]
    if missing:
        raise ValueError(f"{label}: missing required key column(s) {missing}")
    nulls = df.loc[df[list(keys)].isna().any(axis=1), list(keys)]
    if not nulls.empty:
        raise ValueError(
            f"{label}: {len(nulls)} row(s) have null required key {list(keys)}\n"
            f"{nulls.head(20).to_string(index=False)}"
        )


def require_unique(df: pd.DataFrame, keys: Sequence[str], label: str) -> None:
    """Reject ambiguous data instead of silently deleting a game row."""
    require_columns_and_non_null(df, keys, label)
    dup = df[df.duplicated(list(keys), keep=False)].sort_values(list(keys))
    if not dup.empty:
        raise ValueError(
            f"{label}: {len(dup)} rows violate unique key {list(keys)}\n"
            f"{dup.head(20).to_string(index=False)}"
        )
