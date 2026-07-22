"""Fail-closed validation for separate Hits execution-product contracts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.utils.provenance import sha256_file


SCHEMA = "hits-execution-product-contracts-v1"
PRODUCTS = frozenset({"onyx", "novig", "chalkboard", "prizepicks"})


class ExecutionProductContractError(ValueError):
    """Raised when products are pooled or a missing gate fails open."""


@dataclass(frozen=True)
class ExecutionProductReadiness:
    product: str
    ready: bool
    blockers: tuple[str, ...]


def validate_execution_product_contracts(path: str | Path) -> tuple[dict[str, Any], str]:
    source = Path(path).resolve()
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ExecutionProductContractError("execution-product contract is malformed") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA:
        raise ExecutionProductContractError("execution-product contract has an unknown schema")
    if payload.get("status") != "RESEARCH_ONLY" or payload.get("betting_authorized") is not False:
        raise ExecutionProductContractError("execution-product contract must remain research-only")
    reference = payload.get("reference_market")
    if not isinstance(reference, dict) or (
        reference.get("product") != "DraftKings Sportsbook"
        or reference.get("role") != "reference_market_only"
        or reference.get("execution_product") is not False
        or reference.get("may_authorize_execution") is not False
    ):
        raise ExecutionProductContractError("DraftKings must remain a non-executable reference market")
    if payload.get("cross_product_inheritance_permitted") is not False:
        raise ExecutionProductContractError("cross-product rule or payout inheritance must be forbidden")
    products = payload.get("products")
    if not isinstance(products, dict) or set(products) != PRODUCTS:
        raise ExecutionProductContractError("execution products must be exactly Onyx, Novig, Chalkboard, and PrizePicks")
    contract_ids: set[str] = set()
    root = source.parent.parent
    for key, product in products.items():
        if not isinstance(product, dict):
            raise ExecutionProductContractError(f"{key} product contract must be an object")
        contract_id = str(product.get("contract_id", "")).strip()
        required = product.get("required_product_fields")
        if not contract_id or contract_id in contract_ids:
            raise ExecutionProductContractError("every product needs a unique nonblank contract_id")
        contract_ids.add(contract_id)
        if not isinstance(required, list) or not required or not all(str(value).strip() for value in required):
            raise ExecutionProductContractError(f"{key} required product fields are incomplete")
        if product.get("authorization") is not False:
            raise ExecutionProductContractError(f"{key} authorization must fail closed")
        rules_path = str(product.get("rules_observation_path", "")).strip()
        rules_hash = str(product.get("rules_and_effective_date_hash", "")).strip().lower()
        rules_source = root / rules_path
        if not rules_path or len(rules_hash) != 64 or not rules_source.is_file():
            raise ExecutionProductContractError(f"{key} public rules observation is missing")
        if sha256_file(rules_source) != rules_hash:
            raise ExecutionProductContractError(f"{key} public rules observation hash differs")
        try:
            rules = json.loads(rules_source.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ExecutionProductContractError(f"{key} public rules observation is malformed") from exc
        if (
            not isinstance(rules, dict)
            or rules.get("schema_version") != "execution-product-public-rules-observation-v1"
            or rules.get("product") != key
            or rules.get("status") != "PARTIAL_PUBLIC_RULES_ONLY"
            or rules.get("betting_authorized") is not False
            or rules.get("cross_product_inheritance_permitted") is not False
        ):
            raise ExecutionProductContractError(f"{key} public rules observation changed scope")
    jurisdiction = payload.get("jurisdiction_context")
    if not isinstance(jurisdiction, dict) or jurisdiction.get("state") != "Texas":
        raise ExecutionProductContractError("declared jurisdiction context is absent or changed")
    return payload, sha256_file(source)


def product_readiness(path: str | Path) -> tuple[ExecutionProductReadiness, ...]:
    payload, _ = validate_execution_product_contracts(path)
    output: list[ExecutionProductReadiness] = []
    for key in sorted(PRODUCTS):
        product = payload["products"][key]
        blockers: list[str] = []
        for field in (
            "legal_and_account_access_verified",
            "live_account_visible_hits_verified",
            "actual_submit_accept_receipt_verified",
            "actual_settlement_receipt_verified",
        ):
            if product.get(field) is not True:
                blockers.append(field)
        for field in ("rules_and_effective_date_hash", "payout_and_fee_schedule_hash"):
            value = product.get(field)
            if not isinstance(value, str) or len(value) != 64:
                blockers.append(field)
        output.append(ExecutionProductReadiness(key, not blockers, tuple(blockers)))
    return tuple(output)
