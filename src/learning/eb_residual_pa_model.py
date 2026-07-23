"""Empirical-Bayes-feature-augmented regularized shared PA probabilities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from src.evaluation.multi_market_foundation import PA_OUTCOMES
from src.learning.shared_pa_model import (
    _validated_probability_matrix,
    normalized_counts,
    weighted_event_rows,
)


HISTORY_COUNT_COLUMNS = [f"history_{outcome}_count" for outcome in PA_OUTCOMES]


def _strict_numeric(frame: pd.DataFrame, columns: list[str], *, label: str) -> pd.DataFrame:
    if len(columns) != len(set(columns)):
        raise ValueError(f"{label} columns must be unique")
    missing = sorted(set(columns).difference(frame.columns))
    if missing:
        raise ValueError(f"{label} columns are missing: {missing}")
    result = pd.DataFrame(index=frame.index)
    for column in columns:
        source = frame[column]
        numeric = pd.to_numeric(source, errors="coerce")
        if (source.notna() & numeric.isna()).any():
            raise ValueError(f"{label} column is nonnumeric: {column}")
        values = numeric.to_numpy(float)
        if np.isinf(values).any():
            raise ValueError(f"{label} column is infinite: {column}")
        result[column] = numeric.astype(float)
    return result


def _history_counts(frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    numeric = _strict_numeric(
        frame,
        ["history_pa", *HISTORY_COUNT_COLUMNS],
        label="history count",
    )
    if numeric.isna().any().any():
        raise ValueError("history counts and exposure cannot be missing")
    values = numeric[HISTORY_COUNT_COLUMNS].to_numpy(float)
    exposure = numeric["history_pa"].to_numpy(float)
    if (values < 0.0).any() or (exposure < 0.0).any():
        raise ValueError("history counts and exposure must be nonnegative")
    if not np.equal(values, np.floor(values)).all() or not np.equal(exposure, np.floor(exposure)).all():
        raise ValueError("history counts and exposure must be integral")
    if not np.array_equal(values.sum(axis=1), exposure):
        raise ValueError("history outcome counts do not equal history PA exposure")
    return values, exposure


def _model_features(
    frame: pd.DataFrame,
    *,
    league_probability: np.ndarray,
    prior_strength_pa: float,
    numeric_features: list[str],
    log1p_features: list[str],
) -> pd.DataFrame:
    history, exposure = _history_counts(frame)
    league = np.asarray(league_probability, dtype=float)
    if league.shape != (len(PA_OUTCOMES),) or not np.isfinite(league).all():
        raise ValueError("league PA probability has the wrong shape or support")
    if (league <= 0.0).any() or not np.isclose(league.sum(), 1.0, rtol=0.0, atol=1e-12):
        raise ValueError("league PA probability must be positive unit mass")
    if not np.isfinite(prior_strength_pa) or prior_strength_pa <= 0.0:
        raise ValueError("prior strength must be positive and finite")
    posterior = (
        history + prior_strength_pa * league.reshape(1, -1)
    ) / (exposure.reshape(-1, 1) + prior_strength_pa)
    posterior = _validated_probability_matrix(
        posterior,
        expected_shape=(len(frame), len(PA_OUTCOMES)),
    )
    reference = posterior[:, -1]
    prepared = pd.DataFrame(index=frame.index)
    for index, outcome in enumerate(PA_OUTCOMES[:-1]):
        prepared[f"eb_log_ratio_{outcome}_vs_{PA_OUTCOMES[-1]}"] = np.log(
            posterior[:, index] / reference
        )
    prepared["log1p_history_pa"] = np.log1p(exposure)

    overlap = sorted(set(numeric_features).intersection(log1p_features))
    if overlap:
        raise ValueError(f"numeric and log1p feature contracts overlap: {overlap}")
    numeric = _strict_numeric(frame, numeric_features, label="numeric process feature")
    logged = _strict_numeric(frame, log1p_features, label="log1p process feature")
    for column in log1p_features:
        values = logged[column].to_numpy(float)
        if (values[~np.isnan(values)] < 0.0).any():
            raise ValueError(f"log1p process feature must be nonnegative: {column}")
        logged[column] = np.log1p(logged[column])
    # Process columns must not retain source names.  The training frame also
    # carries the raw columns, and duplicate DataFrame labels would let pandas
    # select two different feature matrices under one apparent contract.
    # Prefixing is therefore an identity boundary, not cosmetic naming.
    for column in numeric_features:
        prepared[f"numeric__{column}"] = numeric[column]
    for column in log1p_features:
        prepared[f"log1p__{column}"] = logged[column]
    return prepared


@dataclass
class EBAugmentedPAModel:
    pipeline: Any
    league_probability: np.ndarray
    prior_strength_pa: float
    numeric_features: list[str]
    log1p_features: list[str]

    def predict_proba(self, frame: pd.DataFrame) -> np.ndarray:
        prepared = _model_features(
            frame,
            league_probability=self.league_probability,
            prior_strength_pa=self.prior_strength_pa,
            numeric_features=self.numeric_features,
            log1p_features=self.log1p_features,
        )
        probabilities = np.asarray(self.pipeline.predict_proba(prepared), dtype=float)
        classes = np.asarray(self.pipeline.named_steps["model"].classes_)
        if not np.array_equal(classes, np.arange(len(PA_OUTCOMES))):
            raise ValueError("EB-augmented PA model class identity changed")
        return _validated_probability_matrix(
            probabilities,
            expected_shape=(len(frame), len(PA_OUTCOMES)),
        )


def fit_eb_augmented_pa_model(
    frame: pd.DataFrame,
    *,
    prior_strength_pa: float,
    regularization_c: float,
    numeric_features: list[str],
    log1p_features: list[str],
    seed: int,
    max_iter: int = 2000,
) -> EBAugmentedPAModel:
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    if isinstance(prior_strength_pa, bool) or not np.isfinite(float(prior_strength_pa)) or float(prior_strength_pa) <= 0.0:
        raise ValueError("prior_strength_pa must be positive and finite")
    if isinstance(regularization_c, bool) or not np.isfinite(float(regularization_c)) or float(regularization_c) <= 0.0:
        raise ValueError("regularization_c must be positive and finite")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")
    if isinstance(max_iter, bool) or not isinstance(max_iter, int) or max_iter <= 0:
        raise ValueError("max_iter must be a positive integer")
    counts, _ = normalized_counts(frame)
    league_counts = counts.sum(axis=0).to_numpy(float)
    league = league_counts / league_counts.sum()
    if (league <= 0.0).any():
        raise ValueError("training data must expose every PA outcome class")
    prepared = _model_features(
        frame,
        league_probability=league,
        prior_strength_pa=float(prior_strength_pa),
        numeric_features=list(numeric_features),
        log1p_features=list(log1p_features),
    )
    event_frame = pd.concat([
        frame.loc[:, ["out_pa", "out_ab", "out_hits", "out_doubles", "out_triples", "out_hr", "out_bb", "out_k"]].reset_index(drop=True),
        prepared.reset_index(drop=True),
    ], axis=1)
    if event_frame.columns.duplicated().any():
        raise ValueError("EB-augmented PA event frame has duplicate column names")
    events, labels, weights = weighted_event_rows(event_frame, prepared.columns.tolist())
    pipeline = Pipeline([
        ("imputer", SimpleImputer(strategy="median", add_indicator=True, keep_empty_features=True)),
        ("scale", StandardScaler()),
        ("model", LogisticRegression(
            C=float(regularization_c),
            # The full multiclass Hessian is small for this locked feature
            # contract.  Unlike the rejected v1 L-BFGS path, this solver has a
            # deterministic convergence criterion for the exact objective.
            solver="newton-cholesky",
            max_iter=max_iter,
            random_state=seed,
            tol=1e-8,
        )),
    ])
    pipeline.fit(events, labels, model__sample_weight=weights)
    model = pipeline.named_steps["model"]
    if not np.array_equal(model.classes_, np.arange(len(PA_OUTCOMES))):
        raise ValueError("training did not retain every PA outcome class")
    if np.asarray(model.n_iter_).max() >= max_iter:
        raise ValueError("EB-augmented PA model did not converge")
    fitted = EBAugmentedPAModel(
        pipeline=pipeline,
        league_probability=league,
        prior_strength_pa=float(prior_strength_pa),
        numeric_features=list(numeric_features),
        log1p_features=list(log1p_features),
    )
    fitted.predict_proba(frame.iloc[: min(len(frame), 32)])
    return fitted
