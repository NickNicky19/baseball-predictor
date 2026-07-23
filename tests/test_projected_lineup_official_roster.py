from __future__ import annotations

import json

import pytest

from src.evaluation.projected_lineup_official_roster import (
    OfficialRosterReceiptError,
    RawOfficialRosterResponse,
    parse_active_roster_receipt,
)


def _response() -> RawOfficialRosterResponse:
    roster = []
    for player_id in range(1, 10):
        roster.append({
            "jerseyNumber": str(player_id), "parentTeamId": 147,
            "person": {"fullName": f"Player {player_id}", "id": player_id, "link": f"/api/v1/people/{player_id}"},
            "position": {"abbreviation": "SS", "code": "6", "name": "Shortstop", "type": "Infielder"},
            "status": {"code": "A", "description": "Active"},
        })
    payload = {"copyright": "x", "link": "/api", "roster": roster, "rosterType": "active", "teamId": 147}
    return RawOfficialRosterResponse(json.dumps(payload).encode(), "2026-07-24T15:00:00Z")


def test_active_roster_receipt_is_identity_only_and_hash_bound():
    record = parse_active_roster_receipt(
        response=_response(), requested_date="2026-07-24", team_id=147, target_horizon_utc="2026-07-24T16:00:00Z"
    )
    assert record["source_kind"] == "official_mlb_active_roster_t4"
    assert [item["player_id"] for item in record["players"]] == list(range(1, 10))
    assert "outcome" not in record


@pytest.mark.parametrize("mutation", ["late", "may", "team", "unknown_field", "outcome_field"])
def test_unsafe_roster_inputs_fail_closed(mutation: str):
    response = _response()
    date = "2026-07-24"
    team = 147
    horizon = "2026-07-24T16:00:00Z"
    if mutation == "late":
        response = RawOfficialRosterResponse(response.body, "2026-07-24T16:00:01Z")
    elif mutation == "may":
        date = "2026-05-14"
    elif mutation == "team":
        team = 121
    else:
        raw = json.loads(response.body)
        key = "unexpected" if mutation == "unknown_field" else "outcome"
        raw["roster"][0][key] = "postgame"
        response = RawOfficialRosterResponse(json.dumps(raw).encode(), response.received_at_utc)
    with pytest.raises(OfficialRosterReceiptError):
        parse_active_roster_receipt(response=response, requested_date=date, team_id=team, target_horizon_utc=horizon)
