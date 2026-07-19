#!/usr/bin/env python3
"""Prove the Hits 1.5 decision gate rejects synthetic regressions."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.hits_1_5_eb_adjudication import decide  # noqa: E402


def role() -> dict:
    return {
        "candidate": {"brier": 0.18, "log_loss": 0.54, "auc": 0.64, "positive_ev_rows": 10},
        "production": {"brier": 0.20, "log_loss": 0.58, "auc": 0.62, "positive_ev_rows": 10},
        "league": {"brier": 0.21, "log_loss": 0.60, "auc": 0.60, "positive_ev_rows": 10},
        "date_block_intervals": {
            "candidate_minus_production_brier_95": [-0.03, -0.01],
            "candidate_minus_production_log_loss_95": [-0.06, -0.01],
            "candidate_minus_league_brier_95": [-0.04, -0.01],
            "candidate_minus_league_log_loss_95": [-0.08, -0.02],
            "candidate_positive_ev_raw_movement_95": [0.001, 0.02],
            "candidate_positive_ev_theoretical_roi_95": [0.01, 0.20],
        },
    }


def main() -> int:
    baseline = {"diagnostic": role(), "confirmation": role()}
    good = decide(baseline, exact_coverage=True, all_draws_valid=True)
    if not good["probability_ready"] or not good["historical_economic_ready"]:
        raise ValueError("synthetic passing decision did not pass")
    mutations = []

    def add(name: str, mutate, field: str) -> None:
        payload = copy.deepcopy(baseline)
        mutate(payload)
        result = decide(payload, exact_coverage=True, all_draws_valid=True)
        mutations.append((name, not result[field]))

    add("diagnostic Brier", lambda p: p["diagnostic"]["candidate"].update(brier=0.22), "probability_ready")
    add("confirmation log", lambda p: p["confirmation"]["candidate"].update(log_loss=0.61), "probability_ready")
    add("league", lambda p: p["diagnostic"]["candidate"].update(brier=0.22), "probability_ready")
    add("AUC", lambda p: p["confirmation"]["candidate"].update(auc=0.59), "probability_ready")
    add("uncertainty", lambda p: p["confirmation"]["date_block_intervals"]["candidate_minus_production_brier_95"].__setitem__(1, 0.001), "probability_ready")
    add("movement", lambda p: p["diagnostic"]["date_block_intervals"]["candidate_positive_ev_raw_movement_95"].__setitem__(0, -0.001), "historical_economic_ready")
    add("ROI", lambda p: p["confirmation"]["date_block_intervals"]["candidate_positive_ev_theoretical_roi_95"].__setitem__(0, -0.01), "historical_economic_ready")
    coverage = decide(baseline, exact_coverage=False, all_draws_valid=True)
    draws = decide(baseline, exact_coverage=True, all_draws_valid=False)
    mutations.extend([("coverage", not coverage["probability_ready"]), ("draws", not draws["probability_ready"])])
    failed = [name for name, caught in mutations if not caught]
    if failed:
        raise ValueError(f"logic mutations escaped: {failed}")
    print(f"HITS_1_5_LOGIC_MUTATIONS_VALID {len(mutations)}/{len(mutations)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
