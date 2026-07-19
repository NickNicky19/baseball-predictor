#!/usr/bin/env python3
"""Mutation checks for the hierarchical selection protocol and recombination."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.shared_pa_hierarchical_selection import (  # noqa: E402
    clears_primary, eligible_market, load_protocol, stage_features,
)
from src.learning.shared_pa_hierarchical_model import combine  # noqa: E402


def caught(fn: object) -> bool:
    try:
        fn()  # type: ignore[operator]
    except (KeyError, TypeError, ValueError):
        return True
    return False


def main() -> int:
    evidence_root = Path(r"C:\Projects\baseball_predictor")
    protocol_path = ROOT / "config/shared_pa_hierarchical_selection_protocol.json"
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    loaded, cumulative, _ = load_protocol(protocol_path, code_root=ROOT, evidence_root=evidence_root)
    checks = [
        ("locked protocol accepted", loaded == protocol),
        ("stage 1 uses only recent features", len(stage_features(loaded, cumulative, "stage_1")) == len(cumulative["feature_groups"]["canonical_hitter_46d"])),
        ("stage 2 adds cumulative features", len(stage_features(loaded, cumulative, "stage_2")) == len(cumulative["feature_groups"]["canonical_hitter_46d"]) + len(cumulative["feature_groups"]["canonical_hitter_cumulative"])),
    ]
    mutations = [
        ("authorization", lambda p: p.update(betting_authorized=True)),
        ("production", lambda p: p.update(production_unchanged=False)),
        ("2025", lambda p: p.update(confirmation_2025_forbidden_during_selection=False)),
        ("May", lambda p: p.update(may_2026_forbidden=False)),
        ("input hash", lambda p: p["inputs"]["selection_oof"].update(sha256="0" * 64)),
        ("measured basis", lambda p: p["single_intervention"]["measured_basis"].update(largest_offsetting_log_loss_regression="walk")),
        ("stage 1 class", lambda p: p["hierarchy"]["stage_1"]["outcomes"].append("bip_out")),
        ("stage 2 feature", lambda p: p["hierarchy"]["stage_2"]["feature_groups"].append("raw_pregame_context_safe")),
        ("parameter", lambda p: p["model"]["fixed_params_from_prior_bounded_selection"].update(depth=6)),
        ("seed", lambda p: p["model"]["stability_audit_seeds"].pop()),
        ("fold", lambda p: p["selection_folds"][0].__setitem__(0, "2024-03-29")),
        ("inference", lambda p: p["inference"].update(bootstrap_draws=9999)),
        ("materiality", lambda p: p["materiality"]["vs_strongest_simple_required_point_and_upper_below"].update(multiclass_log_loss=0.0)),
        ("v1 gate", lambda p: p["materiality"]["vs_canonical_v1_required_point_and_upper_below"].update(multiclass_brier=1.0)),
        ("coverage", lambda p: p["materiality"].update(coverage_loss_allowed=1)),
        ("calibration", lambda p: p["calibration"].update(first_fold_temperature=0.9)),
        ("market", lambda p: p["derived_market_gates"]["markets"].pop()),
        ("market separation", lambda p: p["derived_market_gates"].update(one_market_cannot_validate_another=False)),
        ("output", lambda p: p["selection_output"].update(betting_authorized=True)),
    ]
    for name, mutate in mutations:
        changed = copy.deepcopy(protocol)
        mutate(changed)
        temp = ROOT / f"config/.hierarchical_{name.replace(' ', '_')}.tmp.json"
        temp.write_text(json.dumps(changed), encoding="utf-8")
        try:
            checks.append((f"{name} mutation rejected", caught(lambda: load_protocol(temp, code_root=ROOT, evidence_root=evidence_root))))
        finally:
            temp.unlink(missing_ok=True)
    stage_1 = np.array([[.2, .1, .05, .65], [.1, .1, .1, .7]])
    stage_2 = np.array([[.3, .1, .02, .08, .5], [.2, .1, .02, .08, .6]])
    combined = combine(stage_1, stage_2)
    checks.extend([
        ("recombination sums to one", np.allclose(combined.sum(axis=1), 1.0)),
        ("recombination preserves strikeout", np.allclose(combined[:, 0], stage_1[:, 0])),
        ("recombination preserves contact mass", np.allclose(combined[:, [2, 3, 4, 5, 6]].sum(axis=1), stage_1[:, 3])),
        ("invalid stage sum rejected", caught(lambda: combine(stage_1 * 0.9, stage_2))),
    ])
    good_simple = {
        "multiclass_log_loss": {"point": -0.02, "upper": -0.013},
        "multiclass_brier": {"point": -0.006, "upper": -0.005},
    }
    good_v1 = {
        "multiclass_log_loss": {"point": -0.003, "upper": -0.001},
        "multiclass_brier": {"point": -0.002, "upper": -0.001},
    }
    primary_passed, _ = clears_primary(protocol, vs_simple=good_simple, vs_v1=good_v1)
    weak_simple = copy.deepcopy(good_simple)
    weak_simple["multiclass_log_loss"]["upper"] = -0.01
    primary_weak, _ = clears_primary(protocol, vs_simple=weak_simple, vs_v1=good_v1)
    market_comparisons = {
        f"vs_{comparator}": {metric: {"point": -0.01, "upper": -0.001} for metric in ("log_loss", "brier")}
        for comparator in ("strongest_simple", "canonical_v1")
    }
    weak_market = copy.deepcopy(market_comparisons)
    weak_market["vs_canonical_v1"]["brier"]["upper"] = 0.001
    checks.extend([
        ("primary gate accepts full material improvement", primary_passed),
        ("primary gate rejects floor miss", not primary_weak),
        ("market gate accepts both comparators", eligible_market(market_comparisons)),
        ("market gate rejects one weak bound", not eligible_market(weak_market)),
    ])
    failed = [name for name, passed in checks if not passed]
    if failed:
        raise SystemExit(f"hierarchical selection checks failed: {failed}")
    print(f"HIERARCHICAL SELECTION PROTOCOL CHECKS VALID: {len(checks)}/{len(checks)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
