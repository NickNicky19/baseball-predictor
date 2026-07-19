#!/usr/bin/env python3
"""Mutation tests for the cumulative point-in-time history contract."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def valid(contract: dict) -> bool:
    try:
        if contract["status"] != "LOCKED_BEFORE_CUMULATIVE_HISTORY_BUILD":
            return False
        if contract["selection_seasons"] != [2023, 2024] or contract["selection_rows"] != 87462:
            return False
        if not contract["confirmation_2025_forbidden"] or not contract["may_2026_forbidden"]:
            return False
        window = contract["history_window"]
        if window["start_inclusive"] != "2023-01-01":
            return False
        if window["source_years_allowed"] != [2023, 2024] or not window["same_day_excluded"]:
            return False
        if not window["missing_history_remains_missing"]:
            return False
        if window["league_fallback_imputed"] or window["hand_specified_shrinkage_applied"]:
            return False
        if contract["canonical_transformer"]["function"] != "cumulative_profiles":
            return False
        taxonomy = contract["event_taxonomy"]
        if taxonomy["required_status"] != "VALID_COMPLETE_CUMULATIVE_TAXONOMY":
            return False
        if taxonomy["expected_player_year_groups"] != 1421 or taxonomy["verified_files"] != 1290:
            return False
        if taxonomy["missing_player_year_groups"] != 131:
            return False
        transformer = ROOT / contract["canonical_transformer"]["path"]
        if sha256(transformer) != contract["canonical_transformer"]["sha256"]:
            return False
        expected = {f"history_{name}" for name in (
            "pitch_count", "pa", "bip", "k_rate", "bb_rate", "single_rate",
            "double_rate", "triple_rate", "home_run_rate", "bip_out_rate",
            "other_non_ab_rate", "xwoba", "xba", "xslg", "avg_exit_velocity",
            "avg_launch_angle", "barrel_rate", "hard_hit_rate", "whiff_rate",
            "chase_rate", "contact_rate", "swing_rate", "zone_rate",
        )}
        if set(contract["feature_group"]) != expected:
            return False
        protected = contract["protected_invariants"]
        if not all(protected.values()) or contract["betting_authorized"] or not contract["production_unchanged"]:
            return False
    except (KeyError, TypeError, OSError):
        return False
    return True


def mutate(contract: dict, fn: object) -> dict:
    result = copy.deepcopy(contract)
    fn(result)  # type: ignore[operator]
    return result


def main() -> int:
    path = ROOT / "config/shared_pa_cumulative_history_contract.json"
    contract = json.loads(path.read_text(encoding="utf-8"))
    checks = [
        ("locked contract", valid(contract)),
        ("2025 source rejected", not valid(mutate(contract, lambda c: c["history_window"]["source_years_allowed"].append(2025)))),
        ("same-day inclusion rejected", not valid(mutate(contract, lambda c: c["history_window"].update(same_day_excluded=False)))),
        ("league fallback rejected", not valid(mutate(contract, lambda c: c["history_window"].update(league_fallback_imputed=True)))),
        ("shrinkage rejected", not valid(mutate(contract, lambda c: c["history_window"].update(hand_specified_shrinkage_applied=True)))),
        ("confirmation release rejected", not valid(mutate(contract, lambda c: c.update(confirmation_2025_forbidden=False)))),
        ("May release rejected", not valid(mutate(contract, lambda c: c.update(may_2026_forbidden=False)))),
        ("row-boundary mutation rejected", not valid(mutate(contract, lambda c: c.update(selection_rows=131202)))),
        ("transformer hash mutation rejected", not valid(mutate(contract, lambda c: c["canonical_transformer"].update(sha256="0" * 64)))),
        ("feature omission rejected", not valid(mutate(contract, lambda c: c["feature_group"].pop()))),
        ("incomplete taxonomy rejected", not valid(mutate(contract, lambda c: c["event_taxonomy"].update(required_status="VALID_COMPLETE_TAXONOMY")))),
        ("taxonomy coverage mutation rejected", not valid(mutate(contract, lambda c: c["event_taxonomy"].update(verified_files=1288)))),
        ("production mutation rejected", not valid(mutate(contract, lambda c: c.update(production_unchanged=False)))),
        ("authorization mutation rejected", not valid(mutate(contract, lambda c: c.update(betting_authorized=True)))),
    ]
    failed = [name for name, passed in checks if not passed]
    if failed:
        raise SystemExit(f"cumulative history contract checks failed: {failed}")
    print(f"CUMULATIVE HISTORY CONTRACT VALID: {len(checks)}/{len(checks)} checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
