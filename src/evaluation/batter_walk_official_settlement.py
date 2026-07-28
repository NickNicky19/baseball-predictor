"""Replay-only official settlement boundary for batter-walk research.

This module has no transport.  After the sealed opening instant, it accepts
retained official response bytes and replays the declared request, parser, game,
team, side, and player identities into one immutable disposition record.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import parse_qs, urlsplit

from src.evaluation.batter_walk_forward_control import (
    BatterWalkForwardControlError,
    LoadedBatterWalkContract,
    _bound_path,
    canonical_bytes,
    replay_contract,
    sha256_value,
    validate_player_output,
    validate_side_terminal,
)
from src.evaluation.shadow_capture_plan import ShadowCapturePlan


class BatterWalkSettlementError(ValueError):
    """The sealed settlement contract or retained official evidence is invalid."""


SETTLEMENT_CONTRACT_SCHEMA = "batter-walk-official-settlement-contract-v1"
OPENING_GATE_SCHEMA = "batter-walk-official-opening-gate-v1"
TRANSPORT_RECEIPT_SCHEMA = "batter-walk-official-transport-receipt-v1"
SETTLEMENT_SCHEMA = "batter-walk-official-settlement-record-v1"
SOURCE_ID = "official_mlb_statsapi_live_feed_v1"
PARSER_ID = "batter_walk_official_raw_parser_v1"
STARTER_ORDERS = {str(value) for value in range(100, 1000, 100)}
DISPOSITIONS = {
    "untrusted_evidence_not_gradeable",
    "retained_coverage_failure_not_scored",
    "final_status_missing_not_scored",
    "support_breach_model_invalid_not_scored",
    "malformed_official_projection_not_scored",
}
UNTRUSTED_EVIDENCE_AUTHORITY = "UNBOUND_NO_TRUSTED_APPEND_ONLY_SETTLEMENT_COLLECTOR"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_FIELDS = (
    "gamePk,gameData,datetime,officialDate,status,abstractGameState,"
    "liveData,boxscore,teams,away,home,team,id,players,person,battingOrder,"
    "stats,batting,plateAppearances,baseOnBalls"
)
_RECORD_FIELDS = {
    "schema_version", "research_only", "promotion_eligible", "betting_authorized",
    "contract_sha256", "settlement_contract_sha256", "parser_id",
    "opening_gate_sha256", "transport_receipt_sha256", "raw_payload_sha256",
    "market_hard_player_key", "player_output_sha256", "disposition",
    "official_projection", "evidence_authority_state", "record_sha256",
}


@dataclass(frozen=True)
class RawOfficialSettlementResponse:
    body: bytes
    transport_receipt_raw: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.body, bytes) or not self.body:
            raise BatterWalkSettlementError("official settlement bytes are missing")
        if not isinstance(self.transport_receipt_raw, bytes) or not self.transport_receipt_raw:
            raise BatterWalkSettlementError("official transport receipt bytes are missing")
        receipt = _canonical_object(self.transport_receipt_raw, "official transport receipt")
        required = {
            "schema_version", "source_id", "parser_id", "request_url",
            "received_at_utc", "http_status", "mlb_game_pk", "plan_sha256",
            "target_id", "collector_manifest_sha256", "collector_code_sha256",
            "observation_sequence", "raw_payload_sha256", "research_only",
        }
        if set(receipt) != required or receipt.get("schema_version") != TRANSPORT_RECEIPT_SCHEMA:
            raise BatterWalkSettlementError("official transport receipt schema changed")
        if receipt.get("source_id") != SOURCE_ID or receipt.get("parser_id") != PARSER_ID:
            raise BatterWalkSettlementError("official transport receipt source or parser changed")
        if receipt.get("research_only") is not True or receipt.get("http_status") != 200:
            raise BatterWalkSettlementError("official transport receipt governance or status changed")
        _utc(receipt.get("received_at_utc"), "official response receipt")
        _integer(receipt.get("mlb_game_pk"), "transport game identity", positive=True)
        for field in (
            "plan_sha256", "target_id", "collector_manifest_sha256",
            "collector_code_sha256", "raw_payload_sha256",
        ):
            _sha(receipt.get(field), f"transport.{field}")
        _integer(receipt.get("observation_sequence"), "transport observation sequence", positive=True)
        if receipt.get("raw_payload_sha256") != hashlib.sha256(self.body).hexdigest():
            raise BatterWalkSettlementError("official transport receipt does not bind the raw body")

    @property
    def receipt(self) -> dict[str, Any]:
        return _canonical_object(self.transport_receipt_raw, "official transport receipt")

    @property
    def received_at_utc(self) -> str:
        return str(self.receipt["received_at_utc"])

    @property
    def request_url(self) -> str:
        return str(self.receipt["request_url"])

    @property
    def transport_sha256(self) -> str:
        return hashlib.sha256(self.transport_receipt_raw).hexdigest()

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.body).hexdigest()


def _sha(value: Any, label: str) -> str:
    text = str(value).strip().lower()
    if not _SHA256.fullmatch(text):
        raise BatterWalkSettlementError(f"{label} must be a lowercase SHA-256 digest")
    return text


def _utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise BatterWalkSettlementError(f"{label} must be timezone-aware ISO-8601")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BatterWalkSettlementError(f"{label} must be timezone-aware ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise BatterWalkSettlementError(f"{label} must be timezone-aware ISO-8601")
    return parsed.astimezone(timezone.utc)


def _integer(value: Any, label: str, *, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < (1 if positive else 0):
        raise BatterWalkSettlementError(f"{label} must be a {'positive' if positive else 'non-negative'} integer")
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise BatterWalkSettlementError("official settlement JSON contains duplicate keys")
        value[key] = item
    return value


def _canonical_object(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw, object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BatterWalkSettlementError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict) or raw != canonical_bytes(value) + b"\n":
        raise BatterWalkSettlementError(f"{label} is not a canonical retained object")
    return value


def build_untrusted_test_transport_receipt(
    *, body: bytes, received_at_utc: str, request_url: str,
    player_output: Mapping[str, Any], observation_sequence: int = 1,
) -> bytes:
    """Create only an explicitly untrusted test receipt.

    Caller-created bytes can exercise replay but can never establish
    gradeability.  A future trusted collector requires a new, independently
    release-bound append-only ledger contract rather than promoting this helper.
    """
    _utc(received_at_utc, "official response receipt")
    receipt = {
        "schema_version": TRANSPORT_RECEIPT_SCHEMA,
        "source_id": SOURCE_ID,
        "parser_id": PARSER_ID,
        "request_url": request_url,
        "received_at_utc": received_at_utc,
        "http_status": 200,
        "mlb_game_pk": player_output["mlb_game_pk"],
        "plan_sha256": player_output["plan_sha256"],
        "target_id": player_output["target_id"],
        "collector_manifest_sha256": "0" * 64,
        "collector_code_sha256": "0" * 64,
        "observation_sequence": _integer(
            observation_sequence, "transport observation sequence", positive=True
        ),
        "raw_payload_sha256": hashlib.sha256(body).hexdigest(),
        "research_only": True,
    }
    return canonical_bytes(receipt) + b"\n"


def load_settlement_contract(*, root: str | Path, contract: LoadedBatterWalkContract) -> dict[str, Any]:
    contract = replay_contract(contract)
    repository = Path(root).resolve()
    if repository != contract.repository_root:
        raise BatterWalkSettlementError("settlement repository differs from the loaded control contract")
    binding = contract.payload.get("settlement")
    if not isinstance(binding, Mapping) or set(binding) != {"path", "sha256"}:
        raise BatterWalkSettlementError("settlement binding is malformed")
    try:
        path = _bound_path(repository, binding["path"], "settlement contract")
    except BatterWalkForwardControlError as exc:
        raise BatterWalkSettlementError("settlement contract path is unsafe") from exc
    try:
        path.relative_to(repository)
        raw = path.read_bytes()
        payload = json.loads(raw, object_pairs_hook=_unique_object)
    except (ValueError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BatterWalkSettlementError("settlement contract is unreadable") from exc
    expected = _sha(binding["sha256"], "settlement.sha256")
    if hashlib.sha256(raw).hexdigest() != expected or expected != contract.settlement_sha256:
        raise BatterWalkSettlementError("settlement contract hash differs")
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version", "status", "market_id", "research_target", "source_protocol",
        "parser_protocol", "evidence_authority", "opening_boundary", "dispositions", "commercial_boundary",
        "governance",
    }:
        raise BatterWalkSettlementError("settlement contract schema changed")
    if payload.get("schema_version") != SETTLEMENT_CONTRACT_SCHEMA or payload.get("status") != "SEALED_SCHEMA_BOUNDARY_NOT_OPENED" or payload.get("market_id") != "batter_walks":
        raise BatterWalkSettlementError("settlement contract identity changed")
    if payload.get("research_target") != {
        "source": "semantic replay of retained official MLB final live-feed bytes",
        "count_field": "baseOnBalls",
        "plate_appearance_field": "plateAppearances",
        "definition": "official integer baseOnBalls; hit-by-pitch and other non-walk reaches excluded",
        "final_status_required": True,
        "raw_payload_and_projection_hashes_required": True,
        "hard_game_team_side_player_identity_required": True,
        "official_starter_batting_orders": sorted(STARTER_ORDERS),
    }:
        raise BatterWalkSettlementError("official settlement truth definition changed")
    if payload.get("source_protocol") != {
        "source_id": SOURCE_ID,
        "scheme": "https",
        "host": "statsapi.mlb.com",
        "path_template": "/api/v1.1/game/{mlb_game_pk}/feed/live",
        "fields": _FIELDS,
        "retained_raw_bytes_required": True,
        "request_url_and_receipt_required": True,
        "transport_receipt_schema": TRANSPORT_RECEIPT_SCHEMA,
        "immutable_transport_receipt_required": True,
    }:
        raise BatterWalkSettlementError("official settlement source protocol changed")
    if payload.get("parser_protocol") != {
        "parser_id": PARSER_ID,
        "raw_bytes_replayed_for_every_disposition": True,
        "transport_receipt_replayed_for_every_disposition": True,
        "label_only_projection_forbidden": True,
        "duplicate_json_keys_rejected": True,
    }:
        raise BatterWalkSettlementError("official settlement parser protocol changed")
    if payload.get("evidence_authority") != {
        "state": UNTRUSTED_EVIDENCE_AUTHORITY,
        "trusted_collector_ledger_present": False,
        "exact_collector_release_manifest_required": True,
        "collector_code_identity_required": True,
        "monotonic_observation_sequence_required": True,
        "retained_gate_transport_and_raw_bytes_required": True,
        "caller_helper_or_self_hash_can_establish_gradeability": False,
    }:
        raise BatterWalkSettlementError("settlement evidence authority changed")
    opening = payload.get("opening_boundary")
    if not isinstance(opening, Mapping) or opening != {
        "opening_gate_schema": OPENING_GATE_SCHEMA,
        "not_before_utc": "2026-09-28T00:00:00Z",
        "opening_gate_sha256_required": True,
        "opening_gate_independently_retained_required": True,
        "opening_gate_must_run_before_raw_parse": True,
        "outcome_paths_forbidden_during_control_collection": True,
        "settlement_projection_is_not_a_pregame_input": True,
    }:
        raise BatterWalkSettlementError("settlement opening boundary changed")
    if payload.get("dispositions") != {
        "untrusted_evidence_not_gradeable": "semantic parser replay only; no independently release-bound append-only settlement collector exists",
        "retained_coverage_failure_not_scored": "DNP or official nonstarter",
        "final_status_missing_not_scored": "game is not official final",
        "support_breach_model_invalid_not_scored": "official walk count exceeds the declared PMF support",
        "malformed_official_projection_not_scored": "official counts or identity are invalid",
    }:
        raise BatterWalkSettlementError("settlement disposition population changed")
    if payload.get("commercial_boundary") != {
        "product_rules_assumed_identical": False,
        "commercial_quote_gradeable_only_if_retained_rules_prove_identical_settlement": True,
        "prices_required": False,
        "executability_claimed": False,
    }:
        raise BatterWalkSettlementError("settlement commercial boundary changed")
    governance = payload.get("governance")
    if governance != {
        "research_only": True, "promotion_eligible": False,
        "betting_authorized": False, "post_hoc_settlement_change_forbidden": True,
    }:
        raise BatterWalkSettlementError("settlement governance changed")
    return payload


def expected_request_url(mlb_game_pk: int) -> str:
    game_pk = _integer(mlb_game_pk, "mlb_game_pk", positive=True)
    return f"https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live?fields={_FIELDS}"


def _validate_request(url: str, game_pk: int) -> None:
    actual = urlsplit(url)
    expected = urlsplit(expected_request_url(game_pk))
    if (
        actual.scheme != expected.scheme or actual.netloc != expected.netloc
        or actual.path != expected.path or actual.fragment
        or parse_qs(actual.query, keep_blank_values=True) != parse_qs(expected.query, keep_blank_values=True)
    ):
        raise BatterWalkSettlementError("official settlement request source or game identity changed")


def build_opening_gate(
    *, contract: LoadedBatterWalkContract, plan: ShadowCapturePlan, opened_at_utc: str,
    observation_sequence: int = 1,
) -> dict[str, Any]:
    contract = replay_contract(contract)
    settlement = load_settlement_contract(root=contract.repository_root, contract=contract)
    opened = _utc(opened_at_utc, "outcome opening time")
    not_before = _utc(settlement["opening_boundary"]["not_before_utc"], "opening not-before")
    if opened < not_before:
        raise BatterWalkSettlementError("official settlement boundary remains sealed")
    if plan.entry_hours != 4 or plan.official_game_date.startswith("2026-05-"):
        raise BatterWalkSettlementError("opening gate plan is outside the locked forward boundary")
    unsigned = {
        "schema_version": OPENING_GATE_SCHEMA,
        "contract_sha256": contract.sha256,
        "settlement_contract_sha256": contract.settlement_sha256,
        "plan_sha256": plan.plan_sha256,
        "opened_at_utc": opened.isoformat().replace("+00:00", "Z"),
        "collector_manifest_sha256": "0" * 64,
        "collector_code_sha256": "0" * 64,
        "observation_sequence": _integer(
            observation_sequence, "opening observation sequence", positive=True
        ),
        "evidence_authority_state": UNTRUSTED_EVIDENCE_AUTHORITY,
        "research_only": True,
        "promotion_eligible": False,
        "betting_authorized": False,
    }
    return {**unsigned, "opening_gate_sha256": sha256_value(unsigned)}


def retain_opening_gate(gate: Mapping[str, Any]) -> bytes:
    """Serialize an opening decision as independently retained canonical bytes."""
    if not isinstance(gate, Mapping):
        raise BatterWalkSettlementError("opening gate must be an object")
    return canonical_bytes(dict(gate)) + b"\n"


def _validate_opening_gate(
    gate: Mapping[str, Any], *, contract: LoadedBatterWalkContract, plan_sha256: str
) -> None:
    if not isinstance(gate, Mapping) or set(gate) != {
        "schema_version", "contract_sha256", "settlement_contract_sha256",
        "plan_sha256", "opened_at_utc", "collector_manifest_sha256",
        "collector_code_sha256", "observation_sequence", "evidence_authority_state",
        "research_only", "promotion_eligible", "betting_authorized",
        "opening_gate_sha256",
    }:
        raise BatterWalkSettlementError("opening gate schema changed")
    unsigned = dict(gate)
    supplied = unsigned.pop("opening_gate_sha256")
    if supplied != sha256_value(unsigned):
        raise BatterWalkSettlementError("opening gate hash differs")
    if (
        gate.get("schema_version") != OPENING_GATE_SCHEMA
        or gate.get("contract_sha256") != contract.sha256
        or gate.get("settlement_contract_sha256") != contract.settlement_sha256
        or gate.get("plan_sha256") != plan_sha256
        or gate.get("research_only") is not True
        or gate.get("promotion_eligible") is not False
        or gate.get("betting_authorized") is not False
        or gate.get("collector_manifest_sha256") != "0" * 64
        or gate.get("collector_code_sha256") != "0" * 64
        or gate.get("evidence_authority_state") != UNTRUSTED_EVIDENCE_AUTHORITY
    ):
        raise BatterWalkSettlementError("opening gate identity or governance changed")
    _integer(gate.get("observation_sequence"), "opening observation sequence", positive=True)
    settlement = load_settlement_contract(root=contract.repository_root, contract=contract)
    if _utc(gate.get("opened_at_utc"), "opening gate timestamp") < _utc(settlement["opening_boundary"]["not_before_utc"], "opening not-before"):
        raise BatterWalkSettlementError("opening gate predates the sealed boundary")


def _projection_from_raw(raw_bytes: bytes, output: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str]:
    try:
        raw = json.loads(raw_bytes, object_pairs_hook=_unique_object)
        if not isinstance(raw, Mapping) or set(raw) != {"gamePk", "gameData", "liveData"}:
            raise BatterWalkSettlementError("official raw root surface changed")
        if raw.get("gamePk") != output["mlb_game_pk"]:
            raise BatterWalkSettlementError("official game identity differs")
        game_data = raw.get("gameData")
        live_data = raw.get("liveData")
        if not isinstance(game_data, Mapping) or not isinstance(live_data, Mapping):
            raise BatterWalkSettlementError("official raw game paths are missing")
        if set(game_data) != {"datetime", "status"} or set(live_data) != {"boxscore"}:
            raise BatterWalkSettlementError("official raw game surface changed")
        date_block = game_data.get("datetime")
        status = game_data.get("status")
        if not isinstance(date_block, Mapping) or set(date_block) != {"officialDate"} or date_block.get("officialDate") != output["official_game_date"]:
            raise BatterWalkSettlementError("official date identity differs")
        final = isinstance(status, Mapping) and set(status) == {"abstractGameState"} and status.get("abstractGameState") == "Final"
        if not isinstance(status, Mapping) or set(status) != {"abstractGameState"}:
            raise BatterWalkSettlementError("official status surface changed")
        boxscore = live_data.get("boxscore")
        teams = boxscore.get("teams") if isinstance(boxscore, Mapping) and set(boxscore) == {"teams"} else None
        if not isinstance(teams, Mapping) or set(teams) != {"home", "away"}:
            raise BatterWalkSettlementError("official team surface changed")
        team_ids: dict[str, int] = {}
        matched: Mapping[str, Any] | None = None
        for side in ("home", "away"):
            team = teams.get(side)
            if not isinstance(team, Mapping) or set(team) != {"team", "players"}:
                raise BatterWalkSettlementError("official side surface changed")
            identity = team.get("team")
            players = team.get("players")
            if not isinstance(identity, Mapping) or set(identity) != {"id"} or not isinstance(players, Mapping):
                raise BatterWalkSettlementError("official team identity or players changed")
            team_ids[side] = _integer(identity.get("id"), f"{side} team id", positive=True)
            for key, player in players.items():
                if not isinstance(player, Mapping) or not isinstance(key, str):
                    raise BatterWalkSettlementError("official player surface changed")
                person = player.get("person")
                if not isinstance(person, Mapping) or set(person) != {"id"}:
                    raise BatterWalkSettlementError("official player identity changed")
                pid = _integer(person.get("id"), "official player id", positive=True)
                if key != f"ID{pid}":
                    raise BatterWalkSettlementError("official player key differs")
                if side == output["side"] and pid == output["player_id"]:
                    if matched is not None:
                        raise BatterWalkSettlementError("official player identity is ambiguous")
                    matched = player
        if team_ids["home"] != output["home_team_id"] or team_ids["away"] != output["away_team_id"]:
            raise BatterWalkSettlementError("official team identity differs")
        if not final:
            return None, "final_status_missing_not_scored"
        if matched is None:
            return None, "retained_coverage_failure_not_scored"
        if set(matched) != {"person", "battingOrder", "stats"}:
            raise BatterWalkSettlementError("official matched-player surface changed")
        batting_order = matched.get("battingOrder")
        if batting_order in {None, ""} or batting_order not in STARTER_ORDERS:
            return None, "retained_coverage_failure_not_scored"
        stats = matched.get("stats")
        batting = stats.get("batting") if isinstance(stats, Mapping) and set(stats) == {"batting"} else None
        if not isinstance(batting, Mapping) or set(batting) != {"plateAppearances", "baseOnBalls"}:
            raise BatterWalkSettlementError("official batting count surface changed")
        pa = _integer(batting.get("plateAppearances"), "plateAppearances")
        walks = _integer(batting.get("baseOnBalls"), "baseOnBalls")
        if walks > pa:
            raise BatterWalkSettlementError("official walks exceed plate appearances")
        projection = {
            "mlb_game_pk": output["mlb_game_pk"], "official_game_date": output["official_game_date"],
            "side": output["side"], "home_team_id": team_ids["home"],
            "away_team_id": team_ids["away"], "player_id": output["player_id"],
            "batting_order": batting_order, "plateAppearances": pa, "baseOnBalls": walks,
        }
        disposition = "support_breach_model_invalid_not_scored" if walks >= len(output["walks_pmf"]) else "gradeable"
        return projection, disposition
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, BatterWalkSettlementError):
        return None, "malformed_official_projection_not_scored"


def build_settlement_record(
    *, response: RawOfficialSettlementResponse, player_output: Mapping[str, Any],
    contract: LoadedBatterWalkContract, opening_gate_raw: bytes,
) -> dict[str, Any]:
    """Run the opening gate before parsing retained official bytes."""
    contract = replay_contract(contract)
    validate_player_output(player_output, contract=contract)
    opening_gate = _canonical_object(opening_gate_raw, "retained opening gate")
    _validate_opening_gate(opening_gate, contract=contract, plan_sha256=player_output["plan_sha256"])
    if _utc(response.received_at_utc, "official response receipt") < _utc(opening_gate.get("opened_at_utc"), "opening gate timestamp"):
        raise BatterWalkSettlementError("official response predates the outcome opening gate")
    _validate_request(response.request_url, int(player_output["mlb_game_pk"]))
    receipt = response.receipt
    expected_receipt = {
        "mlb_game_pk": player_output["mlb_game_pk"],
        "plan_sha256": player_output["plan_sha256"],
        "target_id": player_output["target_id"],
    }
    if any(receipt.get(key) != value for key, value in expected_receipt.items()):
        raise BatterWalkSettlementError("official transport receipt target identity differs")
    if receipt["observation_sequence"] <= opening_gate["observation_sequence"]:
        raise BatterWalkSettlementError("official transport observation sequence does not follow opening gate")
    if (
        receipt["collector_manifest_sha256"] != opening_gate["collector_manifest_sha256"]
        or receipt["collector_code_sha256"] != opening_gate["collector_code_sha256"]
    ):
        raise BatterWalkSettlementError("opening and transport collector authority differ")
    projection, _semantic_disposition = _projection_from_raw(response.body, player_output)
    # No independently release-bound append-only settlement collector exists in
    # this candidate.  Semantic replay is useful for testing the parser, but
    # caller-created receipts/self-hashes can never make an observation gradeable.
    disposition = "untrusted_evidence_not_gradeable"
    unsigned = {
        "schema_version": SETTLEMENT_SCHEMA,
        "research_only": True, "promotion_eligible": False, "betting_authorized": False,
        "contract_sha256": contract.sha256,
        "settlement_contract_sha256": contract.settlement_sha256,
        "parser_id": PARSER_ID,
        "opening_gate_sha256": hashlib.sha256(opening_gate_raw).hexdigest(),
        "transport_receipt_sha256": response.transport_sha256,
        "raw_payload_sha256": response.sha256,
        "market_hard_player_key": player_output["market_hard_player_key"],
        "player_output_sha256": player_output["output_sha256"],
        "disposition": disposition,
        "official_projection": projection,
        "evidence_authority_state": UNTRUSTED_EVIDENCE_AUTHORITY,
    }
    return {**unsigned, "record_sha256": sha256_value(unsigned)}


def replay_settlement_record(
    record: Mapping[str, Any], *, raw_payload: bytes, transport_receipt_raw: bytes,
    opening_gate_raw: bytes, player_output: Mapping[str, Any],
    contract: LoadedBatterWalkContract,
) -> str:
    contract = replay_contract(contract)
    if not isinstance(record, Mapping) or set(record) != _RECORD_FIELDS:
        raise BatterWalkSettlementError("settlement record schema changed")
    unsigned = dict(record)
    supplied = unsigned.pop("record_sha256")
    if supplied != sha256_value(unsigned):
        raise BatterWalkSettlementError("settlement record hash differs")
    if record.get("raw_payload_sha256") != hashlib.sha256(raw_payload).hexdigest():
        raise BatterWalkSettlementError("retained official raw bytes differ")
    if record.get("transport_receipt_sha256") != hashlib.sha256(transport_receipt_raw).hexdigest():
        raise BatterWalkSettlementError("retained official transport receipt differs")
    if record.get("opening_gate_sha256") != hashlib.sha256(opening_gate_raw).hexdigest():
        raise BatterWalkSettlementError("independently retained opening gate differs")
    response = RawOfficialSettlementResponse(
        body=raw_payload, transport_receipt_raw=transport_receipt_raw,
    )
    reproduced = build_settlement_record(
        response=response, player_output=player_output, contract=contract,
        opening_gate_raw=opening_gate_raw,
    )
    if reproduced != dict(record):
        raise BatterWalkSettlementError("settlement record differs from raw semantic replay")
    return str(record["disposition"])


def coverage_and_gradeability(
    *, plan: ShadowCapturePlan, walk_ledger: Any,
    contract: LoadedBatterWalkContract,
    settlement_records: Sequence[Mapping[str, Any]] | None = None,
    settlement_raw_by_sha256: Mapping[str, bytes] | None = None,
    settlement_transport_by_sha256: Mapping[str, bytes] | None = None,
    opening_gate_by_sha256: Mapping[str, bytes] | None = None,
) -> dict[str, Any]:
    """Derive side coverage from the plan and dispositions from replayed records."""
    contract = replay_contract(contract)
    if not isinstance(plan, ShadowCapturePlan) or plan.entry_hours != 4 or plan.official_game_date.startswith("2026-05-"):
        raise BatterWalkSettlementError("coverage requires an immutable non-May T-4 plan")
    expected = {
        f"{target.mlb_game_pk}:{side}:batter_walks"
        for target in plan.targets for side in ("home", "away")
    }
    if not expected:
        raise BatterWalkSettlementError("coverage plan has no game sides")
    try:
        if walk_ledger.plan.to_dict() != plan.to_dict():
            raise BatterWalkSettlementError("walk ledger plan differs from coverage plan")
        side_terminals, player_outputs = walk_ledger.verified_population()
    except BatterWalkSettlementError:
        raise
    except Exception as exc:
        raise BatterWalkSettlementError("walk coverage requires an exact replayed ledger population") from exc
    terminal_by_key: dict[str, Mapping[str, Any]] = {}
    for terminal in side_terminals:
        try:
            validate_side_terminal(terminal, contract=contract)
        except BatterWalkForwardControlError as exc:
            raise BatterWalkSettlementError("walk side terminal is invalid") from exc
        key = str(terminal["market_hard_side_key"])
        if key in terminal_by_key:
            raise BatterWalkSettlementError("duplicate walk side terminal")
        terminal_by_key[key] = terminal
    if set(terminal_by_key) != expected:
        raise BatterWalkSettlementError("walk side terminal coverage differs from the immutable plan")
    output_by_key: dict[str, Mapping[str, Any]] = {}
    outputs_by_side: dict[str, list[Mapping[str, Any]]] = {key: [] for key in expected}
    for output in player_outputs:
        validate_player_output(output, contract=contract)
        key = str(output["market_hard_player_key"])
        if key in output_by_key:
            raise BatterWalkSettlementError("duplicate walk player output")
        output_by_key[key] = output
        side_key = f"{output['mlb_game_pk']}:{output['side']}:batter_walks"
        if side_key not in outputs_by_side:
            raise BatterWalkSettlementError("walk player output belongs to an unplanned side")
        outputs_by_side[side_key].append(output)
    complete_sides = 0
    excluded_sides = 0
    for side_key, terminal in terminal_by_key.items():
        rows = outputs_by_side[side_key]
        if terminal["terminal_state"] == "captured_complete":
            complete_sides += 1
            if len(rows) != 9 or set(terminal["player_output_sha256"]) != {row["output_sha256"] for row in rows}:
                raise BatterWalkSettlementError("complete walk side population differs")
        else:
            excluded_sides += 1
            if rows:
                raise BatterWalkSettlementError("excluded walk side contains predictions")
    if settlement_records is None:
        if any(value not in (None, {}) for value in (
            settlement_raw_by_sha256, settlement_transport_by_sha256,
            opening_gate_by_sha256,
        )):
            raise BatterWalkSettlementError("settlement evidence supplied without records")
        dispositions: dict[str, str] = {}
        gradeable_fraction = None
    else:
        raws = settlement_raw_by_sha256 or {}
        transports = settlement_transport_by_sha256 or {}
        gates = opening_gate_by_sha256 or {}
        records_by_key: dict[str, Mapping[str, Any]] = {}
        consumed_raw: set[str] = set()
        consumed_transport: set[str] = set()
        consumed_gate: set[str] = set()
        for record in settlement_records:
            key = str(record.get("market_hard_player_key"))
            if key in records_by_key or key not in output_by_key:
                raise BatterWalkSettlementError("settlement record identity is duplicate or unplanned")
            digest = str(record.get("raw_payload_sha256"))
            raw = raws.get(digest)
            if not isinstance(raw, bytes):
                raise BatterWalkSettlementError("settlement record lacks retained official raw bytes")
            transport_digest = str(record.get("transport_receipt_sha256"))
            transport = transports.get(transport_digest)
            gate_digest = str(record.get("opening_gate_sha256"))
            gate_raw = gates.get(gate_digest)
            if not isinstance(transport, bytes) or not isinstance(gate_raw, bytes):
                raise BatterWalkSettlementError("settlement record lacks retained transport or opening-gate bytes")
            disposition = replay_settlement_record(
                record, raw_payload=raw, transport_receipt_raw=transport,
                opening_gate_raw=gate_raw, player_output=output_by_key[key], contract=contract,
            )
            records_by_key[key] = record
            consumed_raw.add(digest)
            consumed_transport.add(transport_digest)
            consumed_gate.add(gate_digest)
        if (
            set(records_by_key) != set(output_by_key)
            or consumed_raw != set(raws)
            or consumed_transport != set(transports)
            or consumed_gate != set(gates)
        ):
            raise BatterWalkSettlementError("every prediction requires exact replayed settlement evidence with no orphans")
        dispositions = {key: str(value["disposition"]) for key, value in records_by_key.items()}
        gradeable_fraction = sum(value == "gradeable" for value in dispositions.values()) / len(output_by_key) if output_by_key else 0.0
    return {
        "schema_version": "batter-walk-forward-coverage-v1",
        "research_only": True, "promotion_eligible": False, "betting_authorized": False,
        "plan_sha256": plan.plan_sha256,
        "planned_sides": len(expected), "terminal_sides": len(terminal_by_key),
        "complete_sides": complete_sides, "excluded_sides": excluded_sides,
        "side_coverage_fraction": len(terminal_by_key) / len(expected),
        "prediction_rows": len(output_by_key), "settlement_rows": len(dispositions),
        "explicit_disposition_rows": len(dispositions),
        "disposition_counts": {name: sum(value == name for value in dispositions.values()) for name in sorted(DISPOSITIONS)},
        "gradeable_rows": sum(value == "gradeable" for value in dispositions.values()),
        "gradeable_fraction": gradeable_fraction,
        "future_minimum_gradeable_fraction": 0.95,
        "release_authority_bound": contract.payload["release_authority"]["status"] == "BOUND_IMMUTABLE_RELEASE",
        "future_gradeability_gate_met": (
            None if gradeable_fraction is None
            else gradeable_fraction >= 0.95
            and contract.payload["release_authority"]["status"] == "BOUND_IMMUTABLE_RELEASE"
        ),
        "control_is_not_a_challenger": True,
    }
