#!/usr/bin/env python3
"""Mutation checks for the shared PA benchmark protocol."""
from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.shared_pa_benchmark_protocol import load_protocol, validate_protocol  # noqa: E402


def must_fail(payload: dict, evidence_root: Path, label: str) -> None:
    try:
        validate_protocol(payload, evidence_root=evidence_root)
    except ValueError:
        print(f"[OK] MUTATION {label} fails")
        return
    raise AssertionError(f"mutation unexpectedly passed: {label}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    args = parser.parse_args()
    path = ROOT / "config/shared_pa_benchmark_protocol.json"
    original = load_protocol(path, evidence_root=args.evidence_root)
    print("[OK] shared PA benchmark chronology, features, baselines, and gates validate")
    mutations = [
        ("May admitted", lambda p: p.update(may_2026_forbidden=False)),
        ("2025 selection", lambda p: p["chronology"].update(rolling_selection=[2024, 2025])),
        ("confirmation rows readable", lambda p: p["selection_input_boundary"].update(maximum_rows_read=131202)),
        ("realized PA class removed", lambda p: p["outcome_classes"].remove("other_non_ab")),
        ("hits leakage", lambda p: p["forbidden_features"].remove("out_hits")),
        ("weather unquarantined", lambda p: p["quarantined_until_availability_proven"].remove("weather_temp")),
        ("official lineup slot admitted", lambda p: p["quarantined_until_availability_proven"].remove("lineup_slot")),
        ("postgame starter admitted", lambda p: p["historical_feature_sanitization"].update(postgame_only_opposing_pitcher_sources=[])),
        ("variant sees confirmation", lambda p: p["sequential_feature_variants"][0].update(selection_data="2025")),
        ("simple baseline removed", lambda p: p["required_baselines"].remove("league_rate")),
        ("confirmation fold", lambda p: p["selection_folds"].append(["2025-01-01", "2025-12-31"])),
        ("baseline tuned on confirmation", lambda p: p["simple_baseline_selection"].update(source="2025")),
        ("postgame slot made selection eligible", lambda p: p["simple_baseline_selection"]["selection_eligible"].append("lineup_slot_rate")),
        ("hidden baseline tie breaker", lambda p: p["simple_baseline_selection"].update(tie_breaker="none")),
        ("larger unbound grid", lambda p: p["model_family"]["bounded_grid"]["depth"].append(10)),
        ("early stopping changed", lambda p: p["model_family"]["inner_early_stopping"].update(holdout_tail_official_dates=7)),
        ("PA artifact changed", lambda p: p["pa_volume"].update(sha256="0" * 64)),
        ("log loss removed", lambda p: p["required_metrics"].update(pa_primary=["multiclass_brier"])),
        ("weak confirmation", lambda p: p["confirmation_gate"].update(paired_log_loss_interval_upper_below_zero=False)),
        ("calibration on 2025", lambda p: p["calibration"].update(selection_source="2025")),
        ("calibration not cross-fitted", lambda p: p["calibration"].update(selection_evaluation="same_oof_rows")),
        ("temperature bounds changed", lambda p: p["calibration"].update(log_temperature_bounds=[-20.0, 20.0])),
        ("betting authorized", lambda p: p.update(betting_authorized=True)),
    ]
    for label, mutate in mutations:
        candidate = copy.deepcopy(original)
        mutate(candidate)
        must_fail(candidate, args.evidence_root, label)
    print("24/24")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
