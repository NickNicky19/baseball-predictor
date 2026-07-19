#!/usr/bin/env python3
"""Prove the independent benchmark adjudicator detects material mutations."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

import pandas as pd

from validate_open_2026_eb_production_benchmark import (
    recompute, validate_prediction_rows, validate_report_claims,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    args = parser.parse_args()
    root = args.evidence_root.resolve()
    protocol = json.loads(args.protocol.read_text(encoding="utf-8"))
    source = json.loads((root / protocol["source_manifest"]["path"]).read_text(encoding="utf-8"))
    original_report = json.loads(args.report.read_text(encoding="utf-8"))
    original_frame = pd.read_csv(args.predictions)
    expected = recompute(original_frame, protocol)

    report_mutations = [
        ("status", lambda r: r.update(status="PROMOTED")),
        ("betting", lambda r: r.update(betting_authorized=True)),
        ("May", lambda r: r.update(may_2026_opened=True)),
        ("confirmation", lambda r: r.update(confirmation_2025_opened=True)),
        ("rows", lambda r: r.update(rows=r["rows"] + 1)),
        ("proper score", lambda r: r["markets"]["hits_1.5"]["metrics"]["pooled_open"]["empirical_bayes"].update(binary_log_loss=0.0)),
        ("calibration", lambda r: r["markets"]["hits_1.5"]["metrics"]["pooled_open"]["empirical_bayes"].update(calibration_intercept=10.0)),
        ("interval", lambda r: r["markets"]["home_runs_0.5"]["comparisons"]["pooled_open"]["eb_vs_production"]["binary_brier"].update(upper=1.0)),
        ("screen", lambda r: r["markets"]["hits_1.5"]["predictive_screen"].update(predictive_screen_passed=False)),
        ("passed markets", lambda r: r.update(measured_simplification_limiter_markets=[])),
        ("economics", lambda r: r.update(economic_analysis_allowed=False)),
        ("publish", lambda r: r.update(model_publishable=True)),
        ("selection", lambda r: r.update(selection_passed=True)),
        ("next action", lambda r: r.update(next_action="install model")),
    ]
    checked = 0
    for label, mutate in report_mutations:
        candidate = copy.deepcopy(original_report)
        mutate(candidate)
        try:
            validate_report_claims(candidate, original_frame, protocol, source, expected)
        except (ValueError, KeyError):
            print(f"[OK] MUTATION {label} fails")
            checked += 1
        else:
            raise AssertionError(f"report mutation survived: {label}")

    frame_mutations = [
        ("duplicate identity", lambda f: pd.concat([f, f.iloc[[0]]], ignore_index=True)),
        ("May date", lambda f: f.assign(game_date=f["game_date"].mask(f.index == 0, "2026-05-01"))),
        ("official grade", lambda f: f.assign(**{"actual_hits_0.5": f["actual_hits_0.5"].mask(f.index == 0, 1 - f.loc[0, "actual_hits_0.5"])})),
        ("probability range", lambda f: f.assign(**{"eb_hits_0.5": f["eb_hits_0.5"].mask(f.index == 0, 1.1)})),
        ("fallback label", lambda f: f.assign(eb_player_fallback=f["eb_player_fallback"].mask(f.index == 0, 2))),
        ("total base accounting", lambda f: f.assign(total_bases=f["total_bases"].mask(f.index == 0, f.loc[0, "total_bases"] + 1))),
    ]
    for label, mutate in frame_mutations:
        candidate = mutate(original_frame.copy())
        try:
            validate_prediction_rows(candidate, source)
        except ValueError:
            print(f"[OK] MUTATION {label} fails")
            checked += 1
        else:
            raise AssertionError(f"prediction mutation survived: {label}")
    if checked != 20:
        raise AssertionError(f"expected 20 detected mutations, found {checked}")
    print("20/20")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
