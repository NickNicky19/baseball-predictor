"""Chronological pre-2026 incremental-signal screen for HR batted-ball inputs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


class HrBattedBallSignalError(ValueError):
    pass


@dataclass(frozen=True)
class ArmFit:
    features: tuple[str, ...]
    selected_c: float
    validation_brier: float
    validation_log_loss: float
    model: Pipeline


def require_exact_seasons(frame: pd.DataFrame) -> None:
    values = pd.to_numeric(frame.get("season"), errors="coerce")
    if values.isna().any() or set(values.astype(int).unique()) != {2023, 2024, 2025}:
        raise HrBattedBallSignalError("signal screen requires exactly seasons 2023, 2024, and 2025")


def validate_numeric_features(frame: pd.DataFrame, features: Sequence[str]) -> pd.DataFrame:
    if not features or len(features) != len(set(features)):
        raise HrBattedBallSignalError("feature list must be non-empty and unique")
    forbidden = sorted(set(features) & {"out_hr", "out_hits", "out_pa"})
    if forbidden:
        raise HrBattedBallSignalError(f"official outcomes cannot enter model features: {forbidden}")
    missing = sorted(set(features) - set(frame.columns))
    if missing:
        raise HrBattedBallSignalError(f"missing model features: {missing}")
    out = frame.loc[:, features].apply(pd.to_numeric, errors="coerce")
    newly_invalid = frame.loc[:, features].notna() & out.isna()
    if newly_invalid.any().any():
        bad = sorted(newly_invalid.columns[newly_invalid.any()].tolist())
        raise HrBattedBallSignalError(f"non-numeric feature values: {bad}")
    return out


def target(frame: pd.DataFrame) -> np.ndarray:
    values = pd.to_numeric(frame["out_hr"], errors="coerce")
    if values.isna().any() or (values < 0).any() or not (values == values.round()).all():
        raise HrBattedBallSignalError("out_hr must be a complete non-negative integer target")
    return values.gt(0).astype(int).to_numpy()


def _pipeline(c: float) -> Pipeline:
    return Pipeline([
        ("imputer", SimpleImputer(strategy="median", add_indicator=True, keep_empty_features=True)),
        ("scaler", StandardScaler()),
        ("logistic", LogisticRegression(C=float(c), l1_ratio=0, solver="lbfgs", max_iter=2000)),
    ])


def brier(y: np.ndarray, p: np.ndarray) -> float:
    return float(np.mean((p - y) ** 2))


def select_regularization(
    fit: pd.DataFrame,
    selection: pd.DataFrame,
    features: Sequence[str],
    c_grid: Sequence[float],
) -> ArmFit:
    x_fit = validate_numeric_features(fit, features)
    x_selection = validate_numeric_features(selection, features)
    y_fit, y_selection = target(fit), target(selection)
    if np.unique(y_fit).size != 2 or np.unique(y_selection).size != 2:
        raise HrBattedBallSignalError("fit and selection periods must each contain both outcomes")
    candidates = []
    for c in c_grid:
        if not np.isfinite(c) or c <= 0:
            raise HrBattedBallSignalError("C_grid must contain finite positive values")
        model = _pipeline(float(c)).fit(x_fit, y_fit)
        probability = model.predict_proba(x_selection)[:, 1]
        candidates.append((brier(y_selection, probability), float(log_loss(y_selection, probability)), float(c), model))
    score_brier, score_log, selected_c, selected_model = min(
        candidates, key=lambda item: (item[0], item[1], item[2])
    )
    return ArmFit(tuple(features), selected_c, score_brier, score_log, selected_model)


def refit_predict(
    train: pd.DataFrame,
    evaluation: pd.DataFrame,
    features: Sequence[str],
    c: float,
) -> tuple[Pipeline, np.ndarray]:
    model = _pipeline(c).fit(validate_numeric_features(train, features), target(train))
    probability = model.predict_proba(validate_numeric_features(evaluation, features))[:, 1]
    return model, probability


def score_arm(y: np.ndarray, probability: np.ndarray) -> dict[str, float]:
    return {
        "brier": brier(y, probability),
        "log_loss": float(log_loss(y, probability)),
        "roc_auc": float(roc_auc_score(y, probability)),
        "mean_probability": float(probability.mean()),
        "observed_rate": float(y.mean()),
        "mean_probability_bias": float(probability.mean() - y.mean()),
    }


def paired_date_interval(
    dates: Sequence[str],
    candidate_loss: np.ndarray,
    baseline_loss: np.ndarray,
    *,
    draws: int,
    seed: int,
) -> dict[str, float]:
    if len(dates) != len(candidate_loss) or len(dates) != len(baseline_loss):
        raise HrBattedBallSignalError("paired loss/date lengths differ")
    rows = pd.DataFrame({
        "date": list(map(str, dates)),
        "delta": np.asarray(candidate_loss) - np.asarray(baseline_loss),
    })
    daily = rows.groupby("date", sort=True).delta.mean().to_numpy(float)
    if len(daily) < 2:
        raise HrBattedBallSignalError("paired interval needs at least two dates")
    rng = np.random.default_rng(seed)
    sampled = rng.choice(daily, size=(draws, len(daily)), replace=True).mean(axis=1)
    return {
        "daily_mean_delta": float(daily.mean()),
        "lower_95": float(np.quantile(sampled, 0.025)),
        "upper_95": float(np.quantile(sampled, 0.975)),
        "dates": int(len(daily)),
    }


def model_parameters(model: Pipeline) -> dict[str, object]:
    logistic = model.named_steps["logistic"]
    imputer = model.named_steps["imputer"]
    scaler = model.named_steps["scaler"]
    return {
        "imputer_statistics": np.asarray(imputer.statistics_).tolist(),
        "scaler_mean": np.asarray(scaler.mean_).tolist(),
        "scaler_scale": np.asarray(scaler.scale_).tolist(),
        "intercept": np.asarray(logistic.intercept_).tolist(),
        "coefficients": np.asarray(logistic.coef_).tolist(),
    }
