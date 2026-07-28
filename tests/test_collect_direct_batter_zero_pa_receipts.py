from __future__ import annotations

import copy

import pytest

from scripts.collect_direct_batter_zero_pa_receipts import (
    ZeroPAReceiptError, verify_zero_pa_receipt,
)


def receipt() -> dict:
    batting = {
        "plateAppearances": 0, "atBats": 0, "hits": 0, "doubles": 0,
        "triples": 0, "homeRuns": 0, "baseOnBalls": 0, "strikeOuts": 0,
    }
    return {
        "gamePk": 100,
        "gameData": {
            "datetime": {"officialDate": "2023-06-26"},
            "game": {"type": "R"},
            "status": {"abstractGameState": "Final"},
        },
        "liveData": {
            "boxscore": {
                "teams": {
                    "away": {
                        "players": {
                            "ID7": {"person": {"id": 7}, "stats": {"batting": batting}},
                        },
                    },
                    "home": {"players": {}},
                },
            },
        },
    }


def target() -> dict:
    return {"season": 2023, "game_date": "2023-06-26", "game_pk": 100, "player_id": 7}


def test_official_final_zero_pa_receipt_is_accepted() -> None:
    verify_zero_pa_receipt(receipt(), target())


@pytest.mark.parametrize("mutation,match", [
    ("date", "date mismatch"),
    ("not_final", "not final"),
    ("player", "missing or ambiguous"),
    ("positive_pa", "not verified zero"),
    ("game_type", "not a regular-season"),
])
def test_zero_pa_receipt_mutations_fail_closed(mutation: str, match: str) -> None:
    value = copy.deepcopy(receipt())
    if mutation == "date":
        value["gameData"]["datetime"]["officialDate"] = "2023-06-27"
    elif mutation == "not_final":
        value["gameData"]["status"]["abstractGameState"] = "Live"
    elif mutation == "player":
        value["liveData"]["boxscore"]["teams"]["away"]["players"]["ID7"]["person"]["id"] = 8
    elif mutation == "positive_pa":
        value["liveData"]["boxscore"]["teams"]["away"]["players"]["ID7"]["stats"]["batting"]["plateAppearances"] = 1
    elif mutation == "game_type":
        value["gameData"]["game"]["type"] = "S"
    with pytest.raises(ZeroPAReceiptError, match=match):
        verify_zero_pa_receipt(value, target())


def test_duplicate_player_identity_is_rejected() -> None:
    value = receipt()
    duplicate = copy.deepcopy(
        value["liveData"]["boxscore"]["teams"]["away"]["players"]["ID7"]
    )
    value["liveData"]["boxscore"]["teams"]["home"]["players"]["ID7B"] = duplicate
    with pytest.raises(ZeroPAReceiptError, match="ambiguous"):
        verify_zero_pa_receipt(value, target())
