"""Fail-closed validation for account-visible, non-submitted product evidence.

This schema records what the user could actually see in an execution product
without placing an entry.  It does not prove acceptance, fill, settlement, or
profitability and can never authorize wagering by itself.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from src.evaluation.execution_product_contracts import validate_execution_product_contracts
from src.utils.provenance import sha256_file


SCHEMA = "execution-product-account-visible-observation-v1"
PRODUCTS = frozenset({"onyx", "novig", "chalkboard", "prizepicks"})


class ExecutionProductObservationError(ValueError):
    """Raised when account-visible evidence is incomplete, pooled, or overstated."""


def _hash(value: Any, label: str) -> str:
    out = str(value).strip().lower()
    if len(out) != 64 or any(char not in "0123456789abcdef" for char in out):
        raise ExecutionProductObservationError(f"{label} must be a SHA-256 digest")
    return out


def _positive(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise ExecutionProductObservationError(f"{label} must be positive")
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise ExecutionProductObservationError(f"{label} must be positive") from exc
    if out <= 0:
        raise ExecutionProductObservationError(f"{label} must be positive")
    return out


def _nonempty(value: Any, label: str) -> str:
    out = str(value).strip()
    if not out:
        raise ExecutionProductObservationError(f"{label} cannot be blank")
    return out


def _load(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExecutionProductObservationError(f"cannot read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ExecutionProductObservationError(f"{label} must be a JSON object")
    return value


def _lineup(offer: dict[str, Any], *, product: str) -> None:
    legs = offer.get("complete_lineup")
    if not isinstance(legs, list) or len(legs) < 2:
        raise ExecutionProductObservationError(f"{product} requires the complete multi-leg lineup")
    teams: set[str] = set()
    hits = 0
    for index, leg in enumerate(legs):
        if not isinstance(leg, dict):
            raise ExecutionProductObservationError(f"{product} lineup leg {index} is malformed")
        for field in ("player_display", "team", "category", "side", "projection", "leg_multiplier"):
            _nonempty(leg.get(field), f"{product}.complete_lineup[{index}].{field}")
        _positive(leg["projection"], f"{product}.complete_lineup[{index}].projection")
        _positive(leg["leg_multiplier"], f"{product}.complete_lineup[{index}].leg_multiplier")
        teams.add(str(leg["team"]))
        hits += str(leg["category"]).lower() == "hits"
    if len(teams) < 2 or hits < 1:
        raise ExecutionProductObservationError(
            f"{product} lineup must retain the observed Hits leg and at least two teams"
        )
    for field in ("lineup_type", "overall_multiplier", "risk_amount", "potential_payout"):
        _nonempty(offer.get(field), f"{product}.{field}")
    for field in ("overall_multiplier", "risk_amount", "potential_payout"):
        _positive(offer[field], f"{product}.{field}")


def validate_execution_product_observation(
    path: str | Path,
    *,
    evidence_root: str | Path,
    contracts_path: str | Path,
) -> tuple[dict[str, Any], str]:
    source = Path(path).resolve()
    payload = _load(source, "execution-product observation")
    if payload.get("schema_version") != SCHEMA:
        raise ExecutionProductObservationError("unknown execution-product observation schema")
    product = str(payload.get("product", "")).lower()
    if product not in PRODUCTS:
        raise ExecutionProductObservationError("observation product is unknown")
    if (
        payload.get("status") != "ACCOUNT_VISIBLE_SHADOW_ONLY"
        or payload.get("betting_authorized") is not False
        or payload.get("entry_submitted") is not False
        or payload.get("accepted_receipt") is not None
        or payload.get("actual_return") is not None
    ):
        raise ExecutionProductObservationError("account-visible evidence cannot claim submission or authorization")
    try:
        stamp = datetime.fromisoformat(str(payload.get("captured_at_utc", "")).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ExecutionProductObservationError("captured_at_utc is invalid") from exc
    if stamp.tzinfo is None:
        raise ExecutionProductObservationError("captured_at_utc must include a timezone")
    jurisdiction = payload.get("jurisdiction")
    if not isinstance(jurisdiction, dict) or jurisdiction.get("state") != "Texas" or jurisdiction.get("county") != "Harris":
        raise ExecutionProductObservationError("observation jurisdiction differs from the declared context")
    capture = payload.get("capture_method")
    if not isinstance(capture, dict) or (
        capture.get("kind") not in {"user_supplied_account_screenshot", "approved_product_export"}
        or capture.get("automated_scraping") is not False
    ):
        raise ExecutionProductObservationError("only user evidence or an approved export is admissible")
    raw = payload.get("raw_evidence")
    if not isinstance(raw, dict):
        raise ExecutionProductObservationError("raw evidence binding is absent")
    raw_path = Path(evidence_root).resolve() / _nonempty(raw.get("path"), "raw_evidence.path")
    if not raw_path.is_file() or sha256_file(raw_path) != _hash(raw.get("sha256"), "raw_evidence.sha256"):
        raise ExecutionProductObservationError("raw account-visible evidence is missing or changed")

    identity = payload.get("market_identity")
    if not isinstance(identity, dict):
        raise ExecutionProductObservationError("hard market identity is absent")
    if (
        int(identity.get("mlb_game_pk", 0)) <= 0
        or int(identity.get("player_id", 0)) <= 0
        or str(identity.get("category", "")).lower() != "hits"
        or str(identity.get("side", "")).lower() not in {"over", "under", "more", "less"}
    ):
        raise ExecutionProductObservationError("market identity is unresolved or outside Hits")
    _positive(identity.get("line"), "market_identity.line")
    _hash(identity.get("game_identity_sha256"), "game_identity_sha256")
    _hash(identity.get("player_identity_sha256"), "player_identity_sha256")
    for field in ("provider_event_id", "player_display", "provider_start_time"):
        _nonempty(identity.get(field), f"market_identity.{field}")

    contracts, _ = validate_execution_product_contracts(contracts_path)
    contract = contracts["products"][product]
    binding = payload.get("product_contract")
    if not isinstance(binding, dict) or (
        binding.get("contract_id") != contract["contract_id"]
        or _hash(binding.get("rules_sha256"), "product_contract.rules_sha256")
        != contract["rules_and_effective_date_hash"]
    ):
        raise ExecutionProductObservationError("product observation is not bound to its own current rules")

    offer = payload.get("observed_offer")
    if not isinstance(offer, dict):
        raise ExecutionProductObservationError("product-specific observed offer is absent")
    if product == "onyx":
        for field in (
            "currency", "redemption_value_per_unit", "offered_odds_or_multiplier",
            "minimum_entry", "maximum_entry", "hypothetical_risk", "displayed_payout",
        ):
            _nonempty(offer.get(field), f"onyx.{field}")
        for field in ("redemption_value_per_unit", "minimum_entry", "maximum_entry", "hypothetical_risk", "displayed_payout"):
            _positive(offer[field], f"onyx.{field}")
    elif product == "novig":
        for field in ("displayed_bid", "displayed_ask", "available_liquidity"):
            _positive(offer.get(field), f"novig.{field}")
        if any(offer.get(field) is not None for field in ("requested_price", "matched_price", "matched_amount")):
            raise ExecutionProductObservationError("non-submitted Novig evidence cannot claim an order or match")
    else:
        _lineup(offer, product=product)

    return payload, sha256_file(source)
