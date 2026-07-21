#!/usr/bin/env python3
"""Audit repaired HR PA probabilities against every valid upstream comparator."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path: os.sys.path.insert(0, str(ROOT))
from scripts.select_direct_batter_pa_foundation import _calibration, _component, _paired_component_interval, _scores  # noqa: E402
from src.evaluation.multi_market_foundation import PA_OUTCOMES  # noqa: E402
from src.evaluation.shared_pa_training_data import outcome_counts  # noqa: E402
from src.learning.shared_pa_model import fit_rate_baseline  # noqa: E402


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""): digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    if path.exists(): raise FileExistsError(f"refusing to overwrite HR comparator audit: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, sort_keys=True, indent=2, allow_nan=False); handle.write("\n"); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try: os.unlink(temporary)
        except FileNotFoundError: pass
        raise


def high_probability_tail(counts: np.ndarray, probability: np.ndarray) -> dict[str, float | int]:
    exposure = counts.sum(axis=1)
    positive = counts[:, 1]
    p = probability[:, 1]
    cutoff = float(np.quantile(p, 0.9))
    mask = p >= cutoff
    total = float(exposure[mask].sum())
    if total <= 0: raise ValueError("HR high-probability tail has no exposure")
    return {"row_count": int(mask.sum()), "pa": int(total), "probability_cutoff": cutoff,
            "mean_predicted": float((p[mask] * exposure[mask]).sum() / total),
            "mean_observed": float(positive[mask].sum() / total)}


def audit(*, panel: Path, report: Path, predictions: Path, output: Path) -> dict[str, Any]:
    selection_report = json.loads(report.read_text(encoding="utf-8"))
    if selection_report.get("status") != "SELECTION_REJECTED_NO_CANDIDATE":
        raise ValueError("HR required-comparator audit requires a locked rejected selection")
    if selection_report["predictions"]["sha256"] != sha256_file(predictions):
        raise ValueError("HR prediction hash mismatch")
    frame = pd.read_csv(panel, low_memory=False)
    raw_truth = frame[[f"target_{name}" for name in PA_OUTCOMES]].rename(columns={f"target_{name}": name for name in PA_OUTCOMES}).astype(int)
    if not outcome_counts(frame).loc[:, PA_OUTCOMES].astype(int).equals(raw_truth):
        raise ValueError("HR audit probability consumer is not bound to raw terminal truth")
    eligible = outcome_counts(frame).sum(axis=1).gt(0)
    fit = frame.loc[eligible & frame["season"].eq(2023)].reset_index(drop=True)
    selection = frame.loc[eligible & frame["season"].eq(2024)].reset_index(drop=True)
    prediction = pd.read_csv(predictions, low_memory=False)
    identity = ["game_pk", "player_id", "game_date"]
    if not selection[identity].astype(str).equals(prediction[identity].astype(str)):
        raise ValueError("HR comparator identity/order mismatch")
    candidate = prediction[[f"candidate_{name}" for name in PA_OUTCOMES]].to_numpy(float)
    if not np.isfinite(candidate).all() or not np.allclose(candidate.sum(axis=1), 1.0, atol=1e-9):
        raise ValueError("invalid candidate PA probabilities")
    league_model = fit_rate_baseline(fit, kind="league_rate")
    league, _ = league_model.predict(selection)
    counts, candidate_hr = _component(selection, candidate, "hr_over_0_5")
    _, league_hr = _component(selection, league, "hr_over_0_5")
    candidate_scores = _scores(counts, candidate_hr)
    league_scores = _scores(counts, league_hr)
    interval = _paired_component_interval(counts, candidate_hr, league_hr, selection["game_date"])
    gates = {metric: {"required_below": -0.01 * league_scores[metric],
                      "passed": interval[metric]["point"] < -0.01 * league_scores[metric] and interval[metric]["upper"] < -0.01 * league_scores[metric]}
             for metric in ("brier", "log_loss")}
    eb = selection_report["components"]["hr_over_0_5"]["comparators"]["empirical_bayes_player_rate_pa_200"]
    core = selection_report["components"]["hr_over_0_5"]["comparators"]["all_prior_core"]
    all_upstream = all(item["passed"] for item in gates.values()) and all(item["passed"] for item in eb["materiality"].values()) and bool(core["auc_noninferior_point"])
    result = {
        "schema_version": "repaired-hr-required-comparator-audit-v1",
        "status": "HR_REPAIRED_CANDIDATE_REJECTED_UPSTREAM" if not all_upstream else "HR_UPSTREAM_COMPARATORS_PASSED_DOWNSTREAM_STILL_REQUIRED",
        "inputs": {"panel_sha256": sha256_file(panel), "selection_report_sha256": sha256_file(report), "predictions_sha256": sha256_file(predictions)},
        "chronology": {"fit_year": 2023, "selection_year": 2024, "confirmation_opened": False, "spent_2025_hr_reused": False, "may_2026_opened": False},
        "coverage": {"fit_rows": len(fit), "selection_rows": len(selection), "selection_pa": int(counts.sum()), "identity_loss": 0},
        "candidate": {"scores": candidate_scores, "calibration": _calibration(counts, candidate_hr), "high_probability_tail": high_probability_tail(counts, candidate_hr)},
        "league_rate_2023_fit": {"scores": league_scores, "calibration": _calibration(counts, league_hr), "paired_interval": interval, "materiality": gates, "auc_noninferior_point": candidate_scores["auc"] >= league_scores["auc"]},
        "player_empirical_bayes_2023_fit_pa_200": eb,
        "all_prior_batter_core": core,
        "downstream_comparators": {
            "frozen_production_simulator": {"status": "NOT_REACHED", "reason": "Repaired HR per-PA candidate failed the player-EB materiality gate and core discrimination gate; full-game comparison also requires receipt-proven pregame PA volume."},
            "valid_market_implied_probability": {"status": "NOT_AVAILABLE_FOR_THIS_PANEL", "reason": "No hash-bound executable pregame HR prices are joined to this 2024 per-PA selection panel; historical or unverified prices are forbidden."}
        },
        "promotion_eligible": False,
        "production_changed": False, "betting_authorized": False,
        "script_sha256": sha256_file(Path(__file__)),
    }
    atomic_json(output, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel", required=True, type=Path); parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--predictions", required=True, type=Path); parser.add_argument("--output", required=True, type=Path)
    result = audit(**vars(parser.parse_args()))
    print(json.dumps({"status": result["status"], "league_materiality": result["league_rate_2023_fit"]["materiality"], "promotion_eligible": False}, sort_keys=True))
    return 0


if __name__ == "__main__": raise SystemExit(main())
