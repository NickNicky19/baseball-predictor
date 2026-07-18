#!/usr/bin/env python3
"""Run the locked 2023/2024/2025 HR batted-ball incremental-signal screen."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.hr_batted_ball_signal import (  # noqa: E402
    model_parameters,
    paired_date_interval,
    refit_predict,
    require_exact_seasons,
    score_arm,
    select_regularization,
    target,
)
from src.evaluation.hr_pre2026_batted_ball_mapping import validate_historical_input  # noqa: E402
from src.utils.provenance import sha256_file  # noqa: E402


DEFAULT_PROTOCOL = ROOT / "data/analysis/hr_over_contract_v1/pre2026_fitted_batted_ball_mapping_v1/signal_screen_protocol.json"
DEFAULT_INPUT = ROOT / "data/training/training_hitters_2023_2025_statcast.csv.gz"
DEFAULT_OUTPUT = ROOT / "data/analysis/hr_over_contract_v1/pre2026_fitted_batted_ball_mapping_v1/signal_screen_report.json"


def load_protocol(path: Path) -> dict:
    protocol = json.loads(path.read_text(encoding="utf-8"))
    schema = protocol.get("schema_version")
    if schema not in {
        "hr-pre2026-batted-ball-signal-screen-protocol-v1",
        "hr-pre2026-batted-ball-signal-screen-protocol-v2",
        "hr-pre2026-batted-ball-signal-screen-protocol-v3",
    }:
        raise ValueError("unknown HR signal-screen protocol schema")
    if schema.endswith(("v2", "v3")):
        sidecar = path.with_suffix(path.suffix + ".sha256")
        if not sidecar.is_file() or sidecar.read_text(encoding="utf-8").strip() != sha256_file(path):
            raise ValueError("signal protocol hash sidecar mismatch")
    audit = protocol["source_input_audit"]
    audit_path = ROOT / audit["path"]
    if sha256_file(audit_path) != audit["sha256"]:
        raise ValueError("source input audit hash mismatch")
    if protocol["chronology"] != {
        "regularization_fit": [2023], "regularization_selection": [2024],
        "confirmation_open_once": [2025], "allowed_seasons": [2023, 2024, 2025],
        "may_2026_forbidden": True,
    }:
        raise ValueError("chronology protocol drifted")
    if protocol.get("may_2026_opened") is not False or protocol.get("betting_authorized") is not False:
        raise ValueError("protocol cannot open May or authorize betting")
    if schema in {
        "hr-pre2026-batted-ball-signal-screen-protocol-v2",
        "hr-pre2026-batted-ball-signal-screen-protocol-v3",
    }:
        if protocol.get("builder_schema") != "a3.2":
            raise ValueError("corrected signal screen requires builder_schema a3.2")
        source = protocol.get("source_input") or {}
        source_path = ROOT / str(source.get("path", ""))
        if not source_path.is_file() or sha256_file(source_path) != source.get("sha256"):
            raise ValueError("signal source input hash mismatch")
        upstream = protocol.get("upstream_certifications") or {}
        for name, record in upstream.items():
            bound = ROOT / str(record.get("path", ""))
            if not bound.is_file() or sha256_file(bound) != record.get("sha256"):
                raise ValueError(f"signal upstream certification hash mismatch: {name}")
        output = protocol.get("output") or {}
        if not isinstance(output.get("path"), str):
            raise ValueError("signal output path missing")
    if schema == "hr-pre2026-batted-ball-signal-screen-protocol-v3":
        prior = protocol.get("supersedes_protocol") or {}
        if prior.get("sha256") != "71fbd58f63b432ae61ecea73bf361bafacd645705f1758a82ef7730566ce0601":
            raise ValueError("signal v3 superseded protocol binding changed")
    return protocol


def report_provenance(protocol_path: Path, input_path: Path) -> dict[str, str]:
    """Hash the actual invoked files, never module-level defaults."""
    return {
        "protocol_sha256": sha256_file(protocol_path),
        "input_sha256": sha256_file(input_path),
    }


def require_locked_paths(
    protocol: dict, input_path: Path, output_path: Path
) -> None:
    """Enforce the source/output paths and create-once confirmation rule."""
    if protocol.get("schema_version", "").endswith(("v2", "v3")):
        expected_input = (ROOT / protocol["source_input"]["path"]).resolve()
        expected_output = (ROOT / protocol["output"]["path"]).resolve()
        if input_path != expected_input:
            raise ValueError("CLI input differs from locked signal source")
        if output_path != expected_output:
            raise ValueError("CLI output differs from locked confirmation output")
        if output_path.exists():
            raise FileExistsError(
                "confirmation output already exists; 2025 may be opened exactly once"
            )


def run(
    protocol: dict,
    frame: pd.DataFrame,
    *,
    protocol_path: Path = DEFAULT_PROTOCOL,
    input_path: Path = DEFAULT_INPUT,
) -> dict:
    validated = validate_historical_input(
        frame, expected_builder_schema=protocol.get("builder_schema")
    ).frame
    require_exact_seasons(validated)
    fit = validated[pd.to_numeric(validated.season).eq(2023)].copy()
    selection = validated[pd.to_numeric(validated.season).eq(2024)].copy()
    confirmation = validated[pd.to_numeric(validated.season).eq(2025)].copy()
    baseline_features = protocol["baseline_features"]
    candidate_features = baseline_features + protocol["candidate_additional_features"]
    grid = protocol["model_family"]["C_grid"]
    baseline_fit = select_regularization(fit, selection, baseline_features, grid)
    candidate_fit = select_regularization(fit, selection, candidate_features, grid)
    refit = pd.concat([fit, selection], ignore_index=True)
    baseline_model, baseline_probability = refit_predict(
        refit, confirmation, baseline_features, baseline_fit.selected_c
    )
    candidate_model, candidate_probability = refit_predict(
        refit, confirmation, candidate_features, candidate_fit.selected_c
    )
    y = target(confirmation)
    epsilon = 1e-12
    baseline_brier_loss = (baseline_probability - y) ** 2
    candidate_brier_loss = (candidate_probability - y) ** 2
    baseline_log_loss = -(y * np.log(np.clip(baseline_probability, epsilon, 1 - epsilon)) + (1-y) * np.log(np.clip(1-baseline_probability, epsilon, 1-epsilon)))
    candidate_log_loss = -(y * np.log(np.clip(candidate_probability, epsilon, 1 - epsilon)) + (1-y) * np.log(np.clip(1-candidate_probability, epsilon, 1-epsilon)))
    uncertainty = protocol["uncertainty"]
    brier_interval = paired_date_interval(
        confirmation.game_date.astype(str), candidate_brier_loss, baseline_brier_loss,
        draws=uncertainty["paired_bootstrap_draws"], seed=uncertainty["seed"],
    )
    log_interval = paired_date_interval(
        confirmation.game_date.astype(str), candidate_log_loss, baseline_log_loss,
        draws=uncertainty["paired_bootstrap_draws"], seed=uncertainty["seed"] + 1,
    )
    baseline_score = score_arm(y, baseline_probability)
    candidate_score = score_arm(y, candidate_probability)
    checks = {
        "exact_row_and_identity_coverage": len(baseline_probability) == len(candidate_probability) == len(confirmation),
        "candidate_2025_Brier_lower": candidate_score["brier"] < baseline_score["brier"],
        "candidate_2025_log_loss_lower": candidate_score["log_loss"] < baseline_score["log_loss"],
        "candidate_2025_ROC_AUC_not_lower": candidate_score["roc_auc"] >= baseline_score["roc_auc"],
        "paired_date_Brier_delta_upper_95_below_zero": brier_interval["upper_95"] < 0,
        "paired_date_log_loss_delta_upper_95_below_zero": log_interval["upper_95"] < 0,
    }
    passed = all(checks.values())
    provenance = report_provenance(protocol_path, input_path)
    return {
        "schema_version": (
            "hr-pre2026-batted-ball-signal-screen-report-v2"
            if protocol.get("schema_version", "").endswith(("v2", "v3"))
            else "hr-pre2026-batted-ball-signal-screen-report-v1"
        ),
        "status": "PASS_QUALIFIES_MODEL_RECONSTRUCTION" if passed else "REJECTED_SIGNAL_SCREEN",
        **provenance,
        "period_rows": {"2023_fit": len(fit), "2024_selection": len(selection), "2025_confirmation": len(confirmation)},
        "selected_regularization": {
            "baseline_C": baseline_fit.selected_c, "candidate_C": candidate_fit.selected_c,
            "baseline_2024_brier": baseline_fit.validation_brier,
            "candidate_2024_brier": candidate_fit.validation_brier,
        },
        "confirmation": {
            "baseline": baseline_score, "candidate": candidate_score,
            "candidate_minus_baseline_brier": candidate_score["brier"] - baseline_score["brier"],
            "candidate_minus_baseline_log_loss": candidate_score["log_loss"] - baseline_score["log_loss"],
            "brier_paired_date_interval": brier_interval,
            "log_loss_paired_date_interval": log_interval,
        },
        "success_checks": checks,
        "model_parameters": {
            "baseline": model_parameters(baseline_model),
            "candidate": model_parameters(candidate_model),
        },
        "decision": {
            "signal_screen_passed": passed,
            "current_model_reconstruction_permitted": passed,
            "candidate_install_permitted": False,
            "may_2026_opened": False,
            "betting_authorized": False,
        },
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    ap.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    args = ap.parse_args(argv)
    protocol_path = args.protocol.resolve()
    input_path = args.input.resolve()
    output_path = args.out.resolve()
    protocol = load_protocol(protocol_path)
    require_locked_paths(protocol, input_path, output_path)
    frame = pd.read_csv(input_path, low_memory=False)
    report = run(
        protocol, frame, protocol_path=protocol_path, input_path=input_path
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    tmp = output_path.with_suffix(output_path.suffix + ".tmp")
    tmp.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(output_path)
    print(f"HR PRE-2026 BATTED-BALL SIGNAL SCREEN: {report['status']}")
    print(json.dumps(report["confirmation"], indent=2))
    print(f"wrote {output_path}")
    print(f"sha256 {sha256_file(output_path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
