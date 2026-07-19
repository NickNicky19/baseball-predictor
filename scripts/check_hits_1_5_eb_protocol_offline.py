#!/usr/bin/env python3
"""Mutation-test the locked Hits 1.5 protocol without scoring outcomes."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.hits_1_5_eb_adjudication import validate_protocol  # noqa: E402


def rejected(payload: dict, evidence_root: Path) -> bool:
    try:
        validate_protocol(payload, evidence_root=evidence_root)
    except ValueError:
        return True
    return False


def main() -> int:
    evidence_root = ROOT.parent
    path = ROOT / "config/hits_1_5_eb_open_gate_protocol.json"
    original = json.loads(path.read_text(encoding="utf-8"))
    validate_protocol(original, evidence_root=evidence_root)
    mutations = []

    def add(name: str, mutate) -> None:
        payload = copy.deepcopy(original)
        mutate(payload)
        mutations.append((name, rejected(payload, evidence_root)))

    add("input hash", lambda p: p["inputs"]["benchmark_predictions"].update(sha256="0" * 64))
    add("May opened", lambda p: p.update(may_2026_opened=True))
    add("inject May", lambda p: p["chronology"]["confirmation_dates"].__setitem__(0, "2026-05-01"))
    add("duplicate exclusion", lambda p: p["coverage"].update(conflicting_duplicate_keys_excluded_whole=[]))
    add("unavailable identity", lambda p: p["coverage"]["model_unavailable_keys"].__setitem__(0, "changed"))
    add("coverage count", lambda p: p["coverage"].update(model_available_rows=1382))
    add("edge threshold", lambda p: p["scoring"].update(edge_threshold=0.01))
    add("candidate", lambda p: p["candidate"].update(prior_strength_pa=199))
    add("probability gate", lambda p: p["probability_readiness"].update(candidate_brier_below_production_in_both_blocks=False))
    add("economic gate", lambda p: p["historical_economic_readiness"].update(candidate_positive_ev_flat_stake_roi_95_lower_strictly_above_zero_in_both_blocks=False))
    add("production", lambda p: p.update(production_unchanged=False))
    add("betting", lambda p: p.update(betting_authorized=True))
    add("protected invariant", lambda p: p["protected_invariants"].update(no_policy_tuning=False))
    failed = [name for name, caught in mutations if not caught]
    if failed:
        raise ValueError(f"protocol mutations escaped: {failed}")
    print(f"HITS_1_5_PROTOCOL_MUTATIONS_VALID {len(mutations)}/{len(mutations)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
