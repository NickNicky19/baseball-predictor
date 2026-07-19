#!/usr/bin/env python3
"""Prove the HR EB simplification result validator detects material mutations."""
from __future__ import annotations

import argparse
import copy
import json
import tempfile
from pathlib import Path

import pandas as pd

from validate_hr_eb_simplification_candidate import recompute, validate_report_claims
from src.evaluation.hr_eb_simplification import load_protocol


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    args = parser.parse_args()
    evidence_root = args.evidence_root.resolve()
    protocol = load_protocol(args.protocol.resolve(), evidence_root=evidence_root)
    report = json.loads(args.report.read_text(encoding="utf-8"))
    expected = recompute(evidence_root, protocol, args.candidate.resolve())
    mutations = [
        ("status", lambda r: r.update(status="PROMOTED")),
        ("betting", lambda r: r.update(betting_authorized=True)),
        ("production", lambda r: r.update(production_unchanged=False)),
        ("May", lambda r: r.update(may_2026_opened=True)),
        ("2025", lambda r: r.update(confirmation_2025_opened=True)),
        ("executability", lambda r: r.update(historical_executability_verified=True)),
        ("candidate", lambda r: r["candidate"].update(prior_strength_pa=50)),
        ("coverage", lambda r: r["candidate_funnel"].update(gradeable_candidate_rows=1)),
        ("proper score", lambda r: r["roles"]["confirmation"]["candidate"].update(brier=0.0)),
        ("bootstrap", lambda r: r["roles"]["confirmation"]["date_block_intervals"]["candidate_minus_frozen_brier_95"].__setitem__(1, 1.0)),
        ("probability decision", lambda r: r["decision"].update(probability_confirmation_ready=False)),
        ("economic decision", lambda r: r["decision"].update(historical_economic_ready=True)),
        ("next action", lambda r: r.update(next_action="install production")),
    ]
    checked = 0
    for label, mutate in mutations:
        candidate = copy.deepcopy(report)
        mutate(candidate)
        try:
            validate_report_claims(candidate, protocol, expected)
        except (ValueError, KeyError):
            print(f"[OK] MUTATION {label} fails")
            checked += 1
        else:
            raise AssertionError(f"report mutation survived: {label}")

    original = pd.read_csv(args.candidate)
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        variants = []
        dropped = original.iloc[:-1].copy()
        variants.append(("candidate row removed", dropped))
        probability = original.copy()
        probability.loc[0, "sim_p_over"] = 0.999
        variants.append(("candidate probability changed", probability))
        source = original.copy()
        fallback_index = source.index[source["candidate_source"].eq("production_fallback_VOID_NO_PA_only")][0]
        source.loc[fallback_index, "candidate_source"] = "rolling_eb_shared_pa"
        variants.append(("VOID_NO_PA fallback relabeled", source))
        for index, (label, frame) in enumerate(variants):
            path = directory / f"candidate_{index}.csv"
            frame.to_csv(path, index=False)
            try:
                recompute(evidence_root, protocol, path)
            except ValueError:
                print(f"[OK] MUTATION {label} fails")
                checked += 1
            else:
                raise AssertionError(f"candidate mutation survived: {label}")
    if checked != 16:
        raise AssertionError(f"expected 16 detected mutations, got {checked}")
    print("16/16")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
