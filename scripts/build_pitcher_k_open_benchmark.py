#!/usr/bin/env python3
"""Build the locked research-only open-2026 pitcher-K benchmark."""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.pitcher_k_open_benchmark import (  # noqa: E402
    add_comparators,
    build_model_market_universe,
    build_official_pitcher_outcomes,
    crosscheck_reconstruction_outcomes,
    evaluate,
    fit_empirical_baseline,
    load_protocol,
    sha256,
)


def atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        frame.to_csv(handle, index=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def atomic_json(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(body)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def state_counts(frame: pd.DataFrame) -> dict[str, int]:
    return {str(key): int(value) for key, value in frame["terminal_state"].value_counts().sort_index().items()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, default=ROOT / "config/pitcher_k_open_benchmark_protocol.json")
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()

    protocol = load_protocol(args.protocol, code_root=ROOT, evidence_root=args.evidence_root)
    market = build_model_market_universe(protocol, evidence_root=args.evidence_root)
    outcomes = build_official_pitcher_outcomes(protocol, market, evidence_root=args.evidence_root)
    crosscheck_reconstruction_outcomes(protocol, outcomes, evidence_root=args.evidence_root)
    pmf = fit_empirical_baseline(protocol, evidence_root=args.evidence_root)
    rows = add_comparators(market, outcomes, pmf)
    metrics, calibration, differences = evaluate(protocol, rows)

    outcome_path = args.out_dir / "official_pitcher_k_outcomes.csv"
    row_path = args.out_dir / "pitcher_k_benchmark_rows.csv"
    metric_path = args.out_dir / "pitcher_k_benchmark_metrics.csv"
    calibration_path = args.out_dir / "pitcher_k_calibration.csv"
    report_path = args.out_dir / "pitcher_k_open_benchmark_report.json"
    atomic_csv(outcomes, outcome_path)
    atomic_csv(rows, row_path)
    atomic_csv(metrics, metric_path)
    atomic_csv(calibration, calibration_path)

    primary = rows[rows.terminal_state.eq("started")]
    report = {
        "schema_version": "pitcher-k-open-benchmark-report-v1",
        "status": "OPEN_2026_PRODUCTION_BENCHMARK_COMPLETE",
        "betting_authorized": False,
        "production_unchanged": True,
        "may_2026_opened": False,
        "economic_evidence_eligible": False,
        "protocol": {
            "path": args.protocol.resolve().relative_to(ROOT.resolve()).as_posix(),
            "sha256": sha256(args.protocol), "status": protocol["status"],
        },
        "implementation": {
            "module_path": "src/evaluation/pitcher_k_open_benchmark.py",
            "module_sha256": sha256(ROOT / "src/evaluation/pitcher_k_open_benchmark.py"),
            "builder_path": Path(__file__).resolve().relative_to(ROOT.resolve()).as_posix(),
            "builder_sha256": sha256(Path(__file__)),
        },
        "source_bindings": protocol["inputs"],
        "locked_denominators": protocol["locked_denominators"],
        "official_outcome_contract": protocol["official_outcome_contract"],
        "comparators": protocol["comparators"],
        "metrics_contract": protocol["metrics"],
        "summary": {
            "official_pitcher_games": int(len(outcomes)),
            "official_outcome_terminal_states": state_counts(outcomes),
            "market_rows": int(len(rows)),
            "primary_scoring_rows": int(len(primary)),
            "primary_scoring_pitcher_games": int(primary[["mlb_game_pk", "player_id", "game_date"]].drop_duplicates().shape[0]),
            "primary_scoring_rows_by_line": {str(float(line)): int(count) for line, count in primary.groupby("line").size().items()},
            "mean_entry_to_close_reference_probability_movement": float(rows.reference_probability_movement.mean()),
            "median_entry_to_close_reference_probability_movement": float(rows.reference_probability_movement.median()),
        },
        "paired_uncertainty": differences,
        "empirical_baseline_pmf": {str(key): float(value) for key, value in pmf.items()},
        "artifacts": {
            "official_outcomes": {"path": outcome_path.resolve().relative_to(args.evidence_root.resolve()).as_posix(), "sha256": sha256(outcome_path), "rows": int(len(outcomes))},
            "benchmark_rows": {"path": row_path.resolve().relative_to(args.evidence_root.resolve()).as_posix(), "sha256": sha256(row_path), "rows": int(len(rows))},
            "metrics": {"path": metric_path.resolve().relative_to(args.evidence_root.resolve()).as_posix(), "sha256": sha256(metric_path), "rows": int(len(metrics))},
            "calibration": {"path": calibration_path.resolve().relative_to(args.evidence_root.resolve()).as_posix(), "sha256": sha256(calibration_path), "rows": int(len(calibration))},
        },
        "protected_invariants": protocol["protected_invariants"],
        "interpretation": "Research-only probability diagnosis. Historical DraftKings prices are reference observations, not proven executable prices or settlement truth. No ROI, payout, selection policy, or authorization conclusion is produced.",
    }
    atomic_json(report, report_path)
    print(json.dumps({"report": str(report_path), "sha256": sha256(report_path), **report["summary"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
