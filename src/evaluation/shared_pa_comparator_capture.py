"""Fail-closed T-4 comparator capture for shared batter PA research.

This module has no network client and never reads outcomes.  It validates
already-retained pregame bytes, binds each consumed probability to hard MLB
identity, and makes missing/contradictory evidence explicit.  It cannot turn a
one-sided quote into a de-vigged market probability and cannot substitute an
eventual starter for a missing probable-pitcher receipt.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import unicodedata
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from src.evaluation.forward_pitcher_context import ForwardPitcherContext
from src.evaluation.shadow_capture_plan import CaptureTarget
from src.evaluation.shared_pa_forward_evidence import validate_player_snapshot


SCHEMA_VERSION = "shared-pa-comparator-capture-contract-v1"
CONTRACT_SHA256 = "0db198cc31b127b80d6ac84f2a944722cb649b068552bb6c069cada635fe37f0"
CONTRACT_CANONICAL_SHA256 = "89df9661bae3b98708308de68883713ffae33a5bbeff5ba93ef34647677c7ef4"
LOCKED_AT_UTC = "2026-07-23T00:36:00Z"
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
_SHA = re.compile(r"^[0-9a-f]{64}$")
_WS = re.compile(r"\s+")
MARKET_SPEC = {
    "hits": ("batter_hits", "hits", (0.5, 1.5)),
    "home_runs_over_0_5": ("batter_home_runs", "home_runs", (0.5,)),
    "total_bases": (
        "batter_total_bases", "total_bases", (0.5, 1.5, 2.5, 3.5, 4.5, 5.5)
    ),
}


class SharedPAComparatorCaptureError(ValueError):
    """Comparator evidence is incomplete, contradictory, late, or unsafe."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise SharedPAComparatorCaptureError(f"duplicate JSON key: {key}")
        out[key] = value
    return out


