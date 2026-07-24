"""Strict prior-only historical features for the projected-lineup research lane.

Completed lineups are valid *past* observations.  They are never evidence that a
player was available before a later target game.  Consequently this module needs
an independently receipted active roster for the target, emits only counts and
conditional historical distributions, and deliberately does not emit a fitted
start probability or a fallback lineup.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
from typing import Any, Mapping, Sequence

from src.evaluation.projected_lineup_contract import sha256_value


class HistoricalLineupFeatureError(ValueError):
    """Historical lineup input cannot truthfully produce a T-minus-4 feature record."""


SCHEMA_VERSION = "projected-lineup-history-feature-store-v1"
_ROSTER_RECEIPT_KEYS = {
    "source_kind", "source_record_id", "received_at_utc", "payload_sha256", "input_surface_sha256", "players"
}
_LINEUP_KEYS = {"official_game_date", "mlb_game_pk", "team_id", "player_id", "slot"}


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise HistoricalLineupFeatureError(f"{label} must be a positive integer")
    return value


def _date(value: Any, label: str) -> date:
    if not isinstance(value, str):
        raise HistoricalLineupFeatureError(f"{label} must be an ISO date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise HistoricalLineupFeatureError(f"{label} must be an ISO date") from exc
    if parsed.isoformat() != value:
        raise HistoricalLineupFeatureError(f"{label} must be canonical ISO date")
    if value.startswith("2026-05-"):
        raise HistoricalLineupFeatureError("May 2026 is sealed from historical-lineup features")
    return parsed


def _roster_player_ids(receipt: Mapping[str, Any]) -> list[int]:
    if not isinstance(receipt, Mapping) or set(receipt) != _ROSTER_RECEIPT_KEYS:
        raise HistoricalLineupFeatureError("active roster receipt schema changed")
    if receipt["source_kind"] != "official_mlb_active_roster_t4":
        raise HistoricalLineupFeatureError("active roster is not a receipted official T-minus-4 roster")
    if not isinstance(receipt["players"], list) or len(receipt["players"]) < 9:
        raise HistoricalLineupFeatureError("active roster receipt must contain at least nine players")
    ids: list[int] = []
    for player in receipt["players"]:
        if not isinstance(player, Mapping) or set(player) != {"player_id", "position_code", "position_type"}:
            raise HistoricalLineupFeatureError("active roster player schema changed")
        ids.append(_positive_int(player["player_id"], "active roster player_id"))
    if len(set(ids)) != len(ids):
        raise HistoricalLineupFeatureError("active roster contains duplicate player identities")
    return sorted(ids)


def _normalize_completed_lineups(value: Sequence[Mapping[str, Any]], *, target_date: date, team_id: int) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise HistoricalLineupFeatureError("completed lineups must be a sequence")
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    game_slots: dict[int, set[int]] = defaultdict(set)
    game_players: dict[int, set[int]] = defaultdict(set)
    for raw in value:
        if not isinstance(raw, Mapping) or set(raw) != _LINEUP_KEYS:
            raise HistoricalLineupFeatureError("completed lineup record schema changed")
        source_date = _date(raw["official_game_date"], "completed lineup official_game_date")
        if source_date >= target_date:
            raise HistoricalLineupFeatureError("completed lineup is same-day or future relative to the target")
        if _positive_int(raw["team_id"], "completed lineup team_id") != team_id:
            raise HistoricalLineupFeatureError("completed lineup team identity differs from target team")
        game_pk = _positive_int(raw["mlb_game_pk"], "completed lineup mlb_game_pk")
        player_id = _positive_int(raw["player_id"], "completed lineup player_id")
        slot = _positive_int(raw["slot"], "completed lineup slot")
        if slot > 9:
            raise HistoricalLineupFeatureError("completed lineup slot must be one through nine")
        identity = (game_pk, player_id)
        if identity in seen:
            raise HistoricalLineupFeatureError("completed lineup has duplicate game/player identity")
        if slot in game_slots[game_pk] or player_id in game_players[game_pk]:
            raise HistoricalLineupFeatureError("completed lineup violates unique player or slot identity")
        seen.add(identity)
        game_slots[game_pk].add(slot)
        game_players[game_pk].add(player_id)
        normalized.append({
            "official_game_date": source_date.isoformat(), "mlb_game_pk": game_pk,
            "team_id": team_id, "player_id": player_id, "slot": slot,
        })
    for game_pk, slots in game_slots.items():
        if slots != set(range(1, 10)):
            raise HistoricalLineupFeatureError(f"completed lineup game {game_pk} is incomplete")
    return sorted(normalized, key=lambda item: (item["official_game_date"], item["mlb_game_pk"], item["slot"]))


def build_feature_store(*, official_game_date: str, team_id: int, active_roster_receipt: Mapping[str, Any], completed_lineups: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Build a hash-bound, non-probabilistic feature store for one target team.

    ``completed_lineups`` must consist solely of completed lineups strictly
    before the target date.  The returned start share is a descriptive prior
    count divided by *completed team games*, never a claim about target-player
    eligibility; only the T-minus-4 roster receipt establishes eligibility.
    """
    target_date = _date(official_game_date, "official_game_date")
    target_team = _positive_int(team_id, "team_id")
    roster_ids = _roster_player_ids(active_roster_receipt)
    history = _normalize_completed_lineups(completed_lineups, target_date=target_date, team_id=target_team)
    game_dates = {row["mlb_game_pk"]: row["official_game_date"] for row in history}
    starts = Counter(row["player_id"] for row in history)
    slot_counts: dict[int, Counter[int]] = defaultdict(Counter)
    last_source_date: dict[int, str] = {}
    for row in history:
        player_id = row["player_id"]
        slot_counts[player_id][row["slot"]] += 1
        last_source_date[player_id] = row["official_game_date"]
    completed_team_games = len(game_dates)
    rows: list[dict[str, Any]] = []
    for player_id in roster_ids:
        player_starts = starts[player_id]
        conditional_slots = {
            str(slot): count / player_starts for slot, count in sorted(slot_counts[player_id].items())
        } if player_starts else {}
        rows.append({
            "player_id": player_id,
            "prior_completed_team_games": completed_team_games,
            "prior_completed_starts": player_starts,
            "prior_completed_start_share": (player_starts / completed_team_games) if completed_team_games else None,
            "prior_slot_distribution_given_start": conditional_slots,
            "max_source_game_date": last_source_date.get(player_id),
        })
    unsigned = {
        "schema_version": SCHEMA_VERSION,
        "research_only": True,
        "betting_authorized": False,
        "official_game_date": target_date.isoformat(),
        "team_id": target_team,
        "active_roster_receipt": {
            key: active_roster_receipt[key] for key in _ROSTER_RECEIPT_KEYS if key != "players"
        },
        "history_input_sha256": sha256_value(history),
        "history_game_count": completed_team_games,
        "features": rows,
        "prohibited_interpretations": [
            "no target-game final lineup", "no historical active-roster reconstruction",
            "no league-average fallback", "no supplied or fitted start probability",
            "no downstream player-prop probability consumption",
        ],
    }
    return {**unsigned, "feature_store_sha256": sha256_value(unsigned)}
