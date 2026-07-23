#!/usr/bin/env python3
"""Locked 2023-only development of the EB-augmented shared PA foundation."""
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
from sklearn.model_selection import TimeSeriesSplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from src.evaluation.multi_market_foundation import PA_OUTCOMES  # noqa: E402
from src.evaluation.shared_pa_training_data import outcome_counts  # noqa: E402
from src.learning.eb_residual_pa_model import fit_eb_augmented_pa_model  # noqa: E402
from src.learning.shared_pa_model import (  # noqa: E402
    fit_catboost,
    fit_rate_baseline,
    paired_date_block_interval,
    proper_scores,
)


CORE_PARAMS = {
    "depth": 4,
    "iterations": 1200,
    "l2_leaf_reg": 10.0,
    "learning_rate": 0.03,
    "thread_count": 2,
}


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
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _load_contract(protocol: Path) -> dict[str, Any]:
    contract = json.loads(protocol.read_text(encoding="utf-8"))
    _require(contract.get("schema_version") == "shared-pa-eb-augmented-development-protocol-v2", "unexpected EB-augmented protocol schema")
    _require(contract.get("status") == "LOCKED_BEFORE_2023_FOLD_SCORING", "EB-augmented protocol is not locked before scoring")
    _require(contract.get("research_only") is True, "EB-augmented protocol must be research only")
    _require(contract.get("betting_authorized") is False and contract.get("production_changed") is False, "protocol must not claim production or betting")
    candidate = contract.get("candidate")
    _require(isinstance(candidate, dict), "protocol candidate is missing")
    _require(candidate.get("outcomes") == PA_OUTCOMES, "protocol PA outcome identity changed")
    numeric = candidate.get("numeric_features")
    logged = candidate.get("log1p_features")
    _require(isinstance(numeric, list) and numeric and len(numeric) == len(set(numeric)), "numeric feature contract is invalid")
    _require(isinstance(logged, list) and logged and len(logged) == len(set(logged)), "log1p feature contract is invalid")
    _require(not set(numeric).intersection(logged), "process feature contracts overlap")
    grid = candidate.get("inner_selection", {}).get("grid", {})
    _require(grid.get("prior_strength_pa") == [50.0, 200.0, 800.0], "prior grid changed")
    _require(grid.get("regularization_c") == [0.01, 0.1, 1.0], "regularization grid changed")
    return contract


