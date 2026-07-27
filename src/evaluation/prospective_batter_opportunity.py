"""Prospective-only batter eligibility and PA-opportunity evidence.

This module does not project a lineup and does not produce a player-prop
probability.  It turns a receipt-proven T-4 active roster plus previously
captured, opportunity-only final-game records into a descriptive input
snapshot.  Missing prior games remain explicit denominator losses.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Mapping, Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit

from src.evaluation.projected_lineup_contract import canonical_bytes, sha256_value
from src.evaluation.projected_lineup_official_roster import (
    RawOfficialRosterResponse,
    OfficialRosterReceiptError,
    parse_active_roster_receipt,
)


class ProspectiveBatterOpportunityError(ValueError):
    """An input cannot support truthful prospective opportunity evidence."""


HISTORY_SCHEMA_VERSION = "prospective-batter-opportunity-history-v1"
HISTORY_PLAN_SCHEMA_VERSION = "prospective-batter-opportunity-history-capture-plan-v1"
SCHEDULE_SCHEMA_VERSION = "prospective-batter-opportunity-schedule-v1"
COVERAGE_SCHEMA_VERSION = "prospective-batter-opportunity-coverage-v1"
SNAPSHOT_SCHEMA_VERSION = "prospective-batter-opportunity-snapshot-v1"
SOURCE_KIND = "official_mlb_final_opportunity_only_v1"
SCHEDULE_SOURCE_KIND = "official_mlb_prior_schedule_denominator_v1"
SCHEDULE_RECEIPT_MAX_AGE_SECONDS = 120
MLB_SOURCE_HOST = "statsapi.mlb.com"
OPPORTUNITY_FIELDS = (
    "gamePk,gameData,datetime,officialDate,status,abstractGameState,codedGameState,"
    "liveData,boxscore,teams,away,home,team,id,players,person,battingOrder,stats,batting,plateAppearances"
)
SCHEDULE_FIELDS = (
    "dates,date,games,gamePk,officialDate,status,abstractGameState,codedGameState,"
    "teams,away,home,team,id"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ROOT_KEYS = {"gamePk", "gameData", "liveData"}
_GAME_DATA_KEYS = {"datetime", "status"}
_DATETIME_KEYS = {"officialDate"}
_STATUS_KEYS = {"abstractGameState", "codedGameState"}
_LIVE_DATA_KEYS = {"boxscore"}
_BOXSCORE_KEYS = {"teams"}
_TEAM_COLLECTION_KEYS = {"away", "home"}
_TEAM_KEYS = {"team", "players"}
_TEAM_ID_KEYS = {"id"}
_PLAYER_KEYS = {"person", "battingOrder", "stats"}
_PERSON_KEYS = {"id"}
_STATS_KEYS = {"batting"}
_BATTING_KEYS = {"plateAppearances"}
_SCHEDULE_ROOT_KEYS = {"dates"}
_SCHEDULE_DATE_KEYS = {"date", "games"}
_SCHEDULE_GAME_KEYS = {"gamePk", "officialDate", "status", "teams"}
_SCHEDULE_SIDE_KEYS = {"team"}


@dataclass(frozen=True)
class RawOpportunityResponse:
    """Exact bytes and observation time from the fields-limited MLB response."""

    body: bytes
    received_at_utc: str
    request_url: str
    http_status: int = 200
    content_type: str = "application/json"
    transport_payload_sha256: str | None = None
    transport_payload_size: int | None = None


def build_history_capture_plan(
    *,
    created_at_utc: str,
    mlb_game_pk: int,
    official_game_date: str,
    official_start_time_utc: str,
    capture_deadline_utc: str,
    side: str,
    team_id: int,
    source_t4_plan_sha256: str,
    roster_side_target_id: str,
    active_roster_receipt_sha256: str,
) -> dict[str, Any]:
    """Create the immutable pregame authorization for one future final receipt."""

    game_pk = _positive_int(mlb_game_pk, "mlb_game_pk")
    game_date = _canonical_date(official_game_date, "official_game_date")
    target_team = _positive_int(team_id, "team_id")
    if side not in _TEAM_COLLECTION_KEYS:
        raise ProspectiveBatterOpportunityError("side must be home or away")
    created = _utc(created_at_utc, "created_at_utc")
    starts = _utc(official_start_time_utc, "official_start_time_utc")
    deadline = _utc(capture_deadline_utc, "capture_deadline_utc")
    if not created < starts < deadline:
        raise ProspectiveBatterOpportunityError("history capture plan must be created before its future capture window")
    request_url = f"https://{MLB_SOURCE_HOST}/api/v1.1/game/{game_pk}/feed/live?" + urlencode(
        {"fields": OPPORTUNITY_FIELDS}
    )
    unsigned = {
        "schema_version": HISTORY_PLAN_SCHEMA_VERSION,
        "created_at_utc": _stamp(created),
        "mlb_game_pk": game_pk,
        "official_game_date": game_date.isoformat(),
        "official_start_time_utc": _stamp(starts),
        "capture_deadline_utc": _stamp(deadline),
        "side": side,
        "team_id": target_team,
        "source_t4_plan_sha256": _sha(source_t4_plan_sha256, "source_t4_plan_sha256"),
        "roster_side_target_id": _sha(roster_side_target_id, "roster_side_target_id"),
        "active_roster_receipt_sha256": _sha(
            active_roster_receipt_sha256, "active_roster_receipt_sha256"
        ),
        "source_kind": SOURCE_KIND,
        "source_request_url": request_url,
        "research_only": True,
        "backfill_authorized": False,
        "betting_authorized": False,
    }
    return {**unsigned, "capture_plan_sha256": sha256_value(unsigned)}


def validate_history_capture_plan(value: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "schema_version", "created_at_utc", "mlb_game_pk", "official_game_date",
        "official_start_time_utc", "capture_deadline_utc", "side", "team_id",
        "source_t4_plan_sha256", "roster_side_target_id", "active_roster_receipt_sha256",
        "source_kind", "source_request_url", "research_only", "backfill_authorized",
        "betting_authorized", "capture_plan_sha256",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise ProspectiveBatterOpportunityError("history capture plan schema changed")
    rebuilt = build_history_capture_plan(
        created_at_utc=str(value["created_at_utc"]),
        mlb_game_pk=value["mlb_game_pk"],
        official_game_date=str(value["official_game_date"]),
        official_start_time_utc=str(value["official_start_time_utc"]),
        capture_deadline_utc=str(value["capture_deadline_utc"]),
        side=str(value["side"]),
        team_id=value["team_id"],
        source_t4_plan_sha256=str(value["source_t4_plan_sha256"]),
        roster_side_target_id=str(value["roster_side_target_id"]),
        active_roster_receipt_sha256=str(value["active_roster_receipt_sha256"]),
    )
    if rebuilt != dict(value):
        raise ProspectiveBatterOpportunityError("history capture plan identity or hash differs")
    return rebuilt


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ProspectiveBatterOpportunityError(f"duplicate JSON key {key!r}")
        out[key] = value
    return out


def _mapping(value: Any, keys: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise ProspectiveBatterOpportunityError(f"{label} source surface changed")
    return value


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ProspectiveBatterOpportunityError(f"{label} must be a positive integer")
    return value


def _nonnegative_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProspectiveBatterOpportunityError(f"{label} must be a non-negative integer")
    return value


def _canonical_date(value: Any, label: str) -> date:
    if not isinstance(value, str):
        raise ProspectiveBatterOpportunityError(f"{label} must be a canonical ISO date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ProspectiveBatterOpportunityError(f"{label} must be a canonical ISO date") from exc
    if parsed.isoformat() != value:
        raise ProspectiveBatterOpportunityError(f"{label} must be a canonical ISO date")
    if parsed.year == 2026 and parsed.month == 5:
        raise ProspectiveBatterOpportunityError("May 2026 is sealed from opportunity evidence")
    return parsed


def _utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise ProspectiveBatterOpportunityError(f"{label} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProspectiveBatterOpportunityError(f"{label} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProspectiveBatterOpportunityError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ProspectiveBatterOpportunityError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _decode(payload: bytes) -> Mapping[str, Any]:
    if not isinstance(payload, bytes) or not payload:
        raise ProspectiveBatterOpportunityError("opportunity source bytes are missing")
    try:
        value = json.loads(payload, object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProspectiveBatterOpportunityError("opportunity source is not valid JSON") from exc
    return _mapping(value, _ROOT_KEYS, "root")


def _required_mapping(value: Any, required: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or not required <= set(value):
        raise ProspectiveBatterOpportunityError(f"{label} is missing required source fields")
    return value


def sanitize_opportunity_transport(
    *,
    response: RawOpportunityResponse,
    expected_game_pk: int,
) -> RawOpportunityResponse:
    """Reduce the real MLB transport body to the approved opportunity surface.

    MLB's ``fields`` parameter does not remove every nested player field.  The
    original transport body is therefore never handed to a feature consumer or
    persisted by this component.  Its hash and size remain in the receipt; the
    returned body contains only the exact positive schema consumed downstream.
    """

    game_pk = _positive_int(expected_game_pk, "expected_game_pk")
    request_url = _validated_request(
        response,
        expected_path=f"/api/v1.1/game/{game_pk}/feed/live",
        expected_query={"fields": OPPORTUNITY_FIELDS},
    )
    if not isinstance(response.body, bytes) or not response.body:
        raise ProspectiveBatterOpportunityError("opportunity transport bytes are missing")
    try:
        payload = json.loads(response.body, object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProspectiveBatterOpportunityError("opportunity transport is not valid JSON") from exc
    root = _required_mapping(payload, _ROOT_KEYS, "transport root")
    if _positive_int(root["gamePk"], "transport gamePk") != game_pk:
        raise ProspectiveBatterOpportunityError("opportunity transport game identity differs")
    game_data = _required_mapping(root["gameData"], _GAME_DATA_KEYS, "transport gameData")
    datetime_row = _required_mapping(game_data["datetime"], _DATETIME_KEYS, "transport gameData.datetime")
    status_row = _required_mapping(game_data["status"], _STATUS_KEYS, "transport gameData.status")
    live_data = _required_mapping(root["liveData"], _LIVE_DATA_KEYS, "transport liveData")
    boxscore = _required_mapping(live_data["boxscore"], _BOXSCORE_KEYS, "transport liveData.boxscore")
    teams = _required_mapping(boxscore["teams"], _TEAM_COLLECTION_KEYS, "transport teams")
    sanitized_teams: dict[str, Any] = {}
    for side in ("away", "home"):
        team = _required_mapping(teams[side], _TEAM_KEYS, f"transport teams.{side}")
        team_row = _required_mapping(team["team"], _TEAM_ID_KEYS, f"transport teams.{side}.team")
        players = team["players"]
        if not isinstance(players, Mapping):
            raise ProspectiveBatterOpportunityError("opportunity transport players must be an object")
        sanitized_players: dict[str, Any] = {}
        for player_key, raw_player in players.items():
            if not isinstance(player_key, str) or not isinstance(raw_player, Mapping):
                raise ProspectiveBatterOpportunityError("opportunity transport player identity is malformed")
            person = _required_mapping(raw_player.get("person"), _PERSON_KEYS, f"transport players.{player_key}.person")
            player_id = _positive_int(person["id"], f"transport players.{player_key}.person.id")
            if player_key != f"ID{player_id}":
                raise ProspectiveBatterOpportunityError("opportunity transport player key differs from person identity")
            batting_order = raw_player.get("battingOrder")
            raw_stats = raw_player.get("stats")
            batting = raw_stats.get("batting") if isinstance(raw_stats, Mapping) else None
            has_pa = isinstance(batting, Mapping) and "plateAppearances" in batting
            if batting_order in {None, ""} and not has_pa:
                continue
            if not has_pa:
                raise ProspectiveBatterOpportunityError("opportunity transport batter lacks plate appearances")
            sanitized_players[player_key] = {
                "person": {"id": player_id},
                "battingOrder": batting_order,
                "stats": {
                    "batting": {
                        "plateAppearances": _nonnegative_int(
                            batting["plateAppearances"],
                            f"transport players.{player_key}.plateAppearances",
                        )
                    }
                },
            }
        sanitized_teams[side] = {
            "team": {"id": _positive_int(team_row["id"], f"transport teams.{side}.team.id")},
            "players": sanitized_players,
        }
    sanitized = {
        "gamePk": game_pk,
        "gameData": {
            "datetime": {"officialDate": datetime_row["officialDate"]},
            "status": {
                "abstractGameState": status_row["abstractGameState"],
                "codedGameState": status_row["codedGameState"],
            },
        },
        "liveData": {"boxscore": {"teams": sanitized_teams}},
    }
    transport_sha = hashlib.sha256(response.body).hexdigest()
    return RawOpportunityResponse(
        body=canonical_bytes(sanitized),
        received_at_utc=response.received_at_utc,
        request_url=request_url,
        http_status=200,
        content_type="application/json",
        transport_payload_sha256=transport_sha,
        transport_payload_size=len(response.body),
    )


def _validated_request(
    response: RawOpportunityResponse,
    *,
    expected_path: str,
    expected_query: Mapping[str, str],
) -> str:
    if response.http_status != 200 or response.content_type.split(";", 1)[0].strip().lower() != "application/json":
        raise ProspectiveBatterOpportunityError("opportunity source HTTP response is not approved JSON")
    if not isinstance(response.request_url, str):
        raise ProspectiveBatterOpportunityError("opportunity request URL is missing")
    parsed = urlsplit(response.request_url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != MLB_SOURCE_HOST
        or parsed.port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != expected_path
        or parsed.fragment
    ):
        raise ProspectiveBatterOpportunityError("opportunity request source identity differs")
    pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    if len(pairs) != len(expected_query) or len({key for key, _ in pairs}) != len(pairs) or dict(pairs) != dict(expected_query):
        raise ProspectiveBatterOpportunityError("opportunity request parameters differ")
    return response.request_url


def _parse_opportunity_team(value: Any, side: str) -> tuple[int, list[dict[str, Any]]]:
    team = _mapping(value, _TEAM_KEYS, f"teams.{side}")
    identity = _mapping(team["team"], _TEAM_ID_KEYS, f"teams.{side}.team")
    team_id = _positive_int(identity["id"], f"teams.{side}.team.id")
    players = team["players"]
    if not isinstance(players, Mapping):
        raise ProspectiveBatterOpportunityError("opportunity players must be an object")
    records: list[dict[str, Any]] = []
    seen_players: set[int] = set()
    starter_slots: set[int] = set()
    for player_key, raw in players.items():
        if not isinstance(player_key, str):
            raise ProspectiveBatterOpportunityError("opportunity player key is invalid")
        player = _mapping(raw, _PLAYER_KEYS, f"players.{player_key}")
        person = _mapping(player["person"], _PERSON_KEYS, f"players.{player_key}.person")
        player_id = _positive_int(person["id"], f"players.{player_key}.person.id")
        if player_key != f"ID{player_id}" or player_id in seen_players:
            raise ProspectiveBatterOpportunityError("opportunity player identity is duplicated or inconsistent")
        seen_players.add(player_id)
        batting_order = player["battingOrder"]
        stats = player["stats"]
        if batting_order in {None, ""}:
            if not isinstance(stats, Mapping) or (set(stats) not in (set(), _STATS_KEYS)):
                raise ProspectiveBatterOpportunityError("non-batter source surface changed")
            if set(stats) == _STATS_KEYS:
                batting = _mapping(stats["batting"], _BATTING_KEYS, f"players.{player_key}.stats.batting")
                _nonnegative_int(batting["plateAppearances"], f"players.{player_key}.plateAppearances")
            continue
        if not isinstance(batting_order, str) or len(batting_order) != 3 or not batting_order.isdigit():
            raise ProspectiveBatterOpportunityError("opportunity battingOrder is invalid")
        slot, sequence = divmod(int(batting_order), 100)
        if not 1 <= slot <= 9:
            raise ProspectiveBatterOpportunityError("opportunity lineup slot is invalid")
        is_starter = sequence == 0
        if is_starter and slot in starter_slots:
            raise ProspectiveBatterOpportunityError("opportunity source has duplicate original starter slots")
        if is_starter:
            starter_slots.add(slot)
        parsed_stats = _mapping(stats, _STATS_KEYS, f"players.{player_key}.stats")
        batting = _mapping(parsed_stats["batting"], _BATTING_KEYS, f"players.{player_key}.stats.batting")
        records.append(
            {
                "player_id": player_id,
                "lineup_slot": slot,
                "lineup_sequence": sequence,
                "is_starter": is_starter,
                "plate_appearances": _nonnegative_int(
                    batting["plateAppearances"], f"players.{player_key}.plateAppearances"
                ),
            }
        )
    if starter_slots != set(range(1, 10)):
        raise ProspectiveBatterOpportunityError("opportunity source does not contain exactly nine original starters")
    return team_id, records


def parse_opportunity_history(
    *,
    response: RawOpportunityResponse,
    expected_game_pk: int,
    expected_official_date: str,
    side: str,
    expected_team_id: int,
    capture_plan: Mapping[str, Any],
) -> dict[str, Any]:
    """Parse a fields-limited final feed containing only lineup and PA facts."""

    game_pk = _positive_int(expected_game_pk, "expected_game_pk")
    game_date = _canonical_date(expected_official_date, "expected_official_date")
    team_id = _positive_int(expected_team_id, "expected_team_id")
    if side not in _TEAM_COLLECTION_KEYS:
        raise ProspectiveBatterOpportunityError("side must be home or away")
    received = _utc(response.received_at_utc, "received_at_utc")
    plan = validate_history_capture_plan(capture_plan)
    if (
        plan["mlb_game_pk"] != game_pk
        or plan["official_game_date"] != game_date.isoformat()
        or plan["side"] != side
        or plan["team_id"] != team_id
    ):
        raise ProspectiveBatterOpportunityError("history capture plan target identity differs")
    if not _utc(plan["official_start_time_utc"], "plan official_start_time_utc") <= received <= _utc(
        plan["capture_deadline_utc"], "plan capture_deadline_utc"
    ):
        raise ProspectiveBatterOpportunityError("history response is outside its predeclared capture window")
    request_url = _validated_request(
        response,
        expected_path=f"/api/v1.1/game/{game_pk}/feed/live",
        expected_query={"fields": OPPORTUNITY_FIELDS},
    )
    if request_url != plan["source_request_url"]:
        raise ProspectiveBatterOpportunityError("history response request differs from its capture plan")
    payload = _decode(response.body)
    if _positive_int(payload["gamePk"], "gamePk") != game_pk:
        raise ProspectiveBatterOpportunityError("opportunity game identity differs")
    game_data = _mapping(payload["gameData"], _GAME_DATA_KEYS, "gameData")
    date_data = _mapping(game_data["datetime"], _DATETIME_KEYS, "gameData.datetime")
    source_date = _canonical_date(date_data["officialDate"], "gameData.datetime.officialDate")
    if source_date != game_date:
        raise ProspectiveBatterOpportunityError("opportunity official date differs")
    status = _mapping(game_data["status"], _STATUS_KEYS, "gameData.status")
    if status["abstractGameState"] != "Final" or status["codedGameState"] != "F":
        raise ProspectiveBatterOpportunityError("opportunity source is not coded final")
    live_data = _mapping(payload["liveData"], _LIVE_DATA_KEYS, "liveData")
    boxscore = _mapping(live_data["boxscore"], _BOXSCORE_KEYS, "liveData.boxscore")
    teams = _mapping(boxscore["teams"], _TEAM_COLLECTION_KEYS, "liveData.boxscore.teams")
    parsed_teams = {team_side: _parse_opportunity_team(teams[team_side], team_side) for team_side in ("away", "home")}
    if parsed_teams["away"][0] == parsed_teams["home"][0]:
        raise ProspectiveBatterOpportunityError("opportunity home and away team identities collide")
    parsed_team_id, records = parsed_teams[side]
    if parsed_team_id != team_id:
        raise ProspectiveBatterOpportunityError("opportunity team identity differs")
    sanitized_sha = hashlib.sha256(response.body).hexdigest()
    transport_sha = response.transport_payload_sha256 or sanitized_sha
    transport_size = response.transport_payload_size if response.transport_payload_size is not None else len(response.body)
    _sha(transport_sha, "transport_payload_sha256")
    if isinstance(transport_size, bool) or not isinstance(transport_size, int) or transport_size <= 0:
        raise ProspectiveBatterOpportunityError("transport_payload_size must be a positive integer")
    unsigned = {
        "schema_version": HISTORY_SCHEMA_VERSION,
        "source_kind": SOURCE_KIND,
        "mlb_game_pk": game_pk,
        "official_game_date": game_date.isoformat(),
        "side": side,
        "team_id": team_id,
        "capture_plan": plan,
        "capture_plan_sha256": plan["capture_plan_sha256"],
        "source_received_at_utc": _stamp(received),
        "source_request_url": request_url,
        "source_http_status": 200,
        "source_content_type": "application/json",
        "source_payload_sha256": sanitized_sha,
        "transport_payload_sha256": transport_sha,
        "transport_payload_size": transport_size,
        "transport_payload_retained": transport_sha == sanitized_sha and transport_size == len(response.body),
        "input_surface_sha256": hashlib.sha256(
            b"official-mlb-final-opportunity-only-input-surface-v1"
        ).hexdigest(),
        "players": sorted(records, key=lambda item: (item["lineup_slot"], item["lineup_sequence"])),
        "research_only": True,
        "betting_authorized": False,
    }
    return {**unsigned, "history_record_sha256": sha256_value(unsigned)}


def parse_prior_schedule_denominator(
    *,
    response: RawOpportunityResponse,
    requested_start_date: str,
    requested_end_date: str,
    target_official_game_date: str,
    target_horizon_utc: str,
    team_id: int,
) -> dict[str, Any]:
    """Parse a fields-limited official schedule receipt for denominator truth."""

    start = _canonical_date(requested_start_date, "requested_start_date")
    end = _canonical_date(requested_end_date, "requested_end_date")
    target = _canonical_date(target_official_game_date, "target_official_game_date")
    target_team = _positive_int(team_id, "team_id")
    if start > end or end >= target:
        raise ProspectiveBatterOpportunityError("prior schedule range must end before the target date")
    cursor = start
    while cursor <= end:
        if cursor.year == 2026 and cursor.month == 5:
            raise ProspectiveBatterOpportunityError("May 2026 is sealed from schedule denominator evidence")
        cursor += timedelta(days=1)
    received = _utc(response.received_at_utc, "schedule received_at_utc")
    horizon = _utc(target_horizon_utc, "target_horizon_utc")
    if received > horizon:
        raise ProspectiveBatterOpportunityError("prior schedule receipt arrived after T-minus-4")
    if (horizon - received).total_seconds() > SCHEDULE_RECEIPT_MAX_AGE_SECONDS:
        raise ProspectiveBatterOpportunityError("prior schedule receipt is stale at T-minus-4")
    request_url = _validated_request(
        response,
        expected_path="/api/v1/schedule",
        expected_query={
            "sportId": "1",
            "teamId": str(target_team),
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "fields": SCHEDULE_FIELDS,
        },
    )
    try:
        payload = json.loads(response.body, object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProspectiveBatterOpportunityError("prior schedule source is not valid JSON") from exc
    root = _mapping(payload, _SCHEDULE_ROOT_KEYS, "schedule root")
    dates = root["dates"]
    if not isinstance(dates, list):
        raise ProspectiveBatterOpportunityError("schedule dates must be a list")
    games: list[dict[str, Any]] = []
    seen_dates: set[str] = set()
    seen_games: set[int] = set()
    for raw_date in dates:
        date_row = _mapping(raw_date, _SCHEDULE_DATE_KEYS, "schedule date")
        schedule_date = _canonical_date(date_row["date"], "schedule date.date")
        if not start <= schedule_date <= end or schedule_date.isoformat() in seen_dates:
            raise ProspectiveBatterOpportunityError("schedule date is duplicated or outside the requested range")
        seen_dates.add(schedule_date.isoformat())
        if not isinstance(date_row["games"], list):
            raise ProspectiveBatterOpportunityError("schedule games must be a list")
        for raw_game in date_row["games"]:
            game = _mapping(raw_game, _SCHEDULE_GAME_KEYS, "schedule game")
            game_pk = _positive_int(game["gamePk"], "schedule gamePk")
            official_date = _canonical_date(game["officialDate"], "schedule officialDate")
            if official_date != schedule_date or game_pk in seen_games:
                raise ProspectiveBatterOpportunityError("schedule game date or identity is duplicated")
            status = _mapping(game["status"], _STATUS_KEYS, "schedule status")
            teams = _mapping(game["teams"], _TEAM_COLLECTION_KEYS, "schedule teams")
            team_ids = []
            for schedule_side in ("away", "home"):
                side_row = _mapping(teams[schedule_side], _SCHEDULE_SIDE_KEYS, f"schedule teams.{schedule_side}")
                team_row = _mapping(side_row["team"], _TEAM_ID_KEYS, f"schedule teams.{schedule_side}.team")
                team_ids.append(_positive_int(team_row["id"], f"schedule teams.{schedule_side}.team.id"))
            if len(set(team_ids)) != 2 or team_ids.count(target_team) != 1:
                raise ProspectiveBatterOpportunityError("schedule team identity is missing or ambiguous")
            seen_games.add(game_pk)
            is_final = status["abstractGameState"] == "Final" and status["codedGameState"] == "F"
            games.append(
                {
                    "mlb_game_pk": game_pk,
                    "official_game_date": official_date.isoformat(),
                    "coded_final": is_final,
                }
            )
    unsigned = {
        "schema_version": SCHEDULE_SCHEMA_VERSION,
        "source_kind": SCHEDULE_SOURCE_KIND,
        "team_id": target_team,
        "requested_start_date": start.isoformat(),
        "requested_end_date": end.isoformat(),
        "target_official_game_date": target.isoformat(),
        "target_horizon_utc": _stamp(_utc(target_horizon_utc, "target_horizon_utc")),
        "source_received_at_utc": _stamp(received),
        "source_request_url": request_url,
        "source_http_status": 200,
        "source_content_type": "application/json",
        "source_payload_sha256": hashlib.sha256(response.body).hexdigest(),
        "input_surface_sha256": hashlib.sha256(
            b"official-mlb-prior-schedule-denominator-input-surface-v1"
        ).hexdigest(),
        "games": sorted(games, key=lambda item: (item["official_game_date"], item["mlb_game_pk"])),
        "research_only": True,
        "betting_authorized": False,
    }
    return {**unsigned, "schedule_receipt_sha256": sha256_value(unsigned)}


def build_history_coverage(
    *,
    collection_epoch_date: str,
    target_official_game_date: str,
    team_id: int,
    schedule_receipts: Sequence[Mapping[str, Any]],
    captured_prior_game_pks: Sequence[int],
    terminal_missing_prior_game_pks: Sequence[int],
) -> dict[str, Any]:
    """Bind denominator coverage to parsed schedule receipts without hiding gaps."""

    epoch = _canonical_date(collection_epoch_date, "collection_epoch_date")
    target = _canonical_date(target_official_game_date, "target_official_game_date")
    target_team = _positive_int(team_id, "team_id")
    if epoch >= target:
        raise ProspectiveBatterOpportunityError("collection epoch must precede the target date")
    if not isinstance(schedule_receipts, Sequence) or isinstance(schedule_receipts, (str, bytes)) or not schedule_receipts:
        raise ProspectiveBatterOpportunityError("schedule denominator receipts are required")
    required_receipt_keys = {
        "schema_version", "source_kind", "team_id", "requested_start_date", "requested_end_date",
        "target_official_game_date", "target_horizon_utc", "source_received_at_utc",
        "source_request_url", "source_http_status", "source_content_type",
        "source_payload_sha256", "input_surface_sha256", "games", "research_only",
        "betting_authorized", "schedule_receipt_sha256",
    }
    covered_dates: set[date] = set()
    expected: list[int] = []
    nonfinal: list[int] = []
    receipt_hashes: list[str] = []
    raw_hashes: list[str] = []
    for receipt in schedule_receipts:
        if not isinstance(receipt, Mapping) or set(receipt) != required_receipt_keys:
            raise ProspectiveBatterOpportunityError("schedule denominator receipt schema changed")
        unsigned_receipt = dict(receipt)
        supplied = _sha(unsigned_receipt.pop("schedule_receipt_sha256"), "schedule_receipt_sha256")
        if sha256_value(unsigned_receipt) != supplied:
            raise ProspectiveBatterOpportunityError("schedule denominator receipt hash differs")
        if receipt["schema_version"] != SCHEDULE_SCHEMA_VERSION or receipt["source_kind"] != SCHEDULE_SOURCE_KIND or receipt["team_id"] != target_team or receipt["target_official_game_date"] != target.isoformat():
            raise ProspectiveBatterOpportunityError("schedule denominator receipt identity differs")
        start = _canonical_date(receipt["requested_start_date"], "schedule requested_start_date")
        end = _canonical_date(receipt["requested_end_date"], "schedule requested_end_date")
        cursor = start
        while cursor <= end:
            if cursor in covered_dates:
                raise ProspectiveBatterOpportunityError("schedule denominator date ranges overlap")
            covered_dates.add(cursor)
            cursor += timedelta(days=1)
        for game in receipt["games"]:
            game_pk = _positive_int(game.get("mlb_game_pk"), "schedule receipt game_pk")
            (expected if game.get("coded_final") is True else nonfinal).append(game_pk)
        receipt_hashes.append(supplied)
        raw_hashes.append(_sha(receipt["source_payload_sha256"], "schedule source_payload_sha256"))
    required_dates: set[date] = set()
    cursor = epoch
    while cursor < target:
        if not (cursor.year == 2026 and cursor.month == 5):
            required_dates.add(cursor)
        cursor += timedelta(days=1)
    if covered_dates != required_dates:
        raise ProspectiveBatterOpportunityError("schedule denominator receipts do not cover every permitted date")
    if len(set(expected + nonfinal)) != len(expected) + len(nonfinal):
        raise ProspectiveBatterOpportunityError("schedule denominator game identities are duplicated")
    captured = [_positive_int(value, "captured_prior_game_pk") for value in captured_prior_game_pks]
    missing = [_positive_int(value, "terminal_missing_prior_game_pk") for value in terminal_missing_prior_game_pks]
    if len(set(captured)) != len(captured) or len(set(missing)) != len(missing):
        raise ProspectiveBatterOpportunityError("coverage game identities contain duplicates")
    if set(captured) & set(missing) or set(captured) | set(missing) != set(expected):
        raise ProspectiveBatterOpportunityError("coverage terminal states do not partition expected games")
    unsigned = {
        "schema_version": COVERAGE_SCHEMA_VERSION,
        "collection_epoch_date": epoch.isoformat(),
        "target_official_game_date": target.isoformat(),
        "team_id": target_team,
        "schedule_receipts": [dict(value) for value in schedule_receipts],
        "schedule_receipt_sha256s": sorted(receipt_hashes),
        "schedule_raw_sha256s": sorted(raw_hashes),
        "expected_prior_game_pks": sorted(expected),
        "nonfinal_prior_game_pks": sorted(nonfinal),
        "captured_prior_game_pks": sorted(captured),
        "terminal_missing_prior_game_pks": sorted(missing),
        "expected_game_count": len(expected),
        "captured_game_count": len(captured),
        "terminal_missing_game_count": len(missing),
        "complete_coverage": not missing,
        "denominator_receipt_bound": True,
        "research_only": True,
        "betting_authorized": False,
    }
    return {**unsigned, "coverage_sha256": sha256_value(unsigned)}


def _validate_coverage(
    value: Mapping[str, Any],
    *,
    target_date: date,
    target_horizon_utc: str,
    team_id: int,
    schedule_raw_by_sha256: Mapping[str, bytes],
) -> dict[str, Any]:
    required = {
        "schema_version", "collection_epoch_date", "target_official_game_date",
        "team_id", "schedule_receipts", "schedule_receipt_sha256s", "schedule_raw_sha256s",
        "expected_prior_game_pks", "nonfinal_prior_game_pks", "captured_prior_game_pks", "terminal_missing_prior_game_pks",
        "expected_game_count", "captured_game_count", "terminal_missing_game_count",
        "complete_coverage", "denominator_receipt_bound", "research_only", "betting_authorized", "coverage_sha256",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise ProspectiveBatterOpportunityError("history coverage schema changed")
    supplied = _sha(value["coverage_sha256"], "coverage_sha256")
    receipts = value["schedule_receipts"]
    if not isinstance(receipts, list) or not isinstance(schedule_raw_by_sha256, Mapping):
        raise ProspectiveBatterOpportunityError("schedule denominator replay inputs are missing")
    replayed_receipts: list[dict[str, Any]] = []
    consumed_raw: set[str] = set()
    for receipt in receipts:
        if not isinstance(receipt, Mapping):
            raise ProspectiveBatterOpportunityError("schedule denominator receipt is malformed")
        raw_hash = _sha(receipt.get("source_payload_sha256"), "schedule source_payload_sha256")
        raw = schedule_raw_by_sha256.get(raw_hash)
        if not isinstance(raw, bytes) or hashlib.sha256(raw).hexdigest() != raw_hash:
            raise ProspectiveBatterOpportunityError("schedule denominator raw is missing or differs")
        replayed_receipts.append(
            parse_prior_schedule_denominator(
                response=RawOpportunityResponse(
                    raw,
                    str(receipt.get("source_received_at_utc")),
                    str(receipt.get("source_request_url")),
                    int(receipt.get("source_http_status")),
                    str(receipt.get("source_content_type")),
                ),
                requested_start_date=str(receipt.get("requested_start_date")),
                requested_end_date=str(receipt.get("requested_end_date")),
                target_official_game_date=target_date.isoformat(),
                target_horizon_utc=target_horizon_utc,
                team_id=team_id,
            )
        )
        consumed_raw.add(raw_hash)
    if replayed_receipts != receipts or consumed_raw != set(schedule_raw_by_sha256):
        raise ProspectiveBatterOpportunityError("schedule denominator receipts/raw do not replay exactly")
    rebuilt = build_history_coverage(
        collection_epoch_date=value["collection_epoch_date"],
        target_official_game_date=value["target_official_game_date"],
        team_id=team_id,
        schedule_receipts=replayed_receipts,
        captured_prior_game_pks=value["captured_prior_game_pks"],
        terminal_missing_prior_game_pks=value["terminal_missing_prior_game_pks"],
    )
    if rebuilt["coverage_sha256"] != supplied or rebuilt != dict(value):
        raise ProspectiveBatterOpportunityError("history coverage content differs from its hash")
    if rebuilt["target_official_game_date"] != target_date.isoformat():
        raise ProspectiveBatterOpportunityError("history coverage target date differs")
    return rebuilt


def build_pregame_opportunity_snapshot(
    *,
    official_game_date: str,
    mlb_game_pk: int,
    side: str,
    team_id: int,
    target_horizon_utc: str,
    assembled_at_utc: str,
    active_roster_receipt: Mapping[str, Any],
    active_roster_raw: bytes,
    history_records: Sequence[Mapping[str, Any]],
    history_raw_by_sha256: Mapping[str, bytes],
    history_coverage: Mapping[str, Any],
    schedule_raw_by_sha256: Mapping[str, bytes],
) -> dict[str, Any]:
    """Build one non-probabilistic, replayable T-4 opportunity snapshot."""

    target_date = _canonical_date(official_game_date, "official_game_date")
    game_pk = _positive_int(mlb_game_pk, "mlb_game_pk")
    target_team = _positive_int(team_id, "team_id")
    if side not in _TEAM_COLLECTION_KEYS:
        raise ProspectiveBatterOpportunityError("side must be home or away")
    horizon = _utc(target_horizon_utc, "target_horizon_utc")
    assembled = _utc(assembled_at_utc, "assembled_at_utc")
    if assembled > horizon:
        raise ProspectiveBatterOpportunityError("opportunity snapshot was assembled after T-minus-4")
    try:
        reproduced_roster = parse_active_roster_receipt(
            response=RawOfficialRosterResponse(active_roster_raw, str(active_roster_receipt.get("received_at_utc"))),
            requested_date=target_date.isoformat(),
            team_id=target_team,
            target_horizon_utc=_stamp(horizon),
        )
    except OfficialRosterReceiptError as exc:
        raise ProspectiveBatterOpportunityError("active-roster receipt cannot be replayed") from exc
    if dict(active_roster_receipt) != reproduced_roster:
        raise ProspectiveBatterOpportunityError("active-roster receipt differs from retained raw bytes")
    coverage = _validate_coverage(
        history_coverage,
        target_date=target_date,
        target_horizon_utc=_stamp(horizon),
        team_id=target_team,
        schedule_raw_by_sha256=schedule_raw_by_sha256,
    )
    captured = set(coverage["captured_prior_game_pks"])
    if not isinstance(history_records, Sequence) or isinstance(history_records, (str, bytes)):
        raise ProspectiveBatterOpportunityError("history_records must be a sequence")
    if not isinstance(history_raw_by_sha256, Mapping):
        raise ProspectiveBatterOpportunityError("history_raw_by_sha256 must be an object")
    normalized: list[dict[str, Any]] = []
    seen_games: set[int] = set()
    consumed_raw: set[str] = set()
    for value in history_records:
        if not isinstance(value, Mapping):
            raise ProspectiveBatterOpportunityError("history record is malformed")
        required = {
            "schema_version", "source_kind", "mlb_game_pk", "official_game_date", "side", "team_id",
            "capture_plan", "capture_plan_sha256", "source_received_at_utc", "source_request_url",
            "source_http_status", "source_content_type",
            "source_payload_sha256", "transport_payload_sha256", "transport_payload_size",
            "transport_payload_retained", "input_surface_sha256", "players",
            "research_only", "betting_authorized", "history_record_sha256",
        }
        if set(value) != required or value.get("schema_version") != HISTORY_SCHEMA_VERSION or value.get("source_kind") != SOURCE_KIND:
            raise ProspectiveBatterOpportunityError("history record schema changed")
        prior_date = _canonical_date(value["official_game_date"], "history official_game_date")
        if prior_date >= target_date:
            raise ProspectiveBatterOpportunityError("same-day or future opportunity history is forbidden")
        if _utc(value["source_received_at_utc"], "history source_received_at_utc") > horizon:
            raise ProspectiveBatterOpportunityError("opportunity history arrived after the target horizon")
        prior_game = _positive_int(value["mlb_game_pk"], "history mlb_game_pk")
        if prior_game in seen_games or prior_game not in captured:
            raise ProspectiveBatterOpportunityError("history game identity is duplicated or outside captured coverage")
        raw_hash = _sha(value["source_payload_sha256"], "history source_payload_sha256")
        raw = history_raw_by_sha256.get(raw_hash)
        if not isinstance(raw, bytes) or hashlib.sha256(raw).hexdigest() != raw_hash:
            raise ProspectiveBatterOpportunityError("history raw payload is missing or differs")
        replayed = parse_opportunity_history(
            response=RawOpportunityResponse(
                raw,
                str(value["source_received_at_utc"]),
                str(value["source_request_url"]),
                int(value["source_http_status"]),
                str(value["source_content_type"]),
                str(value["transport_payload_sha256"]),
                int(value["transport_payload_size"]),
            ),
            expected_game_pk=prior_game,
            expected_official_date=prior_date.isoformat(),
            side=side,
            expected_team_id=target_team,
            capture_plan=value["capture_plan"],
        )
        if replayed != dict(value):
            raise ProspectiveBatterOpportunityError("history record differs from replayed raw bytes")
        seen_games.add(prior_game)
        consumed_raw.add(raw_hash)
        normalized.append(dict(value))
    if seen_games != captured or consumed_raw != set(history_raw_by_sha256):
        raise ProspectiveBatterOpportunityError("history records/raw payloads do not exactly cover captured games")
    roster_ids = sorted(_positive_int(player["player_id"], "active roster player_id") for player in reproduced_roster["players"])
    aggregates: dict[int, dict[str, Any]] = {
        player_id: {
            "player_id": player_id,
            "captured_team_games": len(captured),
            "prior_starts": 0,
            "prior_substitute_appearances": 0,
            "prior_total_plate_appearances": 0,
            "prior_starter_plate_appearances": 0,
            "prior_start_pa_distribution": {},
            "prior_start_slot_counts": {},
            "max_source_game_date": None,
        }
        for player_id in roster_ids
    }
    for record in normalized:
        for player in record["players"]:
            player_id = player["player_id"]
            if player_id not in aggregates:
                continue
            row = aggregates[player_id]
            pa = player["plate_appearances"]
            row["prior_total_plate_appearances"] += pa
            if player["is_starter"]:
                row["prior_starts"] += 1
                row["prior_starter_plate_appearances"] += pa
                pa_key = str(pa)
                slot_key = str(player["lineup_slot"])
                row["prior_start_pa_distribution"][pa_key] = row["prior_start_pa_distribution"].get(pa_key, 0) + 1
                row["prior_start_slot_counts"][slot_key] = row["prior_start_slot_counts"].get(slot_key, 0) + 1
            else:
                row["prior_substitute_appearances"] += 1
            if row["max_source_game_date"] is None or record["official_game_date"] > row["max_source_game_date"]:
                row["max_source_game_date"] = record["official_game_date"]
    features: list[dict[str, Any]] = []
    for player_id in roster_ids:
        row = aggregates[player_id]
        starts = row["prior_starts"]
        row["descriptive_start_share_over_captured_games"] = starts / len(captured) if captured else None
        row["descriptive_mean_pa_given_start"] = row["prior_starter_plate_appearances"] / starts if starts else None
        row["fit_eligible"] = bool(
            coverage["complete_coverage"]
            and coverage["denominator_receipt_bound"]
            and captured
        )
        features.append(row)
    unsigned = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "terminal_state": "captured_complete" if coverage["complete_coverage"] else "captured_with_terminal_history_missingness",
        "research_only": True,
        "betting_authorized": False,
        "production_probability_consumption_authorized": False,
        "official_game_date": target_date.isoformat(),
        "mlb_game_pk": game_pk,
        "side": side,
        "team_id": target_team,
        "target_horizon_utc": _stamp(horizon),
        "assembled_at_utc": _stamp(assembled),
        "active_roster_receipt_sha256": sha256_value(reproduced_roster),
        "active_roster_raw_sha256": hashlib.sha256(active_roster_raw).hexdigest(),
        "history_coverage": coverage,
        "history_record_sha256s": sorted(value["history_record_sha256"] for value in normalized),
        "history_raw_sha256s": sorted(consumed_raw),
        "schedule_raw_sha256s": sorted(schedule_raw_by_sha256),
        "features": features,
        "prohibited_interpretations": [
            "not a projected lineup",
            "not a target start probability",
            "not a PA prediction",
            "no target-game final lineup or outcome",
            "no implicit zero for missing prior games",
            "no downstream player-prop probability consumption",
        ],
    }
    return {**unsigned, "snapshot_sha256": sha256_value(unsigned)}


def validate_snapshot(record: Mapping[str, Any]) -> None:
    """Validate the immutable snapshot surface and internal probability-free invariants."""

    if not isinstance(record, Mapping) or record.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
        raise ProspectiveBatterOpportunityError("snapshot schema changed")
    unsigned = dict(record)
    supplied = _sha(unsigned.pop("snapshot_sha256", None), "snapshot_sha256")
    if supplied != sha256_value(unsigned):
        raise ProspectiveBatterOpportunityError("snapshot content differs from its hash")
    if record.get("research_only") is not True or record.get("betting_authorized") is not False or record.get("production_probability_consumption_authorized") is not False:
        raise ProspectiveBatterOpportunityError("snapshot authorization boundary changed")
    coverage = record.get("history_coverage")
    if not isinstance(coverage, Mapping):
        raise ProspectiveBatterOpportunityError("snapshot history coverage is missing")
    complete = coverage.get("complete_coverage") is True
    expected_state = "captured_complete" if complete else "captured_with_terminal_history_missingness"
    if record.get("terminal_state") != expected_state:
        raise ProspectiveBatterOpportunityError("snapshot terminal state contradicts coverage")
    features = record.get("features")
    if not isinstance(features, list) or len(features) < 9:
        raise ProspectiveBatterOpportunityError("snapshot features are missing")
    seen: set[int] = set()
    for row in features:
        if not isinstance(row, Mapping):
            raise ProspectiveBatterOpportunityError("snapshot feature row is malformed")
        player_id = _positive_int(row.get("player_id"), "snapshot player_id")
        if player_id in seen:
            raise ProspectiveBatterOpportunityError("snapshot player identity is duplicated")
        seen.add(player_id)
        for key in ("descriptive_start_share_over_captured_games", "descriptive_mean_pa_given_start"):
            value = row.get(key)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) < 0):
                raise ProspectiveBatterOpportunityError(f"snapshot {key} is invalid")
        if row.get("fit_eligible") is not bool(
            complete
            and coverage.get("denominator_receipt_bound") is True
            and coverage.get("captured_game_count", 0)
        ):
            raise ProspectiveBatterOpportunityError("snapshot fit eligibility contradicts coverage")
