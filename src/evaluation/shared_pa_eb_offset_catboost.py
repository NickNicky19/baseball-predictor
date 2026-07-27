"""True empirical-Bayes-offset CatBoost model for shared PA outcomes.

This module is research-only.  The CatBoost model learns residual raw formula
values on top of a time-safe eight-class empirical-Bayes posterior.  It does
not treat EB probabilities as ordinary features and it never consumes a
target-game outcome or realized PA as a predictor.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import log
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from src.evaluation.shared_pa_training_data import outcome_counts


PA_OUTCOMES = (
    "strikeout",
    "walk",
    "single",
    "double",
    "triple",
    "home_run",
    "bip_out",
    "other_non_ab",
)
HISTORY_COUNT_COLUMNS = tuple(f"history_{name}_count" for name in PA_OUTCOMES)
EPSILON = 1e-12


def _probabilities(values: np.ndarray, *, rows: int | None = None) -> np.ndarray:
    result = np.asarray(values, dtype=float)
    expected = (rows, len(PA_OUTCOMES)) if rows is not None else None
    if result.ndim != 2 or result.shape[1] != len(PA_OUTCOMES):
        raise ValueError("PA probabilities must have eight columns")
    if expected is not None and result.shape != expected:
        raise ValueError(f"PA probability shape changed: {result.shape} != {expected}")
    if not np.isfinite(result).all() or (result <= 0.0).any() or (result >= 1.0).any():
        raise ValueError("PA probabilities must be finite and strictly inside (0,1)")
    if not np.allclose(result.sum(axis=1), 1.0, rtol=0.0, atol=1e-9):
        raise ValueError("PA probabilities do not sum to one")
    return result


def target_counts(frame: pd.DataFrame) -> np.ndarray:
    counts = outcome_counts(frame).loc[:, PA_OUTCOMES]
    numeric = counts.apply(pd.to_numeric, errors="coerce").to_numpy(float)
    if not np.isfinite(numeric).all() or (numeric < 0.0).any():
        raise ValueError("target PA outcome counts are missing, nonfinite, or negative")
    if not np.equal(numeric, np.floor(numeric)).all():
        raise ValueError("target PA outcome counts are not integral")
    exposure = numeric.sum(axis=1)
    if (exposure <= 0.0).any():
        raise ValueError("model fitting and scoring require positive PA exposure")
    return numeric


def history_counts(frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    required = ["history_pa", *HISTORY_COUNT_COLUMNS]
    missing = sorted(set(required).difference(frame.columns))
    if missing:
        raise ValueError(f"history count lineage is missing: {missing}")
    source = frame.loc[:, required]
    numeric = source.apply(pd.to_numeric, errors="coerce")
    if (source.notna() & numeric.isna()).any().any():
        raise ValueError("history count lineage contains nonnumeric values")
    values = numeric.to_numpy(float)
    if not np.isfinite(values).all() or (values < 0.0).any():
        raise ValueError("history count lineage is missing, nonfinite, or negative")
    if not np.equal(values, np.floor(values)).all():
        raise ValueError("history count lineage is not integral")
    exposure = values[:, 0]
    counts = values[:, 1:]
    if not np.array_equal(counts.sum(axis=1), exposure):
        raise ValueError("history outcome counts do not equal history PA")
    return counts, exposure


def league_probability(frame: pd.DataFrame) -> np.ndarray:
    counts = target_counts(frame).sum(axis=0)
    if (counts <= 0.0).any():
        raise ValueError("outer training data must contain every PA outcome")
    return counts / counts.sum()


def fit_dirichlet_concentration(
    frame: pd.DataFrame,
    league: np.ndarray,
    *,
    lower: float = 0.001,
    upper: float = 1_000_000.0,
) -> float:
    """Fit one player-heterogeneity concentration by marginal likelihood."""

    from scipy.optimize import minimize_scalar
    from scipy.special import gammaln

    league = np.asarray(league, dtype=float)
    if league.shape != (len(PA_OUTCOMES),) or not np.isfinite(league).all():
        raise ValueError("league probability has the wrong shape or support")
    if (league <= 0.0).any() or not np.isclose(league.sum(), 1.0, rtol=0.0, atol=1e-12):
        raise ValueError("league probability must be positive unit mass")
    if not 0.0 < lower < upper or not np.isfinite([lower, upper]).all():
        raise ValueError("Dirichlet concentration bounds are invalid")
    if "player_id" not in frame or frame["player_id"].isna().any():
        raise ValueError("player identity is required for empirical Bayes fitting")
    counts = pd.DataFrame(target_counts(frame), columns=PA_OUTCOMES)
    counts["player_id"] = frame["player_id"].to_numpy()
    grouped = counts.groupby("player_id", sort=True).sum().loc[:, PA_OUTCOMES].to_numpy(float)
    if len(grouped) < 2:
        raise ValueError("empirical Bayes concentration needs at least two players")
    exposure = grouped.sum(axis=1)

    def negative_log_likelihood(log_concentration: float) -> float:
        concentration = float(np.exp(log_concentration))
        alpha = concentration * league
        value = (
            gammaln(concentration)
            - gammaln(concentration + exposure)
            + (gammaln(grouped + alpha) - gammaln(alpha)).sum(axis=1)
        ).sum()
        return -float(value)

    log_lower, log_upper = log(lower), log(upper)
    result = minimize_scalar(
        negative_log_likelihood,
        method="bounded",
        bounds=(log_lower, log_upper),
        options={"xatol": 1e-8, "maxiter": 500},
    )
    if not result.success or not np.isfinite(result.x) or not np.isfinite(result.fun):
        raise ValueError("Dirichlet concentration optimization failed")
    if result.x <= log_lower + 1e-5 or result.x >= log_upper - 1e-5:
        raise ValueError("Dirichlet concentration optimization reached a safety bound")
    concentration = float(np.exp(result.x))
    if not lower < concentration < upper:
        raise ValueError("Dirichlet concentration lies outside its open safety interval")
    return concentration


def empirical_bayes_probability(
    frame: pd.DataFrame,
    league: np.ndarray,
    concentration: float,
) -> np.ndarray:
    league = np.asarray(league, dtype=float)
    if league.shape != (len(PA_OUTCOMES),) or (league <= 0.0).any():
        raise ValueError("league probability has the wrong shape or support")
    if not np.isclose(league.sum(), 1.0, rtol=0.0, atol=1e-12):
        raise ValueError("league probability does not sum to one")
    if isinstance(concentration, bool) or not np.isfinite(float(concentration)) or float(concentration) <= 0.0:
        raise ValueError("empirical-Bayes concentration must be positive and finite")
    counts, exposure = history_counts(frame)
    posterior = (
        counts + float(concentration) * league.reshape(1, -1)
    ) / (exposure.reshape(-1, 1) + float(concentration))
    return _probabilities(posterior, rows=len(frame))


def strict_numeric_features(frame: pd.DataFrame, features: Sequence[str]) -> pd.DataFrame:
    names = list(features)
    if not names or len(names) != len(set(names)):
        raise ValueError("model feature contract must be nonempty and unique")
    missing = sorted(set(names).difference(frame.columns))
    if missing:
        raise ValueError(f"model feature columns are missing: {missing}")
    output = pd.DataFrame(index=frame.index)
    for name in names:
        source = frame[name]
        numeric = pd.to_numeric(source, errors="coerce")
        if (source.notna() & numeric.isna()).any():
            raise ValueError(f"model feature is nonnumeric: {name}")
        values = numeric.to_numpy(float)
        if np.isinf(values).any():
            raise ValueError(f"model feature is infinite: {name}")
        output[name] = numeric.astype(float)
    return output


def expanded_events(
    frame: pd.DataFrame,
    features: Sequence[str],
    baseline: np.ndarray | None,
) -> tuple[pd.DataFrame, np.ndarray, np.ndarray, np.ndarray | None]:
    feature_frame = strict_numeric_features(frame, features)
    counts = target_counts(frame)
    pieces: list[pd.DataFrame] = []
    labels: list[int] = []
    weights: list[float] = []
    baselines: list[np.ndarray] = []
    if baseline is not None:
        baseline = _probabilities(baseline, rows=len(frame))
    for class_index in range(len(PA_OUTCOMES)):
        mask = counts[:, class_index] > 0.0
        if not mask.any():
            continue
        pieces.append(feature_frame.loc[mask])
        labels.extend([class_index] * int(mask.sum()))
        weights.extend(counts[mask, class_index].tolist())
        if baseline is not None:
            baselines.append(np.log(baseline[mask]))
    if not pieces or set(labels) != set(range(len(PA_OUTCOMES))):
        raise ValueError("expanded training data does not contain every PA class")
    expanded_baseline = np.vstack(baselines) if baseline is not None else None
    return (
        pd.concat(pieces, ignore_index=True),
        np.asarray(labels, dtype=int),
        np.asarray(weights, dtype=float),
        expanded_baseline,
    )


def _params(value: Mapping[str, Any]) -> dict[str, Any]:
    expected = {
        "library": "catboost==1.2.10",
        "loss_function": "MultiClass",
        "iterations": 1200,
        "depth": 4,
        "learning_rate": 0.03,
        "l2_leaf_reg": 10.0,
        "thread_count": 2,
        "random_seed": 20260721,
        "allow_writing_files": False,
        "parameter_selection": "none; inherited once from the frozen all-prior CatBoost core comparator",
    }
    if dict(value) != expected:
        raise ValueError("residual model parameters differ from the locked protocol")
    return {
        "loss_function": "MultiClass",
        "classes_count": len(PA_OUTCOMES),
        "iterations": expected["iterations"],
        "depth": expected["depth"],
        "learning_rate": expected["learning_rate"],
        "l2_leaf_reg": expected["l2_leaf_reg"],
        "thread_count": expected["thread_count"],
        "random_seed": expected["random_seed"],
        "allow_writing_files": expected["allow_writing_files"],
        "verbose": False,
    }


@dataclass
class SharedPAEBCatBoost:
    model: Any
    features: tuple[str, ...]
    league: np.ndarray
    concentration: float

    def predict_proba(self, frame: pd.DataFrame) -> np.ndarray:
        from catboost import Pool

        features = strict_numeric_features(frame, self.features)
        baseline = empirical_bayes_probability(frame, self.league, self.concentration)
        pool = Pool(features, baseline=np.log(baseline))
        result = np.asarray(self.model.predict_proba(pool), dtype=float)
        return _probabilities(result, rows=len(frame))


@dataclass
class SharedPACoreCatBoost:
    model: Any
    features: tuple[str, ...]

    def predict_proba(self, frame: pd.DataFrame) -> np.ndarray:
        from catboost import Pool

        features = strict_numeric_features(frame, self.features)
        result = np.asarray(self.model.predict_proba(Pool(features)), dtype=float)
        return _probabilities(result, rows=len(frame))


def fit_eb_offset_catboost(
    frame: pd.DataFrame,
    *,
    features: Sequence[str],
    locked_parameters: Mapping[str, Any],
) -> SharedPAEBCatBoost:
    from catboost import CatBoostClassifier, Pool

    league = league_probability(frame)
    concentration = fit_dirichlet_concentration(frame, league)
    baseline = empirical_bayes_probability(frame, league, concentration)
    events, labels, weights, expanded_baseline = expanded_events(
        frame, features, baseline
    )
    model = CatBoostClassifier(**_params(locked_parameters))
    model.fit(Pool(events, label=labels, weight=weights, baseline=expanded_baseline))
    if not np.array_equal(np.asarray(model.classes_, dtype=int), np.arange(len(PA_OUTCOMES))):
        raise ValueError("residual model class identity changed")
    fitted = SharedPAEBCatBoost(
        model=model,
        features=tuple(features),
        league=league,
        concentration=concentration,
    )
    fitted.predict_proba(frame.iloc[: min(32, len(frame))])
    return fitted


def fit_frozen_core_catboost(
    frame: pd.DataFrame,
    *,
    features: Sequence[str],
    locked_parameters: Mapping[str, Any],
) -> SharedPACoreCatBoost:
    from catboost import CatBoostClassifier, Pool

    events, labels, weights, _ = expanded_events(frame, features, None)
    model = CatBoostClassifier(**_params(locked_parameters))
    model.fit(Pool(events, label=labels, weight=weights))
    if not np.array_equal(np.asarray(model.classes_, dtype=int), np.arange(len(PA_OUTCOMES))):
        raise ValueError("frozen core model class identity changed")
    fitted = SharedPACoreCatBoost(model=model, features=tuple(features))
    fitted.predict_proba(frame.iloc[: min(32, len(frame))])
    return fitted
