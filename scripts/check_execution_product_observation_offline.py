#!/usr/bin/env python3
"""Mutation checks for account-visible, non-submitted product observations."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.execution_product_observation import (  # noqa: E402
    ExecutionProductObservationError,
    validate_execution_product_observation,
)


CONTRACTS = ROOT / "config/hits_execution_product_contracts.json"
PASS = 0
FAIL = 0


def check(condition: bool, label: str) -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [OK] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}")


def valid(product: str, raw_hash: str, contract: dict) -> dict:
    common = {
        "schema_version": "execution-product-account-visible-observation-v1",
        "product": product,
        "status": "ACCOUNT_VISIBLE_SHADOW_ONLY",
        "betting_authorized": False,
        "entry_submitted": False,
        "accepted_receipt": None,
        "actual_return": None,
        "captured_at_utc": "2026-07-17T12:00:00Z",
        "jurisdiction": {"state": "Texas", "county": "Harris", "product_geolocation_passed": True},
        "capture_method": {"kind": "user_supplied_account_screenshot", "automated_scraping": False},
        "raw_evidence": {"path": "screen.bin", "sha256": raw_hash},
        "market_identity": {
            "provider_event_id": "event-1",
            "provider_start_time": "2026-07-18T00:00:00Z",
            "mlb_game_pk": 123456,
            "game_identity_sha256": "1" * 64,
            "player_display": "Example Hitter",
            "player_id": 654321,
            "player_identity_sha256": "2" * 64,
            "category": "hits",
            "line": 0.5,
            "side": "more" if product in {"chalkboard", "prizepicks"} else "over",
        },
        "product_contract": {
            "contract_id": contract["contract_id"],
            "rules_sha256": contract["rules_and_effective_date_hash"],
        },
    }
    if product == "onyx":
        common["observed_offer"] = {
            "currency": "Onyx Cash",
            "redemption_value_per_unit": 1.0,
            "offered_odds_or_multiplier": "+110",
            "minimum_entry": 1.0,
            "maximum_entry": 50.0,
            "hypothetical_risk": 1.0,
            "displayed_payout": 2.1,
        }
    elif product == "novig":
        common["observed_offer"] = {
            "displayed_bid": 0.48,
            "displayed_ask": 0.52,
            "available_liquidity": 25.0,
            "requested_price": None,
            "matched_price": None,
            "matched_amount": None,
        }
    else:
        common["observed_offer"] = {
            "lineup_type": "Power",
            "overall_multiplier": 3.0,
            "risk_amount": 5.0,
            "potential_payout": 15.0,
            "complete_lineup": [
                {
                    "player_display": "Example Hitter",
                    "team": "AAA",
                    "category": "hits",
                    "side": "more",
                    "projection": 0.5,
                    "leg_multiplier": 1.7,
                },
                {
                    "player_display": "Second Player",
                    "team": "BBB",
                    "category": "strikeouts",
                    "side": "more",
                    "projection": 4.5,
                    "leg_multiplier": 1.8,
                },
            ],
        }
    return common


def rejects(path: Path, root: Path) -> bool:
    try:
        validate_execution_product_observation(path, evidence_root=root, contracts_path=CONTRACTS)
    except ExecutionProductObservationError:
        return True
    return False


def main() -> int:
    contracts = json.loads(CONTRACTS.read_text(encoding="utf-8"))["products"]
    with tempfile.TemporaryDirectory(prefix="product_observation_") as temporary:
        root = Path(temporary)
        raw = root / "screen.bin"
        raw.write_bytes(b"redacted account-visible fixture")
        raw_hash = hashlib.sha256(raw.read_bytes()).hexdigest()
        path = root / "observation.json"
        observations = {product: valid(product, raw_hash, contracts[product]) for product in contracts}
        all_valid = True
        for payload in observations.values():
            path.write_text(json.dumps(payload), encoding="utf-8")
            try:
                validate_execution_product_observation(path, evidence_root=root, contracts_path=CONTRACTS)
            except ExecutionProductObservationError:
                all_valid = False
        check(all_valid, "four product observations validate independently")

        changed = json.loads(json.dumps(observations["onyx"]))
        changed["capture_method"]["automated_scraping"] = True
        path.write_text(json.dumps(changed), encoding="utf-8")
        check(rejects(path, root), "MUTATION automated scraping evidence is inadmissible")

        changed = json.loads(json.dumps(observations["onyx"]))
        changed["raw_evidence"]["sha256"] = "0" * 64
        path.write_text(json.dumps(changed), encoding="utf-8")
        check(rejects(path, root), "MUTATION raw evidence hash cannot be decoration")

        changed = json.loads(json.dumps(observations["onyx"]))
        changed["market_identity"]["player_id"] = 0
        path.write_text(json.dumps(changed), encoding="utf-8")
        check(rejects(path, root), "MUTATION unresolved player identity cannot enter evidence")

        changed = json.loads(json.dumps(observations["onyx"]))
        changed["product_contract"] = observations["novig"]["product_contract"]
        path.write_text(json.dumps(changed), encoding="utf-8")
        check(rejects(path, root), "MUTATION one product cannot inherit another product's rules")

        changed = json.loads(json.dumps(observations["prizepicks"]))
        changed["observed_offer"]["complete_lineup"] = changed["observed_offer"]["complete_lineup"][:1]
        path.write_text(json.dumps(changed), encoding="utf-8")
        check(rejects(path, root), "MUTATION an isolated PrizePicks leg cannot imply lineup payout")

        changed = json.loads(json.dumps(observations["novig"]))
        changed["observed_offer"]["matched_price"] = 0.51
        path.write_text(json.dumps(changed), encoding="utf-8")
        check(rejects(path, root), "MUTATION a displayed Novig quote cannot become a match")

        changed = json.loads(json.dumps(observations["onyx"]))
        changed["betting_authorized"] = True
        changed["entry_submitted"] = True
        path.write_text(json.dumps(changed), encoding="utf-8")
        check(rejects(path, root), "MUTATION account-visible shadow evidence cannot authorize or submit")

    print(f"\n{PASS}/{PASS + FAIL} checks passed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
