#!/usr/bin/env python3
"""Mutation checks for untouched-2025 per-PA HR result certification."""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.validate_hr_eb_per_pa_2025_confirmation import validate_loaded
from src.evaluation.hr_eb_per_pa_confirmation import load_protocol, validate_source


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    root = args.evidence_root.resolve()
    protocol = load_protocol(args.protocol.resolve(), evidence_root=root)
    source = validate_source(pd.read_csv(root / protocol["inputs"]["training"]["path"], compression="gzip"))
    report = json.loads((args.out_dir / "report.json").read_text(encoding="utf-8"))
    predictions = pd.read_csv(args.out_dir / "predictions.csv")
    validate_loaded(protocol, source, report, predictions)

    mutations: list[tuple[str, dict, pd.DataFrame]] = []
    bad_rows = predictions.copy(); bad_rows.loc[0, "candidate"] += 0.001; mutations.append(("probability", report, bad_rows))
    bad_rows = predictions.iloc[:-1].copy(); mutations.append(("drop", report, bad_rows))
    bad_rows = pd.concat([predictions, predictions.iloc[[0]]], ignore_index=True); mutations.append(("duplicate", report, bad_rows))
    bad_rows = predictions.copy(); bad_rows.loc[0, "prior_player_pa"] += 1; mutations.append(("chronology evidence", report, bad_rows))
    bad = copy.deepcopy(report); bad["decision"]["betting_authorized"] = True; mutations.append(("decision betting", bad, predictions))
    bad = copy.deepcopy(report); bad["betting_authorized"] = True; mutations.append(("report betting", bad, predictions))
    bad = copy.deepcopy(report); bad["may_2026_opened"] = True; mutations.append(("May", bad, predictions))
    bad = copy.deepcopy(report); bad["full_game_probability_confirmed"] = True; mutations.append(("full game", bad, predictions))
    bad = copy.deepcopy(report); bad["comparisons"]["rolling_league"]["intervals"]["candidate_minus_baseline_brier_95"][1] = -1.0; mutations.append(("interval", bad, predictions))
    bad = copy.deepcopy(report); bad["coverage"]["zero_pa_rows"] += 1; mutations.append(("coverage", bad, predictions))
    bad = copy.deepcopy(report); bad["status"] = "PASS"; mutations.append(("status", bad, predictions))
    rejected = 0
    for name, bad_report, bad_predictions in mutations:
        try:
            validate_loaded(protocol, source, bad_report, bad_predictions)
        except (ValueError, AssertionError, KeyError, TypeError):
            rejected += 1
        else:
            raise AssertionError(f"result mutation survived: {name}")
    print(f"HR EB PER-PA 2025 RESULT MUTATIONS VALID: {rejected}/{len(mutations)} rejected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
