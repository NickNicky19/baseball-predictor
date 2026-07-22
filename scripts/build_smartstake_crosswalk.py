#!/usr/bin/env python3
"""Build an auditable SmartStake-to-MLB identity crosswalk.

The two outputs are deliberately separate from scoring:

* a vendor event (vendor_game_id + start_time + both teams) maps to one MLB
  ``mlb_game_pk``;
* a normalized vendor player name within that event maps to one MLB player ID
  from that game's roster.

There is no fuzzy time window.  Both feeds carry scheduled UTC first-pitch
timestamps, so an exact normalized timestamp plus the two teams is the
verifiable contract.  A tolerance would be an arbitrary, untested join rule.
Unresolved or ambiguous rows fail in strict mode; no name/date fallback exists.
"""

from __future__ import annotations

import argparse
import sys
import unicodedata
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.evaluation.identity_keys import require_unique  # noqa: E402


def normalize_text(value: object) -> str:
    text = "".join(
        char for char in unicodedata.normalize("NFKD", str(value))
        if not unicodedata.combining(char)
    )
    return " ".join("".join(char for char in text.lower() if char.isalnum() or char == " ").split())


def _normalise_columns(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    out = df.copy()
    for column in columns:
        out[column] = out[column].map(normalize_text)
    return out


def build_crosswalk(
    vendor_quotes: pd.DataFrame,
    mlb_games: pd.DataFrame,
    mlb_roster: pd.DataFrame,
    *,
    vendor_game_id_col: str = "vendor_game_id",
    vendor_start_time_col: str = "start_time",
    vendor_away_team_col: str = "away_team",
    vendor_home_team_col: str = "home_team",
    vendor_player_col: str = "player",
    strict: bool = True,
) -> pd.DataFrame:
    """Return a one-to-one player crosswalk, or reject incomplete identity."""
    vendor_required = [
        vendor_game_id_col, vendor_start_time_col, vendor_away_team_col,
        vendor_home_team_col, vendor_player_col,
    ]
    game_required = ["mlb_game_pk", "start_time", "away_team", "home_team"]
    roster_required = ["mlb_game_pk", "player_id", "player_name"]
    for label, frame, required in (
        ("vendor quotes", vendor_quotes, vendor_required),
        ("MLB games", mlb_games, game_required),
        ("MLB roster", mlb_roster, roster_required),
    ):
        missing = [column for column in required if column not in frame.columns]
        if missing:
            raise ValueError(f"{label}: missing required columns {missing}")

    vendor = vendor_quotes[vendor_required].rename(columns={
        vendor_game_id_col: "vendor_game_id",
        vendor_start_time_col: "start_time",
        vendor_away_team_col: "vendor_away_team",
        vendor_home_team_col: "vendor_home_team",
        vendor_player_col: "vendor_player_name",
    }).copy()
    vendor["start_time"] = pd.to_datetime(vendor["start_time"], utc=True, errors="coerce")
    vendor = _normalise_columns(vendor, ["vendor_away_team", "vendor_home_team"])
    vendor["player_key"] = vendor["vendor_player_name"].map(normalize_text)

    games = mlb_games[game_required].copy()
    games["start_time"] = pd.to_datetime(games["start_time"], utc=True, errors="coerce")
    games["mlb_game_pk"] = pd.to_numeric(games["mlb_game_pk"], errors="coerce").astype("Int64")
    games = _normalise_columns(games, ["away_team", "home_team"])
    require_unique(games, ["mlb_game_pk"], "MLB schedule")
    require_unique(games, ["start_time", "away_team", "home_team"], "MLB schedule event")

    event_keys = ["vendor_game_id", "start_time", "vendor_away_team", "vendor_home_team"]
    events = vendor[event_keys].drop_duplicates().copy()
    require_unique(events, event_keys, "vendor events")
    event_map = events.merge(
        games,
        left_on=["start_time", "vendor_away_team", "vendor_home_team"],
        right_on=["start_time", "away_team", "home_team"],
        how="left",
        validate="many_to_one",
        indicator=True,
    )
    unresolved_events = event_map[event_map["_merge"] != "both"]
    if strict and not unresolved_events.empty:
        raise ValueError(
            "vendor_game_id -> mlb_game_pk could not be verified by exact UTC "
            f"start time and teams for {len(unresolved_events)} event(s)\n"
            f"{unresolved_events[event_keys].head(20).to_string(index=False)}"
        )
    event_map = event_map[event_map["_merge"] == "both"].drop(columns="_merge")
    require_unique(event_map, event_keys, "verified vendor event mapping")

    roster = mlb_roster[roster_required].copy()
    roster["mlb_game_pk"] = pd.to_numeric(roster["mlb_game_pk"], errors="coerce").astype("Int64")
    roster["player_id"] = pd.to_numeric(roster["player_id"], errors="coerce").astype("Int64")
    roster["player_key"] = roster["player_name"].map(normalize_text)
    require_unique(roster, ["mlb_game_pk", "player_key"], "MLB game roster")

    vendor_players = vendor.merge(event_map[event_keys + ["mlb_game_pk"]], on=event_keys,
                                  how="inner", validate="many_to_one")
    crosswalk = vendor_players.merge(
        roster[["mlb_game_pk", "player_key", "player_id", "player_name"]],
        on=["mlb_game_pk", "player_key"], how="left", validate="many_to_one", indicator=True,
    )
    unresolved_players = crosswalk[crosswalk["_merge"] != "both"]
    if strict and not unresolved_players.empty:
        raise ValueError(
            "(vendor_game_id, normalized_market_player_name) -> player_id is "
            f"unresolved for {len(unresolved_players)} row(s)\n"
            f"{unresolved_players[["vendor_game_id", "start_time", "vendor_player_name", "mlb_game_pk"]].head(20).to_string(index=False)}"
        )
    crosswalk = crosswalk[crosswalk["_merge"] == "both"].drop(columns="_merge")
    result_keys = ["vendor_game_id", "start_time", "player_key"]
    require_unique(crosswalk, result_keys, "SmartStake-to-MLB player crosswalk")
    return crosswalk[
        event_keys + ["mlb_game_pk", "player_key", "vendor_player_name", "player_id", "player_name"]
    ].sort_values(result_keys).reset_index(drop=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vendor-quotes", required=True, help="CSV of SmartStake selections including both teams")
    parser.add_argument("--mlb-games", required=True, help="CSV: mlb_game_pk,start_time,away_team,home_team")
    parser.add_argument("--mlb-roster", required=True, help="CSV: mlb_game_pk,player_id,player_name")
    parser.add_argument("--out", required=True)
    parser.add_argument("--vendor-game-id-col", default="vendor_game_id")
    parser.add_argument("--vendor-start-time-col", default="start_time")
    parser.add_argument("--vendor-away-team-col", default="away_team")
    parser.add_argument("--vendor-home-team-col", default="home_team")
    parser.add_argument("--vendor-player-col", default="player")
    parser.add_argument("--non-strict", action="store_true", help="write only uniquely resolved rows")
    args = parser.parse_args(argv)
    crosswalk = build_crosswalk(
        pd.read_csv(args.vendor_quotes), pd.read_csv(args.mlb_games), pd.read_csv(args.mlb_roster),
        vendor_game_id_col=args.vendor_game_id_col,
        vendor_start_time_col=args.vendor_start_time_col,
        vendor_away_team_col=args.vendor_away_team_col,
        vendor_home_team_col=args.vendor_home_team_col,
        vendor_player_col=args.vendor_player_col,
        strict=not args.non_strict,
    )
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    crosswalk.to_csv(args.out, index=False)
    print(f"wrote {len(crosswalk):,} audited player mappings -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
