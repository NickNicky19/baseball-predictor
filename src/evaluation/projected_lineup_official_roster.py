"""Allow-listed official MLB active-roster receipt for the projected-lineup lane.

The adapter is deliberately small: it verifies an exact source payload surface,
binds it to the requested team/date/horizon, and returns roster identity only.
It does not infer a starter, a slot, an injury, or a player-prop probability.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping


class OfficialRosterReceiptError(ValueError):
    """An official roster response is unsafe for pregame lineup inputs."""


@dataclass(frozen=True)
class RawOfficialRosterResponse:
    body: bytes
    received_at_utc: str

    def __post_init__(self) -> None:
        if not isinstance(self.body, bytes) or not self.body:
            raise OfficialRosterReceiptError("official roster response must be non-empty bytes")
        _utc(self.received_at_utc, "received_at_utc")

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.body).hexdigest()


def _utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise OfficialRosterReceiptError(f"{label} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise OfficialRosterReceiptError(f"{label} is not an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise OfficialRosterReceiptError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise OfficialRosterReceiptError(f"{label} must be a positive integer")
    return value


def _exact_object(value: Any, keys: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise OfficialRosterReceiptError(f"{label} source schema changed or contains an unapproved field")
    return value


def parse_active_roster_receipt(
    *, response: RawOfficialRosterResponse, requested_date: str, team_id: int, target_horizon_utc: str
) -> dict[str, Any]:
    """Return identity-only active-roster evidence captured by the T−4 horizon."""
    if not isinstance(requested_date, str) or requested_date.startswith("2026-05-"):
        raise OfficialRosterReceiptError("May 2026 is sealed from official roster requests")
    requested_team = _positive_int(team_id, "team_id")
    if _utc(response.received_at_utc, "received_at_utc") > _utc(target_horizon_utc, "target_horizon_utc"):
        raise OfficialRosterReceiptError("official active-roster receipt arrived after T-minus-4")
    try:
        payload = json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OfficialRosterReceiptError("official roster response is not UTF-8 JSON") from exc
    root = _exact_object(payload, {"copyright", "link", "roster", "rosterType", "teamId"}, "official roster root")
    if root["rosterType"] != "active" or _positive_int(root["teamId"], "response.teamId") != requested_team:
        raise OfficialRosterReceiptError("official roster response identity differs from the requested active team roster")
    if not isinstance(root["roster"], list) or len(root["roster"]) < 9:
        raise OfficialRosterReceiptError("official active roster must contain at least nine players")
    players: list[dict[str, Any]] = []
    for raw in root["roster"]:
        entry = _exact_object(raw, {"jerseyNumber", "parentTeamId", "person", "position", "status"}, "official roster entry")
        if _positive_int(entry["parentTeamId"], "parentTeamId") != requested_team:
            raise OfficialRosterReceiptError("official roster entry belongs to a different team")
        person = _exact_object(entry["person"], {"fullName", "id", "link"}, "official roster person")
        position = _exact_object(entry["position"], {"abbreviation", "code", "name", "type"}, "official roster position")
        status = _exact_object(entry["status"], {"code", "description"}, "official roster status")
        if status["code"] != "A":
            raise OfficialRosterReceiptError("active-roster endpoint returned a non-active player")
        if not all(isinstance(position[key], str) and position[key].strip() for key in ("abbreviation", "code", "name", "type")):
            raise OfficialRosterReceiptError("official roster position identity is incomplete")
        players.append({
            "player_id": _positive_int(person["id"], "person.id"),
            "position_code": position["code"],
            "position_type": position["type"],
        })
    if len({entry["player_id"] for entry in players}) != len(players):
        raise OfficialRosterReceiptError("official active roster contains duplicate player identities")
    return {
        "source_kind": "official_mlb_active_roster_t4",
        "source_record_id": f"official_mlb_active_roster:{requested_date}:{requested_team}",
        "received_at_utc": _utc(response.received_at_utc, "received_at_utc").isoformat(timespec="microseconds").replace("+00:00", "Z"),
        "payload_sha256": response.sha256,
        "input_surface_sha256": hashlib.sha256(b"official-mlb-active-roster-input-surface-v1").hexdigest(),
        "players": sorted(players, key=lambda value: value["player_id"]),
    }
