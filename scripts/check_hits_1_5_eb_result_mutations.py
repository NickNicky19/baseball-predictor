#!/usr/bin/env python3
"""Prove material Hits 1.5 report mutations are rejected."""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.validate_hits_1_5_eb_candidate import recompute, validate_report  # noqa: E402
from src.evaluation.hits_1_5_eb_adjudication import load_protocol  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    evidence_root = args.evidence_root.resolve()
    protocol = load_protocol(args.protocol.resolve(), evidence_root=evidence_root)
    original = json.loads(args.report.resolve().read_text(encoding="utf-8"))
    expected = recompute(evidence_root, protocol)
    validate_report(original, protocol, expected)
    mutations = []

    def add(name: str, mutate) -> None:
        payload = copy.deepcopy(original)
        mutate(payload)
        caught = False
        try:
            validate_report(payload, protocol, expected)
        except ValueError:
            caught = True
        mutations.append((name, caught))

    add("status", lambda p: p.update(status="PASS"))
    add("betting", lambda p: p.update(betting_authorized=True))
    add("production", lambda p: p.update(production_unchanged=False))
    add("May", lambda p: p.update(may_2026_opened=True))
    add("market funnel", lambda p: p["market_funnel"].update(strict_market_rows=1390))
    add("coverage funnel", lambda p: p["coverage_funnel"].update(model_unavailable_rows=7))
    add("candidate Brier", lambda p: p["roles"]["confirmation"]["candidate"].update(brier=0.1))
    add("candidate AUC", lambda p: p["roles"]["diagnostic"]["candidate"].update(auc=0.9))
    add("probability interval", lambda p: p["roles"]["confirmation"]["date_block_intervals"]["candidate_minus_production_brier_95"].__setitem__(1, -0.01))
    add("ROI interval", lambda p: p["roles"]["confirmation"]["date_block_intervals"]["candidate_positive_ev_theoretical_roi_95"].__setitem__(0, 0.01))
    add("decision", lambda p: p["decision"].update(probability_ready=True))
    add("next action", lambda p: p.update(next_action="install"))
    add("protected invariant", lambda p: p["protected_invariants"].update(no_betting_authorization=False))
    failed = [name for name, caught in mutations if not caught]
    if failed:
        raise ValueError(f"result mutations escaped: {failed}")
    print(f"HITS_1_5_RESULT_MUTATIONS_VALID {len(mutations)}/{len(mutations)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
