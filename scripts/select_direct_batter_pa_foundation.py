#!/usr/bin/env python3
"""Fit 2023 once and strictly adjudicate the direct batter PA family on 2024."""
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
from sklearn.metrics import roc_auc_score

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from src.evaluation.multi_market_foundation import PA_OUTCOMES  # noqa: E402
from src.evaluation.shared_pa_training_data import outcome_counts  # noqa: E402
from src.learning.shared_pa_model import (  # noqa: E402
    fit_catboost, fit_rate_baseline, paired_date_block_interval, proper_scores,
)


SEED = 20260721
PARAMS = {"depth": 4, "iterations": 1200, "l2_leaf_reg": 10.0, "learning_rate": 0.03, "thread_count": 2}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try: os.unlink(temporary)
        except FileNotFoundError: pass
        raise


def _component(frame: pd.DataFrame, probabilities: np.ndarray, name: str) -> tuple[np.ndarray, np.ndarray]:
    counts = outcome_counts(frame).loc[:, PA_OUTCOMES].to_numpy(float)
    index = {value: i for i, value in enumerate(PA_OUTCOMES)}
    if name == "hits":
        members = [index[x] for x in ("single", "double", "triple", "home_run")]
        positive = counts[:, members].sum(axis=1)
        p = probabilities[:, members].sum(axis=1)
        return np.column_stack([counts.sum(axis=1) - positive, positive]), np.column_stack([1.0 - p, p])
    if name == "hr_over_0_5":
        positive = counts[:, index["home_run"]]
        p = probabilities[:, index["home_run"]]
        return np.column_stack([counts.sum(axis=1) - positive, positive]), np.column_stack([1.0 - p, p])
    if name == "total_bases":
        zero = counts[:, [index[x] for x in ("strikeout", "walk", "bip_out", "other_non_ab")]].sum(axis=1)
        return (
            np.column_stack([zero, counts[:, index["single"]], counts[:, index["double"]], counts[:, index["triple"]], counts[:, index["home_run"]]]),
            np.column_stack([
                probabilities[:, [index[x] for x in ("strikeout", "walk", "bip_out", "other_non_ab")]].sum(axis=1),
                probabilities[:, index["single"]], probabilities[:, index["double"]], probabilities[:, index["triple"]], probabilities[:, index["home_run"]],
            ]),
        )
    raise ValueError(f"unknown component {name}")


def _scores(counts: np.ndarray, probability: np.ndarray) -> dict[str, float]:
    exposure = counts.sum(axis=1)
    total = exposure.sum()
    clipped = np.clip(probability, 1e-12, 1.0)
    brier = (exposure * (1 + np.square(probability).sum(axis=1)) - 2 * (counts * probability).sum(axis=1)).sum() / total
    log_loss = -(counts * np.log(clipped)).sum() / total
    expected_class = (probability * np.arange(probability.shape[1])).sum(axis=1)
    positives = (counts * np.arange(counts.shape[1])).sum(axis=1)
    # Binary AUC is exact.  For TB, use weighted one-vs-rest macro AUC so the
    # discrimination target remains a proper class-distribution diagnostic.
    if counts.shape[1] == 2:
        auc = roc_auc_score(np.r_[np.ones(len(counts)), np.zeros(len(counts))], np.r_[probability[:, 1], probability[:, 1]], sample_weight=np.r_[counts[:, 1], counts[:, 0]])
    else:
        aucs = []
        for j in range(counts.shape[1]):
            aucs.append(roc_auc_score(np.r_[np.ones(len(counts)), np.zeros(len(counts))], np.r_[probability[:, j], probability[:, j]], sample_weight=np.r_[counts[:, j], exposure - counts[:, j]]))
        auc = float(np.mean(aucs))
    return {"brier": float(brier), "log_loss": float(log_loss), "auc": float(auc), "mean_expected_class": float((expected_class * exposure).sum() / total), "mean_observed_class": float(positives.sum() / total)}


def _calibration(counts: np.ndarray, probability: np.ndarray) -> dict[str, Any]:
    """Exact weighted one-vs-rest logistic calibration diagnostics."""
    exposure = counts.sum(axis=1).astype(float)
    result: dict[str, Any] = {}
    classes = [1] if counts.shape[1] == 2 else list(range(counts.shape[1]))
    for index in classes:
        positive = counts[:, index].astype(float)
        p = np.clip(probability[:, index].astype(float), 1e-9, 1 - 1e-9)
        x = np.column_stack([np.ones(len(p)), np.log(p) - np.log1p(-p)])
        beta = np.array([0.0, 1.0])
        converged = False
        for _ in range(50):
            linear = np.clip(x @ beta, -35.0, 35.0)
            fitted = 1.0 / (1.0 + np.exp(-linear))
            gradient = x.T @ (exposure * fitted - positive)
            weight = exposure * fitted * (1.0 - fitted)
            hessian = x.T @ (x * weight[:, None])
            try:
                step = np.linalg.solve(hessian, gradient)
            except np.linalg.LinAlgError:
                break
            beta -= step
            if float(np.max(np.abs(step))) < 1e-8:
                converged = True
                break
        result[str(index)] = {"intercept": float(beta[0]), "slope": float(beta[1]), "converged": converged}
    return result


