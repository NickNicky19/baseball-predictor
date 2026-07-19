#!/usr/bin/env python3
"""Mutation-test the locked HR rolling-EB simplification contract."""
from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.hr_eb_simplification import decide, load_protocol, validate_protocol  # noqa: E402


def must_fail(payload: dict, evidence_root: Path, label: str) -> None:
    try:
        validate_protocol(payload, evidence_root=evidence_root)
    except (ValueError, KeyError):
        print(f"[OK] MUTATION {label} fails")
    else:
        raise AssertionError(f"protocol mutation survived: {label}")


def role() -> dict:
    return {
        "frozen": {"brier": 0.11, "log_loss": 0.36, "auc": 0.57},
        "candidate": {"brier": 0.10, "log_loss": 0.35, "auc": 0.61, "positive_ev_rows": 10},
        "date_block_intervals": {
            "candidate_minus_frozen_brier_95": [-0.02, -0.001],
            "candidate_minus_frozen_log_loss_95": [-0.03, -0.002],
            "candidate_positive_ev_raw_movement_95": [0.001, 0.01],
            "candidate_positive_ev_theoretical_roi_95": [0.01, 0.20],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    args = parser.parse_args()
    protocol_path = ROOT / "config/hr_eb_simplification_open_gate_protocol.json"
    original = load_protocol(protocol_path, evidence_root=args.evidence_root)
    mutations = [
        ("May opened", lambda p: p.update(may_2026_opened=True)),
        ("2025 opened", lambda p: p.update(confirmation_2025_opened=True)),
        ("betting authorized", lambda p: p.update(betting_authorized=True)),
        ("production changed", lambda p: p.update(production_unchanged=False)),
        ("parent hash", lambda p: p["certified_parent"].update(sha256="0" * 64)),
        ("prior retuned", lambda p: p["candidate"].update(prior_strength_pa=50)),
        ("under admitted", lambda p: p["candidate"]["market"].update(side="under")),
        ("edge threshold tuned", lambda p: p["scoring"].update(edge_threshold=0.04)),
        ("coverage lowered", lambda p: p["coverage"].update(official_gradeable_rows=1)),
        ("bootstrap reduced", lambda p: p["inference"].update(bootstrap_draws=100)),
        ("economic gate weakened", lambda p: p["historical_economic_readiness"].update(candidate_positive_ev_flat_stake_roi_95_lower_strictly_above_zero_in_both_blocks=False)),
        ("market separation weakened", lambda p: p["protected_invariants"].update(no_under_or_market_pooling=False)),
    ]
    for label, mutate in mutations:
        candidate = copy.deepcopy(original)
        mutate(candidate)
        must_fail(candidate, args.evidence_root, label)

    roles = {"diagnostic": role(), "confirmation": role()}
    decision = decide(roles, exact_gradeable_coverage=True, all_draws_valid=True)
    if not decision["probability_confirmation_ready"] or not decision["historical_economic_ready"]:
        raise AssertionError("passing HR EB decision fixture did not pass")
    print("[OK] passing decision requires every probability and economic part")
    mutations_checked = len(mutations) + 1
    for label, mutate in (
        ("probability interval", lambda r: r["confirmation"]["date_block_intervals"]["candidate_minus_frozen_brier_95"].__setitem__(1, 0.1)),
        ("movement interval", lambda r: r["diagnostic"]["date_block_intervals"]["candidate_positive_ev_raw_movement_95"].__setitem__(0, -0.1)),
        ("ROI interval", lambda r: r["confirmation"]["date_block_intervals"]["candidate_positive_ev_theoretical_roi_95"].__setitem__(0, -0.1)),
    ):
        candidate_roles = copy.deepcopy(roles)
        mutate(candidate_roles)
        result = decide(candidate_roles, exact_gradeable_coverage=True, all_draws_valid=True)
        relevant = result["probability_confirmation_ready"] if label == "probability interval" else result["historical_economic_ready"]
        if relevant:
            raise AssertionError(f"decision mutation survived: {label}")
        print(f"[OK] MUTATION {label} fails its readiness gate")
        mutations_checked += 1
    try:
        decide({"diagnostic": role()}, exact_gradeable_coverage=True, all_draws_valid=True)
    except ValueError:
        print("[OK] MUTATION missing confirmation block fails")
        mutations_checked += 1
    else:
        raise AssertionError("missing confirmation block survived")
    if mutations_checked != 17:
        raise AssertionError(f"expected 17 checks, got {mutations_checked}")
    print("17/17")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
