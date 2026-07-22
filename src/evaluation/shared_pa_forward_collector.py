"""Source-truth constructors for a separate future shared-PA collector.

Network transport and persistence are injected elsewhere.  These functions
accept exact raw MLB response bytes, preserve their receipt times and hashes,
and either build a complete projected-lineup batter-only snapshot or fail.
No completed-game, price, settlement, or opposing-pitcher field is parsed.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from src.evaluation.shadow_capture_plan import CaptureTarget, ShadowCapturePlan
from src.evaluation.shared_pa_forward_evidence import (
    CONTROL_ID,
    PA_OUTCOMES,
    SCHEMA_VERSION,
    SharedPAForwardEvidenceError,
    derive_market_distributions,
    empirical_bayes_pa_probability,
    pa_distribution_sha256,
    sha256_value,
    snapshot_sha256,
    validate_player_snapshot,
)


class SharedPAForwardCollectorError(ValueError):
    """A raw pregame response cannot truthfully create shared PA evidence."""


class SharedPAForwardUnsafePayloadError(SharedPAForwardCollectorError):
    """A response contains fields outside the locked pregame input surface."""


@dataclass(frozen=True)
class RawPregameResponse:
    body: bytes
    received_at_utc: str

    def __post_init__(self) -> None:
        if not isinstance(self.body, bytes) or not self.body:
            raise SharedPAForwardCollectorError("raw response must be non-empty bytes")
        try:
            parsed = datetime.fromisoformat(str(self.received_at_utc).replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise SharedPAForwardCollectorError("raw response receipt is not ISO-8601") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise SharedPAForwardCollectorError("raw response receipt must include a timezone")
        object.__setattr__(
            self,
            "received_at_utc",
            parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"),
        )

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.body).hexdigest()

    def json_object(self, label: str) -> Mapping[str, Any]:
        try:
            value = json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SharedPAForwardCollectorError(f"{label} response is not UTF-8 JSON") from exc
        if not isinstance(value, Mapping):
            raise SharedPAForwardCollectorError(f"{label} response must be an object")
        return value


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise SharedPAForwardCollectorError(f"{label} must be a positive integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise SharedPAForwardCollectorError(f"{label} must be a positive integer") from exc
    if parsed <= 0 or (isinstance(value, float) and value != parsed):
        raise SharedPAForwardCollectorError(f"{label} must be a positive integer")
    return parsed


def _nonnegative_int(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise SharedPAForwardCollectorError(f"{label} must be a non-negative integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise SharedPAForwardCollectorError(f"{label} must be a non-negative integer") from exc
    if parsed < 0 or (isinstance(value, float) and value != parsed):
        raise SharedPAForwardCollectorError(f"{label} must be a non-negative integer")
    return parsed


def _target_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _exact_keys(value: Any, allowed: set[str], label: str, *, required: set[str] | None = None) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SharedPAForwardUnsafePayloadError(f"{label} must be an object")
    keys = set(value)
    if not keys.issubset(allowed) or (required is not None and not required.issubset(keys)):
        raise SharedPAForwardUnsafePayloadError(f"{label} contains an unapproved or missing field")
    return value


def validate_schedule_input_surface(response: RawPregameResponse) -> Mapping[str, Any]:
    """Prove the retained schedule bytes contain only predeclared input fields."""
    payload = _exact_keys(response.json_object("schedule lineup"), {"dates"}, "schedule root", required={"dates"})
    dates = payload["dates"]
    if not isinstance(dates, list):
        raise SharedPAForwardUnsafePayloadError("schedule dates must be a list")
    for date_row in dates:
        date_object = _exact_keys(date_row, {"games"}, "schedule date", required={"games"})
        games = date_object["games"]
        if not isinstance(games, list):
            raise SharedPAForwardUnsafePayloadError("schedule games must be a list")
        for game_row in games:
            game = _exact_keys(
                game_row,
                {"gamePk", "officialDate", "gameDate", "teams", "lineups"},
                "schedule game",
                required={"gamePk", "officialDate", "gameDate", "teams"},
            )
            teams = _exact_keys(game["teams"], {"home", "away"}, "schedule teams", required={"home", "away"})
            for side in ("home", "away"):
                side_row = _exact_keys(teams[side], {"team"}, f"schedule {side}", required={"team"})
                _exact_keys(
                    side_row["team"], {"id", "name"}, f"schedule {side} team", required={"id", "name"}
                )
            if "lineups" in game:
                lineups = _exact_keys(
                    game["lineups"], {"homePlayers", "awayPlayers"}, "schedule lineups"
                )
                for key in ("homePlayers", "awayPlayers"):
                    if key not in lineups:
                        continue
                    players = lineups[key]
                    if not isinstance(players, list):
                        raise SharedPAForwardUnsafePayloadError(f"schedule {key} must be a list")
                    for player in players:
                        _exact_keys(player, {"id"}, f"schedule {key} player", required={"id"})
    return payload


def validate_stats_input_surface(response: RawPregameResponse) -> Mapping[str, Any]:
    """Prove retained season-stat bytes contain only the locked aggregate counts."""
    payload = _exact_keys(response.json_object("player hitting stats"), {"people"}, "stats root", required={"people"})
    people = payload["people"]
    if not isinstance(people, list):
        raise SharedPAForwardUnsafePayloadError("stats people must be a list")
    approved_stat_fields = {
        "plateAppearances", "atBats", "hits", "doubles", "triples",
        "homeRuns", "baseOnBalls", "strikeOuts",
    }
    for person_row in people:
        person = _exact_keys(person_row, {"id", "stats"}, "stats person", required={"id"})
        if "stats" not in person:
            continue
        blocks = person["stats"]
        if not isinstance(blocks, list):
            raise SharedPAForwardUnsafePayloadError("stats blocks must be a list")
        for block_row in blocks:
            block = _exact_keys(
                block_row, {"group", "type", "splits"}, "stats block", required={"group", "type", "splits"}
            )
            _exact_keys(block["group"], {"displayName"}, "stats group", required={"displayName"})
            _exact_keys(block["type"], {"displayName"}, "stats type", required={"displayName"})
            splits = block["splits"]
            if not isinstance(splits, list):
                raise SharedPAForwardUnsafePayloadError("stats splits must be a list")
            for split_row in splits:
                split = _exact_keys(split_row, {"stat"}, "stats split", required={"stat"})
                _exact_keys(split["stat"], approved_stat_fields, "stats aggregate")
    return payload


def projected_lineups_from_schedule(
    *, response: RawPregameResponse, plan: ShadowCapturePlan, target: CaptureTarget
) -> dict[str, Any]:
    """Extract exact nine-player projected orders and hard team identity."""
    if target.target_id not in {item.target_id for item in plan.targets}:
        raise SharedPAForwardCollectorError("target is not in the immutable plan")
    if target.official_game_date.startswith("2026-05-"):
        raise SharedPAForwardCollectorError("May 2026 schedule input is sealed")
    if _target_time(response.received_at_utc) > _target_time(target.entry_target_at_utc):
        raise SharedPAForwardCollectorError("lineup response arrived after T-minus-4")
    payload = validate_schedule_input_surface(response)
    dates = payload.get("dates")
    if not isinstance(dates, list) or len(dates) != 1 or not isinstance(dates[0], Mapping):
        raise SharedPAForwardCollectorError("schedule response must contain exactly one date")
    games = dates[0].get("games")
    if not isinstance(games, list):
        raise SharedPAForwardCollectorError("schedule response lacks a games list")
    matches = [row for row in games if isinstance(row, Mapping) and row.get("gamePk") == target.mlb_game_pk]
    if len(matches) != 1:
        raise SharedPAForwardCollectorError("schedule response does not uniquely identify the target game")
    game = matches[0]
    try:
        source_start = _target_time(str(game.get("gameDate")))
    except (TypeError, ValueError) as exc:
        raise SharedPAForwardCollectorError("schedule game start is not a timezone-aware timestamp") from exc
    if game.get("officialDate") != target.official_game_date or source_start != _target_time(target.official_start_time_utc):
        raise SharedPAForwardCollectorError("schedule game date/time differs from the immutable target")
    try:
        home_team_id = _positive_int(game["teams"]["home"]["team"]["id"], "home team ID")
        away_team_id = _positive_int(game["teams"]["away"]["team"]["id"], "away team ID")
    except (KeyError, TypeError) as exc:
        raise SharedPAForwardCollectorError("schedule game lacks hard team identity") from exc
    lineups = game.get("lineups")
    if not isinstance(lineups, Mapping):
        return {
            "game": game,
            "home_team_id": home_team_id,
            "away_team_id": away_team_id,
            "home": None,
            "away": None,
            "status": "lineup_unavailable",
        }
    orders: dict[str, list[int] | None] = {}
    for side, key in (("home", "homePlayers"), ("away", "awayPlayers")):
        raw_players = lineups.get(key)
        if raw_players in (None, []):
            orders[side] = None
            continue
        if not isinstance(raw_players, list) or len(raw_players) != 9:
            raise SharedPAForwardCollectorError(f"{side} projected lineup is partial or malformed")
        order: list[int] = []
        for entry in raw_players:
            if not isinstance(entry, Mapping):
                raise SharedPAForwardCollectorError(f"{side} lineup player is not an object")
            order.append(_positive_int(entry.get("id"), f"{side} lineup player ID"))
        if len(set(order)) != 9:
            raise SharedPAForwardCollectorError(f"{side} projected lineup contains duplicate player IDs")
        orders[side] = order
    return {
        "game": game,
        "home_team_id": home_team_id,
        "away_team_id": away_team_id,
        "home": orders["home"],
        "away": orders["away"],
        "status": "captured_complete" if all(orders.values()) else "lineup_unavailable",
    }


def pa_counts_from_hitting_stats(*, response: RawPregameResponse, player_id: int) -> dict[str, int]:
    """Parse one official season-to-receipt hitting snapshot into terminal PA counts."""
    payload = validate_stats_input_surface(response)
    people = payload.get("people")
    if not isinstance(people, list) or len(people) != 1 or not isinstance(people[0], Mapping):
        raise SharedPAForwardCollectorError("player stats response must contain exactly one person")
    person = people[0]
    if _positive_int(person.get("id"), "stats player ID") != _positive_int(player_id, "player_id"):
        raise SharedPAForwardCollectorError("stats response belongs to a different player")
    blocks = person.get("stats") or []
    if not isinstance(blocks, list):
        raise SharedPAForwardCollectorError("player stats blocks are malformed")
    candidates: list[Mapping[str, Any]] = []
    for block in blocks:
        if not isinstance(block, Mapping):
            raise SharedPAForwardCollectorError("player stats block is malformed")
        group = block.get("group")
        stats_type = block.get("type")
        if not isinstance(group, Mapping) or not isinstance(stats_type, Mapping):
            continue
        if group.get("displayName") != "hitting" or stats_type.get("displayName") != "season":
            continue
        splits = block.get("splits") or []
        if not isinstance(splits, list):
            raise SharedPAForwardCollectorError("player season splits are malformed")
        for split in splits:
            if isinstance(split, Mapping) and isinstance(split.get("stat"), Mapping):
                candidates.append(split["stat"])
    if not candidates:
        return {name: 0 for name in PA_OUTCOMES}
    if len(candidates) != 1:
        raise SharedPAForwardCollectorError("player stats response has ambiguous season hitting totals")
    stat = candidates[0]
    required = {
        "plateAppearances", "atBats", "hits", "doubles", "triples",
        "homeRuns", "baseOnBalls", "strikeOuts",
    }
    if not required.issubset(stat):
        raise SharedPAForwardCollectorError("player stats response is missing terminal count fields")
    pa = _nonnegative_int(stat["plateAppearances"], "plateAppearances")
    ab = _nonnegative_int(stat["atBats"], "atBats")
    hits = _nonnegative_int(stat["hits"], "hits")
    doubles = _nonnegative_int(stat["doubles"], "doubles")
    triples = _nonnegative_int(stat["triples"], "triples")
    home_runs = _nonnegative_int(stat["homeRuns"], "homeRuns")
    walks = _nonnegative_int(stat["baseOnBalls"], "baseOnBalls")
    strikeouts = _nonnegative_int(stat["strikeOuts"], "strikeOuts")
    singles = hits - doubles - triples - home_runs
    bip_out = ab - hits - strikeouts
    other_non_ab = pa - ab - walks
    counts = {
        "strikeout": strikeouts,
        "walk": walks,
        "single": singles,
        "double": doubles,
        "triple": triples,
        "home_run": home_runs,
        "bip_out": bip_out,
        "other_non_ab": other_non_ab,
    }
    if any(value < 0 for value in counts.values()) or sum(counts.values()) != pa:
        raise SharedPAForwardCollectorError("official hitting counts do not form a truthful PA partition")
    return counts


def build_projected_player_snapshot(
    *,
    plan: ShadowCapturePlan,
    target: CaptureTarget,
    side: str,
    source_slot: int,
    player_id: int,
    home_team_id: int,
    away_team_id: int,
    lineup_response: RawPregameResponse,
    stats_response: RawPregameResponse,
    loaded_contract: Mapping[str, Any],
    collector_instance_id: str,
    collector_code_sha256: str,
    runtime_manifest_sha256: str,
) -> dict[str, Any]:
    """Build and self-validate a projected-lineup, pooled-volume player record."""
    if side not in {"home", "away"}:
        raise SharedPAForwardCollectorError("side must be home or away")
    horizon = _target_time(target.entry_target_at_utc)
    if any(_target_time(value.received_at_utc) > horizon for value in (lineup_response, stats_response)):
        raise SharedPAForwardCollectorError("pregame source arrived after T-minus-4")
    counts = pa_counts_from_hitting_stats(response=stats_response, player_id=player_id)
    control = loaded_contract.get("control")
    prior = loaded_contract.get("league_prior_probability")
    artifact = loaded_contract.get("pa_volume_artifact")
    if not isinstance(control, Mapping) or not isinstance(prior, Mapping) or artifact is None:
        raise SharedPAForwardCollectorError("loaded contract bundle is incomplete")
    per_pa = empirical_bayes_pa_probability(
        counts=counts,
        league_prior=prior,
        prior_strength_pa=float(control["prior_strength_pa"]),
    )
    pooled = artifact.pooled
    support = sorted(int(value) for value in pooled)
    mass = [float(pooled[value]) for value in support]
    receipt = max(
        _target_time(lineup_response.received_at_utc),
        _target_time(stats_response.received_at_utc),
    )
    feature_payload = {
        "counts": counts,
        "league_prior_probability": dict(prior),
        "prior_strength_pa": 200.0,
        "raw_stats_payload_sha256": stats_response.sha256,
        "stats_receipt_utc": stats_response.received_at_utc,
    }
    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "terminal_state": "captured_complete",
        "research_only": True,
        "betting_authorized": False,
        "promotion_eligible": False,
        "collector_instance_id": str(collector_instance_id),
        "monotonic_receipt_sequence": int(source_slot),
        "receipt_utc": _stamp(receipt),
        "source_observation_utc": _stamp(receipt),
        "collector_code_sha256": str(collector_code_sha256),
        "runtime_manifest_sha256": str(runtime_manifest_sha256),
        "plan_sha256": plan.plan_sha256,
        "target_id": target.target_id,
        "mlb_game_pk": target.mlb_game_pk,
        "official_game_date": target.official_game_date,
        "official_start_utc": target.official_start_time_utc,
        "target_horizon_utc": target.entry_target_at_utc,
        "side": side,
        "home_team_id": int(home_team_id),
        "away_team_id": int(away_team_id),
        "game_identity_sha256": sha256_value({
            "mlb_game_pk": target.mlb_game_pk,
            "official_game_date": target.official_game_date,
            "official_start_utc": target.official_start_time_utc,
            "home_team_id": int(home_team_id),
            "away_team_id": int(away_team_id),
        }),
        "lineup_state": "projected",
        "source_lineup_slot": int(source_slot),
        "effective_lineup_slot": None,
        "lineup_receipt_utc": lineup_response.received_at_utc,
        "raw_lineup_payload_sha256": lineup_response.sha256,
        "player_id": int(player_id),
        "player_identity_sha256": sha256_value({
            "mlb_game_pk": target.mlb_game_pk, "side": side, "player_id": int(player_id)
        }),
        "hard_player_key": f"{target.mlb_game_pk}:{side}:{int(player_id)}:shared_pa",
        "stats_receipt_utc": stats_response.received_at_utc,
        "raw_stats_payload_sha256": stats_response.sha256,
        "stats_counts": counts,
        "stats_pa": sum(counts.values()),
        "stats_season": int(target.official_game_date[:4]),
        "control_id": CONTROL_ID,
        "control_config_sha256": str(loaded_contract["control_sha256"]),
        "prior_strength_pa": 200.0,
        "league_prior_probability": dict(prior),
        "per_pa_probability": per_pa,
        "feature_snapshot_sha256": sha256_value(feature_payload),
        "rate_fallback_labels": ["league_prior_only"] if sum(counts.values()) == 0 else [],
        "pa_volume_candidate_id": artifact.candidate_id,
        "pa_volume_artifact_sha256": str(loaded_contract["pa_volume_sha256"]),
        "pa_distribution_scope": "pooled_projected_lineup",
        "pa_support": support,
        "pa_mass": mass,
        "pa_distribution_sha256": pa_distribution_sha256(support=support, mass=mass),
        "expected_pa": sum(value * weight for value, weight in zip(support, mass)),
        "market_distributions": derive_market_distributions(
            per_pa_probability=per_pa, support=support, mass=mass
        ),
        "prediction_method": "exact_iid_pa_mixture_v1",
        "pitcher_block_status": "excluded_batter_only",
        "policy_status": "none_research_probability_only",
        "snapshot_sha256": "",
    }
    record["snapshot_sha256"] = snapshot_sha256(record)
    try:
        validate_player_snapshot(record)
    except SharedPAForwardEvidenceError as exc:
        raise SharedPAForwardCollectorError(str(exc)) from exc
    return record
