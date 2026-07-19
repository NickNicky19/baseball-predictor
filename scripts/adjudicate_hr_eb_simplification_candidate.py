#!/usr/bin/env python3
"""Run the locked open-period HR rolling-EB simplification gate."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.adjudicate_hr_batted_ball_candidate import apply_official_grading  # noqa: E402
from src.evaluation.hr_eb_simplification import (  # noqa: E402
    auc, build_candidate_probabilities, decide, load_protocol,
)
from src.evaluation.hr_over_evaluation import (  # noqa: E402
    MODEL_KEY, arm_rows, build_pairs, date_block_intervals, point_metrics,
)
from src.evaluation.multi_market_foundation import sha256  # noqa: E402


def atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", dir=path.parent, delete=False) as handle:
        temp = Path(handle.name)
        frame.to_csv(handle, index=False, lineterminator="\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def atomic_json(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as handle:
        temp = Path(handle.name)
        handle.write((json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode())
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def read_input(root: Path, protocol: dict[str, Any], name: str, **kwargs: Any) -> pd.DataFrame:
    return pd.read_csv(root / protocol["inputs"][name]["path"], **kwargs)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    evidence_root = args.evidence_root.resolve()
    out_dir = args.out_dir.resolve()
    if out_dir.exists() and any(out_dir.iterdir()):
        raise ValueError("HR EB output directory is not fresh")
    protocol = load_protocol(args.protocol.resolve(), evidence_root=evidence_root)
    reused = json.loads((evidence_root / protocol["inputs"]["reused_inference_protocol"]["path"]).read_text(encoding="utf-8"))
    diagnostic_dates = list(reused["chronology"]["diagnostic_dates"])
    confirmation_dates = list(reused["chronology"]["confirmation_dates"])
    if len(diagnostic_dates) != 37 or len(confirmation_dates) != 19:
        raise ValueError("HR EB role chronology changed")
    if any(date.startswith("2026-05") for date in diagnostic_dates + confirmation_dates):
        raise ValueError("May entered HR EB adjudication")

    benchmark = read_input(evidence_root, protocol, "benchmark_predictions")
    frozen = read_input(evidence_root, protocol, "frozen_probabilities")
    settlement = read_input(evidence_root, protocol, "official_settlement_bridge")
    candidate, candidate_funnel = build_candidate_probabilities(frozen, benchmark, settlement)
    candidate_path = out_dir / "candidate_probabilities.csv"
    atomic_csv(candidate, candidate_path)

    market = read_input(evidence_root, protocol, "market_source")
    if "result" in market.columns:
        raise ValueError("vendor numeric result entered HR EB adjudication")
    official = read_input(evidence_root, protocol, "official_outcomes")
    pairs, market_funnel = build_pairs(
        market, frozen, candidate, official, diagnostic_dates, confirmation_dates,
    )
    graded, void = apply_official_grading(pairs, settlement, 8854, 8827, 27)
    roles: dict[str, Any] = {}
    draws = int(protocol["inference"]["bootstrap_draws"])
    seed = int(protocol["inference"]["bootstrap_seed"])
    for role, dates in (("diagnostic", diagnostic_dates), ("confirmation", confirmation_dates)):
        subset = graded[graded["official_game_date"].isin(dates)].sort_values(MODEL_KEY).reset_index(drop=True)
        frozen_rows = arm_rows(subset, "frozen")
        candidate_rows = arm_rows(subset, "candidate")
        frozen_metrics = point_metrics(frozen_rows)
        candidate_metrics = point_metrics(candidate_rows)
        frozen_metrics["auc"] = auc(subset, "frozen")
        candidate_metrics["auc"] = auc(subset, "candidate")
        roles[role] = {
            "frozen": frozen_metrics,
            "candidate": candidate_metrics,
            "changed_probabilities": int((~np.isclose(subset["frozen_p_over"], subset["candidate_p_over"])).sum()),
            "date_block_intervals": date_block_intervals(
                frozen_rows, candidate_rows, dates, draws=draws, seed=seed,
            ),
        }
    exact_coverage = (
        market_funnel == {
            "strict_market_rows": 9356,
            "model_available_rows": 8854,
            "model_unavailable_rows": 502,
            "model_market_coverage": 8854 / 9356,
            "officially_scored_rows": 8854,
            "diagnostic_rows": 6657,
            "confirmation_rows": 2197,
        }
        and len(graded) == 8827 and len(void) == 27
    )
    all_draws_valid = all(
        role["date_block_intervals"]["valid_draws"] == draws for role in roles.values()
    )
    decision = decide(
        roles, exact_gradeable_coverage=exact_coverage, all_draws_valid=all_draws_valid,
    )
    if decision["probability_confirmation_ready"] and decision["historical_economic_ready"]:
        status = "PROBABILITY_AND_HISTORICAL_ECONOMIC_RESEARCH_READY_FOR_UNTOUCHED_CONFIRMATION"
        next_action = "Freeze the HR probability candidate and predeclare untouched confirmation; do not install production or authorize betting."
    elif decision["probability_confirmation_ready"]:
        status = "PROBABILITY_READY_HISTORICAL_ECONOMICS_BLOCKED"
        next_action = "Proceed only to untouched probability confirmation; retain the economic block and do not install production."
    else:
        status = "HR_EB_SIMPLIFICATION_REJECTED_ON_MARKET_GRADEABLE_PROBABILITY_GATE"
        next_action = "Reject the HR simplification candidate and preserve production."

    scored_path = out_dir / "scored_gradeable_rows.csv"
    void_path = out_dir / "void_or_unresolved_rows.csv"
    atomic_csv(graded, scored_path)
    atomic_csv(void, void_path)
    report = {
        "schema_version": "hr-eb-simplification-open-gate-report-v1",
        "status": status,
        "betting_authorized": False,
        "production_unchanged": True,
        "may_2026_opened": False,
        "confirmation_2025_opened": False,
        "historical_executability_verified": False,
        "probability_label": "RAW_ONE_SIDED_BREAK_EVEN_INCLUDES_UNKNOWN_MARGIN_AND_IS_NOT_VERIFIED_EXECUTABLE",
        "candidate": protocol["candidate"],
        "candidate_funnel": candidate_funnel,
        "market_funnel": market_funnel,
        "official_gradeable_rows": len(graded),
        "void_or_unresolved_rows": len(void),
        "roles": roles,
        "decision": decision,
        "next_action": next_action,
        "artifacts": {
            "protocol": {"path": args.protocol.resolve().relative_to(ROOT).as_posix(), "sha256": sha256(args.protocol.resolve())},
            "candidate_probabilities": {"path": candidate_path.relative_to(evidence_root).as_posix(), "sha256": sha256(candidate_path), "rows": len(candidate)},
            "scored_gradeable_rows": {"path": scored_path.relative_to(evidence_root).as_posix(), "sha256": sha256(scored_path), "rows": len(graded)},
            "void_or_unresolved_rows": {"path": void_path.relative_to(evidence_root).as_posix(), "sha256": sha256(void_path), "rows": len(void)},
        },
        "runtime": {
            "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "runner": {"path": "scripts/adjudicate_hr_eb_simplification_candidate.py", "sha256": sha256(Path(__file__))},
            "module": {"path": "src/evaluation/hr_eb_simplification.py", "sha256": sha256(ROOT / "src/evaluation/hr_eb_simplification.py")},
        },
        "protected_invariants": protocol["protected_invariants"],
    }
    report_path = out_dir / "report.json"
    atomic_json(report, report_path)
    print(status)
    print(f"probability_confirmation_ready={decision['probability_confirmation_ready']}")
    print(f"historical_economic_ready={decision['historical_economic_ready']}")
    print(f"report_sha256={sha256(report_path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
