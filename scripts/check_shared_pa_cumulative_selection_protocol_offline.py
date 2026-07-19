#!/usr/bin/env python3
"""Mutation checks for cumulative 2023-2024 model selection."""
from __future__ import annotations

import copy
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def valid(protocol: dict) -> bool:
    try:
        if protocol["status"] != "LOCKED_BEFORE_2023_2024_CUMULATIVE_SELECTION":
            return False
        if not protocol["confirmation_2025_forbidden_during_selection"] or not protocol["may_2026_forbidden"]:
            return False
        if protocol["inputs"]["cumulative_features"]["rows"] != 87462:
            return False
        if protocol["inputs"]["cumulative_certificate"]["required_status"] != "CUMULATIVE_HISTORY_ARTIFACT_CERTIFIED":
            return False
        if protocol["inputs"]["cumulative_reproducibility"]["required_status"] != "BYTE_REPRODUCIBLE_ARTIFACT_CERTIFIED":
            return False
        if protocol["inputs"]["canonical_v1_report"]["required_status"] != "SELECTION_REJECTED_NO_CANDIDATE":
            return False
        if protocol["inputs"]["canonical_v1_oof"]["rows"] != 43668:
            return False
        if set(protocol["single_intervention"]) != {"id", "rationale", "forbidden_additions"}:
            return False
        if protocol["single_intervention"]["id"] != "add_raw_cumulative_player_history_to_canonical_46d":
            return False
        variants = {item["id"]: item for item in protocol["feature_variants"]}
        if set(variants) != {"canonical_recent_plus_cumulative", "canonical_recent_plus_cumulative_context"}:
            return False
        core = set(variants["canonical_recent_plus_cumulative"]["groups"])
        if core != {"canonical_hitter_46d", "canonical_hitter_cumulative"}:
            return False
        selected = set()
        for variant in variants.values():
            for group in variant["groups"]:
                selected.update(protocol["feature_groups"][group])
        if selected & set(protocol["forbidden_classifier_features"]):
            return False
        if protocol["opposing_pitcher_quarantine"]["classifier_allowed"]:
            return False
        simple = protocol["simple_baseline_selection"]
        if simple["required_exact_selected"] != "empirical_bayes_player_rate_pa_200":
            return False
        material = protocol["materiality"]
        required = (
            "candidate_vs_simple_point_and_upper_must_clear_floor",
            "candidate_vs_canonical_v1_point_and_upper_must_be_below_zero",
            "all_stability_seeds_must_improve_both_scores_vs_simple",
            "context_variant_requires_paired_interval_upper_below_zero_for_both_scores",
            "context_variant_must_pass_same_stability_audit",
            "no_feature_variant_may_reduce_coverage",
        )
        if not all(material[name] for name in required):
            return False
        output = protocol["selection_output"]
        if not output["confirmation_2025_must_remain_unread_on_rejection"]:
            return False
        if not output["May_2026_must_remain_unread"] or not output["production_must_remain_unchanged"]:
            return False
        if output["betting_authorized"] or protocol["betting_authorized"] or not protocol["production_unchanged"]:
            return False
    except (KeyError, TypeError):
        return False
    return True


def mutate(protocol: dict, fn: object) -> dict:
    result = copy.deepcopy(protocol)
    fn(result)  # type: ignore[operator]
    return result


def main() -> int:
    protocol = json.loads((ROOT / "config/shared_pa_cumulative_selection_protocol.json").read_text(encoding="utf-8"))
    checks = [
        ("locked protocol", valid(protocol)),
        ("confirmation release rejected", not valid(mutate(protocol, lambda p: p.update(confirmation_2025_forbidden_during_selection=False)))),
        ("May release rejected", not valid(mutate(protocol, lambda p: p.update(may_2026_forbidden=False)))),
        ("row mutation rejected", not valid(mutate(protocol, lambda p: p["inputs"]["cumulative_features"].update(rows=87463)))),
        ("uncertified artifact rejected", not valid(mutate(protocol, lambda p: p["inputs"]["cumulative_certificate"].update(required_status="UNKNOWN")))),
        ("nonreproducible artifact rejected", not valid(mutate(protocol, lambda p: p["inputs"]["cumulative_reproducibility"].update(required_status="UNKNOWN")))),
        ("v1 comparator release rejected", not valid(mutate(protocol, lambda p: p["inputs"]["canonical_v1_report"].update(required_status="UNKNOWN")))),
        ("identifier feature rejected", not valid(mutate(protocol, lambda p: p["feature_groups"]["canonical_hitter_cumulative"].append("player_id")))),
        ("lineup feature rejected", not valid(mutate(protocol, lambda p: p["feature_groups"]["raw_pregame_context_safe"].append("lineup_slot")))),
        ("pitcher release rejected", not valid(mutate(protocol, lambda p: p["opposing_pitcher_quarantine"].update(classifier_allowed=True)))),
        ("extra core intervention rejected", not valid(mutate(protocol, lambda p: p["feature_variants"][0]["groups"].append("raw_pregame_context_safe")))),
        ("simple materiality removal rejected", not valid(mutate(protocol, lambda p: p["materiality"].update(candidate_vs_simple_point_and_upper_must_clear_floor=False)))),
        ("v1 comparison removal rejected", not valid(mutate(protocol, lambda p: p["materiality"].update(candidate_vs_canonical_v1_point_and_upper_must_be_below_zero=False)))),
        ("stability removal rejected", not valid(mutate(protocol, lambda p: p["materiality"].update(all_stability_seeds_must_improve_both_scores_vs_simple=False)))),
        ("context stability removal rejected", not valid(mutate(protocol, lambda p: p["materiality"].update(context_variant_must_pass_same_stability_audit=False)))),
        ("production mutation rejected", not valid(mutate(protocol, lambda p: p.update(production_unchanged=False)))),
        ("authorization mutation rejected", not valid(mutate(protocol, lambda p: p["selection_output"].update(betting_authorized=True)))),
    ]
    failed = [name for name, passed in checks if not passed]
    if failed:
        raise SystemExit(f"cumulative selection protocol checks failed: {failed}")
    print(f"CUMULATIVE SELECTION PROTOCOL VALID: {len(checks)}/{len(checks)} checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