def _json_bytes(raw: bytes, label: str) -> Mapping[str, Any]:
    if not isinstance(raw, bytes) or not raw:
        raise SharedPAComparatorCaptureError(f"{label} bytes are missing")
    try:
        value = json.loads(raw, object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SharedPAComparatorCaptureError(f"{label} is not unique-key UTF-8 JSON") from exc
    if not isinstance(value, Mapping):
        raise SharedPAComparatorCaptureError(f"{label} root must be an object")
    return value


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise SharedPAComparatorCaptureError(f"{label} must be a lowercase SHA-256")
    return value


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise SharedPAComparatorCaptureError(f"{label} must be a positive integer")
    return value


def _utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise SharedPAComparatorCaptureError(f"{label} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SharedPAComparatorCaptureError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SharedPAComparatorCaptureError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _name(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value)).casefold().strip()
    return _WS.sub(" ", text)


def _line_key(line: float) -> str:
    return f"over_{line:.1f}"


def _probability(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SharedPAComparatorCaptureError(f"{label} must be numeric")
    out = float(value)
    if not math.isfinite(out) or not 0.0 <= out <= 1.0:
        raise SharedPAComparatorCaptureError(f"{label} must be within [0, 1]")
    return out


def _american(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value == 0:
        raise SharedPAComparatorCaptureError(f"{label} must be nonzero integer American odds")
    return value


def _raw_implied(odds: int) -> float:
    return (-odds / (-odds + 100.0)) if odds < 0 else (100.0 / (odds + 100.0))


def _require_target(target: CaptureTarget) -> datetime:
    if not isinstance(target, CaptureTarget) or target.entry_hours != 4:
        raise SharedPAComparatorCaptureError("comparator target must be an exact T-4 capture target")
    if target.official_game_date.startswith("2026-05-"):
        raise SharedPAComparatorCaptureError("May 2026 is sealed")
    return _utc(target.entry_target_at_utc, "target horizon")


def _bound_loaded_contract(value: Mapping[str, Any]) -> tuple[Mapping[str, Any], str, datetime]:
    contract = value.get("contract")
    contract_sha = _digest(value.get("contract_sha256"), "contract_sha256")
    if (
        not isinstance(contract, Mapping)
        or contract_sha != CONTRACT_SHA256
        or contract.get("schema_version") != SCHEMA_VERSION
        or contract.get("status") != "LOCKED_RESEARCH_ONLY_FAIL_CLOSED"
        or contract.get("contract_locked_at_utc") != LOCKED_AT_UTC
        or contract.get("decision_horizon") != "T-4h"
        or hashlib.sha256(_canonical(contract)).hexdigest() != CONTRACT_CANONICAL_SHA256
        or contract.get("markets")
        != {
            name: {
                "provider_market_key": provider_key,
                "archive_category": category,
                "lines": list(lines),
            }
            for name, (provider_key, category, lines) in MARKET_SPEC.items()
        }
    ):
        raise SharedPAComparatorCaptureError("loaded comparator contract was mutated")
    return contract, contract_sha, _utc(LOCKED_AT_UTC, "contract lock")


def load_comparator_contract(*, root: str | Path, path: str | Path) -> dict[str, Any]:
    repository = Path(root).resolve()
    source = Path(path).resolve()
    try:
        source.relative_to(repository)
        raw = source.read_bytes()
    except (OSError, ValueError) as exc:
        raise SharedPAComparatorCaptureError("comparator contract is unreadable or outside the repository") from exc
    contract = _json_bytes(raw, "comparator contract")
    if hashlib.sha256(raw).hexdigest() != CONTRACT_SHA256:
        raise SharedPAComparatorCaptureError("comparator contract bytes changed")
    if set(contract) != {
        "schema_version", "status", "contract_locked_at_utc", "decision_horizon",
        "provider", "markets", "frozen_formula", "valid_market_probability",
        "terminal_dispositions", "protected_boundaries",
    }:
        raise SharedPAComparatorCaptureError("comparator contract root surface changed")
    if contract.get("schema_version") != SCHEMA_VERSION or contract.get("status") != "LOCKED_RESEARCH_ONLY_FAIL_CLOSED":
        raise SharedPAComparatorCaptureError("comparator contract identity changed")
    if contract.get("decision_horizon") != "T-4h":
        raise SharedPAComparatorCaptureError("comparator horizon changed")
    if contract.get("contract_locked_at_utc") != LOCKED_AT_UTC:
        raise SharedPAComparatorCaptureError("comparator lock timestamp changed")
    _utc(contract.get("contract_locked_at_utc"), "contract_locked_at_utc")
    provider = contract.get("provider")
    if not isinstance(provider, Mapping) or provider != {
        "source_name": "the_odds_api", "sport_key": "baseball_mlb",
        "bookmaker_key": "draftkings", "maximum_event_start_delta_seconds": 60,
        "raw_bytes_and_receipt_hash_required": True, "selection_ids_required": True,
    }:
        raise SharedPAComparatorCaptureError("provider contract changed")
    markets = contract.get("markets")
    expected_markets = {
        name: {"provider_market_key": provider_key, "archive_category": category, "lines": list(lines)}
        for name, (provider_key, category, lines) in MARKET_SPEC.items()
    }
    if markets != expected_markets:
        raise SharedPAComparatorCaptureError("market separation or line contract changed")
    frozen = contract.get("frozen_formula")
    if not isinstance(frozen, Mapping) or set(frozen) != {
        "config_path", "config_file_sha256", "effective_config_sha256", "model_version",
        "corrections_active", "total_bases_classification",
        "total_bases_extension_constructor",
        "total_bases_extension_effective_config_sha256",
        "total_bases_extension_model_version",
        "hard_opposing_pitcher_id_required", "receipt_proven_probable_starter_match_required",
        "missing_or_conflicting_pitcher_disposition",
    } or (
        frozen.get("config_path"), frozen.get("config_file_sha256"),
        frozen.get("effective_config_sha256"), frozen.get("model_version"),
        frozen.get("corrections_active"), frozen.get("hard_opposing_pitcher_id_required"),
        frozen.get("receipt_proven_probable_starter_match_required"),
    ) != (
        "config/config.kbb.json", "ff65d440b7571aa490300b26b1cf8a6271c5979e564c1a74d1d0988a0b743bad",
        "3a62fc507956752086919b757ae683e60f9df8fb2143f83292ac61f07830a60c",
        "8c7eed9bb0c3", False, True, True,
    ):
        raise SharedPAComparatorCaptureError("frozen formula binding changed")
    if (
        frozen.get("total_bases_classification")
        != "deterministic additional output from the unchanged frozen game simulator; not a prior production market promotion"
        or frozen.get("missing_or_conflicting_pitcher_disposition")
        != "unassessable_no_substitution"
        or frozen.get("total_bases_extension_constructor")
        != "src.models.total_bases_contract.shared_pa_forward_candidate_config"
        or frozen.get("total_bases_extension_effective_config_sha256")
        != "2563cfc1b8ebd71e787d8adeb0c89849c2732c92b6d7368ba7b149885fdc2abc"
        or frozen.get("total_bases_extension_model_version") != "de116c7c2f33"
    ):
        raise SharedPAComparatorCaptureError("frozen comparator classification changed")
    config_path = repository / str(frozen["config_path"])
    try:
        actual_config_sha = hashlib.sha256(config_path.read_bytes()).hexdigest()
    except OSError as exc:
        raise SharedPAComparatorCaptureError("frozen config is unreadable") from exc
    if actual_config_sha != frozen["config_file_sha256"]:
        raise SharedPAComparatorCaptureError("frozen config bytes changed")
    market_probability = contract.get("valid_market_probability")
    if not isinstance(market_probability, Mapping) or set(market_probability) != {
        "same_event_book_market_player_line_required", "unique_over_and_under_required",
        "unique_selection_ids_required", "same_market_timestamp_required",
        "receipt_and_market_timestamp_no_later_than_horizon", "american_implied_formula",
        "devig_formula", "one_sided_disposition", "cross_book_pairing_forbidden",
        "executable_or_fill_claim",
    } or any(
        market_probability.get(key) is not True for key in (
            "same_event_book_market_player_line_required", "unique_over_and_under_required",
            "unique_selection_ids_required", "same_market_timestamp_required",
            "receipt_and_market_timestamp_no_later_than_horizon", "cross_book_pairing_forbidden",
        )
    ) or market_probability.get("executable_or_fill_claim") is not False:
        raise SharedPAComparatorCaptureError("valid-market boundary weakened")
    if (
        market_probability.get("american_implied_formula")
        != "negative: -odds/(-odds+100); positive: 100/(odds+100)"
        or market_probability.get("devig_formula") != "over_raw/(over_raw+under_raw)"
        or market_probability.get("one_sided_disposition") != "unassessable_raw_retained"
    ):
        raise SharedPAComparatorCaptureError("market probability formula changed")
    if contract.get("terminal_dispositions") != [
        "resolved", "missing_market", "missing_line", "one_sided", "ambiguous_group",
        "invalid_selection", "unmatched_player", "pitcher_receipt_unavailable",
        "pitcher_identity_mismatch", "frozen_probability_missing",
        "source_or_integrity_failure",
    ]:
        raise SharedPAComparatorCaptureError("terminal disposition contract changed")
    protected = contract.get("protected_boundaries")
    if not isinstance(protected, Mapping) or set(protected) != {
        "may_2026_fetch_read_parse_write_forbidden", "outcomes_forbidden",
        "missed_receipts_not_backfillable", "historical_prices_not_executable",
        "market_pooling_forbidden", "operational_smoke_separate",
        "production_probability_formula_changed", "research_only", "betting_authorized",
    } or any(
        protected.get(key) is not True for key in (
            "may_2026_fetch_read_parse_write_forbidden", "outcomes_forbidden",
            "missed_receipts_not_backfillable", "historical_prices_not_executable",
            "market_pooling_forbidden", "operational_smoke_separate", "research_only",
        )
    ) or protected.get("production_probability_formula_changed") is not False or protected.get("betting_authorized") is not False:
        raise SharedPAComparatorCaptureError("protected comparator boundary weakened")
    return {"contract": dict(contract), "contract_sha256": CONTRACT_SHA256}


def _player_identity(player_snapshot: Mapping[str, Any], target: CaptureTarget) -> tuple[str, int, int]:
    validate_player_snapshot(player_snapshot)
    if player_snapshot.get("target_id") != target.target_id or player_snapshot.get("mlb_game_pk") != target.mlb_game_pk:
        raise SharedPAComparatorCaptureError("player snapshot differs from comparator target")
    side = player_snapshot.get("side")
    player_id = _positive_int(player_snapshot.get("player_id"), "player_id")
    team_id = _positive_int(
        player_snapshot.get("home_team_id" if side == "home" else "away_team_id"), "team_id"
    )
    return str(side), team_id, player_id


def _archive_projection_index(
    archive: Mapping[str, Any], *, target: CaptureTarget, horizon: datetime,
    expected_categories: list[str], expected_model_version: str,
    expected_config_sha256: str, expected_include_pitchers: bool,
) -> tuple[dict[tuple[int, str], Mapping[str, Any]], datetime, Mapping[str, Any]]:
    if archive.get("game_date") != target.official_game_date:
        raise SharedPAComparatorCaptureError("frozen archive date differs from target")
    provenance = archive.get("prediction_provenance")
    if not isinstance(provenance, Mapping) or provenance.get("schema_version") != "daily-prediction-provenance-v1":
        raise SharedPAComparatorCaptureError("frozen archive lacks decision-time provenance")
    captured = _utc(provenance.get("captured_at_utc"), "archive captured_at_utc")
    if captured > horizon:
        raise SharedPAComparatorCaptureError("frozen archive was generated after T-4")
    if provenance.get("model_version") != expected_model_version or provenance.get("effective_config_sha256") != expected_config_sha256:
        raise SharedPAComparatorCaptureError("frozen archive model/config identity changed")
    run_options = provenance.get("run_options")
    if not isinstance(run_options, Mapping) or set(run_options) != {
        "game_date", "hitter_categories", "include_pitchers", "corrections_active",
        "edges_requested", "use_projected_lineups",
    } or run_options != {
        "game_date": target.official_game_date,
        "hitter_categories": expected_categories,
        "include_pitchers": expected_include_pitchers,
        "corrections_active": False,
        "edges_requested": False,
        "use_projected_lineups": True,
    }:
        raise SharedPAComparatorCaptureError("frozen archive run options are incomplete or changed")
    code = provenance.get("code")
    if not isinstance(code, Mapping) or code.get("status") != "available" or code.get("tracked_patch_sha256") != EMPTY_SHA256 or code.get("untracked") != []:
        raise SharedPAComparatorCaptureError("frozen archive code checkout was not clean")
    _digest(code.get("snapshot_sha256"), "code.snapshot_sha256")
    head = str(code.get("head", ""))
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        raise SharedPAComparatorCaptureError("frozen archive lacks exact source commit")
    rows = archive.get("hitter_projections")
    if not isinstance(rows, list):
        raise SharedPAComparatorCaptureError("frozen archive hitter projections are missing")
    index: dict[tuple[int, str], Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping) or row.get("mlb_game_pk") != target.mlb_game_pk:
            continue
        player_id = _positive_int(row.get("player_id"), "archive player_id")
        category = str(row.get("category", ""))
        key = (player_id, category)
        if key in index:
            raise SharedPAComparatorCaptureError(f"frozen archive repeats projection {key}")
        index[key] = row
    return index, captured, provenance


def _tail(row: Mapping[str, Any], line: float) -> float:
    simulation = row.get("simulation")
    values = simulation.get("p_ge_threshold") if isinstance(simulation, Mapping) else None
    if not isinstance(values, Mapping):
        raise SharedPAComparatorCaptureError("frozen simulation tail surface is missing")
    threshold = int(math.floor(line)) + 1
    matches = [value for key, value in values.items() if str(key) in {str(threshold), f"{threshold}.0"}]
    if len(matches) != 1:
        raise SharedPAComparatorCaptureError(f"frozen simulation lacks exact threshold {threshold}")
    return _probability(matches[0], f"frozen P(actual>={threshold})")


def _consumed_sha(source_id: str, hard_key: str, horizon: str, probability: Mapping[str, float]) -> str:
    return hashlib.sha256(_canonical({
        "source_id": source_id, "prediction_hard_key": hard_key,
        "target_horizon_utc": horizon, "probability": dict(probability),
    })).hexdigest()


def build_frozen_comparator(
    *, archive_bytes: bytes, total_bases_archive_bytes: bytes,
    target: CaptureTarget, player_snapshot: Mapping[str, Any],
    pitcher_context_record: Mapping[str, Any], loaded_contract: Mapping[str, Any],
) -> dict[str, Any]:
    """Build all three frozen binary comparator surfaces for one batter."""
    horizon = _require_target(target)
    contract, contract_sha, locked_at = _bound_loaded_contract(loaded_contract)
    side, team_id, player_id = _player_identity(player_snapshot, target)
    archive = _json_bytes(archive_bytes, "frozen archive")
    frozen = contract["frozen_formula"]
    index, captured, provenance = _archive_projection_index(
        archive, target=target, horizon=horizon,
        expected_categories=["hits", "hrr", "home_runs"],
        expected_model_version=str(frozen["model_version"]),
        expected_config_sha256=str(frozen["effective_config_sha256"]),
        expected_include_pitchers=True,
    )
    if locked_at > captured:
        raise SharedPAComparatorCaptureError("frozen comparator contract was locked after prediction")
    total_bases_archive = _json_bytes(total_bases_archive_bytes, "Total Bases extension archive")
    total_bases_index, total_bases_captured, total_bases_provenance = _archive_projection_index(
        total_bases_archive, target=target, horizon=horizon,
        expected_categories=["total_bases"],
        expected_model_version=str(frozen["total_bases_extension_model_version"]),
        expected_config_sha256=str(frozen["total_bases_extension_effective_config_sha256"]),
        expected_include_pitchers=False,
    )
    if locked_at > total_bases_captured:
        raise SharedPAComparatorCaptureError("Total Bases extension was generated before its contract lock")
    context = ForwardPitcherContext.from_mapping(pitcher_context_record).bind_target(target)
    expected = context.away_probable_pitcher if side == "home" else context.home_probable_pitcher
    artifact_sha = hashlib.sha256(archive_bytes).hexdigest()
    total_bases_artifact_sha = hashlib.sha256(total_bases_archive_bytes).hexdigest()
    hard_prefix = f"{target.mlb_game_pk}:{side}:{team_id}:{player_id}"
    markets: dict[str, Any] = {}
    names: set[str] = set()
    for market, (_, category, lines) in MARKET_SPEC.items():
        source_index = total_bases_index if market == "total_bases" else index
        source_captured = total_bases_captured if market == "total_bases" else captured
        source_artifact_sha = total_bases_artifact_sha if market == "total_bases" else artifact_sha
        row = source_index.get((player_id, category))
        if row is None:
            markets[market] = {"terminal_state": "frozen_probability_missing"}
            continue
        player_name = str(row.get("player_name", "")).strip()
        if not player_name:
            raise SharedPAComparatorCaptureError("frozen archive player name is blank")
        names.add(player_name)
        if expected.status != "resolved" or expected.player_id is None:
            markets[market] = {"terminal_state": "pitcher_receipt_unavailable"}
            continue
        opposing_pitcher_id = row.get("opposing_pitcher_id")
        if opposing_pitcher_id != expected.player_id:
            markets[market] = {"terminal_state": "pitcher_identity_mismatch"}
            continue
        probabilities = {_line_key(line): _tail(row, line) for line in lines}
        hard_key = f"{hard_prefix}:{market}"
        markets[market] = {
            "terminal_state": "resolved",
            "binary_probabilities": probabilities,
            "prediction_provenance": {
                "source_id": "frozen_production",
                "evidence_mode": "prospective_pre_horizon",
                "input_observed_at_utc": _stamp(source_captured),
                "prediction_generated_at_utc": _stamp(source_captured),
                "model_or_contract_locked_at_utc": contract["contract_locked_at_utc"],
                "artifact_sha256": source_artifact_sha,
                "model_or_contract_sha256": contract_sha,
                "prediction_hard_key": hard_key,
                "target_horizon_utc": target.entry_target_at_utc,
                "consumed_probability_sha256": _consumed_sha(
                    "frozen_production", hard_key, target.entry_target_at_utc, probabilities
                ),
            },
        }
    if len(names) > 1:
        raise SharedPAComparatorCaptureError("frozen categories disagree on player identity")
    states = {value["terminal_state"] for value in markets.values()}
    terminal_state = states.pop() if len(states) == 1 else "partial"
    return {
        "terminal_state": terminal_state,
        "player_name": next(iter(names), ""),
        "player_id": player_id, "side": side, "team_id": team_id,
        "target_id": target.target_id,
        "official_game_date": target.official_game_date,
        "official_start_utc": target.official_start_time_utc,
        "target_horizon_utc": target.entry_target_at_utc,
        "opposing_pitcher_id": expected.player_id if expected.status == "resolved" else None,
        "pitcher_context_sha256": context.context_sha256,
        "archive_code": {
            "frozen_production": {
                "head": provenance["code"]["head"],
                "snapshot_sha256": provenance["code"]["snapshot_sha256"],
            },
            "total_bases_extension": {
                "head": total_bases_provenance["code"]["head"],
                "snapshot_sha256": total_bases_provenance["code"]["snapshot_sha256"],
            },
        },
        "markets": markets,
        "research_only": True,
        "betting_authorized": False,
    }


def _market_groups(
    event: Mapping[str, Any], *, horizon: datetime, received: datetime,
) -> dict[tuple[str, str, float], list[Mapping[str, Any]]]:
    bookmakers = event.get("bookmakers")
    if not isinstance(bookmakers, list):
        raise SharedPAComparatorCaptureError("provider bookmakers are missing")
    books = [book for book in bookmakers if isinstance(book, Mapping) and book.get("key") == "draftkings"]
    if len(books) != 1:
        raise SharedPAComparatorCaptureError("provider has no unique DraftKings bookmaker")
    markets = books[0].get("markets")
    if not isinstance(markets, list):
        raise SharedPAComparatorCaptureError("provider market list is missing")
    allowed = {spec[0] for spec in MARKET_SPEC.values()}
    groups: dict[tuple[str, str, float], list[Mapping[str, Any]]] = {}
    for market in markets:
        if not isinstance(market, Mapping) or market.get("key") not in allowed:
            continue
        updated = _utc(market.get("last_update"), "provider market last_update")
        if updated > horizon:
            raise SharedPAComparatorCaptureError("provider market timestamp is after T-4")
        if updated > received:
            raise SharedPAComparatorCaptureError("provider market timestamp is after its receipt")
        outcomes = market.get("outcomes")
        if not isinstance(outcomes, list):
            raise SharedPAComparatorCaptureError("provider outcomes are missing")
        for outcome in outcomes:
            if not isinstance(outcome, Mapping):
                raise SharedPAComparatorCaptureError("provider outcome is malformed")
            description = _name(outcome.get("description"))
            point = outcome.get("point")
            if not description or isinstance(point, bool) or not isinstance(point, (int, float)) or not math.isfinite(float(point)):
                raise SharedPAComparatorCaptureError("provider player/line identity is malformed")
            retained = dict(outcome)
            retained["_market_last_update_utc"] = _stamp(updated)
            groups.setdefault((str(market["key"]), description, float(point)), []).append(retained)
    return groups


def _event_identity(
    raw: bytes, *, target: CaptureTarget, context: ForwardPitcherContext,
    max_delta: int,
) -> tuple[Mapping[str, Any], str]:
    identity = _json_bytes(raw, "provider game identity")
    required = {
        "schema_version", "resolution_method", "source_name", "source_event_id",
        "source_event_start_time_utc", "source_home_team", "source_away_team",
        "mlb_game_pk", "official_game_date", "official_start_time_utc",
        "official_home_team", "official_away_team", "team_pair_event_count",
        "team_pair_chronological_ordinal", "start_delta_seconds",
        "max_abs_start_delta_seconds", "time_tolerance_used_for_selection",
        "start_delta_bound_used_for_rejection", "fuzzy_matching_used",
    }
    if set(identity) != required:
        raise SharedPAComparatorCaptureError("provider game identity surface changed")
    if (
        identity.get("schema_version") != "shadow-live-game-identity-v2"
        or identity.get("resolution_method")
        != "exact_nfkc_casefold_teams_equal_cardinality_chronological_ordinal"
        or identity.get("source_name") != "the_odds_api"
        or identity.get("mlb_game_pk") != target.mlb_game_pk
        or identity.get("official_game_date") != target.official_game_date
        or _utc(identity.get("official_start_time_utc"), "identity official start")
        != _utc(target.official_start_time_utc, "target official start")
        or _name(identity.get("official_home_team")) != _name(context.home_team)
        or _name(identity.get("official_away_team")) != _name(context.away_team)
        or _name(identity.get("source_home_team")) != _name(context.home_team)
        or _name(identity.get("source_away_team")) != _name(context.away_team)
        or identity.get("max_abs_start_delta_seconds") != max_delta
        or identity.get("time_tolerance_used_for_selection") is not False
        or identity.get("start_delta_bound_used_for_rejection") is not True
        or identity.get("fuzzy_matching_used") is not False
    ):
        raise SharedPAComparatorCaptureError("provider game identity binding changed")
    count = _positive_int(identity.get("team_pair_event_count"), "team_pair_event_count")
    ordinal = _positive_int(
        identity.get("team_pair_chronological_ordinal"), "team_pair_chronological_ordinal"
    )
    if ordinal > count:
        raise SharedPAComparatorCaptureError("provider chronological ordinal exceeds cardinality")
    source_start = _utc(identity.get("source_event_start_time_utc"), "identity source start")
    official_start = _utc(target.official_start_time_utc, "target official start")
    actual_delta = int((source_start - official_start).total_seconds())
    if identity.get("start_delta_seconds") != actual_delta or abs(actual_delta) > max_delta:
        raise SharedPAComparatorCaptureError("provider identity start delta changed")
    if not str(identity.get("source_event_id", "")).strip():
        raise SharedPAComparatorCaptureError("provider identity event id is blank")
    return identity, hashlib.sha256(raw).hexdigest()


def build_market_comparators(
    *, provider_bytes: bytes, received_at_utc: str, game_identity_bytes: bytes,
    target: CaptureTarget, frozen_comparators: Sequence[Mapping[str, Any]],
    pitcher_context_record: Mapping[str, Any], loaded_contract: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Create explicit per-player/market market-comparator dispositions."""
    horizon = _require_target(target)
    received = _utc(received_at_utc, "provider receipt")
    if received > horizon:
        raise SharedPAComparatorCaptureError("provider response arrived after T-4")
    contract, contract_sha, locked_at = _bound_loaded_contract(loaded_contract)
    if locked_at > received:
        raise SharedPAComparatorCaptureError("market comparator contract was locked after capture")
    context = ForwardPitcherContext.from_mapping(pitcher_context_record).bind_target(target)
    max_delta = int(contract["provider"]["maximum_event_start_delta_seconds"])
    identity, identity_sha = _event_identity(
        game_identity_bytes, target=target, context=context, max_delta=max_delta
    )
    provider_event_id = str(identity["source_event_id"])
    event = _json_bytes(provider_bytes, "provider event odds")
    if event.get("id") != provider_event_id or event.get("sport_key") != "baseball_mlb":
        raise SharedPAComparatorCaptureError("provider event identity changed")
    if _name(event.get("home_team")) != _name(context.home_team) or _name(event.get("away_team")) != _name(context.away_team):
        raise SharedPAComparatorCaptureError("provider event teams differ from receipt-bound MLB identity")
    commence = _utc(event.get("commence_time"), "provider commence_time")
    official_start = _utc(target.official_start_time_utc, "official start")
    if _stamp(commence) != _stamp(_utc(identity["source_event_start_time_utc"], "identity source start")):
        raise SharedPAComparatorCaptureError("provider event start differs from its identity artifact")
    if abs((commence - official_start).total_seconds()) > max_delta:
        raise SharedPAComparatorCaptureError("provider event start exceeds the locked rejection bound")
    groups = _market_groups(event, horizon=horizon, received=received)
    artifact_sha = hashlib.sha256(provider_bytes).hexdigest()
    name_index: dict[str, Mapping[str, Any]] = {}
    for frozen in frozen_comparators:
        normalized = _name(frozen.get("player_name"))
        if not normalized or normalized in name_index:
            raise SharedPAComparatorCaptureError("frozen player names are blank or ambiguous within the game")
        name_index[normalized] = frozen
    records: list[dict[str, Any]] = []
    for normalized_name, frozen in sorted(name_index.items()):
        for market, (provider_key, _, lines) in MARKET_SPEC.items():
            hard_key = (
                f"{target.mlb_game_pk}:{frozen['side']}:{frozen['team_id']}:"
                f"{frozen['player_id']}:{market}"
            )
            probabilities: dict[str, float] = {}
            disposition = "resolved"
            selection_evidence: dict[str, Any] = {}
            market_seen = any(key[0] == provider_key for key in groups)
            for line in lines:
                rows = groups.get((provider_key, normalized_name, line), [])
                if not rows:
                    disposition = "missing_line" if market_seen else "missing_market"
                    break
                over = [row for row in rows if row.get("name") == "Over"]
                under = [row for row in rows if row.get("name") == "Under"]
                if len(over) != 1 or len(under) != 1:
                    disposition = "one_sided" if len(over) + len(under) == 1 else "ambiguous_group"
                    break
                sids = [str(over[0].get("sid", "")).strip(), str(under[0].get("sid", "")).strip()]
                if any(not sid for sid in sids) or len(set(sids)) != 2:
                    disposition = "invalid_selection"
                    break
                timestamps = {
                    str(over[0].get("_market_last_update_utc", "")),
                    str(under[0].get("_market_last_update_utc", "")),
                }
                if len(timestamps) != 1 or "" in timestamps:
                    disposition = "ambiguous_group"
                    break
                over_odds = _american(over[0].get("price"), "over price")
                under_odds = _american(under[0].get("price"), "under price")
                over_raw = _raw_implied(over_odds)
                under_raw = _raw_implied(under_odds)
                probabilities[_line_key(line)] = over_raw / (over_raw + under_raw)
                selection_evidence[_line_key(line)] = {
                    "over_sid": sids[0], "under_sid": sids[1],
                    "over_odds_american": over_odds, "under_odds_american": under_odds,
                    "market_last_update_utc": timestamps.pop(),
                }
            record: dict[str, Any] = {
                "terminal_state": disposition, "market": market,
                "mlb_game_pk": target.mlb_game_pk, "side": frozen["side"],
                "team_id": frozen["team_id"], "player_id": frozen["player_id"],
                "target_id": target.target_id,
                "official_game_date": target.official_game_date,
                "official_start_utc": target.official_start_time_utc,
                "target_horizon_utc": target.entry_target_at_utc,
                "provider_event_id": provider_event_id, "provider_artifact_sha256": artifact_sha,
                "game_identity_artifact_sha256": identity_sha,
                "received_at_utc": _stamp(received), "selection_evidence": selection_evidence,
                "binary_probabilities": probabilities if disposition == "resolved" else {},
                "research_only": True, "executable_price_claimed": False,
                "betting_authorized": False,
            }
            if disposition == "resolved":
                record["prediction_provenance"] = {
                    "source_id": "market_implied", "evidence_mode": "prospective_pre_horizon",
                    "input_observed_at_utc": _stamp(received),
                    "prediction_generated_at_utc": _stamp(received),
                    "model_or_contract_locked_at_utc": contract["contract_locked_at_utc"],
                    "artifact_sha256": artifact_sha,
                    "model_or_contract_sha256": contract_sha,
                    "prediction_hard_key": hard_key,
                    "target_horizon_utc": target.entry_target_at_utc,
                    "consumed_probability_sha256": _consumed_sha(
                        "market_implied", hard_key, target.entry_target_at_utc, probabilities
                    ),
                }
            records.append(record)
    provider_to_market = {
        provider_key: market for market, (provider_key, _, _) in MARKET_SPEC.items()
    }
    for (provider_key, normalized_name, line), rows in sorted(groups.items()):
        if normalized_name in name_index:
            continue
        records.append({
            "terminal_state": "unmatched_player",
            "market": provider_to_market[provider_key],
            "mlb_game_pk": target.mlb_game_pk,
            "side": None,
            "team_id": None,
            "player_id": None,
            "target_id": target.target_id,
            "official_game_date": target.official_game_date,
            "official_start_utc": target.official_start_time_utc,
            "target_horizon_utc": target.entry_target_at_utc,
            "provider_event_id": provider_event_id,
            "provider_artifact_sha256": artifact_sha,
            "game_identity_artifact_sha256": identity_sha,
            "received_at_utc": _stamp(received),
            "provider_description_sha256": hashlib.sha256(
                normalized_name.encode("utf-8")
            ).hexdigest(),
            "line": line,
            "candidate_selection_count": len(rows),
            "selection_evidence": {},
            "binary_probabilities": {},
            "research_only": True,
            "executable_price_claimed": False,
            "betting_authorized": False,
        })
    return records


def publish_comparator_record(
    *, record_type: str, record: Mapping[str, Any], path: str | Path,
) -> bool:
    """Publish one canonical immutable record; exact retry verifies only."""
    if record_type not in {"frozen_bundle", "market_record"}:
        raise SharedPAComparatorCaptureError("comparator record type is invalid")
    if not isinstance(record, Mapping):
        raise SharedPAComparatorCaptureError("comparator record must be an object")
    official_date = record.get("official_game_date")
    try:
        parsed_date = date.fromisoformat(str(official_date))
    except ValueError as exc:
        raise SharedPAComparatorCaptureError("comparator record date is invalid") from exc
    if parsed_date.year == 2026 and parsed_date.month == 5:
        raise SharedPAComparatorCaptureError("May 2026 is sealed")
    if record.get("research_only") is not True or record.get("betting_authorized") is not False:
        raise SharedPAComparatorCaptureError("comparator record is not research-only")
    body = dict(record)
    envelope = {
        "schema_version": "shared-pa-comparator-record-v1",
        "record_type": record_type,
        "record_sha256": hashlib.sha256(_canonical(body)).hexdigest(),
        "record": body,
    }
    payload = json.dumps(envelope, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        if output.read_bytes() != payload:
            raise SharedPAComparatorCaptureError("conflicting retry cannot overwrite comparator evidence")
        return False
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, output)
        except FileExistsError:
            if output.read_bytes() != payload:
                raise SharedPAComparatorCaptureError(
                    "concurrent conflicting retry cannot overwrite comparator evidence"
                )
            return False
        return True
    finally:
        temporary.unlink(missing_ok=True)