def _validate_panel(panel: Path, manifest: Path, contract: dict[str, Any]) -> pd.DataFrame:
    evidence = contract["evidence_boundary"]
    _require(str(panel) == evidence["external_panel"], "panel path differs from locked external panel")
    _require(str(manifest) == evidence["external_manifest"], "manifest path differs from locked external manifest")
    _require(sha256_file(panel) == evidence["external_panel_sha256"], "2023-only panel hash mismatch")
    _require(sha256_file(manifest) == evidence["external_manifest_sha256"], "2023-only panel manifest hash mismatch")
    source_manifest = json.loads(manifest.read_text(encoding="utf-8"))
    _require(source_manifest.get("status") == "CERTIFIED_2023_ONLY_DEVELOPMENT_RESEARCH_ONLY", "source panel lacks 2023-only certificate")
    _require(source_manifest.get("output", {}).get("sha256") == evidence["external_panel_sha256"], "source manifest does not bind panel hash")
    _require(source_manifest.get("validation", {}).get("years") == [2023], "source manifest includes a forbidden year")
    _require(source_manifest.get("validation", {}).get("2024_rows_parsed") == 0, "source manifest opened 2024")
    _require(source_manifest.get("validation", {}).get("2025_confirmation_opened") is False, "source manifest opened 2025 confirmation")
    _require(source_manifest.get("validation", {}).get("may_2026_opened") is False, "source manifest opened May 2026")
    frame = pd.read_csv(panel, low_memory=False)
    _require(not frame.columns.duplicated().any(), "2023 panel has duplicate column labels")
    required = {
        "season", "game_date", "game_pk", "player_id", "lineup_slot", "max_source_date",
        "out_pa", "out_ab", "out_hits", "out_doubles", "out_triples", "out_hr", "out_bb", "out_k",
        *numeric_feature_names(contract), *log1p_feature_names(contract),
        *[f"history_{outcome}_count" for outcome in PA_OUTCOMES], "history_pa",
        *[f"target_{outcome}" for outcome in PA_OUTCOMES],
    }
    missing = sorted(required.difference(frame.columns))
    _require(not missing, f"2023 panel misses contracted columns: {missing}")
    years = pd.to_numeric(frame["season"], errors="coerce")
    _require(years.notna().all() and set(years.astype(int)) == {2023}, "development panel is not exclusively 2023")
    dates = pd.to_datetime(frame["game_date"], format="%Y-%m-%d", errors="coerce")
    sources = pd.to_datetime(frame["max_source_date"], format="%Y-%m-%d", errors="coerce")
    _require(dates.notna().all() and (dates.dt.year == 2023).all(), "panel game dates are not exclusively 2023")
    _require(not (sources.notna() & sources.ge(dates)).any(), "point-in-time feature chronology violation")
    _require(frame[["game_pk", "player_id"]].notna().all().all() and not frame.duplicated(["game_pk", "player_id"]).any(), "panel identity is missing or duplicated")
    raw_targets = frame[[f"target_{outcome}" for outcome in PA_OUTCOMES]].rename(columns={f"target_{outcome}": outcome for outcome in PA_OUTCOMES}).astype(int)
    consumed = outcome_counts(frame).loc[:, PA_OUTCOMES].astype(int)
    _require(consumed.equals(raw_targets), "probability consumer outcome columns do not equal raw terminal truth")
    eligible = consumed.sum(axis=1).gt(0)
    _require(eligible.any(), "2023 panel has no positive PA rows")
    return frame.loc[eligible].reset_index(drop=True)


def numeric_feature_names(contract: dict[str, Any]) -> list[str]:
    return list(contract["candidate"]["numeric_features"])


def log1p_feature_names(contract: dict[str, Any]) -> list[str]:
    return list(contract["candidate"]["log1p_features"])


def _date_splits(frame: pd.DataFrame, n_splits: int) -> list[tuple[np.ndarray, np.ndarray]]:
    dates = np.array(sorted(pd.to_datetime(frame["game_date"], format="%Y-%m-%d", errors="raise").dt.date.unique()))
    _require(len(dates) > n_splits, "not enough distinct dates for chronological splits")
    output: list[tuple[np.ndarray, np.ndarray]] = []
    for train_dates_index, validation_dates_index in TimeSeriesSplit(n_splits=n_splits).split(dates):
        train_dates = set(dates[train_dates_index])
        validation_dates = set(dates[validation_dates_index])
        _require(max(train_dates) < min(validation_dates), "chronological split is not strictly ordered")
        date_values = pd.to_datetime(frame["game_date"], format="%Y-%m-%d", errors="raise").dt.date
        train_rows = np.flatnonzero(date_values.isin(train_dates).to_numpy())
        validation_rows = np.flatnonzero(date_values.isin(validation_dates).to_numpy())
        _require(len(train_rows) > 0 and len(validation_rows) > 0, "empty chronological split")
        output.append((train_rows, validation_rows))
    return output


