#!/usr/bin/env python3
"""Open and score the locked untouched-2025 per-PA HR confirmation once."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.hr_eb_per_pa_confirmation import (
    ARMS, build_predictions, decide, load_protocol, metrics, paired_intervals,
    validate_source,
)
from src.evaluation.multi_market_foundation import sha256


def atomic_json(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write((json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode())
        handle.flush(); os.fsync(handle.fileno())
    os.replace(temporary, path)


def atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        frame.to_csv(handle, index=False, lineterminator="\n")
        handle.flush(); os.fsync(handle.fileno())
    os.replace(temporary, path)


def period_block(frame: pd.DataFrame, dates: list[str]) -> dict[str, Any]:
    rows = frame[frame["game_date"].isin(dates)].copy()
    arm_metrics = {arm: metrics(rows, arm) for arm in ARMS}
    deltas: dict[str, dict[str, float]] = {}
    for baseline in ("rolling_league", "rolling_raw_player"):
        deltas[baseline] = {
            name: float(arm_metrics["candidate"][name] - arm_metrics[baseline][name])
            for name in ("pa_weighted_binary_log_loss", "pa_weighted_binary_brier")
        }
    return {"dates": dates, "metrics": arm_metrics, "deltas": deltas}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    evidence_root = args.evidence_root.resolve()
    out_dir = args.out_dir.resolve()
    if out_dir.exists() and any(out_dir.iterdir()):
        raise ValueError("2025 confirmation output directory is not fresh")
    protocol = load_protocol(args.protocol.resolve(), evidence_root=evidence_root)
    training_path = evidence_root / protocol["inputs"]["training"]["path"]

    # This is the first read of the sealed 2025 outcome values after the protocol lock.
    source = validate_source(pd.read_csv(training_path, compression="gzip"))
    dates = list(protocol["confirmation_dates"])
    predictions, coverage = build_predictions(
        source, dates, float(protocol["candidate"]["prior_strength_pa"]),
    )
    expected_eligible = int(
        source["game_date"].isin(dates).mul(source["out_pa"].gt(0)).sum()
    )
    expected_zero = int(
        source["game_date"].isin(dates).mul(source["out_pa"].eq(0)).sum()
    )
    if coverage["eligible_rows"] != expected_eligible or coverage["zero_pa_rows"] != expected_zero:
        raise ValueError("confirmation eligibility accounting is incomplete")

    split = len(dates) // 2
    periods = {
        "early": period_block(predictions, dates[:split]),
        "late": period_block(predictions, dates[split:]),
        "full": period_block(predictions, dates),
    }
    draws = int(protocol["metrics"]["bootstrap_draws"])
    seed = int(protocol["metrics"]["bootstrap_seed"])
    comparisons = {
        baseline: {
            "intervals": paired_intervals(
                predictions, "candidate", baseline, dates=dates, draws=draws, seed=seed,
            )
        }
        for baseline in ("rolling_league", "rolling_raw_player")
    }
    predictions_path = out_dir / "predictions.csv"
    atomic_csv(predictions, predictions_path)
    report: dict[str, Any] = {
        "schema_version": "hr-eb-per-pa-2025-confirmation-report-v1",
        "status": "PENDING_DECISION",
        "betting_authorized": False,
        "production_unchanged": True,
        "may_2026_opened": False,
        "confirmation_2025_opened": True,
        "full_game_probability_confirmed": False,
        "economic_evidence_generated": False,
        "candidate": protocol["candidate"],
        "confirmation_dates": dates,
        "prediction_rows": len(predictions),
        "coverage": coverage,
        "bootstrap_draws": draws,
        "periods": periods,
        "comparisons": comparisons,
    }
    report["decision"] = decide(report)
    if report["decision"]["per_pa_component_confirmed"]:
        report["status"] = "PER_PA_HR_COMPONENT_CONFIRMED_FULL_GAME_AND_ECONOMICS_BLOCKED"
        report["next_action"] = (
            "Preserve this component certificate. Full-game HR probability still requires "
            "timestamp-certified pregame lineup and PA-volume evidence; do not install production or open May."
        )
    else:
        report["status"] = "PER_PA_HR_COMPONENT_REJECTED_ON_UNTOUCHED_2025"
        report["next_action"] = "Reject the HR EB component and preserve production."
    report["artifacts"] = {
        "protocol": {"path": str(args.protocol.resolve()), "sha256": sha256(args.protocol.resolve())},
        "training": {"path": protocol["inputs"]["training"]["path"], "sha256": sha256(training_path)},
        "predictions": {"path": str(predictions_path), "sha256": sha256(predictions_path), "rows": len(predictions)},
    }
    report["runtime"] = {
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "runner": {"path": "scripts/run_hr_eb_per_pa_2025_confirmation.py", "sha256": sha256(Path(__file__))},
        "module": {"path": "src/evaluation/hr_eb_per_pa_confirmation.py", "sha256": sha256(ROOT / "src/evaluation/hr_eb_per_pa_confirmation.py")},
    }
    report["protected_invariants"] = protocol["protected_invariants"]
    report_path = out_dir / "report.json"
    atomic_json(report, report_path)
    print(report["status"])
    print(f"per_pa_component_confirmed={report['decision']['per_pa_component_confirmed']}")
    print(f"report_sha256={sha256(report_path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
