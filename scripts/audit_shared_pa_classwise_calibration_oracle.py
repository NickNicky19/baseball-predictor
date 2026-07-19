#!/usr/bin/env python3
"""Rule out a weak classwise-calibration family before fitting a challenger.

The calculation intentionally fits on all 2024 selection outcomes, so it is
not deployable and may never be promoted.  It is useful only as an optimistic
upper-bound screen: if even that leaky oracle misses the locked materiality
floor, a leakage-free chronological calibration cannot justify a new model.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import minimize

import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.multi_market_foundation import PA_OUTCOMES  # noqa: E402
from src.learning.shared_pa_model import proper_scores  # noqa: E402


EPS = 1e-12
SCHEMA = "shared-pa-classwise-calibration-oracle-v1"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _softmax(values: np.ndarray) -> np.ndarray:
    shifted = values - values.max(axis=1, keepdims=True)
    exponent = np.exp(shifted)
    return exponent / exponent.sum(axis=1, keepdims=True)


def oracle_calibration(counts: pd.DataFrame, probabilities: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
    observed = counts.loc[:, PA_OUTCOMES].to_numpy(float)
    original = np.asarray(probabilities, dtype=float)
    if observed.shape != original.shape or not np.allclose(original.sum(axis=1), 1.0, atol=1e-9):
        raise ValueError("oracle calibration input shape/probability contract is invalid")
    log_probability = np.log(np.clip(original, EPS, 1.0))
    exposure = observed.sum(axis=1, keepdims=True)
    classes = len(PA_OUTCOMES)

    def transform(parameters: np.ndarray) -> np.ndarray:
        return _softmax(log_probability * parameters[:classes][None, :] + parameters[classes:][None, :])

    def objective(parameters: np.ndarray) -> float:
        calibrated = np.clip(transform(parameters), EPS, 1.0)
        return float(-(observed * np.log(calibrated)).sum())

    def gradient(parameters: np.ndarray) -> np.ndarray:
        calibrated = transform(parameters)
        residual = calibrated * exposure - observed
        return np.concatenate([(residual * log_probability).sum(axis=0), residual.sum(axis=0)])

    fitted = minimize(
        objective,
        x0=np.concatenate([np.ones(classes), np.zeros(classes)]),
        jac=gradient,
        method="L-BFGS-B",
        options={"ftol": 1e-12, "gtol": 1e-8, "maxiter": 10000, "maxls": 100},
    )
    if not fitted.success or not np.isfinite(fitted.x).all():
        raise ValueError(f"oracle calibration did not converge: {fitted.message}")
    calibrated = transform(fitted.x)
    return calibrated, {
        "method": "full_2024_in_sample_multinomial_affine_log_probability_oracle",
        "success": bool(fitted.success),
        "message": str(fitted.message),
        "iterations": int(fitted.nit),
        "objective": float(fitted.fun),
        "gradient_norm": float(np.linalg.norm(gradient(fitted.x))),
        "scales": {name: float(value) for name, value in zip(PA_OUTCOMES, fitted.x[:classes])},
        "intercepts": {name: float(value) for name, value in zip(PA_OUTCOMES, fitted.x[classes:])},
    }


def audit(report_path: Path) -> dict[str, Any]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("status") != "SELECTION_REJECTED_NO_CANDIDATE":
        raise ValueError("oracle screen requires the certified rejected regular-season selection")
    if report.get("confirmation_2025_opened") or report.get("may_2026_opened"):
        raise ValueError("oracle screen cannot consume protected evidence")
    oof_path = Path(report["oof_artifact"]["path"])
    if not oof_path.is_file() or sha256(oof_path) != report["oof_artifact"]["sha256"]:
        raise ValueError("oracle OOF artifact hash changed")
    frame = pd.read_csv(oof_path)
    if set(pd.to_datetime(frame["game_date"], format="%Y-%m-%d", errors="raise").dt.year) != {2024}:
        raise ValueError("oracle screen accessed data outside the 2024 selection period")
    counts = frame[[f"actual_{name}" for name in PA_OUTCOMES]].copy()
    counts.columns = PA_OUTCOMES
    candidate = frame[[f"canonical_raw_{name}" for name in PA_OUTCOMES]].to_numpy(float)
    simple = frame[[f"strongest_simple_{name}" for name in PA_OUTCOMES]].to_numpy(float)
    oracle, optimizer = oracle_calibration(counts, candidate)
    raw_scores = proper_scores(counts, candidate)
    simple_scores = proper_scores(counts, simple)
    oracle_scores = proper_scores(counts, oracle)
    floor = report["complexity_benefit_floor"]
    comparisons = {
        metric: {
            "oracle_minus_simple": float(oracle_scores[metric] - simple_scores[metric]),
            "required_strictly_below": -float(floor[metric]),
            "clears_even_optimistic_point_floor": bool(
                oracle_scores[metric] - simple_scores[metric] < -float(floor[metric])
            ),
        }
        for metric in ("multiclass_log_loss", "multiclass_brier")
    }
    clears = all(row["clears_even_optimistic_point_floor"] for row in comparisons.values())
    return {
        "schema_version": SCHEMA,
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "ORACLE_FAMILY_NOT_ELIGIBLE_FOR_CHALLENGER" if not clears else "ORACLE_SCREEN_INCONCLUSIVE",
        "purpose": "Non-deployable upper-bound screen only; it cannot select, calibrate, promote, or authorize a model.",
        "inputs": {
            "selection_report": {"path": str(report_path), "sha256": sha256(report_path)},
            "selection_oof": {"path": str(oof_path), "sha256": sha256(oof_path), "rows": int(len(frame))},
        },
        "scope": {
            "selection_season": [2024],
            "confirmation_2025_read": False,
            "may_2026_read": False,
            "production_changed": False,
            "betting_authorized": False,
        },
        "scores": {"raw_candidate": raw_scores, "strongest_simple": simple_scores, "leaky_oracle": oracle_scores},
        "optimizer": optimizer,
        "materiality_screen": comparisons,
        "decision": {
            "cross_fitted_classwise_calibration_candidate_permitted": bool(clears),
            "reason": (
                "Even the full-period in-sample oracle clears the point materiality floors; a separate leakage-free protocol is required before any candidate."
                if clears else
                "Even the deliberately leaky full-period oracle misses at least one point materiality floor. A chronological calibration version cannot be claimed to supply the required material improvement."
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selection-report", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    payload = audit(args.selection_report.resolve())
    atomic_json(args.out.resolve(), payload)
    print(payload["status"])
    for metric, row in payload["materiality_screen"].items():
        print(f"{metric}: oracle-simple={row['oracle_minus_simple']:.12f}; threshold={row['required_strictly_below']:.12f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