def _paired_component_interval(counts: np.ndarray, candidate: np.ndarray, comparator: np.ndarray, dates: pd.Series, *, draws: int = 2000) -> dict[str, Any]:
    exposure = counts.sum(axis=1)
    def losses(p: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        logloss = -(counts * np.log(np.clip(p, 1e-12, 1.0))).sum(axis=1)
        brier = exposure * (1 + np.square(p).sum(axis=1)) - 2 * (counts * p).sum(axis=1)
        return brier, logloss
    cb, cl = losses(candidate); bb, bl = losses(comparator)
    block = pd.DataFrame({"date": pd.to_datetime(dates).dt.date, "exposure": exposure, "brier": cb-bb, "log_loss": cl-bl}).groupby("date", as_index=False, sort=True).sum()
    rng = np.random.default_rng(SEED)
    result: dict[str, Any] = {"dates": len(block), "draws": draws}
    for metric in ("brier", "log_loss"):
        delta = block[metric].to_numpy(float); exp = block["exposure"].to_numpy(float)
        samples = np.empty(draws)
        for draw in range(draws):
            selected = rng.integers(0, len(block), len(block))
            samples[draw] = delta[selected].sum() / exp[selected].sum()
        result[metric] = {"point": float(delta.sum()/exp.sum()), "lower": float(np.quantile(samples,.025)), "upper": float(np.quantile(samples,.975))}
    return result


def run(*, panel: Path, manifest: Path, protocol: Path, report: Path, predictions: Path) -> dict[str, Any]:
    if report.exists() or predictions.exists():
        raise FileExistsError("refusing to overwrite direct batter PA selection evidence")
    source_manifest = json.loads(manifest.read_text(encoding="utf-8"))
    contract = json.loads(protocol.read_text(encoding="utf-8"))
    if source_manifest.get("status") != "DIRECT_BATTER_PA_TIMING_CONTRACT_PASSED_RESEARCH_ONLY":
        raise ValueError("direct batter panel timing contract did not pass")
    if source_manifest.get("output", {}).get("sha256") != sha256_file(panel):
        raise ValueError("direct batter panel hash mismatch")
    if contract.get("status") not in {"LOCKED_BEFORE_DIRECT_BATTER_PA_BUILD_OR_2024_SELECTION", "LOCKED_BEFORE_REPAIRED_BUILD_OR_2024_RESELECTION"}:
        raise ValueError("direct batter protocol is not locked")
    frame = pd.read_csv(panel, low_memory=False)
    raw_targets = frame[[f"target_{name}" for name in PA_OUTCOMES]].rename(columns={f"target_{name}": name for name in PA_OUTCOMES}).astype(int)
    consumed_targets = outcome_counts(frame).loc[:, PA_OUTCOMES].astype(int)
    if not consumed_targets.equals(raw_targets):
        raise ValueError("probability consumer outcome columns do not equal raw terminal truth")
    years = pd.to_numeric(frame["season"], errors="raise").astype(int)
    if set(years.unique()) != {2023, 2024}:
        raise ValueError("selection panel must contain exactly 2023 and 2024")
    if frame.duplicated(["game_pk", "player_id"]).any():
        raise ValueError("selection identity duplicated")
    target_dates = pd.to_datetime(frame["game_date"], format="%Y-%m-%d", errors="coerce")
    source_dates = pd.to_datetime(frame["max_source_date"], format="%Y-%m-%d", errors="coerce")
    if target_dates.isna().any() or (source_dates.notna() & source_dates.ge(target_dates)).any():
        raise ValueError("selection chronology violation")
    eligible = outcome_counts(frame).sum(axis=1).gt(0)
    frame = frame.loc[eligible].reset_index(drop=True)
    fit = frame["season"].eq(2023); select = frame["season"].eq(2024)
    history = [column for column in frame.columns if column.startswith("history_")]
    core_features = [column for column in history if "age_days" not in column]
    candidate_features = ["player_id", *history, *[column for column in frame.columns if column.startswith("days_since_")]]
    core = fit_catboost(frame.loc[fit], features=core_features, params=PARAMS, seed=SEED)
    candidate = fit_catboost(frame.loc[fit], features=candidate_features, params=PARAMS, seed=SEED, categorical_features=["player_id"])
    p_core = core.predict_proba(frame.loc[select])
    p_candidate = candidate.predict_proba(frame.loc[select])
    baseline_model = fit_rate_baseline(frame.loc[fit], kind="empirical_bayes_player_rate", prior_strength_pa=200.0)
    p_baseline, fallback = baseline_model.predict(frame.loc[select])
    selection = frame.loc[select].reset_index(drop=True)
    counts = outcome_counts(selection)
    overall = {}
    comparator_probabilities = {"empirical_bayes_player_rate_pa_200": p_baseline, "all_prior_core": p_core}
    candidate_scores = proper_scores(counts, p_candidate)
    material_all = True
    for name, probability in comparator_probabilities.items():
        scores = proper_scores(counts, probability)
        intervals = {
            metric: paired_date_block_interval(counts, p_candidate, probability, selection["game_date"], metric=metric, draws=2000, seed=SEED)
            for metric in ("brier", "log_loss")
        }
        gates = {}
        for metric, score_name in (("brier", "multiclass_brier"), ("log_loss", "multiclass_log_loss")):
            required = -0.01 * scores[score_name]
            gates[metric] = {"required_below": required, "point": intervals[metric]["point"], "upper": intervals[metric]["upper"], "passed": intervals[metric]["point"] < required and intervals[metric]["upper"] < required}
            material_all = material_all and gates[metric]["passed"]
        overall[name] = {"scores": scores, "intervals": intervals, "materiality": gates}
    components: dict[str, Any] = {}
    for component in ("hits", "hr_over_0_5", "total_bases"):
        component_counts, candidate_p = _component(selection, p_candidate, component)
        row: dict[str, Any] = {"candidate": _scores(component_counts, candidate_p), "candidate_calibration": _calibration(component_counts, candidate_p), "comparators": {}}
        eligible_component = True
        for name, probability in comparator_probabilities.items():
            _, comparator_p = _component(selection, probability, component)
            comparator_scores = _scores(component_counts, comparator_p)
            interval = _paired_component_interval(component_counts, candidate_p, comparator_p, selection["game_date"])
            gates = {}
            for metric in ("brier", "log_loss"):
                required = -0.01 * comparator_scores[metric]
                gates[metric] = {"required_below": required, "passed": interval[metric]["point"] < required and interval[metric]["upper"] < required}
                eligible_component = eligible_component and gates[metric]["passed"]
            auc_noninferior = row["candidate"]["auc"] >= comparator_scores["auc"]
            eligible_component = eligible_component and auc_noninferior
            row["comparators"][name] = {"scores": comparator_scores, "calibration": _calibration(component_counts, comparator_p), "paired_interval": interval, "materiality": gates, "auc_noninferior_point": auc_noninferior}
        row["selection_eligible"] = bool(eligible_component)
        components[component] = row
    prediction_frame = selection[["game_pk", "player_id", "game_date"]].copy()
    for label, probability in (("candidate", p_candidate), ("core", p_core), ("simple", p_baseline)):
        for index, outcome in enumerate(PA_OUTCOMES): prediction_frame[f"{label}_{outcome}"] = probability[:, index]
    atomic(predictions, prediction_frame.to_csv(index=False, lineterminator="\n").encode("utf-8"))
    result: dict[str, Any] = {
        "schema_version": "direct-batter-pa-selection-report-v1",
        "status": "SELECTION_PASSED_REQUIRES_FRESH_CONFIRMATION" if material_all and any(v["selection_eligible"] for v in components.values()) else "SELECTION_REJECTED_NO_CANDIDATE",
        "inputs": {"panel": {"path": str(panel), "sha256": sha256_file(panel), "rows": len(frame)}, "manifest_sha256": sha256_file(manifest), "protocol_sha256": sha256_file(protocol)},
        "chronology": {"fit_year": 2023, "selection_year": 2024, "confirmation_opened": False, "spent_2025_hr_reused": False, "may_2026_opened": False},
        "population": {"fit_rows": int(fit.sum()), "selection_rows": int(select.sum()), "zero_pa_rows_excluded_as_ineligible": int((~eligible).sum()), "coverage_loss_eligible_rows": 0, "simple_unseen_player_fraction": float(fallback.mean())},
        "features": {"core": core_features, "candidate": candidate_features, "forbidden_context_present": False},
        "model": {"params": PARAMS, "seed": SEED},
        "candidate_scores": candidate_scores,
        "overall_comparators": overall,
        "foundation_materiality_passed": bool(material_all),
        "components": components,
        "predictions": {"path": str(predictions), "sha256": sha256_file(predictions), "rows": len(prediction_frame)},
        "calibration_note": "One-vs-rest weighted logistic calibration intercept/slope are diagnostics only; no post-selection recalibration was fitted. Fresh confirmation must predeclare uncertainty gates before promotion.",
        "full_game_market_blocker": "No component can advance a Hits, HR-over-0.5, or Total-Bases market without separately receipt-proven pregame PA volume and fresh untouched confirmation.",
        "production_changed": False, "betting_authorized": False,
        "script_sha256": sha256_file(Path(__file__)),
    }
    atomic(report, (json.dumps(result, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8"))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--protocol", type=Path, default=ROOT / "config/direct_batter_pa_foundation_v1.json")
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--predictions", required=True, type=Path)
    args = parser.parse_args()
    result = run(**vars(args))
    print(json.dumps({"status": result["status"], "candidate_scores": result["candidate_scores"], "components": {k:v["selection_eligible"] for k,v in result["components"].items()}}, sort_keys=True))
    return 0


if __name__ == "__main__": raise SystemExit(main())
