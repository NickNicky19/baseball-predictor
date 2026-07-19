#!/usr/bin/env python3
"""Mutation checks for the canonical 2023-2024 selector protocol."""
from __future__ import annotations

import copy
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def valid(protocol: dict) -> bool:
    try:
        if protocol["status"] != "LOCKED_BEFORE_2023_2024_CANONICAL_SELECTION":
            return False
        if not protocol["confirmation_2025_forbidden_during_selection"] or not protocol["may_2026_forbidden"]:
            return False
        if protocol["inputs"]["official_outcomes"]["maximum_rows_read"] != 87462:
            return False
        if protocol["inputs"]["canonical_features"]["rows"] != 87462:
            return False
        if protocol["inputs"]["canonical_certificate"]["required_status"] != "CANONICAL_HITTER_ARTIFACT_CERTIFIED":
            return False
        variants = {item["id"]: item for item in protocol["feature_variants"]}
        if set(variants) != {"canonical_hitter_46d", "canonical_hitter_46d_context"}:
            return False
        selected = set()
        for variant in variants.values():
            for group in variant["groups"]:
                selected.update(protocol["feature_groups"][group])
        if selected & set(protocol["forbidden_classifier_features"]):
            return False
        if "opp_sp_throws" in selected or protocol["opposing_pitcher_quarantine"]["classifier_allowed"]:
            return False
        if protocol["model_family"]["random_seed"] in protocol["model_family"]["stability_audit_seeds"]:
            return False
        if len(set(protocol["model_family"]["stability_audit_seeds"])) != 2:
            return False
        material = protocol["materiality"]
        if not material["primary_seed_must_clear_floor"]:
            return False
        if not material["all_stability_seeds_must_improve_both_scores"]:
            return False
        if "league-rate loss minus strongest-simple loss" not in material["complexity_benefit_floor"]:
            return False
        output = protocol["selection_output"]
        if not output["confirmation_2025_must_remain_unread_on_rejection"]:
            return False
        if not output["May_2026_must_remain_unread"] or not output["production_must_remain_unchanged"]:
            return False
        if output["betting_authorized"]:
            return False
    except (KeyError, TypeError):
        return False
    return True


def mutate(protocol: dict, fn: object) -> dict:
    result = copy.deepcopy(protocol)
    fn(result)  # type: ignore[operator]
    return result


def main() -> int:
    protocol = json.loads((ROOT / "config/shared_pa_canonical_selection_protocol.json").read_text(encoding="utf-8"))
    checks = [
        ("locked protocol", valid(protocol)),
        ("confirmation release rejected", not valid(mutate(protocol, lambda p: p.update(confirmation_2025_forbidden_during_selection=False)))),
        ("May release rejected", not valid(mutate(protocol, lambda p: p.update(may_2026_forbidden=False)))),
        ("row-boundary mutation rejected", not valid(mutate(protocol, lambda p: p["inputs"]["official_outcomes"].update(maximum_rows_read=131202)))),
        ("uncertified artifact rejected", not valid(mutate(protocol, lambda p: p["inputs"]["canonical_certificate"].update(required_status="UNKNOWN")))),
        ("identifier feature rejected", not valid(mutate(protocol, lambda p: p["feature_groups"]["canonical_hitter_46d"].append("player_id")))),
        ("lineup feature rejected", not valid(mutate(protocol, lambda p: p["feature_groups"]["raw_pregame_context_safe"].append("lineup_slot")))),
        ("pitcher identity release rejected", not valid(mutate(protocol, lambda p: p["feature_groups"]["raw_pregame_context_safe"].append("opp_sp_throws")))),
        ("pitcher quarantine mutation rejected", not valid(mutate(protocol, lambda p: p["opposing_pitcher_quarantine"].update(classifier_allowed=True)))),
        ("seed overlap rejected", not valid(mutate(protocol, lambda p: p["model_family"].update(stability_audit_seeds=[260719, 260721])))),
        ("materiality removal rejected", not valid(mutate(protocol, lambda p: p["materiality"].update(primary_seed_must_clear_floor=False)))),
        ("stability removal rejected", not valid(mutate(protocol, lambda p: p["materiality"].update(all_stability_seeds_must_improve_both_scores=False)))),
        ("production mutation rejected", not valid(mutate(protocol, lambda p: p["selection_output"].update(production_must_remain_unchanged=False)))),
        ("authorization mutation rejected", not valid(mutate(protocol, lambda p: p["selection_output"].update(betting_authorized=True)))),
    ]
    failed = [name for name, passed in checks if not passed]
    if failed:
        raise SystemExit(f"canonical selection protocol checks failed: {failed}")
    print(f"CANONICAL SELECTION PROTOCOL VALID: {len(checks)}/{len(checks)} checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
