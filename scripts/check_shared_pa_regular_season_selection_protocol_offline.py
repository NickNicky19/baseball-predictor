#!/usr/bin/env python3
"""Mutation checks for the locked regular-season shared-PA selection protocol."""
from __future__ import annotations

import copy
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def valid(protocol: dict) -> bool:
    try:
        if protocol["status"] != "LOCKED_BEFORE_2023_2024_CANONICAL_SELECTION":
            return False
        if protocol["chronology"]["initial_fit"] != [2023] or protocol["chronology"]["rolling_selection"] != [2024]:
            return False
        if not protocol["confirmation_2025_forbidden_during_selection"] or not protocol["may_2026_forbidden"]:
            return False
        if protocol["inputs"]["official_outcomes"]["maximum_rows_read"] != 87462:
            return False
        if protocol["inputs"]["canonical_features"]["rows"] != 87462:
            return False
        if "regular_season_only" not in protocol["single_intervention"]["id"]:
            return False
        if protocol["model_family"]["random_seed"] in protocol["model_family"]["stability_audit_seeds"]:
            return False
        if not protocol["materiality"]["primary_seed_must_clear_floor"]:
            return False
        if not protocol["materiality"]["all_stability_seeds_must_improve_both_scores"]:
            return False
        if not protocol["materiality"]["no_feature_variant_may_reduce_coverage"]:
            return False
        selected = set().union(*(set(protocol["feature_groups"][group]) for variant in protocol["feature_variants"] for group in variant["groups"]))
        if selected & set(protocol["forbidden_classifier_features"]):
            return False
        if "opp_sp_throws" in selected or protocol["opposing_pitcher_quarantine"]["classifier_allowed"]:
            return False
        output = protocol["selection_output"]
        if not output["confirmation_2025_must_remain_unread_on_rejection"] or not output["May_2026_must_remain_unread"]:
            return False
        if not output["production_must_remain_unchanged"] or output["betting_authorized"]:
            return False
    except (KeyError, TypeError):
        return False
    return True


def mutate(protocol: dict, fn: object) -> dict:
    output = copy.deepcopy(protocol)
    fn(output)  # type: ignore[operator]
    return output


def main() -> int:
    protocol = json.loads((ROOT / "config/shared_pa_regular_season_selection_protocol_v1.json").read_text(encoding="utf-8"))
    checks = [
        ("locked protocol", valid(protocol)),
        ("wrong intervention rejected", not valid(mutate(protocol, lambda p: p["single_intervention"].update(id="two_interventions")))),
        ("confirmation release rejected", not valid(mutate(protocol, lambda p: p.update(confirmation_2025_forbidden_during_selection=False)))),
        ("May release rejected", not valid(mutate(protocol, lambda p: p.update(may_2026_forbidden=False)))),
        ("outcome scope expansion rejected", not valid(mutate(protocol, lambda p: p["inputs"]["official_outcomes"].update(maximum_rows_read=131202)))),
        ("lineup feature rejected", not valid(mutate(protocol, lambda p: p["feature_groups"]["raw_pregame_context_safe"].append("lineup_slot")))),
        ("pitcher release rejected", not valid(mutate(protocol, lambda p: p["feature_groups"]["raw_pregame_context_safe"].append("opp_sp_throws")))),
        ("materiality removal rejected", not valid(mutate(protocol, lambda p: p["materiality"].update(primary_seed_must_clear_floor=False)))),
        ("coverage removal rejected", not valid(mutate(protocol, lambda p: p["materiality"].update(no_feature_variant_may_reduce_coverage=False)))),
        ("production mutation rejected", not valid(mutate(protocol, lambda p: p["selection_output"].update(production_must_remain_unchanged=False)))),
        ("authorization mutation rejected", not valid(mutate(protocol, lambda p: p["selection_output"].update(betting_authorized=True)))),
    ]
    failed = [name for name, passed in checks if not passed]
    if failed:
        raise SystemExit(f"regular-season selection protocol checks failed: {failed}")
    print(f"REGULAR-SEASON SELECTION PROTOCOL VALID: {len(checks)}/{len(checks)} checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
