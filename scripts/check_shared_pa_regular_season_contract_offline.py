#!/usr/bin/env python3
"""Mutation checks for the regular-season-only shared-PA candidate contract."""
from __future__ import annotations

import copy
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def valid(contract: dict) -> bool:
    try:
        if contract["status"] != "LOCKED_BEFORE_REGULAR_SEASON_CANONICAL_SELECTION_ARTIFACT":
            return False
        if contract["selection_seasons"] != [2023, 2024]:
            return False
        if contract["confirmation_2025_forbidden_during_selection_build"] is not True:
            return False
        if contract["may_2026_forbidden"] is not True or contract["production_unchanged"] is not True:
            return False
        policy = contract["canonical_transformer"]["game_type_policy"]
        if policy != {"mode": "regular_season_only", "allowed_game_types": ["R"], "unknown_or_missing": "fail_closed"}:
            return False
        basis = contract["measured_basis"]["audit"]
        if basis["affected_player_game_rows"] <= 0 or basis["non_regular_event_rows_in_feature_windows"] <= 0:
            return False
        if "no_automatic_confirmation_or_market_evaluation" not in contract["protected_invariants"]:
            return False
        if "opp_sp_k9" not in contract["quarantined_from_classifier"]:
            return False
    except (KeyError, TypeError):
        return False
    return True


def changed(contract: dict, fn: object) -> dict:
    output = copy.deepcopy(contract)
    fn(output)  # type: ignore[operator]
    return output


def main() -> int:
    contract = json.loads((ROOT / "config/shared_pa_regular_season_runtime_feature_contract_v1.json").read_text(encoding="utf-8"))
    checks = [
        ("locked contract", valid(contract)),
        ("spring inclusion rejected", not valid(changed(contract, lambda c: c["canonical_transformer"]["game_type_policy"].update(allowed_game_types=["R", "S"])))),
        ("unknown game type tolerance rejected", not valid(changed(contract, lambda c: c["canonical_transformer"]["game_type_policy"].update(unknown_or_missing="allow")))),
        ("confirmation release rejected", not valid(changed(contract, lambda c: c.update(confirmation_2025_forbidden_during_selection_build=False)))),
        ("May release rejected", not valid(changed(contract, lambda c: c.update(may_2026_forbidden=False)))),
        ("production mutation rejected", not valid(changed(contract, lambda c: c.update(production_unchanged=False)))),
        ("no measured basis rejected", not valid(changed(contract, lambda c: c["measured_basis"]["audit"].update(affected_player_game_rows=0)))),
        ("pitcher release rejected", not valid(changed(contract, lambda c: c["quarantined_from_classifier"].remove("opp_sp_k9")))),
    ]
    failed = [name for name, passed in checks if not passed]
    if failed:
        raise SystemExit(f"regular-season contract checks failed: {failed}")
    print(f"REGULAR-SEASON CONTRACT VALID: {len(checks)}/{len(checks)} checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
