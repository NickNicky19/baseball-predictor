#!/usr/bin/env python3
"""Offline mutations for the canonical historical/live feature boundary."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.features.canonical_pa_features import PROFILE_FIELDS, SCHEMA_VERSION  # noqa: E402


def valid_contract(protocol: dict) -> bool:
    try:
        if protocol["status"] != "LOCKED_BEFORE_CANONICAL_SELECTION_ARTIFACT":
            return False
        if protocol["selection_seasons"] != [2023, 2024]:
            return False
        if protocol["training_source"]["maximum_rows_read"] != 87462:
            return False
        if protocol["statcast_inventory"]["allowed_years_during_selection_build"] != [2023, 2024]:
            return False
        taxonomy = protocol["event_taxonomy"]
        if taxonomy["required_status"] != "VALID_COMPLETE_TAXONOMY":
            return False
        if taxonomy["selection_files_verified"] != 1288:
            return False
        if taxonomy["unmapped_events_required"] != 0:
            return False
        transformer = protocol["canonical_transformer"]
        if transformer["schema_version"] != SCHEMA_VERSION:
            return False
        window = transformer["window"]
        if window["interval"] != "[target_date-46 calendar days, target_date)":
            return False
        if not window["same_day_excluded"]:
            return False
        fallback = transformer["fallback"]
        if fallback["league_values_imputed"] or not fallback["missing_history_remains_missing"]:
            return False
        expected = {f"hitter_{name}" for name in PROFILE_FIELDS}
        actual = set(protocol["classifier_feature_groups"]["canonical_hitter_46d"])
        if actual != expected:
            return False
        if "lineup_slot" not in protocol["quarantined_from_classifier"]:
            return False
        if "opp_sp_k9" not in protocol["quarantined_from_classifier"]:
            return False
        if not protocol["required_runtime_parity"]["same_transformer_file_and_hash"]:
            return False
        if not protocol["protected_evidence"]["confirmation_2025_unread"]:
            return False
        if not protocol["protected_evidence"]["may_2026_unread"]:
            return False
    except (KeyError, TypeError):
        return False
    return True


def mutated(protocol: dict, update: object) -> dict:
    value = copy.deepcopy(protocol)
    update(value)  # type: ignore[operator]
    return value


def main() -> int:
    path = ROOT / "config/shared_pa_runtime_feature_contract.json"
    protocol = json.loads(path.read_text(encoding="utf-8"))
    checks = [
        ("locked contract", valid_contract(protocol)),
        ("2025 mutation rejected", not valid_contract(mutated(protocol, lambda p: p["selection_seasons"].append(2025)))),
        ("row-boundary mutation rejected", not valid_contract(mutated(protocol, lambda p: p["training_source"].update(maximum_rows_read=131202)))),
        ("same-day mutation rejected", not valid_contract(mutated(protocol, lambda p: p["canonical_transformer"]["window"].update(same_day_excluded=False)))),
        ("taxonomy status mutation rejected", not valid_contract(mutated(protocol, lambda p: p["event_taxonomy"].update(required_status="UNMAPPED_EVENTS_PRESENT")))),
        ("taxonomy coverage mutation rejected", not valid_contract(mutated(protocol, lambda p: p["event_taxonomy"].update(selection_files_verified=1287)))),
        ("taxonomy unmapped mutation rejected", not valid_contract(mutated(protocol, lambda p: p["event_taxonomy"].update(unmapped_events_required=1)))),
        ("window mutation rejected", not valid_contract(mutated(protocol, lambda p: p["canonical_transformer"]["window"].update(interval="[target-45,target]")))),
        ("imputation mutation rejected", not valid_contract(mutated(protocol, lambda p: p["canonical_transformer"]["fallback"].update(league_values_imputed=True)))),
        ("feature removal rejected", not valid_contract(mutated(protocol, lambda p: p["classifier_feature_groups"]["canonical_hitter_46d"].pop()))),
        ("lineup release rejected", not valid_contract(mutated(protocol, lambda p: p["quarantined_from_classifier"].remove("lineup_slot")))),
        ("pitcher release rejected", not valid_contract(mutated(protocol, lambda p: p["quarantined_from_classifier"].remove("opp_sp_k9")))),
        ("runtime parity mutation rejected", not valid_contract(mutated(protocol, lambda p: p["required_runtime_parity"].update(same_transformer_file_and_hash=False)))),
        ("confirmation mutation rejected", not valid_contract(mutated(protocol, lambda p: p["protected_evidence"].update(confirmation_2025_unread=False)))),
        ("May mutation rejected", not valid_contract(mutated(protocol, lambda p: p["protected_evidence"].update(may_2026_unread=False)))),
    ]
    failed = [name for name, passed in checks if not passed]
    if failed:
        raise SystemExit(f"runtime feature contract checks failed: {failed}")
    print(f"SHARED PA RUNTIME FEATURE CONTRACT VALID: {len(checks)}/{len(checks)} checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
