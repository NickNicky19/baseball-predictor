#!/usr/bin/env python3
"""Mutation checks for the locked open-2026 EB benchmark protocol."""
from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.open_2026_probability_benchmark import load_protocol, validate_protocol  # noqa: E402


def must_fail(payload: dict, root: Path, label: str) -> None:
    try:
        validate_protocol(payload, evidence_root=root)
    except ValueError:
        print(f"[OK] MUTATION {label} fails")
        return
    raise AssertionError(f"mutation unexpectedly passed: {label}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    args = parser.parse_args()
    original = load_protocol(ROOT / "config/open_2026_eb_production_benchmark_protocol_v4.json", evidence_root=args.evidence_root)
    mutations = [
        ("May opened", lambda p: p.update(may_2026_opened=True)),
        ("2025 confirmation opened", lambda p: p.update(confirmation_2025_opened=True)),
        ("May update admitted", lambda p: p["chronology"].update(missing_month_between_april_and_june="used")),
        ("prior strength retuned", lambda p: p["probability_arms"]["rolling_empirical_bayes_player_rate_pa_200"].update(prior_strength_pa=100)),
        ("realized PA admitted", lambda p: p["pa_volume"].update(realized_pa_forbidden_as_prediction_input=False)),
        ("Hits line removed", lambda p: p["market_contract"]["production_comparable"].remove("hits_1.5")),
        ("Total Bases made production", lambda p: p["market_contract"]["production_comparable"].append("total_bases_1.5")),
        ("market pooling", lambda p: p["market_contract"].update(market_pooling_forbidden=False)),
        ("zero PA graded", lambda p: p["eligibility"].update(proper_scoring_requires_official_pa_above_zero=False)),
        ("bootstrap reduced", lambda p: p["metrics"].update(bootstrap_draws=100)),
        ("log loss removed", lambda p: p["metrics"].update(proper_scores=["binary_brier"])),
        ("diagnostic promoted", lambda p: p["decision_contract"].update(benchmark_is_diagnostic_not_a_candidate=False)),
        ("mixed result intervention", lambda p: p["decision_contract"].update(weak_or_mixed_result_requires_no_intervention=False)),
        ("betting authorized", lambda p: p.update(betting_authorized=True)),
        ("retry provenance changed", lambda p: p["supersession"]["v1_failure"].update(sha256="0" * 64)),
        ("schema failure provenance changed", lambda p: p["supersession"]["v2_failure"].update(sha256="0" * 64)),
        ("calibration solver changed", lambda p: p["metrics"]["calibration_solver"].update(root_method="guess")),
        ("finite calibration gate removed", lambda p: p["decision_contract"].update(calibration_estimates_must_be_finite=False)),
    ]
    for label, mutate in mutations:
        candidate = copy.deepcopy(original)
        mutate(candidate)
        must_fail(candidate, args.evidence_root, label)
    print("19/19")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