def _component(frame: pd.DataFrame, probabilities: np.ndarray, name: str) -> tuple[np.ndarray, np.ndarray]:
    counts = outcome_counts(frame).loc[:, PA_OUTCOMES].to_numpy(float)
    index = {value: i for i, value in enumerate(PA_OUTCOMES)}
    if name == "hits":
        members = [index[value] for value in ("single", "double", "triple", "home_run")]
        positive = counts[:, members].sum(axis=1)
        p = probabilities[:, members].sum(axis=1)
        return np.column_stack([counts.sum(axis=1) - positive, positive]), np.column_stack([1.0 - p, p])
    if name == "hr_over_0_5":
        positive = counts[:, index["home_run"]]
        p = probabilities[:, index["home_run"]]
        return np.column_stack([counts.sum(axis=1) - positive, positive]), np.column_stack([1.0 - p, p])
    if name == "total_bases":
        zero = counts[:, [index[value] for value in ("strikeout", "walk", "bip_out", "other_non_ab")]].sum(axis=1)
        return (
            np.column_stack([zero, counts[:, index["single"]], counts[:, index["double"]], counts[:, index["triple"]], counts[:, index["home_run"]]]),
            np.column_stack([probabilities[:, [index[value] for value in ("strikeout", "walk", "bip_out", "other_non_ab")]].sum(axis=1), probabilities[:, index["single"]], probabilities[:, index["double"]], probabilities[:, index["triple"]], probabilities[:, index["home_run"]]]),
        )
    raise ValueError(f"unknown market component: {name}")


def _scores(counts: np.ndarray, probability: np.ndarray) -> dict[str, float]:
    exposure = counts.sum(axis=1)
    total = float(exposure.sum())
    _require(total > 0.0, "component has no PA exposure")
    clipped = np.clip(probability, 1e-12, 1.0)
    brier = (exposure * (1.0 + np.square(probability).sum(axis=1)) - 2.0 * (counts * probability).sum(axis=1)).sum() / total
    log_loss = -(counts * np.log(clipped)).sum() / total
    if counts.shape[1] == 2:
        auc = roc_auc_score(np.r_[np.ones(len(counts)), np.zeros(len(counts))], np.r_[probability[:, 1], probability[:, 1]], sample_weight=np.r_[counts[:, 1], counts[:, 0]])
    else:
        aucs = [roc_auc_score(np.r_[np.ones(len(counts)), np.zeros(len(counts))], np.r_[probability[:, index], probability[:, index]], sample_weight=np.r_[counts[:, index], exposure - counts[:, index]]) for index in range(counts.shape[1])]
        auc = float(np.mean(aucs))
    return {"brier": float(brier), "log_loss": float(log_loss), "auc": float(auc)}


def _calibration(counts: np.ndarray, probability: np.ndarray) -> dict[str, Any]:
    """Weighted logistic calibration diagnostics only; no transform is applied."""
    exposure = counts.sum(axis=1).astype(float)
    classes = [1] if counts.shape[1] == 2 else list(range(counts.shape[1]))
    result: dict[str, Any] = {}
    for index in classes:
        positive = counts[:, index].astype(float)
        p = np.clip(probability[:, index].astype(float), 1e-9, 1.0 - 1e-9)
        x = np.column_stack([np.ones(len(p)), np.log(p) - np.log1p(-p)])
        beta = np.array([0.0, 1.0])
        converged = False
        for _ in range(50):
            fitted = 1.0 / (1.0 + np.exp(-np.clip(x @ beta, -35.0, 35.0)))
            gradient = x.T @ (exposure * fitted - positive)
            hessian = x.T @ (x * (exposure * fitted * (1.0 - fitted))[:, None])
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


