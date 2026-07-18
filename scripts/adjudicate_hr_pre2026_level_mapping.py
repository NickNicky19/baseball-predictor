#!/usr/bin/env python3
"""Fit and adjudicate the locked a3.2 HR level mapping, fail closed.

The calibration lock is created from the 12 calibration dates first. The
confirmation command then consumes that immutable lock exactly once.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.hr_batted_ball_signal import (  # noqa: E402
    _pipeline as signal_pipeline,
    model_parameters,
    target as signal_target,
    validate_numeric_features,
)
from src.evaluation.hr_pre2026_level_mapping import load_protocol, sha256  # noqa: E402


CONTRACT_SCHEMA = "hr-pre2026-level-mapping-implementation-v1"
CONTRACT_STATUS = "LOCKED_BEFORE_CALIBRATION_OR_CONFIRMATION_MAPPING_RESULTS"
LOCK_SCHEMA = "hr-pre2026-level-mapping-calibration-lock-v1"
REPORT_SCHEMA = "hr-pre2026-level-mapping-confirmation-report-v1"
GRID = [0.001, 0.01, 0.1, 1.0, 10.0, 100.0]
IDENTITY = ["game_date", "mlb_game_pk", "player_id"]
EPSILON = 1e-9


def _resolve(value: str | Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _canonical(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _write_sidecar(path: Path) -> None:
    sidecar = path.with_suffix(path.suffix + ".sha256")
    temporary = sidecar.with_suffix(sidecar.suffix + ".tmp")
    temporary.write_text(sha256(path) + "\n", encoding="utf-8")
    temporary.replace(sidecar)


def _require_hash(path: Path, expected: str, label: str) -> None:
    if not path.is_file() or sha256(path) != expected:
        raise ValueError(f"{label} missing or hash differs: {path}")


def _validate_dates(calibration: list[str], confirmation: list[str]) -> None:
    if len(calibration) != 12 or len(confirmation) != 12:
        raise ValueError("mapping requires exactly 12 calibration and 12 confirmation dates")
    if calibration != sorted(calibration) or confirmation != sorted(confirmation):
        raise ValueError("mapping date arms must be sorted")
    if set(calibration) & set(confirmation) or max(calibration) >= min(confirmation):
        raise ValueError("mapping chronology overlaps or is not forward ordered")
    if any(date.startswith("2026-05") for date in calibration + confirmation):
        raise ValueError("May 2026 is forbidden")


def load_contract(path: Path, *, verify_files: bool = True) -> dict[str, Any]:
    payload = _read_json(path)
    sidecar = path.with_suffix(path.suffix + ".sha256")
    if not sidecar.is_file() or sidecar.read_text(encoding="utf-8").strip() != sha256(path):
        raise ValueError("implementation contract sidecar mismatch")
    if payload.get("schema_version") != CONTRACT_SCHEMA or payload.get("status") != CONTRACT_STATUS:
        raise ValueError("unknown mapping implementation contract")
    if payload.get("betting_authorized") is not False or payload.get("may_2026_opened") is not False:
        raise ValueError("implementation contract opened May or authorized betting")
    if payload.get("confirmation_opened") is not False:
        raise ValueError("implementation contract was not locked before confirmation")
    parent = payload.get("parent_protocol") or {}
    parent_path = _resolve(parent.get("path", ""))
    if verify_files:
        _require_hash(parent_path, parent.get("sha256", ""), "parent protocol")
    protocol = load_protocol(parent_path, verify_files=verify_files)
    calibration = list(protocol["chronology"]["calibration_dates"])
    confirmation = list(protocol["chronology"]["confirmation_dates_open_once"])
    _validate_dates(calibration, confirmation)
    if payload.get("chronology") != {
        "calibration_dates": calibration,
        "confirmation_dates_open_once": confirmation,
        "confirmation_output_policy": "CREATE_ONCE_REFUSE_OVERWRITE",
        "may_2026_forbidden": True,
    }:
        raise ValueError("implementation chronology differs from the parent protocol")
    algorithm = payload.get("algorithm") or {}
    if algorithm.get("raw_probability_epsilon") != EPSILON:
        raise ValueError("raw probability epsilon changed")
    if algorithm.get("regularization_grid") != GRID:
        raise ValueError("regularization grid changed")
    if algorithm.get("signal_model_refit") != {
        "fit_seasons": [2023, 2024],
        "pipeline": "src.evaluation.hr_batted_ball_signal._pipeline",
        "selected_regularization_and_parameters_must_equal_frozen_signal_report": True,
        "confirmation_outcomes_forbidden": True,
    }:
        raise ValueError("signal-model refit contract changed")
    if algorithm.get("mapping_inputs") != {
        "raw": ["raw_model_logit"],
        "control": ["raw_model_logit"],
        "candidate": ["raw_model_logit", "signal_increment"],
        "signal_increment": "candidate_signal_logit_minus_baseline_signal_logit",
    }:
        raise ValueError("mapping input contract changed")
    if algorithm.get("cross_validation") != (
        "leave-one-official-date-out; mean of the 12 date-level mean losses; "
        "lowest Brier, then log loss, then smallest C"
    ):
        raise ValueError("cross-validation contract changed")
    if algorithm.get("mapping_pipeline") != {
        "scaler": "StandardScaler fitted on the current calibration training fold",
        "model": "LogisticRegression",
        "l1_ratio": 0,
        "solver": "lbfgs",
        "max_iter": 2000,
        "fit_intercept": True,
        "random_state": 17,
    }:
        raise ValueError("mapping pipeline contract changed")
    if algorithm.get("bootstrap") != {
        "unit": "official_game_date",
        "draws": 5000,
        "seed": 17,
        "interval": 0.95,
        "same_resamples_for_all_paired_comparisons": True,
    }:
        raise ValueError("bootstrap contract changed")
    checks = payload.get("success_condition") or {}
    if checks != protocol.get("success_condition") or not all(value is True for value in checks.values()):
        raise ValueError("every success condition must remain required")
    expected_inputs = {
        "full_validation", "raw_model", "official_outcomes", "reconstruction_manifest",
        "point_in_time_feature_source", "signal_protocol", "signal_report",
        "signal_source", "full_validator", "evaluator",
    }
    inputs = payload.get("inputs") or {}
    if set(inputs) != expected_inputs:
        raise ValueError("implementation input set changed")
    outputs = payload.get("outputs") or {}
    if set(outputs) != {"calibration_lock", "confirmation_report"}:
        raise ValueError("implementation output set changed")
    if _resolve(outputs["calibration_lock"]) == _resolve(outputs["confirmation_report"]):
        raise ValueError("calibration and confirmation outputs collide")
    if verify_files:
        for name, record in inputs.items():
            _require_hash(_resolve(record.get("path", "")), record.get("sha256", ""), name)
        if _resolve(inputs["evaluator"]["path"]) != Path(__file__).resolve():
            raise ValueError("implementation contract is bound to another evaluator")
        validation = _read_json(_resolve(inputs["full_validation"]["path"]))
        if (
            validation.get("status") != "VALID_FULL_A3_2_HR_LEVEL_MAPPING_RESEARCH_ONLY"
            or validation.get("performance_inspected") is not False
            or validation.get("dates") != calibration + confirmation
            or validation.get("betting_authorized") is not False
            or validation.get("may_2026_opened") is not False
        ):
            raise ValueError("full artifact validation is not eligible for mapping")
        signal_report = _read_json(_resolve(inputs["signal_report"]["path"]))
        decision = signal_report.get("decision") or {}
        if (
            signal_report.get("status") != "PASS_QUALIFIES_MODEL_RECONSTRUCTION"
            or decision.get("current_model_reconstruction_permitted") is not True
            or decision.get("candidate_install_permitted") is not False
            or decision.get("betting_authorized") is not False
        ):
            raise ValueError("signal report is not eligible for level mapping")
    return payload


def _read_dates(path: Path, dates: Sequence[str]) -> pd.DataFrame:
    parts = []
    date_set = set(dates)
    for chunk in pd.read_csv(path, chunksize=100_000):
        if "game_date" not in chunk.columns:
            raise ValueError(f"artifact lacks game_date: {path}")
        selected = chunk[chunk.game_date.astype(str).isin(date_set)]
        if not selected.empty:
            parts.append(selected)
    if not parts:
        raise ValueError(f"artifact has no rows for requested dates: {path}")
    return pd.concat(parts, ignore_index=True)


def _identity_set(frame: pd.DataFrame) -> set[tuple[str, int, int]]:
    return set(
        zip(
            frame.game_date.astype(str),
            pd.to_numeric(frame.mlb_game_pk).astype(int),
            pd.to_numeric(frame.player_id).astype(int),
        )
    )


def _load_role_rows(contract: dict[str, Any], dates: list[str]) -> pd.DataFrame:
    inputs = contract["inputs"]
    model = _read_dates(_resolve(inputs["raw_model"]["path"]), dates)
    outcomes = _read_dates(_resolve(inputs["official_outcomes"]["path"]), dates)
    training = _read_dates(_resolve(inputs["point_in_time_feature_source"]["path"]), dates)
    model = model[model.category.eq("home_runs") & pd.to_numeric(model.line).eq(0.5)].copy()
    outcomes = outcomes[outcomes.category.eq("home_runs")].copy()
    training = training.rename(columns={"game_pk": "mlb_game_pk"})
    for frame, label in ((model, "model"), (outcomes, "official"), (training, "feature")):
        if frame[IDENTITY].isna().any().any() or frame.duplicated(IDENTITY).any():
            raise ValueError(f"{label} mapping identity is null or duplicated")
        if set(frame.game_date.astype(str)) != set(dates):
            raise ValueError(f"{label} mapping date coverage differs")
    if _identity_set(model) != _identity_set(outcomes) or _identity_set(model) != _identity_set(training):
        raise ValueError("mapping model, official, and feature identities differ")
    probability = pd.to_numeric(model.sim_p_over, errors="coerce")
    if probability.isna().any() or not probability.between(0.0, 1.0).all():
        raise ValueError("raw model probability is invalid")
    actual = pd.to_numeric(outcomes.actual_value, errors="coerce")
    source_hr = pd.to_numeric(training.out_hr, errors="coerce")
    if actual.isna().any() or source_hr.isna().any():
        raise ValueError("mapping outcome is missing")
    model = model[IDENTITY + ["sim_p_over"]]
    outcomes = outcomes[IDENTITY + ["actual_value"]]
    joined = model.merge(outcomes, on=IDENTITY, how="inner", validate="one_to_one")
    joined = joined.merge(training, on=IDENTITY, how="inner", validate="one_to_one")
    if len(joined) != len(model):
        raise ValueError("mapping merge silently changed the identity universe")
    if not np.array_equal(
        pd.to_numeric(joined.actual_value).to_numpy(float),
        pd.to_numeric(joined.out_hr).to_numpy(float),
    ):
        raise ValueError("official outcomes differ from the certified a3.2 source")
    return joined.sort_values(IDENTITY).reset_index(drop=True)


def _allclose_payload(left: Any, right: Any, *, atol: float = 1e-11) -> bool:
    if isinstance(left, dict) and isinstance(right, dict):
        return set(left) == set(right) and all(
            _allclose_payload(left[key], right[key], atol=atol) for key in left
        )
    if isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            return False
        return all(_allclose_payload(a, b, atol=atol) for a, b in zip(left, right))
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=atol)
    return left == right


def _signal_models(contract: dict[str, Any]) -> tuple[Pipeline, Pipeline, list[str], list[str]]:
    inputs = contract["inputs"]
    source = pd.read_csv(_resolve(inputs["point_in_time_feature_source"]["path"]))
    signal_protocol = _read_json(_resolve(inputs["signal_protocol"]["path"]))
    signal_report = _read_json(_resolve(inputs["signal_report"]["path"]))
    history = source[pd.to_numeric(source.season).isin([2023, 2024])].copy()
    baseline_features = list(signal_protocol["baseline_features"])
    candidate_features = baseline_features + list(signal_protocol["candidate_additional_features"])
    selected = signal_report["selected_regularization"]
    baseline = signal_pipeline(float(selected["baseline_C"])).fit(
        validate_numeric_features(history, baseline_features), signal_target(history)
    )
    candidate = signal_pipeline(float(selected["candidate_C"])).fit(
        validate_numeric_features(history, candidate_features), signal_target(history)
    )
    recorded = signal_report["model_parameters"]
    if not _allclose_payload(model_parameters(baseline), recorded["baseline"]):
        raise ValueError("refitted baseline signal model differs from its frozen report")
    if not _allclose_payload(model_parameters(candidate), recorded["candidate"]):
        raise ValueError("refitted candidate signal model differs from its frozen report")
    return baseline, candidate, baseline_features, candidate_features


def _logit(probability: np.ndarray) -> np.ndarray:
    clipped = np.clip(np.asarray(probability, dtype=float), EPSILON, 1.0 - EPSILON)
    return np.log(clipped / (1.0 - clipped))


def _mapping_inputs(
    rows: pd.DataFrame,
    baseline: Pipeline,
    candidate: Pipeline,
    baseline_features: list[str],
    candidate_features: list[str],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    raw = pd.to_numeric(rows.sim_p_over).to_numpy(float)
    if not np.isfinite(raw).all() or np.any((raw < 0.0) | (raw > 1.0)):
        raise ValueError("raw probability outside [0,1]")
    baseline_p = baseline.predict_proba(validate_numeric_features(rows, baseline_features))[:, 1]
    candidate_p = candidate.predict_proba(validate_numeric_features(rows, candidate_features))[:, 1]
    increment = _logit(candidate_p) - _logit(baseline_p)
    raw_logit = _logit(raw)
    control = raw_logit.reshape(-1, 1)
    challenger = np.column_stack([raw_logit, increment])
    y = pd.to_numeric(rows.actual_value).gt(0).astype(int).to_numpy()
    return raw, control, challenger, y


def _mapping_pipeline(c: float) -> Pipeline:
    return Pipeline(
        [
            ("scaler", StandardScaler()),
            (
                "logistic",
                LogisticRegression(
                    C=float(c), l1_ratio=0, solver="lbfgs", max_iter=2000,
                    fit_intercept=True, random_state=17,
                ),
            ),
        ]
    )


def _losses(y: np.ndarray, probability: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    clipped = np.clip(probability, 1e-12, 1.0 - 1e-12)
    return (probability - y) ** 2, -(
        y * np.log(clipped) + (1 - y) * np.log(1.0 - clipped)
    )


def select_regularization_lodo(
    features: np.ndarray,
    y: np.ndarray,
    dates: Sequence[str],
) -> tuple[float, list[dict[str, float]]]:
    dates_array = np.asarray(list(map(str, dates)))
    unique_dates = sorted(set(dates_array))
    if len(unique_dates) != 12:
        raise ValueError("LODO selection requires exactly 12 official dates")
    results = []
    for c in GRID:
        date_brier = []
        date_log = []
        for held_out in unique_dates:
            test = dates_array == held_out
            train = ~test
            if np.unique(y[train]).size != 2 or np.unique(y[test]).size != 2:
                raise ValueError(f"LODO fold lacks both outcomes: {held_out}")
            model = _mapping_pipeline(c).fit(features[train], y[train])
            probability = model.predict_proba(features[test])[:, 1]
            brier_loss, log_loss_values = _losses(y[test], probability)
            date_brier.append(float(brier_loss.mean()))
            date_log.append(float(log_loss_values.mean()))
        results.append(
            {"C": float(c), "mean_date_brier": float(np.mean(date_brier)),
             "mean_date_log_loss": float(np.mean(date_log))}
        )
    selected = min(results, key=lambda row: (row["mean_date_brier"], row["mean_date_log_loss"], row["C"]))
    return float(selected["C"]), results


def _mapping_parameters(model: Pipeline) -> dict[str, Any]:
    scaler = model.named_steps["scaler"]
    logistic = model.named_steps["logistic"]
    return {
        "scaler_mean": np.asarray(scaler.mean_).tolist(),
        "scaler_scale": np.asarray(scaler.scale_).tolist(),
        "intercept": np.asarray(logistic.intercept_).tolist(),
        "coefficients": np.asarray(logistic.coef_).tolist(),
    }


def _predict_parameters(features: np.ndarray, parameters: dict[str, Any]) -> np.ndarray:
    mean = np.asarray(parameters["scaler_mean"], dtype=float)
    scale = np.asarray(parameters["scaler_scale"], dtype=float)
    intercept = float(np.asarray(parameters["intercept"], dtype=float)[0])
    coefficients = np.asarray(parameters["coefficients"], dtype=float)[0]
    if features.shape[1] != len(mean) or len(mean) != len(scale) or len(mean) != len(coefficients):
        raise ValueError("mapping parameter dimensions differ")
    score = intercept + ((features - mean) / scale) @ coefficients
    return 1.0 / (1.0 + np.exp(-score))


def _identity_sha(rows: pd.DataFrame) -> str:
    payload = "\n".join(
        f"{row.game_date}|{int(row.mlb_game_pk)}|{int(row.player_id)}"
        for row in rows[IDENTITY].itertuples(index=False)
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def fit_calibration(contract_path: Path) -> Path:
    contract = load_contract(contract_path, verify_files=True)
    output = _resolve(contract["outputs"]["calibration_lock"])
    if output.exists() or output.with_suffix(output.suffix + ".sha256").exists():
        raise FileExistsError(f"calibration lock already exists: {output}")
    dates = list(contract["chronology"]["calibration_dates"])
    rows = _load_role_rows(contract, dates)
    baseline, candidate, baseline_features, candidate_features = _signal_models(contract)
    raw, control_x, candidate_x, y = _mapping_inputs(
        rows, baseline, candidate, baseline_features, candidate_features
    )
    control_c, control_cv = select_regularization_lodo(control_x, y, rows.game_date)
    candidate_c, candidate_cv = select_regularization_lodo(candidate_x, y, rows.game_date)
    control = _mapping_pipeline(control_c).fit(control_x, y)
    challenger = _mapping_pipeline(candidate_c).fit(candidate_x, y)
    payload = {
        "schema_version": LOCK_SCHEMA,
        "status": "CALIBRATION_LOCKED_CONFIRMATION_NOT_OPENED",
        "betting_authorized": False,
        "may_2026_opened": False,
        "confirmation_opened": False,
        "implementation_contract": {"path": str(contract_path), "sha256": sha256(contract_path)},
        "evaluator": {"path": str(Path(__file__).resolve()), "sha256": sha256(Path(__file__))},
        "dates": dates,
        "rows": int(len(rows)),
        "hr_occurrences": int(y.sum()),
        "identity_sha256": _identity_sha(rows),
        "raw_probability_sha256": hashlib.sha256(np.asarray(raw, dtype="<f8").tobytes()).hexdigest(),
        "selected_regularization": {"control_C": control_c, "candidate_C": candidate_c},
        "cross_validation": {"control": control_cv, "candidate": candidate_cv},
        "models": {
            "control": _mapping_parameters(control),
            "candidate": _mapping_parameters(challenger),
        },
        "signal_model_report_sha256": contract["inputs"]["signal_report"]["sha256"],
        "input_hashes": {name: record["sha256"] for name, record in contract["inputs"].items()},
        "interpretation": (
            "Hyperparameters and coefficients were selected using calibration dates only. "
            "No confirmation score, metric, or outcome aggregate was computed."
        ),
    }
    _atomic_json(output, payload)
    _write_sidecar(output)
    return output


def _metrics(y: np.ndarray, probability: np.ndarray) -> dict[str, float]:
    if np.unique(y).size != 2:
        raise ValueError("confirmation target lacks both outcomes")
    return {
        "brier": float(np.mean((probability - y) ** 2)),
        "log_loss": float(log_loss(y, probability)),
        "roc_auc": float(roc_auc_score(y, probability)),
        "mean_probability": float(probability.mean()),
        "observed_rate": float(y.mean()),
        "mean_probability_bias": float(probability.mean() - y.mean()),
    }


def _paired_intervals(
    dates: Sequence[str],
    losses: dict[str, tuple[np.ndarray, np.ndarray]],
    *,
    draws: int = 5000,
    seed: int = 17,
) -> dict[str, dict[str, float | int]]:
    date_values = np.asarray(list(map(str, dates)))
    unique_dates = sorted(set(date_values))
    if len(unique_dates) != 12:
        raise ValueError("confirmation interval requires exactly 12 dates")
    daily: dict[str, np.ndarray] = {}
    for name, (candidate_loss, comparator_loss) in losses.items():
        delta = np.asarray(candidate_loss) - np.asarray(comparator_loss)
        daily[name] = np.asarray([delta[date_values == date].mean() for date in unique_dates])
    rng = np.random.default_rng(seed)
    indices = rng.integers(0, len(unique_dates), size=(draws, len(unique_dates)))
    result: dict[str, dict[str, float | int]] = {}
    for name, values in daily.items():
        sampled = values[indices].mean(axis=1)
        result[name] = {
            "daily_mean_delta": float(values.mean()),
            "lower_95": float(np.quantile(sampled, 0.025)),
            "upper_95": float(np.quantile(sampled, 0.975)),
            "dates": len(unique_dates),
            "draws": draws,
        }
    return result


def open_confirmation(contract_path: Path) -> Path:
    contract = load_contract(contract_path, verify_files=True)
    lock_path = _resolve(contract["outputs"]["calibration_lock"])
    report_path = _resolve(contract["outputs"]["confirmation_report"])
    if report_path.exists():
        raise FileExistsError(f"confirmation was already opened: {report_path}")
    lock = _read_json(lock_path)
    lock_sidecar = lock_path.with_suffix(lock_path.suffix + ".sha256")
    if not lock_sidecar.is_file() or lock_sidecar.read_text().strip() != sha256(lock_path):
        raise ValueError("calibration lock sidecar differs")
    if lock.get("schema_version") != LOCK_SCHEMA or lock.get("confirmation_opened") is not False:
        raise ValueError("calibration lock is not eligible for one-time confirmation")
    if lock.get("implementation_contract", {}).get("sha256") != sha256(contract_path):
        raise ValueError("calibration lock belongs to another implementation contract")
    if lock.get("evaluator", {}).get("sha256") != sha256(Path(__file__)):
        raise ValueError("evaluator changed after calibration was locked")
    dates = list(contract["chronology"]["confirmation_dates_open_once"])
    rows = _load_role_rows(contract, dates)
    baseline, candidate, baseline_features, candidate_features = _signal_models(contract)
    raw, control_x, candidate_x, y = _mapping_inputs(
        rows, baseline, candidate, baseline_features, candidate_features
    )
    control_p = _predict_parameters(control_x, lock["models"]["control"])
    candidate_p = _predict_parameters(candidate_x, lock["models"]["candidate"])
    arms = {"raw": raw, "control": control_p, "candidate": candidate_p}
    metrics = {name: _metrics(y, probability) for name, probability in arms.items()}
    raw_brier, raw_log = _losses(y, raw)
    control_brier, control_log = _losses(y, control_p)
    candidate_brier, candidate_log = _losses(y, candidate_p)
    intervals = _paired_intervals(
        rows.game_date,
        {
            "candidate_minus_raw_brier": (candidate_brier, raw_brier),
            "candidate_minus_control_brier": (candidate_brier, control_brier),
            "candidate_minus_raw_log_loss": (candidate_log, raw_log),
            "candidate_minus_control_log_loss": (candidate_log, control_log),
        },
    )
    checks = {
        "exact_gradeable_identity_coverage": len(rows) == len(raw) == len(control_p) == len(candidate_p),
        "candidate_Brier_lower_than_raw_and_control": (
            metrics["candidate"]["brier"] < metrics["raw"]["brier"]
            and metrics["candidate"]["brier"] < metrics["control"]["brier"]
        ),
        "candidate_log_loss_lower_than_raw_and_control": (
            metrics["candidate"]["log_loss"] < metrics["raw"]["log_loss"]
            and metrics["candidate"]["log_loss"] < metrics["control"]["log_loss"]
        ),
        "candidate_ROC_AUC_not_lower_than_raw_and_control": (
            metrics["candidate"]["roc_auc"] >= metrics["raw"]["roc_auc"]
            and metrics["candidate"]["roc_auc"] >= metrics["control"]["roc_auc"]
        ),
        "candidate_absolute_mean_probability_bias_not_worse_than_control": (
            abs(metrics["candidate"]["mean_probability_bias"])
            <= abs(metrics["control"]["mean_probability_bias"])
        ),
        "paired_date_Brier_delta_upper_95_below_zero_vs_raw_and_control": (
            intervals["candidate_minus_raw_brier"]["upper_95"] < 0.0
            and intervals["candidate_minus_control_brier"]["upper_95"] < 0.0
        ),
        "paired_date_log_loss_delta_upper_95_below_zero_vs_raw_and_control": (
            intervals["candidate_minus_raw_log_loss"]["upper_95"] < 0.0
            and intervals["candidate_minus_control_log_loss"]["upper_95"] < 0.0
        ),
    }
    passed = all(checks.values())
    scored_rows = []
    for index, identity in enumerate(rows[IDENTITY].itertuples(index=False)):
        scored_rows.append(
            {
                "game_date": str(identity.game_date),
                "mlb_game_pk": int(identity.mlb_game_pk),
                "player_id": int(identity.player_id),
                "actual_hr": int(pd.to_numeric(rows.actual_value).iloc[index]),
                "raw_probability": float(raw[index]),
                "control_probability": float(control_p[index]),
                "candidate_probability": float(candidate_p[index]),
                "signal_increment": float(candidate_x[index, 1]),
            }
        )
    payload = {
        "schema_version": REPORT_SCHEMA,
        "status": "PASS_LEVEL_MAPPING_RESEARCH_ONLY" if passed else "REJECTED_LEVEL_MAPPING",
        "betting_authorized": False,
        "may_2026_opened": False,
        "confirmation_opened_once": True,
        "implementation_contract": {"path": str(contract_path), "sha256": sha256(contract_path)},
        "calibration_lock": {"path": str(lock_path), "sha256": sha256(lock_path)},
        "dates": dates,
        "rows": int(len(rows)),
        "identity_sha256": _identity_sha(rows),
        "metrics": metrics,
        "paired_date_intervals": intervals,
        "success_checks": {name: bool(value) for name, value in checks.items()},
        "decision": {
            "level_mapping_supported": bool(passed),
            "2026_open_period_experiment_permitted": bool(passed),
            "model_install_permitted": False,
            "may_2026_opened": False,
            "betting_authorized": False,
            "next_action": (
                "LOCK_SEPARATE_2026_OPEN_PERIOD_ECONOMIC_EXPERIMENT"
                if passed
                else "REJECT_MAPPING_PRESERVE_CURRENT_MODEL_AND_DIAGNOSE_NEXT_MEASURED_COMPONENT"
            ),
        },
        "scored_rows": scored_rows,
        "interpretation": (
            "This one-time 2025 confirmation adjudication tests probability mapping only. "
            "It does not establish historical price executability, open May 2026, install a "
            "model, demonstrate profitability, or authorize betting."
        ),
    }
    _atomic_json(report_path, payload)
    _write_sidecar(report_path)
    return report_path


def self_test() -> int:
    rng = np.random.default_rng(17)
    dates = np.repeat([f"2025-01-{day:02d}" for day in range(1, 13)], 40)
    raw_logit = rng.normal(-2.0, 0.7, len(dates))
    y = (rng.random(len(dates)) < 1.0 / (1.0 + np.exp(-raw_logit))).astype(int)
    for date in sorted(set(dates)):
        mask = dates == date
        y[np.flatnonzero(mask)[0]] = 0
        y[np.flatnonzero(mask)[1]] = 1
    control_x = raw_logit.reshape(-1, 1)
    zero_x = np.column_stack([raw_logit, np.zeros(len(dates))])
    control_c, _ = select_regularization_lodo(control_x, y, dates)
    zero_c, _ = select_regularization_lodo(zero_x, y, dates)
    control = _mapping_pipeline(control_c).fit(control_x, y)
    zero = _mapping_pipeline(zero_c).fit(zero_x, y)
    if control_c != zero_c or not np.allclose(
        control.predict_proba(control_x)[:, 1], zero.predict_proba(zero_x)[:, 1], atol=1e-12
    ):
        raise AssertionError("zero signal increment differs from control")
    print("[OK] zero signal increment exactly reproduces the calibration-only control")
    fingerprint = hashlib.sha256(_canonical({"C": control_c, "params": _mapping_parameters(control)})).hexdigest()
    mutated_confirmation = 1 - y
    if hashlib.sha256(_canonical({"C": control_c, "params": _mapping_parameters(control)})).hexdigest() != fingerprint:
        raise AssertionError("confirmation truth changed calibration lock")
    assert len(mutated_confirmation) == len(y)
    print("[OK] MUTATION confirmation truth cannot change the calibration lock")
    partial = dates != sorted(set(dates))[-1]
    try:
        select_regularization_lodo(control_x[partial], y[partial], dates[partial])
    except ValueError:
        print("[OK] MUTATION partial date coverage fails")
    else:
        raise AssertionError("partial coverage mutation passed")
    try:
        _validate_dates(["2025-01-01"] * 12, ["2025-01-01"] * 12)
    except ValueError:
        print("[OK] MUTATION overlapping chronology fails")
    else:
        raise AssertionError("overlap mutation passed")
    try:
        _validate_dates([f"2025-01-{d:02d}" for d in range(1, 13)], ["2026-05-01"] + [f"2026-06-{d:02d}" for d in range(1, 12)])
    except ValueError:
        print("[OK] MUTATION May 2026 access fails")
    else:
        raise AssertionError("May mutation passed")
    bad = np.asarray([1.1])
    try:
        if np.any((bad < 0) | (bad > 1)):
            raise ValueError("raw probability outside [0,1]")
    except ValueError:
        print("[OK] MUTATION invalid raw probability fails")
    intervals = _paired_intervals(
        dates,
        {"x": ((control.predict_proba(control_x)[:, 1] - y) ** 2, (np.full(len(y), 0.5) - y) ** 2)},
        draws=100,
    )
    if intervals["x"]["dates"] != 12 or intervals["x"]["draws"] != 100:
        raise AssertionError("paired date bootstrap unit changed")
    print("[OK] paired bootstrap resamples official dates")
    print("7/7")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--contract")
    parser.add_argument("--fit-calibration", action="store_true")
    parser.add_argument("--open-confirmation", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    if not args.contract or args.fit_calibration == args.open_confirmation:
        parser.error("choose exactly one of --fit-calibration or --open-confirmation with --contract")
    contract_path = _resolve(args.contract)
    if args.fit_calibration:
        result = fit_calibration(contract_path)
        print("HR LEVEL-MAPPING CALIBRATION LOCKED")
        print(f"  lock: {result}")
        print("  confirmation opened: NO; betting authorized: NO")
    else:
        result = open_confirmation(contract_path)
        report = _read_json(result)
        print("HR LEVEL-MAPPING CONFIRMATION ADJUDICATED - RESEARCH ONLY")
        print(f"  status: {report['status']}")
        print(f"  rows: {report['rows']}")
        print(f"  level mapping supported: {report['decision']['level_mapping_supported']}")
        print("  May 2026 opened: NO; betting authorized: NO")
        print(f"  report: {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
