#!/usr/bin/env python
"""Evaluate the verified DK HR-over grading denominator without changing q."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.hr_over_baseline_diagnostic import (  # noqa: E402
    MODEL_KEY,
    paired_date_block_interval,
    point_metrics,
    require_exact_keys,
    score_probability,
    settlement_conditioned_over_probability,
    validate_open_dates,
    validate_pa_distribution,
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verified_path(record: dict[str, Any], label: str) -> Path:
    path = (ROOT / str(record["path"])).resolve()
    if not path.is_file():
        raise ValueError(f"{label} is missing: {path}")
    actual = sha256_file(path)
    if actual != record["sha256"]:
        raise ValueError(f"{label} hash mismatch: expected {record['sha256']}, got {actual}")
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--protocol",
        default=(
            "data/analysis/hr_over_contract_v1/"
            "settlement_conditioned_probability_v1/protocol.json"
        ),
    )
    parser.add_argument(
        "--out-dir",
        default="data/analysis/hr_over_contract_v1/settlement_conditioned_probability_v1",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    protocol_path = (ROOT / args.protocol).resolve()
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    if protocol.get("predeclared_before_outputs") is not True:
        raise ValueError("settlement-conditioned protocol was not predeclared")
    if protocol.get("betting_authorized") is not False or protocol.get("may_opened") is not False:
        raise ValueError("settlement-conditioned protocol is not fail-closed")

    inputs = protocol["inputs"]
    baseline_protocol_path = verified_path(inputs["baseline_protocol"], "baseline protocol")
    baseline_report_path = verified_path(inputs["baseline_report"], "baseline report")
    rows_path = verified_path(inputs["baseline_rows"], "baseline rows")
    pa_path = verified_path(inputs["fitted_pa_distribution"], "PA distribution")
    baseline_protocol = json.loads(baseline_protocol_path.read_text(encoding="utf-8"))
    baseline_report = json.loads(baseline_report_path.read_text(encoding="utf-8"))
    if baseline_report.get("betting_authorized") is not False or baseline_report.get("may_opened") is not False:
        raise ValueError("parent baseline report is not fail-closed")
    if baseline_protocol.get("betting_authorized") is not False:
        raise ValueError("parent baseline protocol is not fail-closed")

    pa_weights = validate_pa_distribution(json.loads(pa_path.read_text(encoding="utf-8")))
    rows = pd.read_csv(rows_path)
    if len(rows) != 8_854 or rows.duplicated(MODEL_KEY).any():
        raise ValueError("baseline rows are not the certified 8,854-key universe")
    dates = validate_open_dates(sorted(rows.official_game_date.astype(str).unique()))
    diagnostic_dates = [value for value in dates if value < "2026-05-01"]
    confirmation_dates = [value for value in dates if value >= "2026-06-01"]
    if len(diagnostic_dates) != 37 or len(confirmation_dates) != 19:
        raise ValueError("settlement-conditioned chronology is not the locked 37+19 split")

    conditional = []
    unconditional_win = []
    unconditional_loss = []
    void_probability = []
    grade_probability = []
    for row in rows.itertuples(index=False):
        slot = int(row.lineup_slot)
        result = settlement_conditioned_over_probability(
            float(row.active_exact_per_pa), pa_weights[slot]
        )
        conditional.append(result["conditional_over_probability"])
        unconditional_win.append(result["unconditional_win_probability"])
        unconditional_loss.append(result["unconditional_loss_probability"])
        void_probability.append(result["void_probability"])
        grade_probability.append(result["grade_probability"])
    rows["settlement_conditioned"] = conditional
    rows["contract_unconditional_win_probability"] = unconditional_win
    rows["contract_unconditional_loss_probability"] = unconditional_loss
    rows["contract_void_probability"] = void_probability
    rows["contract_grade_probability"] = grade_probability

    # The transform is required to preserve every certified identity.
    require_exact_keys(rows, pd.read_csv(rows_path), "settlement-conditioned transform")
    gradeable = rows[rows.official_gradeable.astype(bool)].copy()
    summaries: dict[str, Any] = {}
    success_parts: dict[str, bool] = {}
    for role, role_dates in (
        ("diagnostic", diagnostic_dates), ("confirmation", confirmation_dates)
    ):
        period = gradeable[gradeable.official_game_date.isin(role_dates)].copy()
        baseline = score_probability(period, "active_exact")
        candidate = score_probability(period, "settlement_conditioned")
        interval = paired_date_block_interval(
            baseline,
            candidate,
            role_dates,
            draws=int(protocol["bootstrap"]["draws"]),
            seed=int(protocol["bootstrap"]["seed"]),
        )
        summaries[role] = {
            "active_exact_unconditional": {"point": point_metrics(baseline)},
            "settlement_conditioned": {
                "point": point_metrics(candidate),
                "paired_vs_active_exact": interval,
            },
        }
        success_parts[f"{role}_brier_improved"] = (
            candidate.brier.mean() < baseline.brier.mean()
        )
        success_parts[f"{role}_log_loss_improved"] = (
            candidate.log_loss.mean() < baseline.log_loss.mean()
        )
        movement = summaries[role]["settlement_conditioned"]["point"][
            "positive_ev_raw_movement"
        ]
        success_parts[f"{role}_movement_nonnegative"] = (
            movement is not None and movement >= 0.0
        )
        if role == "confirmation":
            success_parts["confirmation_brier_upper_below_zero"] = (
                interval["variant_minus_baseline_brier_95"][1] < 0.0
            )
            success_parts["confirmation_log_loss_upper_below_zero"] = (
                interval["variant_minus_baseline_log_loss_95"][1] < 0.0
            )

    success_parts = {key: bool(value) for key, value in success_parts.items()}
    contract_supported = all(success_parts.values())
    report = {
        "schema_version": "draftkings-hr-over-settlement-conditioned-probability-report-v1",
        "built_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "RESEARCH_ONLY",
        "betting_authorized": False,
        "may_opened": False,
        "historical_executability_verified": False,
        "protocol": {"path": str(protocol_path), "sha256": sha256_file(protocol_path)},
        "funnel": {
            "certified_keys": int(len(rows)),
            "official_gradeable_keys": int(len(gradeable)),
            "void_or_unresolved_historical_keys": int(len(rows) - len(gradeable)),
        },
        "predicted_contract_state_means": {
            "unconditional_win_probability": float(rows.contract_unconditional_win_probability.mean()),
            "unconditional_loss_probability": float(rows.contract_unconditional_loss_probability.mean()),
            "void_probability": float(rows.contract_void_probability.mean()),
            "grade_probability": float(rows.contract_grade_probability.mean()),
            "settlement_conditioned_probability": float(rows.settlement_conditioned.mean()),
        },
        "periods": summaries,
        "decision": {
            "success_parts": success_parts,
            "settlement_conditioned_contract_correction_supported": bool(contract_supported),
            "next_action": (
                "DESIGN_SEPARATE_VERSIONED_CONTRACT_PROBABILITY_CANDIDATE"
                if contract_supported
                else "REJECT_CONTRACT_TRANSFORM_AS_SUPPORTED_CORRECTION"
            ),
            "promotion_permitted": False,
            "betting_authorized": False,
        },
        "interpretation": (
            "This tests the payout/void denominator only. It does not improve the underlying "
            "baseball event model, verify quote executability, open May, or authorize betting."
        ),
    }
    if report["betting_authorized"] is not False or report["may_opened"] is not False:
        raise ValueError("settlement-conditioned report attempted promotion")

    out_dir = (ROOT / args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    output_rows = out_dir / "settlement_conditioned_rows.csv"
    output_report = out_dir / "report.json"
    rows.to_csv(output_rows, index=False)
    report["outputs"] = {
        "rows": {"path": str(output_rows), "sha256": sha256_file(output_rows)}
    }
    tmp = output_report.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(output_report)

    print("HR SETTLEMENT-CONDITIONED PROBABILITY AUDIT COMPLETE — RESEARCH ONLY")
    print(f"  keys: {len(rows):,}; official gradeable: {len(gradeable):,}")
    print(f"  mean predicted void probability: {rows.contract_void_probability.mean():.4%}")
    print(f"  contract correction supported: {contract_supported}")
    print(f"  report: {output_report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
