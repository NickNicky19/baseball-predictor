"""Regression tests for canonical MLB game-date identity."""

from __future__ import annotations

import pytest

from src.data.mlb_api import MLBStatsAPI
from src.utils.errors import DataFetchError


def _game(game_pk: int, official_date: str, coded_state: str = "F") -> dict:
    return {
        "gamePk": game_pk,
        "status": {
            "abstractGameState": "Final",
            "codedGameState": coded_state,
        },
        "teams": {"away": {"team": {"name": "Away"}}, "home": {"team": {"name": "Home"}}},
    }


def _feed(official_date: str) -> dict:
    return {"gameData": {"datetime": {"officialDate": official_date}}}


def test_final_game_pk_is_retained_only_on_feed_official_date():
    """A resumed game listed on two calendar dates belongs to one date only."""
    api = MLBStatsAPI.__new__(MLBStatsAPI)
    schedules = {
        "2026-06-16": [_game(824912, "2026-06-16")],
        # MLB repeats 824912 on the resume date; 824913 is the real Jun 17 game.
        "2026-06-17": [_game(824912, "2026-06-16"), _game(824913, "2026-06-17")],
    }
    feeds = {824912: _feed("2026-06-16"), 824913: _feed("2026-06-17")}
    api.get_schedule = (  # type: ignore[method-assign]
        lambda game_date, include_lineups=False: schedules[game_date]
    )
    api._get_game_feed = lambda game_pk: feeds[game_pk]  # type: ignore[method-assign]

    assert api.get_final_game_pks("2026-06-16") == [824912]
    assert api.get_final_game_pks("2026-06-17") == [824913]


def test_abstract_final_without_coded_f_is_not_an_official_final():
    """Mutation: broadening the state test to abstract Final must fail this."""
    api = MLBStatsAPI.__new__(MLBStatsAPI)
    api.get_schedule = (  # type: ignore[method-assign]
        lambda _date, include_lineups=False: [_game(700, "2026-06-16", coded_state="I")]
    )
    api._get_game_feed = lambda _game_pk: _feed("2026-06-16")  # type: ignore[method-assign]

    assert api.get_final_game_pks("2026-06-16") == []


def test_completed_game_roles_use_player_sequence_not_final_batting_order():
    """Mutation: selecting the final nine incorrectly promotes a substitute."""
    api = MLBStatsAPI.__new__(MLBStatsAPI)
    api._get_game_feed = lambda _game_pk: {  # type: ignore[method-assign]
        "liveData": {"boxscore": {"teams": {
            "away": {
                # The final occupant is the substitute; this list is not the
                # original starting lineup.
                "battingOrder": [11],
                "players": {
                    "ID10": {"battingOrder": "200"},
                    "ID11": {"battingOrder": "201"},
                },
            },
            "home": {"battingOrder": [], "players": {}},
        }}}
    }

    roles = api.get_completed_game_batting_roles(700001)
    assert roles[10] == {
        "team_side": "away",
        "lineup_slot": 2,
        "lineup_sequence": 0,
        "is_starter": True,
        "starter_replaced_in_slot": True,
    }
    assert roles[11]["is_starter"] is False
    assert roles[11]["starter_replaced_in_slot"] is False


def test_completed_original_order_cannot_use_final_slot_occupants():
    """Mutation: routing historical lineups through get_batting_order fails."""
    api = MLBStatsAPI.__new__(MLBStatsAPI)
    away_players = {
        f"ID{100 + slot}": {"battingOrder": f"{slot}00"}
        for slot in range(1, 10)
    }
    # Slot 9's substitute is the final occupant, but never the starter.
    away_players["ID999"] = {"battingOrder": "901"}
    home_players = {
        f"ID{200 + slot}": {"battingOrder": f"{slot}00"}
        for slot in range(1, 10)
    }
    api._get_game_feed = lambda _game_pk: {  # type: ignore[method-assign]
        "liveData": {"boxscore": {"teams": {
            "away": {
                "battingOrder": [101, 102, 103, 104, 105, 106, 107, 108, 999],
                "players": away_players,
            },
            "home": {
                "battingOrder": list(range(201, 210)),
                "players": home_players,
            },
        }}}
    }

    order = api.get_completed_game_original_batting_order(700002)
    assert order["away"] == list(range(101, 110))
    assert 999 not in order["away"]
    assert order["home"] == list(range(201, 210))


def test_original_order_preserves_dual_team_player_without_identity_collapse():
    """A player on both teams cannot erase his original team/slot record."""
    dual_player = 643376
    away_players = {
        f"ID{100 + slot}": {"battingOrder": f"{slot}00"}
        for slot in range(1, 10)
    }
    away_players.pop("ID107")
    away_players[f"ID{dual_player}"] = {"battingOrder": "700"}
    home_players = {
        f"ID{200 + slot}": {"battingOrder": f"{slot}00"}
        for slot in range(1, 10)
    }
    # The same player later entered for the other team. The old dict keyed by
    # player_id overwrote away slot 7 with this role and then failed.
    home_players[f"ID{dual_player}"] = {"battingOrder": "701"}
    feed = {"liveData": {"boxscore": {"teams": {
        "away": {"players": away_players},
        "home": {"players": home_players},
    }}}}

    order = MLBStatsAPI.completed_game_original_batting_order_from_feed(746942, feed)
    assert order["away"][6] == dual_player
    assert order["home"] == list(range(201, 210))

    # The player-keyed projection cannot represent two roles for one identity.
    with pytest.raises(DataFetchError, match="player-keyed role projection is ambiguous"):
        MLBStatsAPI.completed_game_batting_roles_from_feed(746942, feed)
