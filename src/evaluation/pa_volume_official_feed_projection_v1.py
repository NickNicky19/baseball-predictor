"""Strict 2023 original-starter PA projection from retained official MLB feeds.

This module is deliberately offline.  It converts an independently bound
schedule index and a complete mapping of retained final-game response bytes
into the only row schema accepted by :mod:`pa_volume_source_truth_v2`.
Target-game predictions, prices, and prospective evidence are outside scope.
"""
from __future__ import annotations

from datetime import date
import hashlib
import json
import re
from typing import Any, Mapping


SCHEMA_VERSION = "pa-volume-official-feed-projection-v1"
ROW_SCHEMA_VERSION = "pa-volume-official-starter-projection-v2"
SIDES = ("away", "home")
SLOTS = tuple(range(1, 10))
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


class OfficialFeedProjectionError(ValueError):
    """Retained official evidence is incomplete or semantically invalid."""


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        + "\n"
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise OfficialFeedProjectionError(f"{label} must be a positive integer")
    return value


def _nonnegative_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise OfficialFeedProjectionError(f"{label} must be a non-negative integer")
    return value


def _date_2023(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise OfficialFeedProjectionError(f"{label} must be a canonical 2023 date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise OfficialFeedProjectionError(
            f"{label} must be a canonical 2023 date"
        ) from exc
    if parsed.isoformat() != value or parsed.year != 2023:
        raise OfficialFeedProjectionError(f"{label} must be a canonical 2023 date")
    return value


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise OfficialFeedProjectionError(f"{label} must be a lowercase SHA-256")
    return value


def _schedule_games(value: Mapping[str, Any]) -> list[dict[str, Any]]:
    expected = {"schema_version", "season", "fields", "games"}
    fields = [
        "game_pk",
        "official_date",
        "game_type",
        "away_team_id",
        "home_team_id",
    ]
    if not isinstance(value, Mapping) or set(value) != expected:
        raise OfficialFeedProjectionError("schedule index schema changed")
    if value["schema_version"] != "pa-volume-2023-schedule-index-v1":
        raise OfficialFeedProjectionError("schedule index identity changed")
    if value["season"] != 2023 or value["fields"] != fields:
        raise OfficialFeedProjectionError("schedule index scope changed")
    raw_games = value["games"]
    if not isinstance(raw_games, list) or not raw_games:
        raise OfficialFeedProjectionError("schedule index is empty")
    games: list[dict[str, Any]] = []
    seen: set[int] = set()
    for index, raw in enumerate(raw_games):
        if not isinstance(raw, Mapping) or set(raw) != set(fields):
            raise OfficialFeedProjectionError(
                f"schedule game {index} does not match the positive schema"
            )
        game_pk = _positive_int(raw["game_pk"], "schedule game_pk")
        if game_pk in seen:
            raise OfficialFeedProjectionError("schedule game_pk is duplicated")
        seen.add(game_pk)
        official_date = _date_2023(raw["official_date"], "schedule official_date")
        if raw["game_type"] != "R":
            raise OfficialFeedProjectionError("schedule contains a non-regular game")
        away_team_id = _positive_int(raw["away_team_id"], "away_team_id")
        home_team_id = _positive_int(raw["home_team_id"], "home_team_id")
        if away_team_id == home_team_id:
            raise OfficialFeedProjectionError("schedule team identities collide")
        games.append(
            {
                "game_pk": game_pk,
                "official_date": official_date,
                "game_type": "R",
                "away_team_id": away_team_id,
                "home_team_id": home_team_id,
            }
        )
    games.sort(key=lambda row: (row["official_date"], row["game_pk"]))
    if games != raw_games:
        raise OfficialFeedProjectionError("schedule games are not canonically sorted")
    return games


def _document(raw: bytes, game_pk: int) -> Mapping[str, Any]:
    if not isinstance(raw, bytes) or not raw:
        raise OfficialFeedProjectionError(f"game {game_pk} feed bytes are missing")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OfficialFeedProjectionError(
            f"game {game_pk} feed is not valid JSON"
        ) from exc
    if not isinstance(value, Mapping):
        raise OfficialFeedProjectionError(f"game {game_pk} feed root is malformed")
    return value


def _feed_rows(feed: Mapping[str, Any], game: Mapping[str, Any]) -> list[dict[str, Any]]:
    game_pk = game["game_pk"]
    if _positive_int(feed.get("gamePk"), "feed gamePk") != game_pk:
        raise OfficialFeedProjectionError(f"game {game_pk} feed identity differs")
    game_data = feed.get("gameData")
    if not isinstance(game_data, Mapping):
        raise OfficialFeedProjectionError(f"game {game_pk} lacks gameData")
    observed_date = _date_2023(
        ((game_data.get("datetime") or {}).get("officialDate")),
        "feed officialDate",
    )
    if observed_date != game["official_date"]:
        raise OfficialFeedProjectionError(f"game {game_pk} official date differs")
    if (game_data.get("game") or {}).get("type") != "R":
        raise OfficialFeedProjectionError(f"game {game_pk} is not regular season")
    status = game_data.get("status") or {}
    if status.get("codedGameState") != "F" or status.get("abstractGameState") != "Final":
        raise OfficialFeedProjectionError(f"game {game_pk} is not coded final")
    game_teams = game_data.get("teams") or {}
    expected_team = {
        "away": game["away_team_id"],
        "home": game["home_team_id"],
    }
    for side in SIDES:
        if _positive_int((game_teams.get(side) or {}).get("id"), f"{side} team id") != expected_team[side]:
            raise OfficialFeedProjectionError(f"game {game_pk} {side} team differs")
    box_teams = (((feed.get("liveData") or {}).get("boxscore") or {}).get("teams"))
    if not isinstance(box_teams, Mapping):
        raise OfficialFeedProjectionError(f"game {game_pk} lacks boxscore teams")
    rows: list[dict[str, Any]] = []
    for side in SIDES:
        players = ((box_teams.get(side) or {}).get("players"))
        if not isinstance(players, Mapping):
            raise OfficialFeedProjectionError(
                f"game {game_pk} {side} player collection is malformed"
            )
        starters: dict[int, dict[str, Any]] = {}
        for player_key, player in players.items():
            if not isinstance(player_key, str) or not isinstance(player, Mapping):
                raise OfficialFeedProjectionError(
                    f"game {game_pk} {side} player entry is malformed"
                )
            raw_order = player.get("battingOrder")
            if raw_order is None:
                continue
            token = str(raw_order).strip()
            if not token.isascii() or not token.isdigit() or token != str(int(token)):
                raise OfficialFeedProjectionError(
                    f"game {game_pk} has noncanonical battingOrder"
                )
            order = int(token)
            slot, sequence = divmod(order, 100)
            if slot not in SLOTS:
                raise OfficialFeedProjectionError(
                    f"game {game_pk} has invalid batting slot"
                )
            person = player.get("person") or {}
            player_id = _positive_int(person.get("id"), "feed player id")
            if player_key != f"ID{player_id}":
                raise OfficialFeedProjectionError(
                    f"game {game_pk} player key and person identity differ"
                )
            if sequence != 0:
                continue
            if slot in starters:
                raise OfficialFeedProjectionError(
                    f"game {game_pk} {side} has duplicate original slot {slot}"
                )
            batting = ((player.get("stats") or {}).get("batting"))
            if not isinstance(batting, Mapping):
                raise OfficialFeedProjectionError(
                    f"game {game_pk} starter lacks batting statistics"
                )
            starters[slot] = {
                "game_pk": game_pk,
                "official_date": observed_date,
                "side": side,
                "team_id": expected_team[side],
                "player_id": player_id,
                "lineup_slot": slot,
                "out_pa": _nonnegative_int(
                    batting.get("plateAppearances"), "plateAppearances"
                ),
            }
        if set(starters) != set(SLOTS):
            raise OfficialFeedProjectionError(
                f"game {game_pk} {side} original lineup is incomplete"
            )
        rows.extend(starters[slot] for slot in SLOTS)
    return rows


def build_official_feed_projection(
    *,
    schedule_index: Mapping[str, Any],
    feeds_by_game_pk: Mapping[int, bytes],
    schedule_capture_manifest_sha256: str,
    feed_capture_manifest_sha256: str,
    parser_source_sha256: str,
) -> dict[str, Any]:
    """Return a deterministic positive projection and its source bindings."""
    bindings = {
        "schedule_capture_manifest_sha256": _sha(
            schedule_capture_manifest_sha256, "schedule capture manifest"
        ),
        "feed_capture_manifest_sha256": _sha(
            feed_capture_manifest_sha256, "feed capture manifest"
        ),
        "parser_source_sha256": _sha(parser_source_sha256, "parser source"),
    }
    games = _schedule_games(schedule_index)
    if not isinstance(feeds_by_game_pk, Mapping):
        raise OfficialFeedProjectionError("feed mapping is malformed")
    expected = {game["game_pk"] for game in games}
    if set(feeds_by_game_pk) != expected:
        raise OfficialFeedProjectionError(
            "retained feeds do not exactly cover the schedule index"
        )
    rows: list[dict[str, Any]] = []
    feed_hashes: list[dict[str, Any]] = []
    for game in games:
        raw = feeds_by_game_pk[game["game_pk"]]
        rows.extend(_feed_rows(_document(raw, game["game_pk"]), game))
        feed_hashes.append(
            {"game_pk": game["game_pk"], "sha256": sha256_bytes(raw)}
        )
    projection = {
        "schema_version": ROW_SCHEMA_VERSION,
        "season": 2023,
        "fields": [
            "game_pk",
            "official_date",
            "side",
            "team_id",
            "player_id",
            "lineup_slot",
            "out_pa",
        ],
        "rows": rows,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "COMPLETE_OFFICIAL_2023_FINAL_FEED_PROJECTION",
        "season": 2023,
        "research_only": True,
        "betting_authorized": False,
        "prospective_evidence_claimed": False,
        "protected_data": {
            "may_2026_accessed": False,
            "selection_2024_accessed": False,
            "spent_hr_confirmation_2025_accessed": False,
            "prices_accessed": False,
            "prospective_evidence_accessed": False,
            "prospective_backfill_performed": False,
        },
        "bindings": bindings,
        "game_count": len(games),
        "row_count": len(rows),
        "feed_hashes": feed_hashes,
        "projection_sha256": sha256_bytes(canonical_json_bytes(projection)),
        "projection": projection,
    }
