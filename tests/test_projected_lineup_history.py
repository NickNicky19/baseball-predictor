"""Regression and mutation tests for the strict historical lineup feature store."""

from __future__ import annotations

import copy

import pytest

from src.evaluation.projected_lineup_history import HistoricalLineupFeatureError, build_feature_store


def _roster() -> dict:
    return {
        "source_kind": "official_mlb_active_roster_t4", "source_record_id": "roster:2026-07-24:1",
        "received_at_utc": "2026-07-24T12:00:00Z", "payload_sha256": "a" * 64,
        "input_surface_sha256": "b" * 64,
        "players": [{"player_id": number, "position_code": "1B", "position_type": "Infielder"} for number in range(1, 12)],
    }


def _history() -> list[dict]:
    return [
        {"official_game_date": "2024-04-01", "mlb_game_pk": 10, "team_id": 1, "player_id": player, "slot": player}
        for player in range(1, 10)
    ]


def test_prior_completed_lineups_produce_counts_not_target_start_probabilities() -> None:
    record = build_feature_store(official_game_date="2024-04-02", team_id=1, active_roster_receipt=_roster(), completed_lineups=_history())
    assert record["history_game_count"] == 1
    assert record["features"][0]["prior_completed_start_share"] == 1.0
    assert record["features"][0]["prior_slot_distribution_given_start"] == {"1": 1.0}
    assert record["features"][-1]["prior_completed_start_share"] == 0.0
    assert "start_probability" not in repr(record)
    unsigned = dict(record)
    supplied = unsigned.pop("feature_store_sha256")
    assert supplied == __import__("src.evaluation.projected_lineup_contract", fromlist=["sha256_value"]).sha256_value(unsigned)


@pytest.mark.parametrize("mutator, message", [
    (lambda rows: rows.__setitem__(0, {**rows[0], "official_game_date": "2024-04-02"}), "same-day or future"),
    (lambda rows: rows.__setitem__(0, {**rows[0], "team_id": 2}), "team identity"),
    (lambda rows: rows.append(copy.deepcopy(rows[0])), "duplicate game/player"),
    (lambda rows: rows.__setitem__(0, {**rows[0], "slot": 10}), "one through nine"),
])
def test_mutations_cannot_reenter_as_history(mutator, message: str) -> None:
    rows = _history()
    mutator(rows)
    with pytest.raises(HistoricalLineupFeatureError, match=message):
        build_feature_store(official_game_date="2024-04-02", team_id=1, active_roster_receipt=_roster(), completed_lineups=rows)


def test_unreceipted_or_reconstructed_roster_is_rejected() -> None:
    roster = _roster()
    roster["source_kind"] = "historical_final_roster"
    with pytest.raises(HistoricalLineupFeatureError, match="receipted official"):
        build_feature_store(official_game_date="2024-04-02", team_id=1, active_roster_receipt=roster, completed_lineups=_history())


def test_may_2026_is_never_a_permissible_history_target() -> None:
    with pytest.raises(HistoricalLineupFeatureError, match="May 2026"):
        build_feature_store(official_game_date="2026-05-12", team_id=1, active_roster_receipt=_roster(), completed_lineups=_history())
