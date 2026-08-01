from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.evaluation.shared_pa_outcome_source_readiness import (
    OutcomeSourceError,
    REQUIRED_BATTING_FIELDS,
    inspect_release,
)


def _release(root: Path, batting: dict, *, official_date: str = "2023-04-01") -> Path:
    source = root / "release"
    (source / "source-release").mkdir(parents=True)
    (source / "feeds" / "feeds" / "game-1").mkdir(parents=True)
    (source / "source-release" / "source_manifest.json").write_text(
        json.dumps({"schema_version": "pa-volume-official-source-release-v1"}), encoding="utf-8"
    )
    (source / "source-release" / "projection.json").write_text(
        json.dumps({"projection": {"rows": [{"game_pk": 1}]}}), encoding="utf-8"
    )
    player = {"battingOrder": "100", "stats": {"batting": batting}}
    feed = {
        "gamePk": 1,
        "gameData": {"datetime": {"officialDate": official_date}},
        "liveData": {"boxscore": {"teams": {
            "away": {"players": {"ID1": player}},
            "home": {"players": {"ID2": player}},
        }}},
    }
    (source / "feeds" / "feeds" / "game-1" / "response.json").write_text(
        json.dumps(feed), encoding="utf-8"
    )
    (source / "feeds" / "feeds" / "game-1" / "receipt.json").write_text(
        json.dumps({"request": {"full_url": "https://statsapi.mlb.com/api/v1.1/game/1/feed/live?fields=plateAppearances"}}),
        encoding="utf-8",
    )
    return source


def test_pa_only_release_is_not_outcome_complete(tmp_path: Path) -> None:
    result = inspect_release(_release(tmp_path, {"plateAppearances": 4}))
    assert result["status"] == "BLOCKED_SOURCE_LACKS_REQUIRED_PA_OUTCOME_FIELDS"
    assert result["decision"] == "RAW_BYTES_OUTCOME_INCOMPLETE"
    assert result["eligibility"]["opportunity_model_input"] is True
    assert result["eligibility"]["c0_fit"] is False
    assert result["missing_field_occurrences"]["homeRuns"] == 2


def test_complete_release_is_eligible_for_panel_construction(tmp_path: Path) -> None:
    batting = {field: 0 for field in REQUIRED_BATTING_FIELDS}
    batting["plateAppearances"] = 4
    result = inspect_release(_release(tmp_path, batting))
    assert result["status"] == "OUTCOME_COMPLETE_SOURCE_ELIGIBLE_FOR_PANEL_CONSTRUCTION"
    assert result["decision"] == "RAW_BYTES_OUTCOME_COMPLETE"
    assert result["coverage"]["outcome_complete_player_rows"] == 2
    assert result["eligibility"]["c0_fit"] is True


def test_non_2023_feed_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(OutcomeSourceError, match="non-2023"):
        inspect_release(_release(tmp_path, {"plateAppearances": 4}, official_date="2026-05-01"))
