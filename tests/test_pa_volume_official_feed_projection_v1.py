from __future__ import annotations

import copy
import json

import pytest

from src.evaluation.pa_volume_official_feed_projection_v1 import (
    OfficialFeedProjectionError,
    build_official_feed_projection,
)


H = "a" * 64


def schedule() -> dict:
    return {
        "schema_version": "pa-volume-2023-schedule-index-v1",
        "season": 2023,
        "fields": ["game_pk", "official_date", "game_type", "away_team_id", "home_team_id"],
        "games": [{
            "game_pk": 1, "official_date": "2023-03-30", "game_type": "R",
            "away_team_id": 10, "home_team_id": 20,
        }],
    }


def feed() -> dict:
    teams = {}
    for side, base in (("away", 100), ("home", 200)):
        players = {
            f"ID{base + slot}": {
                "person": {"id": base + slot},
                "battingOrder": f"{slot}00",
                "stats": {"batting": {"plateAppearances": 0 if slot == 9 else 5}},
            }
            for slot in range(1, 10)
        }
        players[f"ID{base + 90}"] = {
            "person": {"id": base + 90}, "battingOrder": "101",
            "stats": {"batting": {"plateAppearances": 1}},
        }
        teams[side] = {
            # Final occupants are intentionally irrelevant to original-starter identity.
            "battingOrder": [base + 90] + [base + slot for slot in range(2, 10)],
            "players": players,
        }
    return {
        "gamePk": 1,
        "gameData": {
            "datetime": {"officialDate": "2023-03-30"},
            "game": {"type": "R"},
            "status": {"codedGameState": "F", "abstractGameState": "Final"},
            "teams": {"away": {"id": 10}, "home": {"id": 20}},
        },
        "liveData": {"boxscore": {"teams": teams}},
    }


def build(value: dict | None = None, index: dict | None = None) -> dict:
    raw = json.dumps(value or feed(), sort_keys=True).encode()
    return build_official_feed_projection(
        schedule_index=index or schedule(), feeds_by_game_pk={1: raw},
        schedule_capture_manifest_sha256=H, feed_capture_manifest_sha256="b" * 64,
        parser_source_sha256="c" * 64,
    )


def test_original_sequence_zero_starters_and_zero_pa_are_preserved() -> None:
    result = build()
    rows = result["projection"]["rows"]
    assert result["game_count"] == 1 and result["row_count"] == 18
    assert {row["player_id"] for row in rows if row["lineup_slot"] == 1} == {101, 201}
    assert 190 not in {row["player_id"] for row in rows}
    assert sorted(row["out_pa"] for row in rows).count(0) == 2


@pytest.mark.parametrize(
    "mutate,match",
    [
        (lambda x: x.update({"gamePk": 2}), "identity"),
        (lambda x: x["gameData"]["datetime"].update({"officialDate": "2023-03-31"}), "date"),
        (lambda x: x["gameData"]["game"].update({"type": "S"}), "regular"),
        (lambda x: x["gameData"]["status"].update({"codedGameState": "I"}), "final"),
        (lambda x: x["gameData"]["teams"]["away"].update({"id": 99}), "team"),
        (lambda x: x["liveData"]["boxscore"]["teams"]["away"]["players"].pop("ID101"), "incomplete"),
        (lambda x: x["liveData"]["boxscore"]["teams"]["away"]["players"]["ID102"].update({"battingOrder": "100"}), "duplicate"),
        (lambda x: x["liveData"]["boxscore"]["teams"]["away"]["players"]["ID101"]["stats"]["batting"].update({"plateAppearances": -1}), "non-negative"),
    ],
)
def test_semantic_mutations_fail_closed(mutate, match: str) -> None:
    value = copy.deepcopy(feed())
    mutate(value)
    with pytest.raises(OfficialFeedProjectionError, match=match):
        build(value)


def test_same_player_may_be_team_scoped_across_sides() -> None:
    value = feed()
    home = value["liveData"]["boxscore"]["teams"]["home"]["players"]
    home["ID201"]["person"]["id"] = 101
    home["ID101"] = home.pop("ID201")
    rows = build(value)["projection"]["rows"]
    assert sum(row["player_id"] == 101 for row in rows) == 2


def test_feed_coverage_and_schedule_order_are_exact() -> None:
    with pytest.raises(OfficialFeedProjectionError, match="exactly cover"):
        build_official_feed_projection(
            schedule_index=schedule(), feeds_by_game_pk={},
            schedule_capture_manifest_sha256=H, feed_capture_manifest_sha256="b" * 64,
            parser_source_sha256="c" * 64,
        )
    index = schedule()
    index["games"] = [
        {"game_pk": 2, "official_date": "2023-03-31", "game_type": "R", "away_team_id": 11, "home_team_id": 21},
        *index["games"],
    ]
    with pytest.raises(OfficialFeedProjectionError, match="sorted"):
        build(index=index)


def test_noncanonical_date_and_hash_fail() -> None:
    index = schedule()
    index["games"][0]["official_date"] = "2023-3-30"
    with pytest.raises(OfficialFeedProjectionError, match="canonical"):
        build(index=index)
    with pytest.raises(OfficialFeedProjectionError, match="SHA-256"):
        build_official_feed_projection(
            schedule_index=schedule(),
            feeds_by_game_pk={1: json.dumps(feed()).encode()},
            schedule_capture_manifest_sha256="0" * 63,
            feed_capture_manifest_sha256="b" * 64,
            parser_source_sha256="c" * 64,
        )
