#!/usr/bin/env python3
"""Describe PA-level HR calibration tails without changing selection gates."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd


FIXED_EDGES = np.asarray([0.0, 0.01, 0.02, 0.03, 0.05, 0.075, 0.10, 0.15, 0.25, 1.0])
KEYS = ["game_pk", "player_id", "game_date"]
TARGET_COLUMNS = [
    "target_strikeout", "target_walk", "target_single", "target_double",
    "target_triple", "target_home_run", "target_bip_out", "target_other_non_ab",
]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, payload: dict) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite tail audit: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, sort_keys=True, indent=2, allow_nan=False)
            handle.write("\n"); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try: os.unlink(temporary)
        except FileNotFoundError: pass
        raise


def calibration_summary(probability: np.ndarray, positive: np.ndarray, exposure: np.ndarray) -> dict:
    if not (len(probability) == len(positive) == len(exposure)) or len(probability) == 0:
        raise ValueError("invalid HR tail arrays")
    if (~np.isfinite(probability)).any() or (probability < 0).any() or (probability > 1).any():
        raise ValueError("invalid HR probabilities")
    if (exposure <= 0).any() or (positive < 0).any() or (positive > exposure).any():
        raise ValueError("invalid HR outcome exposure")
    bins = []
    for low, high in zip(FIXED_EDGES[:-1], FIXED_EDGES[1:]):
        mask = (probability >= low) & (probability < high if high < 1.0 else probability <= high)
        if not mask.any():
            continue
        total = float(exposure[mask].sum())
        predicted = float((probability[mask] * exposure[mask]).sum() / total)
        observed = float(positive[mask].sum() / total)
        bins.append({
            "lower": float(low), "upper": float(high), "rows": int(mask.sum()),
            "pa": int(total), "predicted": predicted, "observed": observed,
            "observed_minus_predicted": observed - predicted,
        })
    threshold = float(np.quantile(probability, 0.90))
    tail = probability >= threshold
    tail_exposure = float(exposure[tail].sum())
    return {
        "fixed_probability_bins": bins,
        "top_decile": {
            "threshold_from_predictions_only": threshold,
            "rows": int(tail.sum()), "pa": int(tail_exposure),
            "predicted": float((probability[tail] * exposure[tail]).sum() / tail_exposure),
            "observed": float(positive[tail].sum() / tail_exposure),
            "observed_minus_predicted": float(positive[tail].sum() / tail_exposure - (probability[tail] * exposure[tail]).sum() / tail_exposure),
        },
    }


def run(*, panel: Path, predictions: Path, selection_report: Path, output: Path) -> dict:
    report = json.loads(selection_report.read_text(encoding="utf-8"))
    if report.get("status") != "SELECTION_REJECTED_NO_CANDIDATE":
        raise ValueError("tail audit is restricted to the frozen rejected selection")
    frame = pd.read_csv(panel, low_memory=False)
    frame = frame.loc[pd.to_numeric(frame["season"], errors="raise").eq(2024)].copy()
    if missing := sorted(set(TARGET_COLUMNS).difference(frame.columns)):
        raise ValueError(f"tail audit target columns missing: {missing}")
    frame["_exposure"] = frame[TARGET_COLUMNS].apply(pd.to_numeric, errors="raise").sum(axis=1)
    frame = frame.loc[frame["_exposure"].gt(0), [*KEYS, "_exposure", "target_home_run"]]
    prediction = pd.read_csv(predictions, low_memory=False)
    merged = prediction.merge(frame, on=KEYS, how="inner", validate="one_to_one")
    if len(merged) != len(prediction) or len(merged) != len(frame):
        raise ValueError("tail audit identity/coverage mismatch")
    exposure = merged["_exposure"].to_numpy(float)
    positive = merged["target_home_run"].to_numpy(float)
    models = {}
    for label in ("candidate", "core", "simple", "league"):
        models[label] = calibration_summary(
            merged[f"{label}_home_run"].to_numpy(float), positive, exposure
        )
    result = {
        "schema_version": "direct-batter-hr-pa-tail-audit-v1",
        "status": "DESCRIPTIVE_ONLY_SELECTION_REMAINS_REJECTED",
        "scope": "PA-level HR event calibration; not HR-over-0.5 game-market calibration",
        "gate_effect": "none; thresholds were not locked in the candidate protocol and cannot rescue or promote the candidate",
        "population": {"rows": len(merged), "pa": int(exposure.sum()), "hr": int(positive.sum())},
        "models": models,
        "inputs": {
            "panel_sha256": sha256_file(panel),
            "predictions_sha256": sha256_file(predictions),
            "selection_report_sha256": sha256_file(selection_report),
        },
        "script_sha256": sha256_file(Path(__file__)),
        "may_2026_opened": False,
        "confirmation_2025_opened": False,
        "production_changed": False,
        "betting_authorized": False,
    }
    atomic_json(output, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel", required=True, type=Path)
    parser.add_argument("--predictions", required=True, type=Path)
    parser.add_argument("--selection-report", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = run(**vars(args))
    print(json.dumps({"status": result["status"], "population": result["population"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