def _component_interval(counts: np.ndarray, candidate: np.ndarray, comparator: np.ndarray, dates: pd.Series, *, draws: int, seed: int) -> dict[str, Any]:
    exposure = counts.sum(axis=1)
    def loss(probability: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        return (
            exposure * (1.0 + np.square(probability).sum(axis=1)) - 2.0 * (counts * probability).sum(axis=1),
            -(counts * np.log(np.clip(probability, 1e-12, 1.0))).sum(axis=1),
        )
    candidate_brier, candidate_log = loss(candidate)
    comparator_brier, comparator_log = loss(comparator)
    block = pd.DataFrame({"date": pd.to_datetime(dates, format="%Y-%m-%d", errors="raise").dt.date, "exposure": exposure, "brier": candidate_brier - comparator_brier, "log_loss": candidate_log - comparator_log}).groupby("date", as_index=False, sort=True).sum()
    _require(len(block) >= 2, "component interval requires at least two dates")
    rng = np.random.default_rng(seed)
    result: dict[str, Any] = {"dates": int(len(block)), "draws": draws}
    for metric in ("brier", "log_loss"):
        delta = block[metric].to_numpy(float)
        block_exposure = block["exposure"].to_numpy(float)
        samples = np.empty(draws, dtype=float)
        for draw in range(draws):
            selected = rng.integers(0, len(block), size=len(block))
            samples[draw] = delta[selected].sum() / block_exposure[selected].sum()
        result[metric] = {"point": float(delta.sum() / block_exposure.sum()), "lower": float(np.quantile(samples, 0.025)), "upper": float(np.quantile(samples, 0.975))}
    return result


def _inner_params(frame: pd.DataFrame, contract: dict[str, Any], *, outer_train_rows: np.ndarray) -> tuple[dict[str, float], list[dict[str, Any]]]:
    candidate = contract["candidate"]
    inner_frame = frame.iloc[outer_train_rows].reset_index(drop=True)
    scores: list[dict[str, Any]] = []
    for prior_strength in candidate["inner_selection"]["grid"]["prior_strength_pa"]:
        for regularization_c in candidate["inner_selection"]["grid"]["regularization_c"]:
            fold_losses: list[float] = []
            for train_rows, validation_rows in _date_splits(inner_frame, candidate["inner_selection"]["n_splits"]):
                model = fit_eb_augmented_pa_model(inner_frame.iloc[train_rows], prior_strength_pa=prior_strength, regularization_c=regularization_c, numeric_features=numeric_feature_names(contract), log1p_features=log1p_feature_names(contract), seed=candidate["outer_evaluation"]["seed"])
                validation = inner_frame.iloc[validation_rows]
                fold_losses.append(proper_scores(outcome_counts(validation), model.predict_proba(validation))["multiclass_log_loss"])
            scores.append({"prior_strength_pa": prior_strength, "regularization_c": regularization_c, "fold_log_loss": fold_losses, "mean_log_loss": float(np.mean(fold_losses))})
    scores.sort(key=lambda value: (value["mean_log_loss"], value["regularization_c"], -value["prior_strength_pa"]))
    best = scores[0]
    return {"prior_strength_pa": float(best["prior_strength_pa"]), "regularization_c": float(best["regularization_c"])}, scores


def run(*, panel: Path, manifest: Path, protocol: Path, report: Path, predictions: Path) -> dict[str, Any]:
    if report.exists() or predictions.exists():
        raise FileExistsError("refusing to overwrite EB-augmented development evidence")
    contract = _load_contract(protocol)
    frame = _validate_panel(panel, manifest, contract)
    candidate = contract["candidate"]
    outer_splits = _date_splits(frame, candidate["outer_evaluation"]["n_splits"])
    oof = np.full((len(frame), len(PA_OUTCOMES)), np.nan, dtype=float)
    league_oof = np.full_like(oof, np.nan)
    eb_oof = np.full_like(oof, np.nan)
    core_oof = np.full_like(oof, np.nan)
    fold_records: list[dict[str, Any]] = []
    history = [column for column in frame.columns if column.startswith("history_")]
    core_features = [column for column in history if "age_days" not in column]
    seed = candidate["outer_evaluation"]["seed"]
    for fold_index, (train_rows, validation_rows) in enumerate(outer_splits, start=1):
        selected, inner_scores = _inner_params(frame, contract, outer_train_rows=train_rows)
        train = frame.iloc[train_rows]
        validation = frame.iloc[validation_rows]
        model = fit_eb_augmented_pa_model(train, prior_strength_pa=selected["prior_strength_pa"], regularization_c=selected["regularization_c"], numeric_features=numeric_feature_names(contract), log1p_features=log1p_feature_names(contract), seed=seed)
        league = fit_rate_baseline(train, kind="league_rate")
        eb = fit_rate_baseline(train, kind="empirical_bayes_player_rate", prior_strength_pa=200.0)
        core = fit_catboost(train, features=core_features, params=CORE_PARAMS, seed=seed)
        candidate_probability = model.predict_proba(validation)
        league_probability, _ = league.predict(validation)
        eb_probability, eb_fallback = eb.predict(validation)
        core_probability = core.predict_proba(validation)
        oof[validation_rows] = candidate_probability
        league_oof[validation_rows] = league_probability
        eb_oof[validation_rows] = eb_probability
        core_oof[validation_rows] = core_probability
        fold_components: dict[str, Any] = {}
        for name in ("hits", "hr_over_0_5", "total_bases"):
            component_counts, candidate_component = _component(validation, candidate_probability, name)
            fold_components[name] = {"candidate": _scores(component_counts, candidate_component), "comparators": {}}
            for label, probability in (("league_rate", league_probability), ("empirical_bayes_player_rate_pa_200", eb_probability), ("all_prior_catboost_core_v3_family", core_probability)):
                _, comparator_component = _component(validation, probability, name)
                fold_components[name]["comparators"][label] = _scores(component_counts, comparator_component)
        fold_records.append({"fold": fold_index, "train_date_min": train["game_date"].min(), "train_date_max": train["game_date"].max(), "validation_date_min": validation["game_date"].min(), "validation_date_max": validation["game_date"].max(), "train_rows": int(len(train)), "validation_rows": int(len(validation)), "inner_grid": inner_scores, "selected": selected, "empirical_bayes_unseen_player_fraction": float(eb_fallback.mean()), "components": fold_components})
    evaluated = np.isfinite(oof).all(axis=1)
    _require(evaluated.any() and not np.isnan(league_oof[evaluated]).any() and not np.isnan(eb_oof[evaluated]).any() and not np.isnan(core_oof[evaluated]).any(), "OOF probability coverage is incomplete")
    selection = frame.loc[evaluated].reset_index(drop=True)
    probability_map = {"candidate": oof[evaluated], "league_rate": league_oof[evaluated], "empirical_bayes_player_rate_pa_200": eb_oof[evaluated], "all_prior_catboost_core_v3_family": core_oof[evaluated]}
    overall = {label: proper_scores(outcome_counts(selection), probability) for label, probability in probability_map.items()}
    components: dict[str, Any] = {}
    all_gates = True
    for name in ("hits", "hr_over_0_5", "total_bases"):
        counts, candidate_probability = _component(selection, probability_map["candidate"], name)
        row: dict[str, Any] = {"candidate": _scores(counts, candidate_probability), "candidate_calibration": _calibration(counts, candidate_probability), "comparators": {}}
        component_passed = True
        for comparator_name in ("league_rate", "empirical_bayes_player_rate_pa_200", "all_prior_catboost_core_v3_family"):
            _, comparator_probability = _component(selection, probability_map[comparator_name], name)
            comparator_scores = _scores(counts, comparator_probability)
            interval = _component_interval(counts, candidate_probability, comparator_probability, selection["game_date"], draws=candidate["outer_evaluation"]["bootstrap_draws"], seed=seed)
            materiality: dict[str, Any] = {}
            for metric in ("brier", "log_loss"):
                required = -0.01 * comparator_scores[metric]
                passed = interval[metric]["point"] < required and interval[metric]["upper"] < required
                materiality[metric] = {"required_below": required, **interval[metric], "passed": passed}
                component_passed = component_passed and passed
            fold_noninferior = all(record["components"][name]["candidate"]["auc"] >= record["components"][name]["comparators"][comparator_name]["auc"] for record in fold_records)
            aggregate_noninferior = row["candidate"]["auc"] >= comparator_scores["auc"]
            component_passed = component_passed and fold_noninferior and aggregate_noninferior
            row["comparators"][comparator_name] = {"scores": comparator_scores, "calibration": _calibration(counts, comparator_probability), "paired_interval": interval, "materiality": materiality, "auc_noninferior_every_outer_fold": fold_noninferior, "auc_noninferior_aggregate": aggregate_noninferior}
        row["development_gate_passed"] = bool(component_passed)
        row["scope"] = "PA_OUTCOME_FOUNDATION_ONLY_NOT_FULL_GAME_MARKET_PROBABILITY"
        components[name] = row
        all_gates = all_gates and component_passed
    prediction_frame = selection[["game_pk", "player_id", "game_date"]].copy()
    for label, probability in probability_map.items():
        for index, outcome in enumerate(PA_OUTCOMES):
            prediction_frame[f"{label}_{outcome}"] = probability[:, index]
    atomic(predictions, prediction_frame.to_csv(index=False, lineterminator="\n").encode("utf-8"))
    result: dict[str, Any] = {
        "schema_version": "shared-pa-eb-augmented-development-report-v2",
        "candidate_id": candidate["id"],
        "status": "DEVELOPMENT_SURVIVOR_REQUIRES_SEPARATE_LOCKED_2024_SELECTION" if all_gates else "DEVELOPMENT_REJECTED_NO_CANDIDATE",
        "inputs": {"panel": {"path": str(panel), "sha256": sha256_file(panel), "rows_consumed": int(len(frame))}, "manifest": {"path": str(manifest), "sha256": sha256_file(manifest)}, "protocol": {"path": str(protocol), "sha256": sha256_file(protocol)}},
        "chronology": {"development_years": [2023], "outer_folds": len(fold_records), "2024_opened": False, "2025_opened": False, "may_2026_opened": False, "all_validation_predictions_out_of_fold": True, "realized_pa_used_as_feature": False},
        "population": {"input_rows": int(len(frame)), "oof_evaluated_rows": int(evaluated.sum()), "oof_coverage": float(evaluated.mean()), "coverage_loss": 0, "identity_key": ["game_pk", "player_id"]},
        "features": {"numeric": numeric_feature_names(contract), "log1p": log1p_feature_names(contract), "core_catboost_comparator": core_features, "forbidden_context_present": False},
        "folds": fold_records,
        "overall_eight_class_pa_scores": overall,
        "components": components,
        "predictions": {"path": str(predictions), "sha256": sha256_file(predictions), "rows": int(len(prediction_frame))},
        "identity_hashes": {"code": {"scripts/develop_shared_pa_eb_residual_2023.py": sha256_file(Path(__file__)), "src/learning/eb_residual_pa_model.py": sha256_file(ROOT / "src/learning/eb_residual_pa_model.py"), "src/learning/shared_pa_model.py": sha256_file(ROOT / "src/learning/shared_pa_model.py"), "src/evaluation/shared_pa_training_data.py": sha256_file(ROOT / "src/evaluation/shared_pa_training_data.py")}, "tests": {"tests/test_eb_residual_pa_model.py": sha256_file(ROOT / "tests/test_eb_residual_pa_model.py"), "tests/test_develop_shared_pa_eb_residual_2023.py": sha256_file(ROOT / "tests/test_develop_shared_pa_eb_residual_2023.py")}},
        "next_action": "Do not open 2024 or 2025 from this runner. If and only if the development gate passes, define and lock a separate 2024 selection protocol before reading its panel.",
        "production_changed": False,
        "betting_authorized": False,
    }
    atomic(report, (json.dumps(result, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8"))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--protocol", type=Path, default=ROOT / "config/shared_pa_eb_augmented_development_2023_v2.json")
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--predictions", required=True, type=Path)
    result = run(**vars(parser.parse_args()))
    print(json.dumps({"status": result["status"], "components": {name: value["development_gate_passed"] for name, value in result["components"].items()}}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
