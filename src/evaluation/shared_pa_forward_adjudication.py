"""Outcome-blind contracts for later shared-PA prospective adjudication.

This module deliberately has no HTTP client.  It validates a hash-bound,
normalized projection from a retained official final boxscore only after the
separately locked opening boundary has passed.
The build and mutation tests use synthetic payloads and never read a 2026
outcome artifact.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping


class SharedPAAdjudicationError(ValueError):
    """The locked prospective adjudication contract or outcome is unsafe."""


CONTRACT_SCHEMA = "shared-pa-forward-adjudication-contract-v1"
OUTCOME_SCHEMA = "shared-pa-official-player-outcome-v1"
FIRST_DATE = date(2026, 7, 23)
LAST_DATE = date(2026, 9, 16)
OPEN_AFTER = datetime(2026, 9, 17, 12, tzinfo=timezone.utc)
OFFICIAL_STARTER_ORDERS = {f"{slot}00" for slot in range(1, 10)}
COUNT_FIELDS = (
    "plateAppearances", "atBats", "hits", "doubles", "triples", "homeRuns"
)
MARKET_LINES = {
    "hits": (0.5, 1.5),
    "home_runs_over_0_5": (0.5,),
    "total_bases": (0.5, 1.5, 2.5, 3.5, 4.5, 5.5),
}
DATE_DISPOSITIONS = {
    "complete_nonempty",
    "complete_official_no_games",
    "failed_missing_plan",
    "failed_incomplete_terminal_records",
    "failed_source_or_integrity",
}
COMPLETE_DATE_DISPOSITIONS = {
    "complete_nonempty",
    "complete_official_no_games",
}


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise SharedPAAdjudicationError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise SharedPAAdjudicationError(f"{label} must be a lowercase SHA-256")
    return value


def _integer(value: Any, label: str, *, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SharedPAAdjudicationError(f"{label} must be an integer")
    if value < (1 if positive else 0):
        raise SharedPAAdjudicationError(f"{label} is outside its allowed range")
    return value


def _utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise SharedPAAdjudicationError(f"{label} must be canonical UTC")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise SharedPAAdjudicationError(f"{label} is invalid") from exc
    if parsed.tzinfo != timezone.utc:
        raise SharedPAAdjudicationError(f"{label} must be UTC")
    return parsed


def _aware_utc(value: Any, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise SharedPAAdjudicationError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


def required_dates() -> tuple[str, ...]:
    span = (LAST_DATE - FIRST_DATE).days + 1
    if span != 56:
        raise AssertionError("locked prospective window is not 56 dates")
    return tuple((FIRST_DATE + timedelta(days=index)).isoformat() for index in range(span))


def load_adjudication_contract(*, root: Path, path: Path) -> dict[str, Any]:
    source = path.resolve()
    repository = root.resolve()
    try:
        source.relative_to(repository)
        raw = source.read_bytes()
        contract = json.loads(raw)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SharedPAAdjudicationError("adjudication contract is unreadable") from exc
    if not isinstance(contract, dict) or contract.get("schema_version") != CONTRACT_SCHEMA:
        raise SharedPAAdjudicationError("adjudication contract schema changed")
    if set(contract) != {
        "schema_version", "status", "protocol_locked_at_utc",
        "source_release_commit", "shared_pa_evidence_contract",
        "prospective_window", "official_outcome_contract", "markets",
        "comparators", "metrics", "promotion_gates", "protected_boundaries",
    }:
        raise SharedPAAdjudicationError("adjudication contract root surface changed")
    if contract.get("status") != "LOCKED_OUTCOME_BLIND_BEFORE_FIRST_ELIGIBLE_DATE":
        raise SharedPAAdjudicationError("adjudication contract is not outcome-blind locked")
    if _utc(contract.get("protocol_locked_at_utc"), "protocol_locked_at_utc") >= datetime(
        2026, 7, 23, tzinfo=timezone.utc
    ):
        raise SharedPAAdjudicationError("contract was not locked before the first eligible date")
    if contract.get("source_release_commit") != "55ed4103e64bfefd0457541c5ffa18f1a50068a3":
        raise SharedPAAdjudicationError("source release commit changed")
    window = contract.get("prospective_window")
    if not isinstance(window, Mapping) or (
        window.get("first_official_date"), window.get("last_official_date"),
        window.get("required_consecutive_calendar_dates"),
        window.get("outcomes_must_not_be_opened_before_utc"),
    ) != (FIRST_DATE.isoformat(), LAST_DATE.isoformat(), 56, "2026-09-17T12:00:00Z"):
        raise SharedPAAdjudicationError("prospective window changed")
    if any(window.get(key) is not True for key in (
        "every_nonempty_pitcher_plan_required",
        "every_planned_game_side_requires_one_terminal_record",
        "incomplete_or_failed_dates_cannot_be_skipped_or_replaced",
        "historical_or_missed_records_cannot_be_backfilled",
        "may_2026_must_not_be_accessed",
    )):
        raise SharedPAAdjudicationError("prospective completeness boundary weakened")
    evidence = contract.get("shared_pa_evidence_contract")
    if not isinstance(evidence, Mapping) or set(evidence) != {"path", "sha256"} or evidence.get("path") != "config/shared_pa_forward_evidence_contract_v1.json":
        raise SharedPAAdjudicationError("shared PA evidence binding is missing")
    bound = repository / str(evidence.get("path"))
    expected = _sha(evidence.get("sha256"), "shared PA evidence contract sha256")
    try:
        actual = hashlib.sha256(bound.read_bytes()).hexdigest()
    except OSError as exc:
        raise SharedPAAdjudicationError("shared PA evidence contract is unreadable") from exc
    if actual != expected:
        raise SharedPAAdjudicationError("shared PA evidence contract hash differs")
    markets = contract.get("markets")
    if not isinstance(markets, Mapping) or set(markets) != set(MARKET_LINES):
        raise SharedPAAdjudicationError("market separation changed")
    for market, lines in MARKET_LINES.items():
        value = markets.get(market)
        if not isinstance(value, Mapping) or value.get("binary_lines") != list(lines) or value.get("full_count_distribution_required") is not True:
            raise SharedPAAdjudicationError(f"{market} market contract changed")
    if markets["home_runs_over_0_5"].get("high_probability_tail") != "candidate top decile fixed from candidate probabilities":
        raise SharedPAAdjudicationError("HR high-probability tail contract changed")
    outcome = contract.get("official_outcome_contract")
    if not isinstance(outcome, Mapping) or any(outcome.get(key) is not True for key in (
        "final_status_required", "hard_game_team_side_player_identity_required",
        "raw_payload_and_projection_hashes_required",
        "opening_gate_must_run_before_projection_parse",
    )):
        raise SharedPAAdjudicationError("official outcome integrity boundary changed")
    if outcome.get("official_starter_batting_orders") != sorted(OFFICIAL_STARTER_ORDERS):
        raise SharedPAAdjudicationError("official starter definition changed")
    if outcome.get("count_fields") != list(COUNT_FIELDS):
        raise SharedPAAdjudicationError("official count surface changed")
    if (
        outcome.get("hits_definition") != "official hits"
        or outcome.get("home_run_over_0_5_definition") != "official homeRuns >= 1"
        or outcome.get("total_bases_definition")
        != "singles + 2*doubles + 3*triples + 4*homeRuns"
        or outcome.get("dnp_or_nonstarter_disposition")
        != "retained_coverage_failure_not_scored"
        or outcome.get("support_breach_disposition")
        != "model_invalid_no_clipping_or_probability_floor"
    ):
        raise SharedPAAdjudicationError("official market truth definition changed")
    comparators = contract.get("comparators")
    if comparators != {
        "league_rate_2023_same_pa_volume": "required",
        "time_safe_player_empirical_bayes": "required_comparator",
        "frozen_production_simulator": "required_exact_pregame_probability_or_gate_unassessable",
        "valid_market_implied_probability": "required_where_receipt_verified_available",
        "historical_price_executability": False,
    }:
        raise SharedPAAdjudicationError("comparator contract changed")
    metrics = contract.get("metrics")
    if not isinstance(metrics, Mapping) or set(metrics) != {
        "proper_scores", "calibration", "discrimination", "coverage", "uncertainty"
    } or metrics.get("proper_scores") != ["brier", "log_loss"] or metrics.get("calibration") != [
        "absolute_bias", "equal_frequency_decile_ece",
        "candidate_fixed_top_decile_absolute_bias",
    ] or metrics.get("discrimination") != ["auc"]:
        raise SharedPAAdjudicationError("metric contract changed")
    if metrics.get("coverage") != ["planned_rows", "gradeable_rows", "explicit_dispositions"]:
        raise SharedPAAdjudicationError("coverage metric contract changed")
    if metrics.get("uncertainty") != {
        "unit": "official_date_cluster", "method": "paired_percentile_bootstrap",
        "confidence": 0.95, "resamples": 10000, "seed": 20260722,
    }:
        raise SharedPAAdjudicationError("uncertainty contract changed")
    gates = contract.get("promotion_gates")
    if not isinstance(gates, Mapping) or gates.get("proper_score_material_fraction") != 0.01 or gates.get("minimum_gradeable_fraction") != 0.95:
        raise SharedPAAdjudicationError("material proper-score gate changed")
    for key in (
        "proper_score_point_and_upper_bound_must_both_clear",
        "calibration_must_not_regress_against_every_required_comparator",
        "high_tail_calibration_must_not_regress", "auc_must_not_regress",
        "coverage_must_not_regress", "all_markets_adjudicated_separately",
        "one_market_cannot_rescue_another",
        "missing_required_comparator_means_no_promotion",
        "post_hoc_threshold_or_policy_changes_forbidden",
        "all_56_calendar_date_dispositions_required",
        "all_date_blocks_must_be_complete",
        "binary_proper_score_materiality_required_at_every_declared_line",
    ):
        if gates.get(key) is not True:
            raise SharedPAAdjudicationError(f"promotion gate weakened: {key}")
    if gates.get("betting_authorized") is not False:
        raise SharedPAAdjudicationError("betting boundary changed")
    protected = contract.get("protected_boundaries")
    if not isinstance(protected, Mapping) or any(protected.get(key) is not False for key in (
        "outcomes_opened_while_contract_created", "may_2026_opened",
        "spent_2025_hr_reused", "pitcher_features_in_candidate",
        "prices_claimed_executable", "production_changed", "betting_authorized",
    )) or protected.get("research_only") is not True or any(
        protected.get(key) is not True for key in (
            "candidate_must_be_locked_before_outcomes_open",
            "outcome_sealed_replay_must_be_labeled_nonprospective",
        )
    ):
        raise SharedPAAdjudicationError("protected boundary changed")
    return contract


def assert_outcome_opening_allowed(*, now: datetime, official_dates: list[str]) -> None:
    if _aware_utc(now, "outcome opening time") < OPEN_AFTER:
        raise SharedPAAdjudicationError("prospective outcomes remain sealed")
    if tuple(official_dates) != required_dates():
        raise SharedPAAdjudicationError("prospective date population is incomplete or reordered")


def project_official_final_feed(
    *, raw_payload_bytes: bytes, expected_game_pk: int,
    expected_official_date: str, opened_at_utc: datetime,
) -> bytes:
    """Create the only outcome projection consumed by adjudication.

    The opening gate runs before JSON parsing.  Tests use synthetic live-feed
    shapes; this function has no transport and cannot fetch an outcome.
    """
    assert_outcome_opening_allowed(
        now=opened_at_utc, official_dates=list(required_dates())
    )
    game_pk = _integer(expected_game_pk, "expected_game_pk", positive=True)
    if expected_official_date not in required_dates():
        raise SharedPAAdjudicationError("expected official date is outside the locked window")
    if not isinstance(raw_payload_bytes, bytes) or not raw_payload_bytes:
        raise SharedPAAdjudicationError("official raw source payload bytes are missing")
    try:
        raw = json.loads(raw_payload_bytes, object_pairs_hook=_unique_json_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SharedPAAdjudicationError("official final feed is invalid JSON") from exc
    if not isinstance(raw, Mapping) or raw.get("gamePk") != game_pk:
        raise SharedPAAdjudicationError("official final feed game identity changed")
    game_data = raw.get("gameData")
    live_data = raw.get("liveData")
    if not isinstance(game_data, Mapping) or not isinstance(live_data, Mapping):
        raise SharedPAAdjudicationError("official final feed root paths are missing")
    datetime_block = game_data.get("datetime")
    status = game_data.get("status")
    if not isinstance(datetime_block, Mapping) or datetime_block.get("officialDate") != expected_official_date:
        raise SharedPAAdjudicationError("official final feed date identity changed")
    if not isinstance(status, Mapping) or status.get("abstractGameState") != "Final":
        raise SharedPAAdjudicationError("official final feed is not final")
    boxscore = live_data.get("boxscore")
    teams = boxscore.get("teams") if isinstance(boxscore, Mapping) else None
    if not isinstance(teams, Mapping) or set(teams) != {"home", "away"}:
        raise SharedPAAdjudicationError("official final boxscore teams are missing")
    projected_teams: dict[str, Any] = {}
    global_players: set[int] = set()
    for side in ("home", "away"):
        team = teams.get(side)
        if not isinstance(team, Mapping):
            raise SharedPAAdjudicationError("official final boxscore side is missing")
        identity = team.get("team")
        players = team.get("players")
        if not isinstance(identity, Mapping) or not isinstance(players, Mapping):
            raise SharedPAAdjudicationError("official final team identity or players are missing")
        team_id = _integer(identity.get("id"), f"{side}.team.id", positive=True)
        projected_players: list[dict[str, Any]] = []
        side_players: set[int] = set()
        for player_key, player in players.items():
            if not isinstance(player_key, str) or not isinstance(player, Mapping):
                raise SharedPAAdjudicationError("official final player entry is malformed")
            person = player.get("person")
            if not isinstance(person, Mapping):
                raise SharedPAAdjudicationError("official final player person identity is missing")
            player_id = _integer(person.get("id"), f"{side}.person.id", positive=True)
            if player_key != f"ID{player_id}":
                raise SharedPAAdjudicationError("official final player key and person identity differ")
            if player_id in side_players or player_id in global_players:
                raise SharedPAAdjudicationError("official final player identity is duplicated")
            side_players.add(player_id)
            global_players.add(player_id)
            batting_order = player.get("battingOrder")
            if batting_order in {None, ""}:
                continue
            if not isinstance(batting_order, str) or len(batting_order) != 3 or not batting_order.isdigit() or batting_order[0] == "0":
                raise SharedPAAdjudicationError("official hitter batting order is invalid")
            stats = player.get("stats")
            batting = stats.get("batting") if isinstance(stats, Mapping) else None
            if not isinstance(batting, Mapping):
                raise SharedPAAdjudicationError("official hitter batting stats are missing")
            counts = {field: _integer(batting.get(field), f"{side}.{player_id}.{field}") for field in COUNT_FIELDS}
            pa, ab, hits, doubles, triples, homers = (counts[field] for field in COUNT_FIELDS)
            if not (pa >= ab >= hits >= doubles + triples + homers):
                raise SharedPAAdjudicationError("official hitter batting counts are impossible")
            projected_players.append({
                "personId": player_id,
                "battingOrder": batting_order,
                "batting": counts,
            })
        projected_teams[side] = {
            "teamId": team_id,
            "players": sorted(projected_players, key=lambda value: value["personId"]),
        }
    if projected_teams["home"]["teamId"] == projected_teams["away"]["teamId"]:
        raise SharedPAAdjudicationError("official home and away team identities collide")
    projection = {
        "gamePk": game_pk,
        "officialDate": expected_official_date,
        "status": {"abstractGameState": "Final"},
        "teams": projected_teams,
    }
    return _canonical(projection)


@dataclass(frozen=True)
class OfficialPlayerOutcome:
    mlb_game_pk: int
    official_game_date: str
    side: str
    team_id: int
    player_id: int
    batting_order: str
    plate_appearances: int
    at_bats: int
    hits: int
    doubles: int
    triples: int
    home_runs: int
    singles: int
    total_bases: int
    source_raw_payload_sha256: str
    source_projection_sha256: str
    source_received_at_utc: str

    @property
    def is_official_starter(self) -> bool:
        return self.batting_order in OFFICIAL_STARTER_ORDERS

    def as_record(self) -> dict[str, Any]:
        value = {
            "schema_version": OUTCOME_SCHEMA,
            **self.__dict__,
            "is_official_starter": self.is_official_starter,
            "hr_over_0_5": self.home_runs >= 1,
            "research_only": True,
            "betting_authorized": False,
        }
        value["outcome_sha256"] = hashlib.sha256(_canonical(value)).hexdigest()
        return value


def parse_official_player_outcome(
    *, payload_bytes: bytes, side: str, player_id: int,
    source_raw_payload_bytes: bytes, source_received_at_utc: str,
    opened_at_utc: datetime,
) -> OfficialPlayerOutcome:
    """Parse one hard-identified player from a hash-verified retained projection."""
    assert_outcome_opening_allowed(now=opened_at_utc, official_dates=list(required_dates()))
    if side not in {"home", "away"}:
        raise SharedPAAdjudicationError("side must be home or away")
    wanted_player = _integer(player_id, "player_id", positive=True)
    _utc(source_received_at_utc, "source_received_at_utc")
    if not isinstance(source_raw_payload_bytes, bytes) or not source_raw_payload_bytes:
        raise SharedPAAdjudicationError("official raw source payload bytes are missing")
    source_raw_payload_sha256 = hashlib.sha256(source_raw_payload_bytes).hexdigest()
    if not isinstance(payload_bytes, bytes) or not payload_bytes:
        raise SharedPAAdjudicationError("official outcome projection bytes are missing")
    projection_sha256 = hashlib.sha256(payload_bytes).hexdigest()
    try:
        payload = json.loads(payload_bytes, object_pairs_hook=_unique_json_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SharedPAAdjudicationError("official outcome projection is invalid JSON") from exc
    if not isinstance(payload, Mapping) or set(payload) != {"gamePk", "officialDate", "status", "teams"}:
        raise SharedPAAdjudicationError("official outcome root surface changed")
    game_pk = _integer(payload.get("gamePk"), "gamePk", positive=True)
    try:
        official_date = date.fromisoformat(str(payload.get("officialDate")))
    except ValueError as exc:
        raise SharedPAAdjudicationError("officialDate is invalid") from exc
    if official_date.isoformat() not in required_dates():
        raise SharedPAAdjudicationError("officialDate is outside the locked prospective window")
    status = payload.get("status")
    if not isinstance(status, Mapping) or set(status) != {"abstractGameState"} or status.get("abstractGameState") != "Final":
        raise SharedPAAdjudicationError("official game is not final or status surface changed")
    teams = payload.get("teams")
    if not isinstance(teams, Mapping) or set(teams) != {"home", "away"}:
        raise SharedPAAdjudicationError("official team surface changed")
    team = teams.get(side)
    if not isinstance(team, Mapping) or set(team) != {"teamId", "players"}:
        raise SharedPAAdjudicationError("official side surface changed")
    team_id = _integer(team.get("teamId"), "teamId", positive=True)
    players = team.get("players")
    if not isinstance(players, list):
        raise SharedPAAdjudicationError("official players must be a list")
    player_ids: set[int] = set()
    for value in players:
        if not isinstance(value, Mapping) or set(value) != {"personId", "battingOrder", "batting"}:
            raise SharedPAAdjudicationError("official player collection surface changed")
        value_player_id = _integer(value.get("personId"), "personId", positive=True)
        if value_player_id in player_ids:
            raise SharedPAAdjudicationError("official player identity is duplicated")
        player_ids.add(value_player_id)
    matches = [value for value in players if value.get("personId") == wanted_player]
    if len(matches) != 1:
        raise SharedPAAdjudicationError("official player identity is missing or ambiguous")
    player = matches[0]
    if set(player) != {"personId", "battingOrder", "batting"}:
        raise SharedPAAdjudicationError("official player surface changed")
    batting_order = player.get("battingOrder")
    if not isinstance(batting_order, str) or len(batting_order) != 3 or not batting_order.isdigit() or batting_order[0] == "0":
        raise SharedPAAdjudicationError("official battingOrder is invalid")
    batting = player.get("batting")
    if not isinstance(batting, Mapping) or set(batting) != set(COUNT_FIELDS):
        raise SharedPAAdjudicationError("official batting count surface changed")
    counts = {field: _integer(batting[field], field) for field in COUNT_FIELDS}
    pa, ab, hits, doubles, triples, homers = (counts[field] for field in COUNT_FIELDS)
    if not (pa >= ab >= hits >= doubles + triples + homers):
        raise SharedPAAdjudicationError("official batting counts are internally impossible")
    singles = hits - doubles - triples - homers
    total_bases = singles + 2 * doubles + 3 * triples + 4 * homers
    return OfficialPlayerOutcome(
        mlb_game_pk=game_pk, official_game_date=official_date.isoformat(), side=side,
        team_id=team_id, player_id=wanted_player, batting_order=batting_order,
        plate_appearances=pa, at_bats=ab, hits=hits, doubles=doubles,
        triples=triples, home_runs=homers, singles=singles,
        total_bases=total_bases,
        source_raw_payload_sha256=source_raw_payload_sha256,
        source_projection_sha256=projection_sha256,
        source_received_at_utc=source_received_at_utc,
    )


def outcome_disposition(outcome: OfficialPlayerOutcome) -> str:
    if not outcome.is_official_starter:
        return "retained_nonstarter_coverage_failure"
    if outcome.plate_appearances == 0:
        return "retained_starter_zero_pa_coverage_failure"
    return "gradeable_official_starter"


def _outcome_from_record(record: Any) -> OfficialPlayerOutcome:
    if not isinstance(record, Mapping) or set(record) != {
        "schema_version", "mlb_game_pk", "official_game_date", "side",
        "team_id", "player_id", "batting_order", "plate_appearances",
        "at_bats", "hits", "doubles", "triples", "home_runs", "singles",
        "total_bases", "source_raw_payload_sha256", "source_projection_sha256",
        "source_received_at_utc", "is_official_starter", "hr_over_0_5",
        "research_only", "betting_authorized", "outcome_sha256",
    }:
        raise SharedPAAdjudicationError("official outcome record surface changed")
    if record.get("schema_version") != OUTCOME_SCHEMA:
        raise SharedPAAdjudicationError("official outcome record schema changed")
    claimed_hash = _sha(record.get("outcome_sha256"), "outcome_sha256")
    unhashed = {key: value for key, value in record.items() if key != "outcome_sha256"}
    if hashlib.sha256(_canonical(unhashed)).hexdigest() != claimed_hash:
        raise SharedPAAdjudicationError("official outcome record hash mismatch")
    official_date = record.get("official_game_date")
    if official_date not in required_dates():
        raise SharedPAAdjudicationError("official outcome record date is outside the locked window")
    side = record.get("side")
    if side not in {"home", "away"}:
        raise SharedPAAdjudicationError("official outcome record side is invalid")
    batting_order = record.get("batting_order")
    if not isinstance(batting_order, str) or len(batting_order) != 3 or not batting_order.isdigit() or batting_order[0] == "0":
        raise SharedPAAdjudicationError("official outcome record batting order is invalid")
    counts = {
        name: _integer(record.get(name), name)
        for name in (
            "plate_appearances", "at_bats", "hits", "doubles", "triples",
            "home_runs", "singles", "total_bases",
        )
    }
    if not (
        counts["plate_appearances"] >= counts["at_bats"] >= counts["hits"]
        >= counts["doubles"] + counts["triples"] + counts["home_runs"]
    ):
        raise SharedPAAdjudicationError("official outcome record counts are impossible")
    singles = (
        counts["hits"] - counts["doubles"] - counts["triples"]
        - counts["home_runs"]
    )
    total_bases = (
        singles + 2 * counts["doubles"] + 3 * counts["triples"]
        + 4 * counts["home_runs"]
    )
    if counts["singles"] != singles or counts["total_bases"] != total_bases:
        raise SharedPAAdjudicationError("official outcome record derived counts changed")
    expected_starter = batting_order in OFFICIAL_STARTER_ORDERS
    if record.get("is_official_starter") is not expected_starter:
        raise SharedPAAdjudicationError("official starter disposition changed")
    if record.get("hr_over_0_5") is not (counts["home_runs"] >= 1):
        raise SharedPAAdjudicationError("official HR target changed")
    if record.get("research_only") is not True or record.get("betting_authorized") is not False:
        raise SharedPAAdjudicationError("official outcome authorization boundary changed")
    _sha(record.get("source_raw_payload_sha256"), "source_raw_payload_sha256")
    _sha(record.get("source_projection_sha256"), "source_projection_sha256")
    _utc(record.get("source_received_at_utc"), "source_received_at_utc")
    return OfficialPlayerOutcome(
        mlb_game_pk=_integer(record.get("mlb_game_pk"), "mlb_game_pk", positive=True),
        official_game_date=str(official_date), side=str(side),
        team_id=_integer(record.get("team_id"), "team_id", positive=True),
        player_id=_integer(record.get("player_id"), "player_id", positive=True),
        batting_order=batting_order,
        plate_appearances=counts["plate_appearances"], at_bats=counts["at_bats"],
        hits=counts["hits"], doubles=counts["doubles"], triples=counts["triples"],
        home_runs=counts["home_runs"], singles=counts["singles"],
        total_bases=counts["total_bases"],
        source_raw_payload_sha256=str(record["source_raw_payload_sha256"]),
        source_projection_sha256=str(record["source_projection_sha256"]),
        source_received_at_utc=str(record["source_received_at_utc"]),
    )


def _market_observed_count(outcome: OfficialPlayerOutcome, market: str) -> int:
    return {
        "hits": outcome.hits,
        "home_runs_over_0_5": outcome.home_runs,
        "total_bases": outcome.total_bases,
    }[market]


def probability_for_observed_count(pmf: list[Any], observed: int) -> float:
    """Return exact mass; never clip or substitute a probability floor."""
    count = _integer(observed, "observed count")
    if not isinstance(pmf, list) or not pmf:
        raise SharedPAAdjudicationError("PMF is missing")
    values = []
    for raw in pmf:
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(float(raw)) or float(raw) < 0:
            raise SharedPAAdjudicationError("PMF contains an invalid probability")
        values.append(float(raw))
    if not math.isclose(sum(values), 1.0, rel_tol=0.0, abs_tol=1e-10):
        raise SharedPAAdjudicationError("PMF does not sum to one")
    if count >= len(values) or values[count] <= 0.0:
        raise SharedPAAdjudicationError("observed outcome breached exact model support")
    return values[count]


def _pmf(pmf: Any, label: str) -> list[float]:
    if not isinstance(pmf, list) or not pmf:
        raise SharedPAAdjudicationError(f"{label} PMF is missing")
    values: list[float] = []
    for raw in pmf:
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise SharedPAAdjudicationError(f"{label} PMF contains a non-number")
        value = float(raw)
        if not math.isfinite(value) or value < 0.0 or value > 1.0:
            raise SharedPAAdjudicationError(f"{label} PMF contains an invalid probability")
        values.append(value)
    if not math.isclose(sum(values), 1.0, rel_tol=0.0, abs_tol=1e-10):
        raise SharedPAAdjudicationError(f"{label} PMF does not sum to one")
    return values


def _distribution_scores(pmf: list[float], observed: int) -> tuple[float, float]:
    if observed >= len(pmf) or pmf[observed] <= 0.0:
        raise SharedPAAdjudicationError("observed outcome breached exact model support")
    brier = sum((probability - (1.0 if index == observed else 0.0)) ** 2
                for index, probability in enumerate(pmf))
    return brier, -math.log(pmf[observed])


def _tail(pmf: list[float], line: float) -> float:
    cutoff = int(math.floor(line))
    if line != cutoff + 0.5 or cutoff < 0:
        raise SharedPAAdjudicationError("market line must be a nonnegative half-integer")
    return sum(pmf[cutoff + 1 :])


def _binary_log_loss(probability: float, label: int) -> float:
    if label == 1:
        if probability <= 0.0:
            raise SharedPAAdjudicationError("observed binary outcome breached exact model support")
        return -math.log(probability)
    if probability >= 1.0:
        raise SharedPAAdjudicationError("observed binary outcome breached exact model support")
    return -math.log1p(-probability)


def _auc(labels: list[int], probabilities: list[float]) -> float | None:
    positive_count = sum(labels)
    negative_count = len(labels) - positive_count
    if positive_count == 0 or negative_count == 0:
        return None
    ordered = sorted(zip(probabilities, labels), key=lambda value: value[0])
    rank_sum = 0.0
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][0] == ordered[index][0]:
            end += 1
        average_rank = ((index + 1) + end) / 2.0
        rank_sum += average_rank * sum(label for _, label in ordered[index:end])
        index = end
    return (rank_sum - positive_count * (positive_count + 1) / 2.0) / (
        positive_count * negative_count
    )


def _calibration(
    labels: list[int], probabilities: list[float], *, candidate_tail: list[int],
) -> dict[str, float]:
    if not labels or len(labels) != len(probabilities):
        raise SharedPAAdjudicationError("calibration vectors are empty or misaligned")
    bias = sum(probabilities[index] - labels[index] for index in range(len(labels))) / len(labels)
    ordered = sorted(range(len(labels)), key=lambda index: (probabilities[index], index))
    groups: list[list[int]] = [[] for _ in range(min(10, len(labels)))]
    for rank, index in enumerate(ordered):
        groups[min(len(groups) - 1, rank * len(groups) // len(labels))].append(index)
    ece = 0.0
    for group in groups:
        if not group:
            continue
        predicted = sum(probabilities[index] for index in group) / len(group)
        observed = sum(labels[index] for index in group) / len(group)
        ece += len(group) / len(labels) * abs(predicted - observed)
    if not candidate_tail or any(index < 0 or index >= len(labels) for index in candidate_tail):
        raise SharedPAAdjudicationError("candidate-fixed calibration tail is invalid")
    tail_bias = (
        sum(probabilities[index] - labels[index] for index in candidate_tail)
        / len(candidate_tail)
    )
    return {
        "absolute_bias": abs(bias),
        "equal_frequency_decile_ece": ece,
        "candidate_fixed_top_decile_absolute_bias": abs(tail_bias),
    }


def _percentile(values: list[float], probability: float) -> float:
    if not values or not 0.0 <= probability <= 1.0:
        raise SharedPAAdjudicationError("invalid percentile request")
    ordered = sorted(values)
    position = probability * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _paired_date_bootstrap(
    *, dates: list[str], differences: list[float], resamples: int = 10000,
    seed: int = 20260722,
) -> dict[str, float]:
    if len(dates) != len(differences) or not dates:
        raise SharedPAAdjudicationError("paired bootstrap rows are empty or misaligned")
    by_date: dict[str, list[float]] = {}
    for official_date, difference in zip(dates, differences):
        by_date.setdefault(official_date, []).append(difference)
    clusters = sorted(by_date)
    generator = random.Random(seed)
    samples: list[float] = []
    for _ in range(resamples):
        drawn = [clusters[generator.randrange(len(clusters))] for _ in clusters]
        numerator = sum(sum(by_date[value]) for value in drawn)
        denominator = sum(len(by_date[value]) for value in drawn)
        samples.append(numerator / denominator)
    return {
        "point": sum(differences) / len(differences),
        "lower_95": _percentile(samples, 0.025),
        "upper_95": _percentile(samples, 0.975),
    }


def _probability(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SharedPAAdjudicationError(f"{label} must be numeric")
    parsed = float(value)
    if not math.isfinite(parsed) or not 0.0 <= parsed <= 1.0:
        raise SharedPAAdjudicationError(f"{label} is outside [0, 1]")
    return parsed


def _validate_prediction_provenance(
    value: Any, *, label: str, horizon: datetime, hard_key: str,
) -> None:
    if not isinstance(value, Mapping) or set(value) != {
        "source_id", "evidence_mode", "input_observed_at_utc",
        "prediction_generated_at_utc", "model_or_contract_locked_at_utc",
        "artifact_sha256", "model_or_contract_sha256", "prediction_hard_key",
        "target_horizon_utc", "consumed_probability_sha256",
    }:
        raise SharedPAAdjudicationError(f"{label} prediction provenance surface changed")
    if value.get("source_id") != label:
        raise SharedPAAdjudicationError(f"{label} source identity changed")
    if value.get("prediction_hard_key") != hard_key:
        raise SharedPAAdjudicationError(f"{label} prediction hard identity changed")
    if _utc(value.get("target_horizon_utc"), f"{label}.target_horizon_utc") != horizon:
        raise SharedPAAdjudicationError(f"{label} prediction horizon changed")
    mode = value.get("evidence_mode")
    if mode not in {"prospective_pre_horizon", "untouched_outcome_sealed_replay"}:
        raise SharedPAAdjudicationError(f"{label} evidence mode is invalid")
    if label in {"frozen_production", "market_implied"} and mode != "prospective_pre_horizon":
        raise SharedPAAdjudicationError(f"{label} must be prospectively captured")
    input_observed = _utc(
        value.get("input_observed_at_utc"), f"{label}.input_observed_at_utc"
    )
    generated = _utc(
        value.get("prediction_generated_at_utc"),
        f"{label}.prediction_generated_at_utc",
    )
    locked = _utc(
        value.get("model_or_contract_locked_at_utc"),
        f"{label}.model_or_contract_locked_at_utc",
    )
    if input_observed > horizon:
        raise SharedPAAdjudicationError(f"{label} input was observed after the decision horizon")
    if locked > generated:
        raise SharedPAAdjudicationError(f"{label} model or contract was locked after prediction")
    if mode == "prospective_pre_horizon" and generated > horizon:
        raise SharedPAAdjudicationError(f"{label} probability was generated after the decision horizon")
    if mode == "untouched_outcome_sealed_replay" and generated >= OPEN_AFTER:
        raise SharedPAAdjudicationError(f"{label} sealed replay was generated after outcomes opened")
    _sha(value.get("artifact_sha256"), f"{label}.artifact_sha256")
    _sha(value.get("model_or_contract_sha256"), f"{label}.model_or_contract_sha256")
    _sha(value.get("consumed_probability_sha256"), f"{label}.consumed_probability_sha256")


def _validate_consumed_probability(
    provenance: Mapping[str, Any], *, source_id: str, hard_key: str,
    target_horizon_utc: str, probability: Any,
) -> None:
    consumed = {
        "source_id": source_id,
        "prediction_hard_key": hard_key,
        "target_horizon_utc": target_horizon_utc,
        "probability": probability,
    }
    if hashlib.sha256(_canonical(consumed)).hexdigest() != provenance.get("consumed_probability_sha256"):
        raise SharedPAAdjudicationError(
            f"{source_id} consumed probability differs from its bound prediction record"
        )


def _binary_metric_bundle(
    labels: list[int], probabilities: list[float], *, candidate_tail: list[int],
) -> dict[str, Any]:
    return {
        "brier": sum(
            (probability - label) ** 2
            for probability, label in zip(probabilities, labels)
        ) / len(labels),
        "log_loss": sum(
            _binary_log_loss(probability, label)
            for probability, label in zip(probabilities, labels)
        ) / len(labels),
        "auc": _auc(labels, probabilities),
        **_calibration(labels, probabilities, candidate_tail=candidate_tail),
    }


def evaluate_market(
    *, market: str, rows: list[Mapping[str, Any]], planned_rows: int,
    explicit_dispositions: Mapping[str, int],
    date_dispositions: Mapping[str, str],
    market_implied_available: bool, opened_at_utc: datetime,
) -> dict[str, Any]:
    """Evaluate one market only; inputs are synthetic until the locked opening time.

    Full-count PMFs are required for the candidate, league-rate baseline, and
    time-safe player-rate baseline.  Frozen production and market comparators
    are consumed only as exact binary line probabilities because pretending a
    single quoted line defines a full count distribution would be false.
    Missing inputs never receive a league-average substitute.
    """
    assert_outcome_opening_allowed(
        now=opened_at_utc, official_dates=list(required_dates())
    )
    if market not in MARKET_LINES:
        raise SharedPAAdjudicationError("unsupported or pooled market")
    total_planned = _integer(planned_rows, "planned_rows", positive=True)
    if not isinstance(date_dispositions, Mapping) or set(date_dispositions) != set(required_dates()):
        raise SharedPAAdjudicationError("all 56 calendar-date dispositions are required")
    if any(value not in DATE_DISPOSITIONS for value in date_dispositions.values()):
        raise SharedPAAdjudicationError("unknown calendar-date disposition")
    all_date_blocks_complete = all(
        value in COMPLETE_DATE_DISPOSITIONS for value in date_dispositions.values()
    )
    allowed_row_dispositions = {
        "gradeable_official_starter",
        "retained_nonstarter_coverage_failure",
        "retained_starter_zero_pa_coverage_failure",
        "retained_missing_prediction_coverage_failure",
        "retained_identity_or_integrity_coverage_failure",
    }
    if not isinstance(explicit_dispositions, Mapping) or set(explicit_dispositions) - allowed_row_dispositions:
        raise SharedPAAdjudicationError("explicit dispositions are missing")
    disposition_total = sum(_integer(value, f"disposition.{key}") for key, value in explicit_dispositions.items())
    if disposition_total != total_planned or len(rows) != int(explicit_dispositions.get("gradeable_official_starter", -1)):
        raise SharedPAAdjudicationError("coverage accounting does not reconcile")
    pmf_models = ["candidate", "league_rate", "player_rate"]
    external_models = ["frozen_production"]
    if market_implied_available:
        external_models.append("market_implied")
    all_sources = [*pmf_models, *external_models]
    if not rows:
        raise SharedPAAdjudicationError("no gradeable rows are available")
    line_names = [f"over_{line:.1f}" for line in MARKET_LINES[market]]
    dates: list[str] = []
    outcomes: list[int] = []
    pmfs: dict[str, list[list[float]]] = {name: [] for name in pmf_models}
    binary_probabilities: dict[str, dict[str, list[float]]] = {
        name: {line_name: [] for line_name in line_names} for name in all_sources
    }
    hard_keys: set[tuple[int, str, int, int, str]] = set()
    for row in rows:
        if set(row) != {
            "market", "official_game_date", "mlb_game_pk", "side", "team_id", "player_id",
            "official_start_utc", "target_horizon_utc", "official_outcome_record", "pmfs",
            "binary_probabilities", "prediction_provenance",
        }:
            raise SharedPAAdjudicationError("adjudication row surface changed")
        if row.get("market") != market:
            raise SharedPAAdjudicationError("market pooling or row relabeling detected")
        raw_date = row.get("official_game_date")
        if raw_date not in required_dates():
            raise SharedPAAdjudicationError("row is outside the locked prospective window")
        game_pk = _integer(row.get("mlb_game_pk"), "mlb_game_pk", positive=True)
        player_id = _integer(row.get("player_id"), "player_id", positive=True)
        team_id = _integer(row.get("team_id"), "team_id", positive=True)
        side = row.get("side")
        if side not in {"home", "away"}:
            raise SharedPAAdjudicationError("row side is invalid")
        official_outcome = _outcome_from_record(row.get("official_outcome_record"))
        if (
            official_outcome.mlb_game_pk != game_pk
            or official_outcome.official_game_date != raw_date
            or official_outcome.side != side
            or official_outcome.team_id != team_id
            or official_outcome.player_id != player_id
        ):
            raise SharedPAAdjudicationError("official outcome hard identity differs from prediction")
        if outcome_disposition(official_outcome) != "gradeable_official_starter":
            raise SharedPAAdjudicationError("non-gradeable outcome entered scored rows")
        hard_identity = (game_pk, str(side), team_id, player_id, market)
        if hard_identity in hard_keys:
            raise SharedPAAdjudicationError("duplicate hard market identity")
        hard_keys.add(hard_identity)
        prediction_hard_key = f"{game_pk}:{side}:{team_id}:{player_id}:{market}"
        official_start = _utc(row.get("official_start_utc"), "official_start_utc")
        horizon = _utc(row.get("target_horizon_utc"), "target_horizon_utc")
        if horizon != official_start - timedelta(hours=4):
            raise SharedPAAdjudicationError("target horizon is not exactly T-4h")
        official_day = date.fromisoformat(str(raw_date))
        if official_start.date() not in {official_day, official_day + timedelta(days=1)}:
            raise SharedPAAdjudicationError("official start is inconsistent with official game date")
        outcome = _market_observed_count(official_outcome, market)
        predictions = row.get("pmfs")
        if not isinstance(predictions, Mapping) or set(predictions) != set(pmf_models):
            raise SharedPAAdjudicationError("required full-count comparator PMF is missing or unexpected")
        external = row.get("binary_probabilities")
        if not isinstance(external, Mapping) or set(external) != set(external_models):
            raise SharedPAAdjudicationError("required binary comparator is missing or unexpected")
        provenance = row.get("prediction_provenance")
        if not isinstance(provenance, Mapping) or set(provenance) != set(all_sources):
            raise SharedPAAdjudicationError("required prediction provenance is missing or unexpected")
        for name in all_sources:
            _validate_prediction_provenance(
                provenance[name], label=name, horizon=horizon,
                hard_key=prediction_hard_key,
            )
        dates.append(str(raw_date))
        outcomes.append(outcome)
        for name in pmf_models:
            values = _pmf(predictions[name], name)
            _validate_consumed_probability(
                provenance[name], source_id=name, hard_key=prediction_hard_key,
                target_horizon_utc=str(row["target_horizon_utc"]),
                probability=predictions[name],
            )
            _distribution_scores(values, outcome)
            pmfs[name].append(values)
            for line, line_name in zip(MARKET_LINES[market], line_names):
                binary_probabilities[name][line_name].append(_tail(values, line))
        for name in external_models:
            probabilities = external[name]
            if not isinstance(probabilities, Mapping) or set(probabilities) != set(line_names):
                raise SharedPAAdjudicationError(f"{name} line probability surface changed")
            _validate_consumed_probability(
                provenance[name], source_id=name, hard_key=prediction_hard_key,
                target_horizon_utc=str(row["target_horizon_utc"]),
                probability=probabilities,
            )
            for line_name in line_names:
                binary_probabilities[name][line_name].append(
                    _probability(probabilities[line_name], f"{name}.{line_name}")
                )
    gradeable_fraction = len(rows) / total_planned
    metrics: dict[str, Any] = {}
    distribution_scores: dict[str, dict[str, float]] = {}
    for name in pmf_models:
        values = [_distribution_scores(pmf, outcome) for pmf, outcome in zip(pmfs[name], outcomes)]
        distribution_scores[name] = {
            "brier": sum(value[0] for value in values) / len(values),
            "log_loss": sum(value[1] for value in values) / len(values),
        }
    metrics["full_count_distribution"] = distribution_scores
    binary: dict[str, Any] = {}
    for line in MARKET_LINES[market]:
        labels = [int(outcome > line) for outcome in outcomes]
        line_metrics: dict[str, Any] = {}
        line_name = f"over_{line:.1f}"
        candidate_order = sorted(
            range(len(labels)),
            key=lambda index: (binary_probabilities["candidate"][line_name][index], index),
        )
        tail_size = max(1, math.ceil(len(labels) / 10))
        candidate_tail = candidate_order[-tail_size:]
        for name in all_sources:
            line_metrics[name] = _binary_metric_bundle(
                labels, binary_probabilities[name][line_name],
                candidate_tail=candidate_tail,
            )
        line_metrics["candidate_fixed_top_decile_rows"] = tail_size
        binary[line_name] = line_metrics
    metrics["binary_lines"] = binary
    comparisons: dict[str, Any] = {}
    all_material = True
    all_calibration = True
    all_discrimination = True
    for comparator in ("league_rate", "player_rate"):
        comparator_result: dict[str, Any] = {}
        for score in ("brier", "log_loss"):
            candidate_values = [
                _distribution_scores(pmf, outcome)[0 if score == "brier" else 1]
                for pmf, outcome in zip(pmfs["candidate"], outcomes)
            ]
            comparator_values = [
                _distribution_scores(pmf, outcome)[0 if score == "brier" else 1]
                for pmf, outcome in zip(pmfs[comparator], outcomes)
            ]
            interval = _paired_date_bootstrap(
                dates=dates,
                differences=[candidate - baseline for candidate, baseline in zip(candidate_values, comparator_values)],
            )
            floor = -0.01 * distribution_scores[comparator][score]
            passed = interval["point"] <= floor and interval["upper_95"] <= floor
            comparator_result[score] = {**interval, "required_below": floor, "passed": passed}
            all_material = all_material and passed
        comparisons[comparator] = comparator_result
    for comparator in ["league_rate", "player_rate", *external_models]:
        comparator_result = comparisons.setdefault(comparator, {})
        comparator_result["binary_lines"] = {}
        for line_name, line_metrics in binary.items():
            labels = [
                int(outcome > float(line_name.removeprefix("over_")))
                for outcome in outcomes
            ]
            candidate_probabilities = binary_probabilities["candidate"][line_name]
            comparator_probabilities = binary_probabilities[comparator][line_name]
            line_result: dict[str, Any] = {}
            for score in ("brier", "log_loss"):
                if score == "brier":
                    candidate_values = [
                        (probability - label) ** 2
                        for probability, label in zip(candidate_probabilities, labels)
                    ]
                    comparator_values = [
                        (probability - label) ** 2
                        for probability, label in zip(comparator_probabilities, labels)
                    ]
                else:
                    candidate_values = [
                        _binary_log_loss(probability, label)
                        for probability, label in zip(candidate_probabilities, labels)
                    ]
                    comparator_values = [
                        _binary_log_loss(probability, label)
                        for probability, label in zip(comparator_probabilities, labels)
                    ]
                interval = _paired_date_bootstrap(
                    dates=dates,
                    differences=[
                        candidate - baseline
                        for candidate, baseline in zip(candidate_values, comparator_values)
                    ],
                )
                floor = -0.01 * float(line_metrics[comparator][score])
                passed = interval["point"] <= floor and interval["upper_95"] <= floor
                line_result[score] = {
                    **interval, "required_below": floor, "passed": passed
                }
                all_material = all_material and passed
            calibration_passed = all(
                float(line_metrics["candidate"][name]) <= float(line_metrics[comparator][name])
                for name in (
                    "absolute_bias", "equal_frequency_decile_ece",
                    "candidate_fixed_top_decile_absolute_bias",
                )
            )
            candidate_auc = line_metrics["candidate"]["auc"]
            comparator_auc = line_metrics[comparator]["auc"]
            discrimination_passed = (
                comparator_auc is None and candidate_auc is None
            ) or (
                comparator_auc is not None and candidate_auc is not None
                and float(candidate_auc) >= float(comparator_auc)
            )
            line_result["calibration_passed"] = calibration_passed
            line_result["auc_passed"] = discrimination_passed
            all_calibration = all_calibration and calibration_passed
            all_discrimination = all_discrimination and discrimination_passed
            comparator_result["binary_lines"][line_name] = line_result
        comparisons[comparator] = comparator_result
    missing_market = not market_implied_available
    promotion = (
        gradeable_fraction >= 0.95 and all_material and all_calibration
        and all_discrimination and not missing_market and all_date_blocks_complete
    )
    return {
        "schema_version": "shared-pa-forward-market-adjudication-v1",
        "market": market,
        "planned_rows": total_planned,
        "gradeable_rows": len(rows),
        "gradeable_fraction": gradeable_fraction,
        "explicit_dispositions": dict(explicit_dispositions),
        "date_dispositions": dict(date_dispositions),
        "all_date_blocks_complete": all_date_blocks_complete,
        "metrics": metrics,
        "paired_proper_score_comparisons": comparisons,
        "market_implied_available": market_implied_available,
        "promotion_eligible": promotion,
        "promotion_blockers": [
            reason for condition, reason in (
                (gradeable_fraction < 0.95, "coverage_below_locked_floor"),
                (not all_material, "material_proper_score_gate_failed"),
                (not all_calibration, "calibration_nonregression_gate_failed"),
                (not all_discrimination, "discrimination_nonregression_gate_failed"),
                (missing_market, "verified_market_comparator_unavailable"),
                (not all_date_blocks_complete, "prospective_date_block_incomplete"),
            ) if condition
        ],
        "research_only": True,
        "betting_authorized": False,
    }
