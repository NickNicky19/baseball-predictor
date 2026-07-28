"""Deterministic, fail-closed, market-separated shared-PA evaluation mechanics.

This module accepts caller-supplied in-memory rows only.  It does not discover,
open, fit, predict, settle, or promote anything.  The checked-in contract is
externally unbound, so public evaluation is restricted to explicit synthetic
test mechanics until independent authorities bind the exact contract,
candidate, registry checkpoint, data manifest, and evidence window.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import re
import stat
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence


CONTRACT_SCHEMA = "shared-pa-market-evaluation-contract-v1"
AUTHORITY_SCHEMA = "shared-pa-market-evaluation-authority-v1"
REPORT_SCHEMA = "shared-pa-market-evaluation-report-v1"
MARKETS = ("hits", "hr_over_0_5", "total_bases")
BASELINES = ("league_rate", "player_time_safe_eb", "frozen_simulator", "valid_market_implied")
CALIBRATION_BIN_EDGES = (0.0, 0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5, 0.65, 0.8, 1.0)
UNBOUND = "UNBOUND_EXTERNAL_AUTHORITIES_REQUIRED"
BOUND = "BOUND_EXTERNAL_AUTHORITIES_VERIFIED"
SYNTHETIC_SOURCE_AUTHORITY = "SYNTHETIC_STRUCTURE_ONLY"
SOURCE_AUTHORITY_SCHEMA = "shared-pa-market-source-authority-manifest-v1"
SOURCE_RECEIPT_PROTOCOL = "shared-pa-market-receipt-protocol-v1"
AVAILABLE_RECEIPT_SCHEMA = "shared-pa-market-available-quote-receipt-v1"
UNAVAILABLE_RECEIPT_SCHEMA = "shared-pa-market-terminal-unavailable-receipt-v1"
_CANONICAL_LINE = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]*[1-9])?\Z")
ABSTENTION_REASONS = {
    "MISSING_POINT_IN_TIME_INPUT",
    "IDENTITY_UNRESOLVED",
    "CONTRADICTORY_INPUT",
    "PITCHER_RECEIPT_UNAVAILABLE",
    "LINEUP_OPPORTUNITY_UNAVAILABLE",
    "CANDIDATE_NOT_APPLICABLE",
}
MARKET_UNAVAILABLE_REASONS = {
    "TERMINAL_SOURCE_UNAVAILABLE",
    "NO_MATCHING_MARKET",
    "QUOTE_AFTER_DECISION_HORIZON",
    "BOOK_OR_PRODUCT_UNVERIFIED",
}


class SharedPAMarketEvaluationError(ValueError):
    """An evaluation input or transition violated the locked contract."""


def canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise SharedPAMarketEvaluationError("value is not canonical JSON") from exc


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_unique_json_file(path: Path, label: str) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        output: dict[str, Any] = {}
        for key, value in pairs:
            if key in output:
                raise SharedPAMarketEvaluationError(
                    f"{label} has duplicate JSON key: {key}"
                )
            output[key] = value
        return output

    try:
        text = path.read_bytes().decode("utf-8")
        return json.loads(text, object_pairs_hook=unique)
    except SharedPAMarketEvaluationError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SharedPAMarketEvaluationError(f"{label} is unreadable") from exc


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise SharedPAMarketEvaluationError(f"{label} must be lowercase SHA-256")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise SharedPAMarketEvaluationError(f"{label} must be a canonical nonempty string")
    return value


def _utc(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise SharedPAMarketEvaluationError(f"{label} must be canonical UTC")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise SharedPAMarketEvaluationError(f"{label} is invalid") from exc
    if parsed.tzinfo != timezone.utc or parsed.microsecond or parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise SharedPAMarketEvaluationError(f"{label} must be second-resolution canonical UTC")
    return value


def _official_date(value: Any) -> str:
    if not isinstance(value, str):
        raise SharedPAMarketEvaluationError("official_game_date must be ISO date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise SharedPAMarketEvaluationError("official_game_date is invalid") from exc
    if parsed.isoformat() != value:
        raise SharedPAMarketEvaluationError("official_game_date is not canonical")
    if parsed.year == 2026 and parsed.month == 5:
        raise SharedPAMarketEvaluationError("May 2026 is sealed and forbidden")
    return value


def _line(value: Any, label: str = "line") -> str:
    if not isinstance(value, str) or _CANONICAL_LINE.fullmatch(value) is None:
        raise SharedPAMarketEvaluationError(
            f"{label} must be a canonical nonnegative decimal string without exponent or trailing zero"
        )
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise SharedPAMarketEvaluationError(f"{label} is invalid") from exc
    if not parsed.is_finite() or parsed < 0:
        raise SharedPAMarketEvaluationError(f"{label} must be finite and nonnegative")
    return value


def _probability(value: Any, label: str, epsilon: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise SharedPAMarketEvaluationError(f"{label} must be finite probability")
    probability = float(value)
    if probability < epsilon or probability > 1.0 - epsilon:
        raise SharedPAMarketEvaluationError(f"{label} violates locked probability bounds; clipping is forbidden")
    return probability


def _distribution(value: Any, label: str) -> dict[int, float]:
    if not isinstance(value, Mapping) or not value:
        raise SharedPAMarketEvaluationError(f"{label} must be a nonempty count distribution")
    output: dict[int, float] = {}
    for raw_key, raw_probability in value.items():
        if not isinstance(raw_key, str) or not raw_key.isdigit() or str(int(raw_key)) != raw_key:
            raise SharedPAMarketEvaluationError(f"{label} keys must be canonical nonnegative integers")
        count = int(raw_key)
        if isinstance(raw_probability, bool) or not isinstance(raw_probability, (int, float)):
            raise SharedPAMarketEvaluationError(f"{label} probabilities must be finite")
        probability = float(raw_probability)
        if not math.isfinite(probability) or probability < 0.0 or probability > 1.0:
            raise SharedPAMarketEvaluationError(f"{label} probabilities must be finite and within [0,1]")
        output[count] = probability
    if sorted(output) != list(range(max(output) + 1)):
        raise SharedPAMarketEvaluationError(f"{label} support must be contiguous from zero")
    if not math.isclose(sum(output.values()), 1.0, rel_tol=0.0, abs_tol=1e-12):
        raise SharedPAMarketEvaluationError(f"{label} probabilities must sum exactly to one within tolerance")
    return output


def _distribution_tail(distribution: Mapping[int, float], line: str) -> float:
    threshold = Decimal(line)
    return sum(probability for count, probability in distribution.items() if Decimal(count) > threshold)


def _is_link_or_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    attributes = getattr(info, "st_file_attributes", 0)
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(info.st_mode) or bool(attributes & reparse)


def safe_regular_file(root: Path, relative: str, *, context: str) -> Path:
    rel = PurePosixPath(relative)
    if rel.is_absolute() or not rel.parts or ".." in rel.parts or any(part in {"", "."} for part in rel.parts):
        raise SharedPAMarketEvaluationError(f"{context} path is unsafe")
    root_absolute = root.absolute()
    if not root_absolute.is_dir():
        raise SharedPAMarketEvaluationError(f"{context} authority root is missing")
    candidate = root_absolute.joinpath(*rel.parts)
    if not candidate.is_file():
        raise SharedPAMarketEvaluationError(f"{context} is missing or not a regular file")
    current = candidate
    while True:
        if _is_link_or_reparse(current):
            raise SharedPAMarketEvaluationError(f"{context} traverses a symlink, junction, or reparse point")
        if current == root_absolute:
            break
        if current.parent == current:
            raise SharedPAMarketEvaluationError(f"{context} path is outside authority root")
        current = current.parent
    root_resolved = root_absolute.resolve(strict=True)
    resolved = candidate.resolve(strict=True)
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise SharedPAMarketEvaluationError(f"{context} escapes authority root") from exc
    if not stat.S_ISREG(candidate.stat().st_mode):
        raise SharedPAMarketEvaluationError(f"{context} is not a regular file")
    return candidate


@dataclass(frozen=True)
class VerifiedSourceAuthority:
    authority_id: str
    authority_state: str
    protocol_id: str
    evidence_window_id: str
    manifest_sha256: str
    receipts: Mapping[str, Mapping[str, Any]]
    receipt_kinds: Mapping[str, str]

    def receipt(self, digest: str, expected_kind: str) -> Mapping[str, Any]:
        _sha(digest, "referenced receipt")
        if self.receipt_kinds.get(digest) != expected_kind or digest not in self.receipts:
            raise SharedPAMarketEvaluationError("referenced receipt is absent or has the wrong typed kind")
        return self.receipts[digest]


def _validate_receipt_payload(payload: Any, expected_kind: str) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise SharedPAMarketEvaluationError("source receipt must be an object")
    common = {
        "schema_version", "protocol_id", "receipt_kind", "source_id", "sportsbook",
        "product_contract_id", "official_game_date", "official_game_pk", "player_id",
        "market", "line",
    }
    if expected_kind == "AVAILABLE_QUOTE_PAIR":
        required = common | {"source_market_id", "quote_observed_at_utc", "quotes"}
        schema = AVAILABLE_RECEIPT_SCHEMA
    elif expected_kind == "TERMINAL_UNAVAILABLE_QUERY":
        required = common | {"availability_observed_at_utc", "unavailable_reason"}
        schema = UNAVAILABLE_RECEIPT_SCHEMA
    else:
        raise SharedPAMarketEvaluationError("source receipt kind is unsupported")
    if set(payload) != required:
        raise SharedPAMarketEvaluationError("source receipt surface changed")
    if (
        payload.get("schema_version") != schema
        or payload.get("protocol_id") != SOURCE_RECEIPT_PROTOCOL
        or payload.get("receipt_kind") != expected_kind
    ):
        raise SharedPAMarketEvaluationError("source receipt protocol or type changed")
    _text(payload.get("source_id"), "receipt source ID")
    _text(payload.get("sportsbook"), "receipt sportsbook")
    _text(payload.get("product_contract_id"), "receipt product contract ID")
    _official_date(payload.get("official_game_date"))
    for field in ("official_game_pk", "player_id"):
        value = payload.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise SharedPAMarketEvaluationError(f"receipt {field} must be a positive integer")
    if payload.get("market") not in MARKETS:
        raise SharedPAMarketEvaluationError("receipt market is unsupported")
    _line(payload.get("line"), "receipt line")
    if expected_kind == "AVAILABLE_QUOTE_PAIR":
        _text(payload.get("source_market_id"), "receipt source market ID")
        observed = _utc(payload.get("quote_observed_at_utc"), "receipt quote_observed_at_utc")
        quotes = payload.get("quotes")
        if not isinstance(quotes, list) or len(quotes) != 2:
            raise SharedPAMarketEvaluationError("receipt must contain exactly one over and one under quote")
        expected_sides = ("over", "under")
        for index, quote in enumerate(quotes):
            if not isinstance(quote, Mapping) or set(quote) != {"side", "decimal_price", "observed_at_utc"}:
                raise SharedPAMarketEvaluationError("receipt quote surface changed")
            if quote.get("side") != expected_sides[index] or _utc(quote.get("observed_at_utc"), "receipt quote timestamp") != observed:
                raise SharedPAMarketEvaluationError("receipt quote sides or timestamps differ")
            price = quote.get("decimal_price")
            if isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(float(price)) or float(price) <= 1.0:
                raise SharedPAMarketEvaluationError("receipt decimal prices must be finite and greater than one")
    else:
        _utc(payload.get("availability_observed_at_utc"), "receipt availability_observed_at_utc")
        if payload.get("unavailable_reason") not in MARKET_UNAVAILABLE_REASONS:
            raise SharedPAMarketEvaluationError("receipt terminal unavailability reason is not canonical")
    return dict(payload)


def load_source_authority(
    *, root: Path, manifest_relative_path: str, expected_manifest_sha256: str,
) -> VerifiedSourceAuthority:
    _sha(expected_manifest_sha256, "source-authority manifest")
    manifest_path = safe_regular_file(root, manifest_relative_path, context="source-authority manifest")
    if sha256_file(manifest_path) != expected_manifest_sha256:
        raise SharedPAMarketEvaluationError("source-authority manifest differs from expected external digest")
    payload = load_unique_json_file(manifest_path, "source-authority manifest")
    required = {
        "schema_version", "authority_id", "authority_state", "protocol_id",
        "evidence_window_id", "receipts",
    }
    if not isinstance(payload, Mapping) or set(payload) != required:
        raise SharedPAMarketEvaluationError("source-authority manifest surface changed")
    if (
        payload.get("schema_version") != SOURCE_AUTHORITY_SCHEMA
        or payload.get("authority_state") != SYNTHETIC_SOURCE_AUTHORITY
        or payload.get("protocol_id") != SOURCE_RECEIPT_PROTOCOL
        or payload.get("evidence_window_id") != "synthetic_test_only"
    ):
        raise SharedPAMarketEvaluationError("only exact synthetic structural source authority is supported")
    authority_id = _text(payload.get("authority_id"), "source-authority ID")
    rows = payload.get("receipts")
    if not isinstance(rows, list) or not rows:
        raise SharedPAMarketEvaluationError("source-authority receipt manifest is empty")
    receipts: dict[str, Mapping[str, Any]] = {}
    kinds: dict[str, str] = {}
    seen_paths: set[str] = set()
    for item in rows:
        if not isinstance(item, Mapping) or set(item) != {"receipt_sha256", "relative_path", "receipt_kind"}:
            raise SharedPAMarketEvaluationError("source-authority receipt row surface changed")
        digest = _sha(item.get("receipt_sha256"), "source receipt")
        relative = item.get("relative_path")
        kind = item.get("receipt_kind")
        if not isinstance(relative, str) or digest in receipts or relative in seen_paths:
            raise SharedPAMarketEvaluationError("source-authority receipt identity is duplicated")
        path = safe_regular_file(root, relative, context="source receipt")
        if sha256_file(path) != digest:
            raise SharedPAMarketEvaluationError("source receipt bytes differ from manifest")
        receipt_payload = load_unique_json_file(path, "source receipt")
        receipts[digest] = _validate_receipt_payload(receipt_payload, kind)
        kinds[digest] = kind
        seen_paths.add(relative)
    return VerifiedSourceAuthority(
        authority_id=authority_id,
        authority_state=SYNTHETIC_SOURCE_AUTHORITY,
        protocol_id=SOURCE_RECEIPT_PROTOCOL,
        evidence_window_id="synthetic_test_only",
        manifest_sha256=expected_manifest_sha256,
        receipts=receipts,
        receipt_kinds=kinds,
    )


def validate_contract(value: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "schema_version", "contract_id", "status", "source_base_commit", "markets",
        "markets_must_be_evaluated_separately", "pooling_forbidden", "cross_market_rescue_forbidden",
        "required_baselines", "valid_market_baseline_optional_only_when_terminally_unavailable",
        "canonical_test_evidence_window_ids", "protected_boundaries", "probability_epsilon",
        "calibration_bin_edges", "hr_upper_tail_thresholds", "uncertainty", "economic_boundary", "qualification",
    }
    if set(value) != required or value.get("schema_version") != CONTRACT_SCHEMA:
        raise SharedPAMarketEvaluationError("evaluation contract surface or schema changed")
    if value.get("contract_id") != "shared_pa_market_evaluation_contract_v1":
        raise SharedPAMarketEvaluationError("evaluation contract identity changed")
    if value.get("source_base_commit") != "9f86f6a34fc71151b76debf48059829aa4fa91da":
        raise SharedPAMarketEvaluationError("evaluation source-base authority changed")
    if value.get("status") != UNBOUND or value.get("markets") != list(MARKETS):
        raise SharedPAMarketEvaluationError("evaluation authority or market separation changed")
    if value.get("required_baselines") != list(BASELINES):
        raise SharedPAMarketEvaluationError("required baseline set or order changed")
    booleans = (
        value.get("markets_must_be_evaluated_separately") is True,
        value.get("pooling_forbidden") is True,
        value.get("cross_market_rescue_forbidden") is True,
        value.get("valid_market_baseline_optional_only_when_terminally_unavailable") is True,
    )
    if not all(booleans):
        raise SharedPAMarketEvaluationError("market separation or missing-market rule weakened")
    if value.get("canonical_test_evidence_window_ids") != ["synthetic_test_only"]:
        raise SharedPAMarketEvaluationError("unbound evidence allowlist changed")
    if value.get("protected_boundaries") != {
        "may_2026_forbidden": True,
        "spent_2024_selection_reusable": False,
        "spent_2025_hr_confirmation_reusable": False,
        "historical_prices_executable": False,
        "missed_prospective_backfill_allowed": False,
        "frozen_baselines_mutable": False,
        "betting_authorized": False,
    }:
        raise SharedPAMarketEvaluationError("protected boundary changed")
    if value.get("probability_epsilon") != 1e-12:
        raise SharedPAMarketEvaluationError("probability boundary changed")
    if value.get("calibration_bin_edges") != list(CALIBRATION_BIN_EDGES):
        raise SharedPAMarketEvaluationError("calibration bins differ from the exact locked list")
    if value.get("hr_upper_tail_thresholds") != [0.1, 0.2, 0.3]:
        raise SharedPAMarketEvaluationError("HR upper-tail thresholds changed")
    if value.get("uncertainty") != {
        "bootstrap_draws": 2000,
        "bootstrap_seed": 20260728,
        "confidence_level": 0.95,
        "date_cluster_required": True,
        "game_cluster_required": True,
    }:
        raise SharedPAMarketEvaluationError("uncertainty contract changed")
    if value.get("economic_boundary") != {
        "capture_lower_bound_minimum": 0.1,
        "historical_prices_executable": False,
        "roi_or_betting_decision_permitted": False,
    }:
        raise SharedPAMarketEvaluationError("+0.10 capture lower-bound or economic boundary changed")
    if value.get("qualification") != {
        "materiality_thresholds_supplied_by_external_authority": True,
        "unbound_engine_may_qualify_or_promote": False,
        "all_markets_must_pass_independently": True,
        "one_market_may_rescue_another": False,
    }:
        raise SharedPAMarketEvaluationError("qualification boundary changed")
    return dict(value)


def load_contract(path: Path) -> dict[str, Any]:
    value = load_unique_json_file(path, "evaluation contract")
    if not isinstance(value, Mapping):
        raise SharedPAMarketEvaluationError("evaluation contract must be an object")
    return validate_contract(value)


@dataclass(frozen=True)
class ValidatedRow:
    identity: tuple[str, int, int, str, str]
    official_game_date: str
    official_game_pk: int
    player_id: int
    market: str
    line: str
    settlement_status: str
    outcome_value: int | None
    outcome: int | None
    candidate_probability: float | None
    candidate_distribution: dict[int, float] | None
    candidate_available: bool
    abstention_reason: str | None
    baselines: dict[str, float | None]
    baseline_distributions: dict[str, dict[int, float] | None]


def _validate_baseline(
    name: str, value: Any, *, row: Mapping[str, Any], epsilon: float,
    source_authority: VerifiedSourceAuthority,
) -> tuple[float | None, dict[int, float] | None]:
    if not isinstance(value, Mapping):
        raise SharedPAMarketEvaluationError(f"baseline {name} must be an object")
    if name == "valid_market_implied":
        required = {
            "available", "probability", "unavailable_reason", "sportsbook", "source_market_id",
            "receipt_sha256", "quote_observed_at_utc", "line", "over_decimal_price",
            "under_decimal_price", "no_vig_method", "probability_scale",
            "official_game_pk", "player_id", "market",
            "source_id", "product_contract_id", "availability_observed_at_utc",
            "availability_receipt_sha256",
        }
        if set(value) != required:
            raise SharedPAMarketEvaluationError("valid-market baseline surface changed")
        if value.get("available") is False:
            if value.get("probability") is not None or value.get("unavailable_reason") not in MARKET_UNAVAILABLE_REASONS:
                raise SharedPAMarketEvaluationError("missing market baseline lacks terminal reason")
            for field in (
                "source_market_id", "receipt_sha256", "quote_observed_at_utc", "over_decimal_price",
                "under_decimal_price", "no_vig_method",
            ):
                if value.get(field) is not None:
                    raise SharedPAMarketEvaluationError("unavailable market baseline contains fabricated metadata")
            _text(value.get("source_id"), "availability source ID")
            _text(value.get("sportsbook"), "availability sportsbook")
            _text(value.get("product_contract_id"), "availability product contract ID")
            digest = _sha(value.get("availability_receipt_sha256"), "availability-query receipt")
            receipt = source_authority.receipt(digest, "TERMINAL_UNAVAILABLE_QUERY")
            availability_observed = _utc(value.get("availability_observed_at_utc"), "availability_observed_at_utc")
            if availability_observed > row["decision_horizon_utc"]:
                raise SharedPAMarketEvaluationError("availability query arrived after decision horizon")
            if value.get("probability_scale") != "binary_market_tail_v1":
                raise SharedPAMarketEvaluationError("unavailable market probability scale changed")
            if value.get("official_game_pk") != row["official_game_pk"] or value.get("player_id") != row["player_id"] or value.get("market") != row["market"]:
                raise SharedPAMarketEvaluationError("availability query event/player/market identity differs")
            if _line(value.get("line"), "availability query line") != row["line"]:
                raise SharedPAMarketEvaluationError("availability query line identity differs")
            expected = {
                "source_id": value["source_id"],
                "sportsbook": value["sportsbook"],
                "product_contract_id": value["product_contract_id"],
                "official_game_date": row["official_game_date"],
                "official_game_pk": row["official_game_pk"],
                "player_id": row["player_id"],
                "market": row["market"],
                "line": row["line"],
                "availability_observed_at_utc": availability_observed,
                "unavailable_reason": value["unavailable_reason"],
            }
            if any(receipt.get(field) != expected_value for field, expected_value in expected.items()):
                raise SharedPAMarketEvaluationError("terminal unavailability receipt semantics differ from row")
            return None, None
        if value.get("available") is not True or value.get("unavailable_reason") is not None:
            raise SharedPAMarketEvaluationError("market baseline availability is contradictory")
        _text(value.get("sportsbook"), "market sportsbook")
        _text(value.get("source_id"), "market source ID")
        _text(value.get("product_contract_id"), "market product contract ID")
        for field in ("availability_observed_at_utc", "availability_receipt_sha256"):
            if value.get(field) is not None:
                raise SharedPAMarketEvaluationError("available market baseline contains unavailable-query metadata")
        _text(value.get("source_market_id"), "market source ID")
        digest = _sha(value.get("receipt_sha256"), "market receipt")
        receipt = source_authority.receipt(digest, "AVAILABLE_QUOTE_PAIR")
        observed = _utc(value.get("quote_observed_at_utc"), "quote_observed_at_utc")
        if observed > row["decision_horizon_utc"]:
            raise SharedPAMarketEvaluationError("market quote arrived after decision horizon")
        if _line(value.get("line"), "market quote line") != row["line"]:
            raise SharedPAMarketEvaluationError("market baseline line identity differs")
        if value.get("probability_scale") != "binary_market_tail_v1":
            raise SharedPAMarketEvaluationError("market probability scale changed")
        if value.get("official_game_pk") != row["official_game_pk"] or value.get("player_id") != row["player_id"] or value.get("market") != row["market"]:
            raise SharedPAMarketEvaluationError("market quote-pair event/player/market identity differs")
        over = value.get("over_decimal_price")
        under = value.get("under_decimal_price")
        if any(isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(float(price)) or float(price) <= 1.0 for price in (over, under)):
            raise SharedPAMarketEvaluationError("paired market decimal prices must be finite and greater than one")
        if value.get("no_vig_method") != "normalized_inverse_decimal_v1":
            raise SharedPAMarketEvaluationError("valid market no-vig method changed")
        expected = {
            "source_id": value["source_id"],
            "sportsbook": value["sportsbook"],
            "product_contract_id": value["product_contract_id"],
            "source_market_id": value["source_market_id"],
            "official_game_date": row["official_game_date"],
            "official_game_pk": row["official_game_pk"],
            "player_id": row["player_id"],
            "market": row["market"],
            "line": row["line"],
            "quote_observed_at_utc": observed,
        }
        if any(receipt.get(field) != expected_value for field, expected_value in expected.items()):
            raise SharedPAMarketEvaluationError("available quote receipt semantics differ from row")
        receipt_quotes = receipt["quotes"]
        if (
            float(receipt_quotes[0]["decimal_price"]) != float(over)
            or float(receipt_quotes[1]["decimal_price"]) != float(under)
        ):
            raise SharedPAMarketEvaluationError("available quote receipt prices differ from row")
        implied = (1.0 / float(over)) / ((1.0 / float(over)) + (1.0 / float(under)))
        probability = _probability(value.get("probability"), "valid market probability", epsilon)
        if not math.isclose(probability, implied, rel_tol=0.0, abs_tol=1e-12):
            raise SharedPAMarketEvaluationError("valid market probability differs from paired no-vig prices")
        return probability, None
    if set(value) != {"available", "probability", "artifact_sha256", "probability_scale", "distribution"}:
        raise SharedPAMarketEvaluationError(f"baseline {name} surface changed")
    if value.get("available") is not True:
        raise SharedPAMarketEvaluationError(f"required baseline {name} is unavailable")
    _sha(value.get("artifact_sha256"), f"baseline {name} artifact")
    probability = _probability(value.get("probability"), f"baseline {name} probability", epsilon)
    if row["market"] == "total_bases":
        if value.get("probability_scale") != "total_bases_count_distribution_v1":
            raise SharedPAMarketEvaluationError(f"baseline {name} Total Bases scale differs")
        distribution = _distribution(value.get("distribution"), f"baseline {name} distribution")
        derived = _distribution_tail(distribution, row["line"])
        if not math.isclose(probability, derived, rel_tol=0.0, abs_tol=1e-12):
            raise SharedPAMarketEvaluationError(f"baseline {name} probability is not the structural Total Bases tail")
    elif value.get("probability_scale") != "binary_market_tail_v1" or value.get("distribution") is not None:
        raise SharedPAMarketEvaluationError(f"baseline {name} probability scale differs")
    return probability, distribution if row["market"] == "total_bases" else None


def validate_rows(
    rows: Sequence[Mapping[str, Any]], contract: Mapping[str, Any],
    source_authority: VerifiedSourceAuthority,
) -> list[ValidatedRow]:
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)) or not rows:
        raise SharedPAMarketEvaluationError("evaluation rows must be a nonempty sequence")
    epsilon = float(contract["probability_epsilon"])
    output: list[ValidatedRow] = []
    if (
        source_authority.authority_state != SYNTHETIC_SOURCE_AUTHORITY
        or source_authority.protocol_id != SOURCE_RECEIPT_PROTOCOL
        or source_authority.evidence_window_id != "synthetic_test_only"
    ):
        raise SharedPAMarketEvaluationError("source authority is not the exact synthetic structural authority")
    seen: set[tuple[str, int, int, str, str]] = set()
    required = {
        "official_game_date", "official_game_pk", "player_id", "market", "line", "settlement_status", "outcome_value",
        "prediction_created_at_utc", "decision_horizon_utc", "scheduled_start_utc", "outcome_observed_at_utc",
        "evidence_window_id", "candidate_available", "candidate_probability", "candidate_distribution",
        "abstention_reason", "baselines",
    }
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping) or set(row) != required:
            raise SharedPAMarketEvaluationError(f"row {index} surface changed")
        official = _official_date(row.get("official_game_date"))
        game = row.get("official_game_pk")
        player = row.get("player_id")
        if isinstance(game, bool) or not isinstance(game, int) or game <= 0:
            raise SharedPAMarketEvaluationError("official_game_pk must be positive integer")
        if isinstance(player, bool) or not isinstance(player, int) or player <= 0:
            raise SharedPAMarketEvaluationError("player_id must be positive integer")
        market = row.get("market")
        if market not in MARKETS:
            raise SharedPAMarketEvaluationError("row market is not independently supported")
        line = _line(row.get("line"))
        if market == "hr_over_0_5" and line != "0.5":
            raise SharedPAMarketEvaluationError("HR contract is exactly over 0.5")
        status = row.get("settlement_status")
        if status not in {"GRADED", "PUSH", "VOID", "UNMATCHED"}:
            raise SharedPAMarketEvaluationError("settlement_status is not canonical")
        outcome_value = row.get("outcome_value")
        if status in {"VOID", "UNMATCHED"}:
            if outcome_value is not None:
                raise SharedPAMarketEvaluationError("void or unmatched row must not contain an outcome value")
            outcome = None
        else:
            if isinstance(outcome_value, bool) or not isinstance(outcome_value, int) or outcome_value < 0:
                raise SharedPAMarketEvaluationError("graded or push outcome_value must be a nonnegative integer")
            if Decimal(outcome_value) == Decimal(line):
                if status != "PUSH":
                    raise SharedPAMarketEvaluationError("outcome equal to line must be an ungradeable push")
                outcome = None
            else:
                if status != "GRADED":
                    raise SharedPAMarketEvaluationError("non-push outcome must be GRADED")
                outcome = int(Decimal(outcome_value) > Decimal(line))
        created = _utc(row.get("prediction_created_at_utc"), "prediction_created_at_utc")
        horizon = _utc(row.get("decision_horizon_utc"), "decision_horizon_utc")
        start = _utc(row.get("scheduled_start_utc"), "scheduled_start_utc")
        observed = _utc(row.get("outcome_observed_at_utc"), "outcome_observed_at_utc")
        if not created <= horizon < start < observed:
            raise SharedPAMarketEvaluationError("chronology requires prediction <= horizon < start < outcome")
        if row.get("evidence_window_id") not in contract["canonical_test_evidence_window_ids"]:
            raise SharedPAMarketEvaluationError("evidence window is not exact allowlisted synthetic authority")
        identity = (official, game, player, market, line)
        if identity in seen:
            raise SharedPAMarketEvaluationError("duplicate canonical market identity")
        seen.add(identity)
        available = row.get("candidate_available")
        if available is True:
            probability = _probability(row.get("candidate_probability"), "candidate probability", epsilon)
            if row.get("abstention_reason") is not None:
                raise SharedPAMarketEvaluationError("available candidate has abstention reason")
            abstention = None
            if market == "total_bases":
                candidate_distribution = _distribution(row.get("candidate_distribution"), "candidate Total Bases distribution")
                derived = _distribution_tail(candidate_distribution, line)
                if not math.isclose(probability, derived, rel_tol=0.0, abs_tol=1e-12):
                    raise SharedPAMarketEvaluationError("candidate probability is not the structural Total Bases tail")
            elif row.get("candidate_distribution") is not None:
                raise SharedPAMarketEvaluationError("binary candidate must not contain a count distribution")
            else:
                candidate_distribution = None
        elif available is False:
            if row.get("candidate_probability") is not None or row.get("candidate_distribution") is not None:
                raise SharedPAMarketEvaluationError("abstained candidate contains fabricated probability evidence")
            abstention = row.get("abstention_reason")
            if abstention not in ABSTENTION_REASONS:
                raise SharedPAMarketEvaluationError("abstention lacks canonical reason")
            probability = None
            candidate_distribution = None
        else:
            raise SharedPAMarketEvaluationError("candidate availability must be boolean")
        baseline_values = row.get("baselines")
        if not isinstance(baseline_values, Mapping) or set(baseline_values) != set(BASELINES):
            raise SharedPAMarketEvaluationError("baseline set changed")
        checked = {
            name: _validate_baseline(
                name, baseline_values[name], row=row, epsilon=epsilon,
                source_authority=source_authority,
            )
            for name in BASELINES
        }
        probabilities = {name: checked[name][0] for name in BASELINES}
        distributions = {name: checked[name][1] for name in BASELINES}
        output.append(ValidatedRow(
            identity, official, game, player, market, line, status,
            int(outcome_value) if isinstance(outcome_value, int) and not isinstance(outcome_value, bool) else None,
            outcome,
            probability, candidate_distribution, available, abstention, probabilities, distributions,
        ))
    return output


def _score(probabilities: Sequence[float], outcomes: Sequence[int]) -> dict[str, float]:
    if not probabilities or len(probabilities) != len(outcomes):
        raise SharedPAMarketEvaluationError("score requires nonempty aligned rows")
    count = len(probabilities)
    brier = sum((p - y) ** 2 for p, y in zip(probabilities, outcomes, strict=True)) / count
    log_loss = -sum(y * math.log(p) + (1 - y) * math.log1p(-p) for p, y in zip(probabilities, outcomes, strict=True)) / count
    return {"count": count, "brier": brier, "log_loss": log_loss}


def _distribution_score(
    distributions: Sequence[Mapping[int, float]], outcomes: Sequence[int], *, epsilon: float,
) -> dict[str, Any]:
    if not distributions or len(distributions) != len(outcomes):
        raise SharedPAMarketEvaluationError("distribution score requires nonempty aligned rows")
    brier_values: list[float] = []
    log_values: list[float] = []
    rps_values: list[float] = []
    zero_mass = 0
    for distribution, outcome in zip(distributions, outcomes, strict=True):
        maximum = max(max(distribution), outcome)
        brier_values.append(sum((distribution.get(count, 0.0) - int(count == outcome)) ** 2 for count in range(maximum + 1)))
        realized = distribution.get(outcome, 0.0)
        if realized < epsilon:
            zero_mass += 1
        else:
            log_values.append(-math.log(realized))
        cumulative = 0.0
        ranked = 0.0
        for count in range(maximum):
            cumulative += distribution.get(count, 0.0)
            ranked += (cumulative - int(outcome <= count)) ** 2
        rps_values.append(ranked)
    return {
        "count": len(distributions),
        "multiclass_brier": sum(brier_values) / len(brier_values),
        "ranked_probability_score": sum(rps_values) / len(rps_values),
        "log_loss": sum(log_values) / len(log_values) if not zero_mass else None,
        "log_loss_status": "OK" if not zero_mass else "ZERO_REALIZED_MASS_FAIL_CLOSED",
        "zero_realized_mass_count": zero_mass,
    }


def _distribution_loss(distribution: Mapping[int, float], outcome: int, metric: str, epsilon: float) -> float | None:
    maximum = max(max(distribution), outcome)
    if metric == "multiclass_brier":
        return sum((distribution.get(count, 0.0) - int(count == outcome)) ** 2 for count in range(maximum + 1))
    if metric == "ranked_probability_score":
        cumulative = 0.0
        result = 0.0
        for count in range(maximum):
            cumulative += distribution.get(count, 0.0)
            result += (cumulative - int(outcome <= count)) ** 2
        return result
    if metric == "log_loss":
        realized = distribution.get(outcome, 0.0)
        return -math.log(realized) if realized >= epsilon else None
    raise SharedPAMarketEvaluationError("unknown full-distribution metric")


def _distribution_cluster_interval(
    rows: Sequence[ValidatedRow], baseline: str, *, metric: str, cluster: str,
    draws: int, seed: int, confidence: float, epsilon: float,
) -> dict[str, Any]:
    common = [
        row for row in rows
        if row.outcome_value is not None and row.candidate_distribution is not None
        and row.baseline_distributions[baseline] is not None
    ]
    groups: dict[Any, list[ValidatedRow]] = defaultdict(list)
    for row in common:
        key = row.official_game_date if cluster == "date" else (row.official_game_date, row.official_game_pk)
        groups[key].append(row)
    keys = sorted(groups)
    if len(keys) < 2:
        return {"status": "INSUFFICIENT_CLUSTERS", "cluster_count": len(keys), "interval": None}
    row_deltas: dict[tuple[str, int, int, str, str], float] = {}
    for row in common:
        candidate_loss = _distribution_loss(row.candidate_distribution, row.outcome_value, metric, epsilon)
        baseline_loss = _distribution_loss(row.baseline_distributions[baseline], row.outcome_value, metric, epsilon)
        if candidate_loss is None or baseline_loss is None:
            return {"status": "ZERO_REALIZED_MASS_FAIL_CLOSED", "cluster_count": len(keys), "interval": None}
        row_deltas[row.identity] = candidate_loss - baseline_loss
    rng = random.Random(seed)
    samples = []
    for _ in range(draws):
        selected = [keys[rng.randrange(len(keys))] for _ in keys]
        deltas = [row_deltas[row.identity] for key in selected for row in groups[key]]
        samples.append(sum(deltas) / len(deltas))
    tail = (1.0 - confidence) / 2.0
    return {
        "status": "OK", "cluster_count": len(keys), "draws": draws,
        "interval": [_percentile(samples, tail), _percentile(samples, 1.0-tail)],
    }


def _auc(probabilities: Sequence[float], outcomes: Sequence[int]) -> dict[str, Any]:
    positives = sum(outcomes)
    negatives = len(outcomes) - positives
    if positives == 0 or negatives == 0:
        return {"status": "INSUFFICIENT_CLASSES", "auc": None, "positive_count": positives, "negative_count": negatives}
    concordance = 0.0
    for p_pos, y_pos in zip(probabilities, outcomes, strict=True):
        if y_pos != 1:
            continue
        for p_neg, y_neg in zip(probabilities, outcomes, strict=True):
            if y_neg != 0:
                continue
            concordance += 1.0 if p_pos > p_neg else 0.5 if p_pos == p_neg else 0.0
    return {"status": "OK", "auc": concordance / (positives * negatives), "positive_count": positives, "negative_count": negatives}


def _calibration_intercept_slope(probabilities: Sequence[float], outcomes: Sequence[int]) -> dict[str, Any]:
    positives = sum(outcomes)
    if positives == 0 or positives == len(outcomes):
        return {"status": "INSUFFICIENT_CLASSES", "intercept": None, "slope": None, "iterations": 0}
    logits = [math.log(probability) - math.log1p(-probability) for probability in probabilities]
    if max(logits) == min(logits):
        return {"status": "UNIDENTIFIABLE_CONSTANT_PREDICTION", "intercept": None, "slope": None, "iterations": 0}
    mean = positives / len(outcomes)
    intercept = math.log(mean) - math.log1p(-mean)
    slope = 1.0
    for iteration in range(1, 101):
        fitted = []
        for value in logits:
            linear = intercept + slope * value
            if linear >= 0:
                fitted.append(1.0 / (1.0 + math.exp(-linear)))
            else:
                exponential = math.exp(linear)
                fitted.append(exponential / (1.0 + exponential))
        weights = [value * (1.0 - value) for value in fitted]
        info_00 = sum(weights)
        info_01 = sum(weight * value for weight, value in zip(weights, logits, strict=True))
        info_11 = sum(weight * value * value for weight, value in zip(weights, logits, strict=True))
        determinant = info_00 * info_11 - info_01 * info_01
        if determinant <= 1e-18:
            return {"status": "UNIDENTIFIABLE_OR_SEPARATED", "intercept": None, "slope": None, "iterations": iteration}
        score_0 = sum(outcome - fit for outcome, fit in zip(outcomes, fitted, strict=True))
        score_1 = sum((outcome - fit) * value for outcome, fit, value in zip(outcomes, fitted, logits, strict=True))
        step_intercept = (info_11 * score_0 - info_01 * score_1) / determinant
        step_slope = (-info_01 * score_0 + info_00 * score_1) / determinant
        intercept += step_intercept
        slope += step_slope
        if not math.isfinite(intercept) or not math.isfinite(slope) or abs(intercept) > 50 or abs(slope) > 50:
            return {"status": "UNIDENTIFIABLE_OR_SEPARATED", "intercept": None, "slope": None, "iterations": iteration}
        if max(abs(step_intercept), abs(step_slope)) <= 1e-10:
            return {"status": "OK", "intercept": intercept, "slope": slope, "iterations": iteration}
    return {"status": "NONCONVERGED", "intercept": None, "slope": None, "iterations": 100}


def _calibration(probabilities: Sequence[float], outcomes: Sequence[int], edges: Sequence[float]) -> dict[str, Any]:
    bins = []
    for index, (lower, upper) in enumerate(zip(edges[:-1], edges[1:], strict=True)):
        selected = [
            i for i, probability in enumerate(probabilities)
            if (lower <= probability <= upper if index == len(edges) - 2 else lower <= probability < upper)
        ]
        bins.append({
            "lower": lower,
            "upper": upper,
            "count": len(selected),
            "mean_probability": sum(probabilities[i] for i in selected) / len(selected) if selected else None,
            "observed_rate": sum(outcomes[i] for i in selected) / len(selected) if selected else None,
        })
    return {
        "mean_probability": sum(probabilities) / len(probabilities),
        "observed_rate": sum(outcomes) / len(outcomes),
        "calibration_in_the_large": sum(probabilities) / len(probabilities) - sum(outcomes) / len(outcomes),
        "intercept_slope": _calibration_intercept_slope(probabilities, outcomes),
        "bins": bins,
    }


def _percentile(values: Sequence[float], quantile: float) -> float:
    ordered = sorted(values)
    position = quantile * (len(ordered) - 1)
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[low]
    return ordered[low] * (high - position) + ordered[high] * (position - low)


def _cluster_interval(
    rows: Sequence[ValidatedRow], baseline: str, *, metric: str, cluster: str, draws: int, seed: int, confidence: float,
) -> dict[str, Any]:
    common = [
        row for row in rows
        if row.outcome is not None and row.candidate_probability is not None and row.baselines[baseline] is not None
    ]
    groups: dict[Any, list[ValidatedRow]] = defaultdict(list)
    for row in common:
        key = row.official_game_date if cluster == "date" else (row.official_game_date, row.official_game_pk)
        groups[key].append(row)
    keys = sorted(groups)
    if len(keys) < 2:
        return {"status": "INSUFFICIENT_CLUSTERS", "cluster_count": len(keys), "interval": None}
    rng = random.Random(seed)
    samples = []
    for _ in range(draws):
        selected = [keys[rng.randrange(len(keys))] for _ in keys]
        deltas = []
        for key in selected:
            for row in groups[key]:
                candidate = row.candidate_probability
                baseline_p = row.baselines[baseline]
                if metric == "brier":
                    deltas.append((candidate - row.outcome) ** 2 - (baseline_p - row.outcome) ** 2)
                else:
                    y = row.outcome
                    deltas.append(-(y * math.log(candidate) + (1-y) * math.log1p(-candidate)) + (y * math.log(baseline_p) + (1-y) * math.log1p(-baseline_p)))
        samples.append(sum(deltas) / len(deltas))
    tail = (1.0 - confidence) / 2.0
    return {"status": "OK", "cluster_count": len(keys), "draws": draws, "interval": [_percentile(samples, tail), _percentile(samples, 1.0-tail)]}


def _product_key(line: str) -> str:
    return f"over_{line}"


def _product_report(
    rows: Sequence[ValidatedRow], market: str, line: str, contract: Mapping[str, Any],
) -> dict[str, Any]:
    universe = [row for row in rows if row.market == market and row.line == line]
    available = [row for row in universe if row.candidate_probability is not None]
    graded_available = [row for row in available if row.outcome is not None]
    abstentions: dict[str, int] = defaultdict(int)
    for row in universe:
        if row.abstention_reason:
            abstentions[row.abstention_reason] += 1
    candidate_metrics = None
    if graded_available:
        probabilities = [row.candidate_probability for row in graded_available]
        outcomes = [row.outcome for row in graded_available]
        candidate_metrics = {
            **_score(probabilities, outcomes),
            "calibration": _calibration(probabilities, outcomes, contract["calibration_bin_edges"]),
            "discrimination": _auc(probabilities, outcomes),
        }
        if market == "total_bases":
            candidate_metrics["full_distribution"] = _distribution_score(
                [row.candidate_distribution for row in graded_available],
                [row.outcome_value for row in graded_available],
                epsilon=float(contract["probability_epsilon"]),
            )
    comparisons: dict[str, Any] = {}
    uncertainty = contract["uncertainty"]
    for offset, baseline in enumerate(BASELINES):
        candidate_only = sum(row.candidate_probability is not None and row.baselines[baseline] is None for row in universe)
        baseline_only = sum(row.candidate_probability is None and row.baselines[baseline] is not None for row in universe)
        neither = sum(row.candidate_probability is None and row.baselines[baseline] is None for row in universe)
        common_all = [row for row in universe if row.candidate_probability is not None and row.baselines[baseline] is not None]
        common = [row for row in common_all if row.outcome is not None]
        coverage = {
            "eligible_universe": len(universe),
            "candidate_available": len(available),
            "baseline_available": sum(row.baselines[baseline] is not None for row in universe),
            "common_support_all_settlements": len(common_all),
            "common_support_graded": len(common),
            "candidate_only": candidate_only,
            "baseline_only": baseline_only,
            "neither": neither,
            "ungradeable_common_support": len(common_all) - len(common),
        }
        if common:
            common_outcomes = [row.outcome for row in common]
            candidate_probabilities = [row.candidate_probability for row in common]
            baseline_probabilities = [row.baselines[baseline] for row in common]
            candidate = _score(candidate_probabilities, common_outcomes)
            baseline_score = _score(baseline_probabilities, common_outcomes)
            comparisons[baseline] = {
                "status": "SCORED_COMMON_SUPPORT",
                "common_support": len(common),
                "coverage": coverage,
                "candidate_common_support_metrics": {
                    **candidate,
                    "calibration": _calibration(candidate_probabilities, common_outcomes, contract["calibration_bin_edges"]),
                    "discrimination": _auc(candidate_probabilities, common_outcomes),
                },
                "baseline_common_support_metrics": {
                    **baseline_score,
                    "calibration": _calibration(baseline_probabilities, common_outcomes, contract["calibration_bin_edges"]),
                    "discrimination": _auc(baseline_probabilities, common_outcomes),
                },
                "candidate_minus_baseline_brier": candidate["brier"] - baseline_score["brier"],
                "candidate_minus_baseline_log_loss": candidate["log_loss"] - baseline_score["log_loss"],
                "date_cluster": {
                    metric: _cluster_interval(common, baseline, metric=metric, cluster="date", draws=uncertainty["bootstrap_draws"], seed=uncertainty["bootstrap_seed"] + offset * 10 + (0 if metric == "brier" else 1), confidence=uncertainty["confidence_level"])
                    for metric in ("brier", "log_loss")
                },
                "game_cluster": {
                    metric: _cluster_interval(common, baseline, metric=metric, cluster="game", draws=uncertainty["bootstrap_draws"], seed=uncertainty["bootstrap_seed"] + offset * 10 + (2 if metric == "brier" else 3), confidence=uncertainty["confidence_level"])
                    for metric in ("brier", "log_loss")
                },
            }
            if market == "total_bases" and baseline != "valid_market_implied":
                candidate_distribution_score = _distribution_score(
                    [row.candidate_distribution for row in common],
                    [row.outcome_value for row in common],
                    epsilon=float(contract["probability_epsilon"]),
                )
                baseline_distribution_score = _distribution_score(
                    [row.baseline_distributions[baseline] for row in common],
                    [row.outcome_value for row in common],
                    epsilon=float(contract["probability_epsilon"]),
                )
                comparisons[baseline]["full_distribution"] = {
                    "candidate": candidate_distribution_score,
                    "baseline": baseline_distribution_score,
                    "candidate_minus_baseline_multiclass_brier": (
                        candidate_distribution_score["multiclass_brier"] - baseline_distribution_score["multiclass_brier"]
                    ),
                    "candidate_minus_baseline_ranked_probability_score": (
                        candidate_distribution_score["ranked_probability_score"] - baseline_distribution_score["ranked_probability_score"]
                    ),
                    "candidate_minus_baseline_log_loss": (
                        candidate_distribution_score["log_loss"] - baseline_distribution_score["log_loss"]
                        if candidate_distribution_score["log_loss"] is not None and baseline_distribution_score["log_loss"] is not None
                        else None
                    ),
                    "date_cluster": {
                        metric: _distribution_cluster_interval(
                            common, baseline, metric=metric, cluster="date",
                            draws=uncertainty["bootstrap_draws"],
                            seed=uncertainty["bootstrap_seed"] + offset * 100 + 20 + metric_index,
                            confidence=uncertainty["confidence_level"],
                            epsilon=float(contract["probability_epsilon"]),
                        )
                        for metric_index, metric in enumerate(("multiclass_brier", "ranked_probability_score", "log_loss"))
                    },
                    "game_cluster": {
                        metric: _distribution_cluster_interval(
                            common, baseline, metric=metric, cluster="game",
                            draws=uncertainty["bootstrap_draws"],
                            seed=uncertainty["bootstrap_seed"] + offset * 100 + 30 + metric_index,
                            confidence=uncertainty["confidence_level"],
                            epsilon=float(contract["probability_epsilon"]),
                        )
                        for metric_index, metric in enumerate(("multiclass_brier", "ranked_probability_score", "log_loss"))
                    },
                }
        else:
            comparisons[baseline] = {"status": "NO_GRADED_COMMON_SUPPORT", "common_support": 0, "coverage": coverage}
    hr_tail = None
    if market == "hr_over_0_5":
        def tail(name: str) -> list[dict[str, Any]]:
            eligible = [
                (row, row.candidate_probability if name == "candidate" else row.baselines[name])
                for row in universe
                if row.outcome is not None and (row.candidate_probability if name == "candidate" else row.baselines[name]) is not None
            ]
            output = []
            for threshold in contract["hr_upper_tail_thresholds"]:
                selected = [(row, probability) for row, probability in eligible if probability >= threshold]
                output.append({
                    "threshold": threshold,
                    "count": len(selected),
                    "mean_probability": sum(probability for _, probability in selected) / len(selected) if selected else None,
                    "observed_rate": sum(row.outcome for row, _ in selected) / len(selected) if selected else None,
                })
            return output
        hr_tail = {"candidate": tail("candidate"), "baselines": {name: tail(name) for name in BASELINES}}
    return {
        "market": market,
        "line": line,
        "product_key": _product_key(line),
        "state": "METRICS_ONLY_UNBOUND_NO_QUALIFICATION",
        "coverage": {
            "eligible_universe": len(universe),
            "candidate_available": len(available),
            "candidate_available_graded": len(graded_available),
            "candidate_abstained": len(universe) - len(available),
            "candidate_coverage": len(available) / len(universe) if universe else 0.0,
            "reason_coded_abstentions": dict(sorted(abstentions.items())),
            "baseline_available": {name: sum(row.baselines[name] is not None for row in universe) for name in BASELINES},
            "settlement_status": {
                status: sum(row.settlement_status == status for row in universe)
                for status in ("GRADED", "PUSH", "VOID", "UNMATCHED")
            },
            "ungradeable": sum(row.outcome is None for row in universe),
        },
        "candidate_metrics": candidate_metrics,
        "comparisons": comparisons,
        "hr_upper_tail": hr_tail,
        "qualification": "REFUSED_EXTERNAL_MATERIALITY_AND_EVIDENCE_AUTHORITIES_ABSENT",
    }


def _market_report(rows: Sequence[ValidatedRow], market: str, contract: Mapping[str, Any]) -> dict[str, Any]:
    lines = sorted({row.line for row in rows if row.market == market}, key=Decimal)
    return {
        "market": market,
        "products": {
            _product_key(line): _product_report(rows, market, line, contract)
            for line in lines
        },
        "pooled_across_products": None,
        "cross_product_rescue": False,
        "qualification": "REFUSED_PRODUCTS_REMAIN_INDEPENDENT_AND_ENGINE_UNBOUND",
    }


def evaluate_market_separated(
    rows: Sequence[Mapping[str, Any]],
    contract: Mapping[str, Any],
    *,
    external_authority: Mapping[str, Any] | None = None,
    source_authority: VerifiedSourceAuthority | None = None,
    allow_unbound_synthetic_test_only: bool = False,
) -> dict[str, Any]:
    checked_contract = validate_contract(contract)
    if external_authority is not None:
        raise SharedPAMarketEvaluationError("external authority verification is not implemented; engine remains UNBOUND")
    if not allow_unbound_synthetic_test_only:
        raise SharedPAMarketEvaluationError("evaluation engine is UNBOUND; authoritative evaluation refused")
    if source_authority is None:
        raise SharedPAMarketEvaluationError("synthetic mechanics require referenced receipt-byte source authority")
    checked = validate_rows(rows, checked_contract, source_authority)
    reports = {market: _market_report(checked, market, checked_contract) for market in MARKETS}
    return {
        "schema_version": REPORT_SCHEMA,
        "contract_id": checked_contract["contract_id"],
        "authority_state": UNBOUND,
        "evidence_class": "SYNTHETIC_STRUCTURAL_MECHANICS_ONLY",
        "source_authority_id": source_authority.authority_id,
        "source_authority_manifest_sha256": source_authority.manifest_sha256,
        "research_only": True,
        "betting_authorized": False,
        "capture_lower_bound_minimum": 0.1,
        "market_order": list(MARKETS),
        "markets": reports,
        "pooled_summary": None,
        "cross_market_rescue": False,
        "overall_qualification": "REFUSED_MARKETS_REMAIN_INDEPENDENT_AND_ENGINE_UNBOUND",
    }
