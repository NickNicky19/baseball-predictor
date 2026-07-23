"""Shared probabilistic PA model and honest simple baselines.

This module predicts one coherent distribution over mutually exclusive plate-
appearance outcomes.  Market probabilities are derived downstream from that
single distribution; Hits, HR, and Total Bases never receive independent
regressions that can contradict one another.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from src.evaluation.multi_market_foundation import PA_OUTCOMES
from src.evaluation.shared_pa_training_data import outcome_counts


EPSILON = 1e-12


def _validated_count_matrix(counts: pd.DataFrame) -> np.ndarray:
    try:
        values = counts.loc[:, PA_OUTCOMES].to_numpy(float)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("PA counts must contain numeric contracted outcome columns") from exc
    if values.ndim != 2 or values.shape[1] != len(PA_OUTCOMES):
        raise ValueError("PA count matrix has the wrong shape")
    if not np.isfinite(values).all():
        raise ValueError("PA counts must be finite")
    if (values < 0.0).any():
        raise ValueError("PA counts must be nonnegative")
    if not np.equal(values, np.floor(values)).all():
        raise ValueError("PA counts must be integral")
    return values


def _validated_probability_matrix(
    probabilities: np.ndarray,
    *,
    expected_shape: tuple[int, int] | None = None,
) -> np.ndarray:
    try:
        probs = np.asarray(probabilities, dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError("PA probabilities must be numeric") from exc
    if probs.ndim != 2 or probs.shape[1] != len(PA_OUTCOMES):
        raise ValueError("PA probability matrix has the wrong shape")
    if expected_shape is not None and probs.shape != expected_shape:
        raise ValueError("probability/count shape mismatch")
    if not np.isfinite(probs).all():
        raise ValueError("PA probabilities must be finite")
    if ((probs < 0.0) | (probs > 1.0)).any():
        raise ValueError("PA probabilities must be within [0,1]")
    if not np.allclose(probs.sum(axis=1), 1.0, rtol=0.0, atol=1e-9):
        raise ValueError("PA probabilities do not sum to one")
    return probs


def normalized_counts(frame: pd.DataFrame) -> tuple[pd.DataFrame, np.ndarray]:
    counts = outcome_counts(frame).loc[:, PA_OUTCOMES].astype(float)
    values = _validated_count_matrix(counts)
    exposure = values.sum(axis=1)
    if float(exposure.sum()) <= 0:
        raise ValueError("shared PA fitting requires nonnegative and positive aggregate exposure")
    return counts, exposure


def proper_scores(counts: pd.DataFrame, probabilities: np.ndarray) -> dict[str, float]:
    values = _validated_count_matrix(counts)
    probs = _validated_probability_matrix(probabilities, expected_shape=values.shape)
    total = float(values.sum())
    if total <= 0:
        raise ValueError("proper scoring requires positive PA exposure")
    clipped = np.clip(probs, EPSILON, 1.0)
    log_loss = float(-(values * np.log(clipped)).sum() / total)
    # For each observed class j, sum_k (1[j=k]-p_k)^2.  Aggregated counts
    # compute the identical per-PA multiclass Brier score without expanding PAs.
    squared_sum = np.square(probs).sum(axis=1)
    brier_numerator = (
        values.sum(axis=1) * (1.0 + squared_sum)
        - 2.0 * (values * probs).sum(axis=1)
    ).sum()
    return {"multiclass_log_loss": log_loss, "multiclass_brier": float(brier_numerator / total)}


def per_row_proper_loss(counts: pd.DataFrame, probabilities: np.ndarray) -> pd.DataFrame:
    """Return loss numerators and PA exposure for paired date-block inference."""
    values = _validated_count_matrix(counts)
    probs = _validated_probability_matrix(probabilities, expected_shape=values.shape)
    clipped = np.clip(probs, EPSILON, 1.0)
    exposure = values.sum(axis=1)
    squared_sum = np.square(probs).sum(axis=1)
    return pd.DataFrame({
        "exposure": exposure,
        "log_loss_numerator": -(values * np.log(clipped)).sum(axis=1),
        "brier_numerator": exposure * (1.0 + squared_sum) - 2.0 * (values * probs).sum(axis=1),
    })


def binary_class_metrics(
    counts: pd.DataFrame,
    probabilities: np.ndarray,
) -> dict[str, dict[str, float | int | bool]]:
    """Exact classwise metrics from aggregated player-game PA counts."""
    from scipy.optimize import minimize
    from sklearn.metrics import roc_auc_score

    values = _validated_count_matrix(counts)
    probs = _validated_probability_matrix(probabilities, expected_shape=values.shape)
    exposure = values.sum(axis=1)
    if (exposure < 0).any() or exposure.sum() <= 0:
        raise ValueError("invalid PA exposure for classwise metrics")
    result: dict[str, dict[str, float | int | bool]] = {}
    for class_index, name in enumerate(PA_OUTCOMES):
        positive = values[:, class_index]
        negative = exposure - positive
        p = np.clip(probs[:, class_index], EPSILON, 1.0 - EPSILON)
        total = float(exposure.sum())
        positives = float(positive.sum())
        negatives = float(negative.sum())
        sufficient = positives > 0 and negatives > 0
        brier = float((positive * np.square(1.0 - p) + negative * np.square(p)).sum() / total)
        log_loss = float(-(positive * np.log(p) + negative * np.log1p(-p)).sum() / total)
        if sufficient:
            auc = float(roc_auc_score(
                np.concatenate([np.ones(len(p)), np.zeros(len(p))]),
                np.concatenate([p, p]),
                sample_weight=np.concatenate([positive, negative]),
            ))
            model_logit = np.log(p) - np.log1p(-p)

            def objective(parameters: np.ndarray) -> float:
                linear = parameters[0] + parameters[1] * model_logit
                calibrated = 1.0 / (1.0 + np.exp(-np.clip(linear, -40.0, 40.0)))
                calibrated = np.clip(calibrated, EPSILON, 1.0 - EPSILON)
                return float(-(positive * np.log(calibrated) + negative * np.log1p(-calibrated)).sum())

            def gradient(parameters: np.ndarray) -> np.ndarray:
                linear = parameters[0] + parameters[1] * model_logit
                calibrated = 1.0 / (1.0 + np.exp(-np.clip(linear, -40.0, 40.0)))
                calibrated = np.clip(calibrated, EPSILON, 1.0 - EPSILON)
                residual = exposure * calibrated - positive
                return np.asarray([residual.sum(), (residual * model_logit).sum()], dtype=float)

            # Supplying the exact gradient avoids BFGS's finite-difference
            # precision-loss false failures on rare PA outcomes.  These are
            # diagnostics only; they never recalibrate model probabilities.
            fitted = minimize(
                objective, x0=np.array([0.0, 1.0]), jac=gradient, method="BFGS"
            )
            if fitted.success and np.isfinite(fitted.x).all():
                calibration_intercept = float(fitted.x[0])
                calibration_slope = float(fitted.x[1])
            else:
                calibration_intercept = float("nan")
                calibration_slope = float("nan")
                sufficient = False
        else:
            auc = float("nan")
            calibration_intercept = float("nan")
            calibration_slope = float("nan")
        result[name] = {
            "binary_brier": brier,
            "binary_log_loss": log_loss,
            "roc_auc": auc,
            "calibration_intercept": calibration_intercept,
            "calibration_slope": calibration_slope,
            "positive_pa": int(positives),
            "negative_pa": int(negatives),
            "sufficient_evidence": bool(sufficient),
        }
    return result


def paired_date_block_interval(
    counts: pd.DataFrame,
    candidate: np.ndarray,
    baseline: np.ndarray,
    dates: pd.Series,
    *,
    metric: str,
    draws: int,
    seed: int,
) -> dict[str, float | int]:
    if metric not in {"log_loss", "brier"}:
        raise ValueError("paired interval metric must be log_loss or brier")
    if isinstance(draws, bool) or not isinstance(draws, int) or draws <= 0:
        raise ValueError("paired interval draws must be a positive integer")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("paired interval seed must be an integer")
    if len(dates) != len(counts):
        raise ValueError("paired interval date/count row mismatch")
    candidate_loss = per_row_proper_loss(counts, candidate)
    baseline_loss = per_row_proper_loss(counts, baseline)
    column = f"{metric}_numerator"
    block = pd.DataFrame({
        "date": pd.to_datetime(dates, errors="raise").dt.date,
        "exposure": candidate_loss["exposure"],
        "delta": candidate_loss[column] - baseline_loss[column],
    }).groupby("date", sort=True, as_index=False).sum()
    if len(block) < 2:
        raise ValueError("paired date-block interval requires at least two dates")
    exposure = block["exposure"].to_numpy(float)
    delta = block["delta"].to_numpy(float)
    point = float(delta.sum() / exposure.sum())
    rng = np.random.default_rng(seed)
    samples = np.empty(draws, dtype=float)
    for draw in range(draws):
        selected = rng.integers(0, len(block), size=len(block))
        samples[draw] = delta[selected].sum() / exposure[selected].sum()
    lower, upper = np.quantile(samples, [0.025, 0.975])
    return {
        "point": point,
        "lower": float(lower),
        "upper": float(upper),
        "dates": int(len(block)),
        "draws": int(draws),
    }


@dataclass(frozen=True)
class RateBaseline:
    kind: str
    league_probability: np.ndarray
    grouped_probability: dict[Any, np.ndarray]
    group_column: str | None
    prior_strength_pa: float

    def predict(self, frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        out = np.tile(self.league_probability, (len(frame), 1))
        fallback = np.ones(len(frame), dtype=bool)
        if self.group_column is None:
            return out, fallback
        keys = frame[self.group_column].tolist()
        for index, key in enumerate(keys):
            probability = self.grouped_probability.get(key)
            if probability is not None:
                out[index] = probability
                fallback[index] = False
        return out, fallback


def fit_rate_baseline(
    frame: pd.DataFrame,
    *,
    kind: str,
    prior_strength_pa: float = 0.0,
) -> RateBaseline:
    if kind not in {"league_rate", "lineup_slot_rate", "empirical_bayes_player_rate"}:
        raise ValueError(f"unknown simple baseline: {kind}")
    if isinstance(prior_strength_pa, bool):
        raise ValueError("prior_strength_pa must be finite and nonnegative")
    try:
        prior_strength_pa = float(prior_strength_pa)
    except (TypeError, ValueError) as exc:
        raise ValueError("prior_strength_pa must be finite and nonnegative") from exc
    if not np.isfinite(prior_strength_pa) or prior_strength_pa < 0.0:
        raise ValueError("prior_strength_pa must be finite and nonnegative")
    counts, exposure = normalized_counts(frame)
    league_counts = counts.sum(axis=0).to_numpy(float)
    league = league_counts / league_counts.sum()
    group_column = {
        "league_rate": None,
        "lineup_slot_rate": "lineup_slot",
        "empirical_bayes_player_rate": "player_id",
    }[kind]
    grouped: dict[Any, np.ndarray] = {}
    if group_column is not None:
        work = counts.copy()
        work[group_column] = frame[group_column].to_numpy()
        for key, group in work.groupby(group_column, sort=False):
            group_counts = group.loc[:, PA_OUTCOMES].sum(axis=0).to_numpy(float)
            posterior = group_counts + prior_strength_pa * league
            grouped[key] = posterior / posterior.sum()
    return RateBaseline(
        kind=kind,
        league_probability=league,
        grouped_probability=grouped,
        group_column=group_column,
        prior_strength_pa=prior_strength_pa,
    )


def weighted_event_rows(
    frame: pd.DataFrame, features: list[str]
) -> tuple[pd.DataFrame, np.ndarray, np.ndarray]:
    """Expand player-games only by nonzero class, using PA counts as weights."""
    counts, _ = normalized_counts(frame)
    feature_parts: list[pd.DataFrame] = []
    labels: list[int] = []
    weights: list[float] = []
    for class_index, column in enumerate(PA_OUTCOMES):
        mask = counts[column].to_numpy(float) > 0
        if not mask.any():
            continue
        feature_parts.append(frame.loc[mask, features])
        labels.extend([class_index] * int(mask.sum()))
        weights.extend(counts.loc[mask, column].astype(float).tolist())
    if not feature_parts:
        raise ValueError("no positive PA outcome counts to train")
    return (
        pd.concat(feature_parts, ignore_index=True),
        np.asarray(labels, dtype=int),
        np.asarray(weights, dtype=float),
    )


@dataclass
class SharedPACatBoost:
    model: Any
    features: list[str]
    categorical_features: list[str]

    def predict_proba(self, frame: pd.DataFrame) -> np.ndarray:
        from catboost import Pool

        prepared = frame[self.features].copy()
        for column in self.categorical_features:
            prepared[column] = prepared[column].astype("object").where(
                prepared[column].notna(), "__NA__"
            ).astype(str)
        pool = Pool(prepared, cat_features=self.categorical_features or None)
        probabilities = np.asarray(self.model.predict_proba(pool), dtype=float)
        try:
            return _validated_probability_matrix(
                probabilities,
                expected_shape=(len(frame), len(PA_OUTCOMES)),
            )
        except ValueError as exc:
            raise ValueError("CatBoost output violates the PA outcome contract") from exc


def fit_catboost(
    frame: pd.DataFrame,
    *,
    features: list[str],
    params: dict[str, Any],
    seed: int,
    validation_frame: pd.DataFrame | None = None,
    early_stopping_rounds: int | None = None,
    categorical_features: list[str] | None = None,
) -> SharedPACatBoost:
    from catboost import CatBoostClassifier, Pool

    if not isinstance(features, list) or not features or len(features) != len(set(features)):
        raise ValueError("features must be a non-empty unique list")
    missing_features = sorted(set(features) - set(frame.columns))
    if missing_features:
        raise ValueError(f"training frame is missing features: {missing_features}")
    if not isinstance(params, dict):
        raise ValueError("CatBoost params must be an object")
    reserved = sorted(set(params).intersection({"loss_function", "random_seed", "verbose"}))
    if reserved:
        raise ValueError(f"CatBoost params cannot override locked fields: {reserved}")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("CatBoost seed must be an integer")

    if categorical_features is None:
        categorical = [
            column for column in features
            if column in {"bats", "opp_sp_throws", "opp_sp_source", "venue"}
        ]
    else:
        categorical = list(categorical_features)
        unknown = sorted(set(categorical).difference(features))
        if unknown or len(categorical) != len(set(categorical)):
            raise ValueError(f"invalid explicit categorical feature contract: {unknown}")
    events, labels, weights = weighted_event_rows(frame, features)
    for column in categorical:
        events[column] = events[column].astype("object").where(events[column].notna(), "__NA__").astype(str)
    model_params = {
        "loss_function": "MultiClass",
        "random_seed": int(seed),
        "verbose": False,
        **params,
    }
    pool = Pool(events, label=labels, weight=weights, cat_features=categorical or None)
    model = CatBoostClassifier(**model_params)
    fit_args: dict[str, Any] = {}
    if validation_frame is not None:
        validation_events, validation_labels, validation_weights = weighted_event_rows(
            validation_frame, features
        )
        for column in categorical:
            validation_events[column] = validation_events[column].astype("object").where(
                validation_events[column].notna(), "__NA__"
            ).astype(str)
        fit_args["eval_set"] = Pool(
            validation_events,
            label=validation_labels,
            weight=validation_weights,
            cat_features=categorical or None,
        )
        fit_args["use_best_model"] = True
        if early_stopping_rounds is not None:
            fit_args["early_stopping_rounds"] = int(early_stopping_rounds)
    model.fit(pool, **fit_args)
    return SharedPACatBoost(model=model, features=list(features), categorical_features=categorical)


def temperature_scale(probabilities: np.ndarray, temperature: float) -> np.ndarray:
    if isinstance(temperature, bool):
        raise ValueError("temperature must be positive and finite")
    try:
        temperature = float(temperature)
    except (TypeError, ValueError) as exc:
        raise ValueError("temperature must be positive and finite") from exc
    if not np.isfinite(temperature) or temperature <= 0:
        raise ValueError("temperature must be positive and finite")
    probs = _validated_probability_matrix(probabilities)
    logp = np.log(np.clip(probs, EPSILON, 1.0)) / temperature
    logp -= logp.max(axis=1, keepdims=True)
    exp = np.exp(logp)
    return exp / exp.sum(axis=1, keepdims=True)


def fit_temperature(
    counts: pd.DataFrame,
    probabilities: np.ndarray,
    *,
    log_temperature_bounds: tuple[float, float],
) -> float:
    from scipy.optimize import minimize_scalar

    lower, upper = (float(value) for value in log_temperature_bounds)
    if not np.isfinite(lower) or not np.isfinite(upper) or lower >= upper:
        raise ValueError("invalid log-temperature bounds")

    def objective(log_temperature: float) -> float:
        scaled = temperature_scale(probabilities, float(np.exp(log_temperature)))
        return proper_scores(counts, scaled)["multiclass_log_loss"]

    result = minimize_scalar(objective, bounds=(lower, upper), method="bounded")
    if not result.success:
        raise ValueError("temperature optimization failed")
    return float(np.exp(result.x))


def derived_market_probabilities(
    pa_probabilities: np.ndarray,
    lineup_slots: pd.Series,
    pa_distribution: dict[str, dict[str, float]],
) -> dict[str, np.ndarray]:
    """Derive exact Hits/HR/TB tails from one PA distribution and fitted PA volume."""
    try:
        probabilities = _validated_probability_matrix(pa_probabilities)
    except ValueError as exc:
        raise ValueError(f"invalid PA probabilities for market derivation: {exc}") from exc
    if len(lineup_slots) != len(probabilities):
        raise ValueError("lineup-slot and PA-probability row counts differ")
    if not isinstance(pa_distribution, dict) or not pa_distribution:
        raise ValueError("PA distribution must be a non-empty object")

    validated_distributions: dict[str, dict[int, float]] = {}
    for raw_slot, raw_distribution in pa_distribution.items():
        if isinstance(raw_slot, bool):
            raise ValueError(f"invalid PA-distribution lineup slot: {raw_slot!r}")
        try:
            slot_number = int(raw_slot)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid PA-distribution lineup slot: {raw_slot!r}") from exc
        if str(slot_number) != str(raw_slot).strip() or not 1 <= slot_number <= 9:
            raise ValueError(f"invalid PA-distribution lineup slot: {raw_slot!r}")
        slot = str(slot_number)
        if slot in validated_distributions:
            raise ValueError(f"duplicate canonical PA-distribution lineup slot: {slot}")
        if not isinstance(raw_distribution, dict) or not raw_distribution:
            raise ValueError(f"missing PA distribution for lineup slot {slot}")
        distribution: dict[int, float] = {}
        for raw_pa, raw_weight in raw_distribution.items():
            if isinstance(raw_pa, bool):
                raise ValueError(f"invalid PA support for lineup slot {slot}: {raw_pa!r}")
            try:
                pa = int(raw_pa)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid PA support for lineup slot {slot}: {raw_pa!r}") from exc
            if str(pa) != str(raw_pa).strip() or pa < 0:
                raise ValueError(f"invalid PA support for lineup slot {slot}: {raw_pa!r}")
            if pa in distribution:
                raise ValueError(f"duplicate canonical PA support for lineup slot {slot}: {pa}")
            if isinstance(raw_weight, bool):
                raise ValueError(f"PA weight for lineup slot {slot}, PA {pa} must be numeric")
            try:
                weight = float(raw_weight)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"PA weight for lineup slot {slot}, PA {pa} must be numeric"
                ) from exc
            if not np.isfinite(weight) or not 0.0 <= weight <= 1.0:
                raise ValueError(
                    f"PA weight for lineup slot {slot}, PA {pa} must be finite within [0,1]"
                )
            distribution[pa] = weight
        if not np.isclose(sum(distribution.values()), 1.0, rtol=0.0, atol=1e-9):
            raise ValueError(f"PA distribution for lineup slot {slot} must sum to one")
        validated_distributions[slot] = distribution

    missing_slots = sorted(set(str(slot) for slot in range(1, 10)) - set(validated_distributions))
    if missing_slots:
        raise ValueError(f"PA distribution is missing lineup slots: {missing_slots}")
    class_index = {name: index for index, name in enumerate(PA_OUTCOMES)}
    output = {
        "hits_0.5": np.zeros(len(probabilities)),
        "hits_1.5": np.zeros(len(probabilities)),
        "home_runs_0.5": np.zeros(len(probabilities)),
    }
    for line in (0.5, 1.5, 2.5, 3.5, 4.5, 5.5):
        output[f"total_bases_{line}"] = np.zeros(len(probabilities))
    hit_indices = [class_index[name] for name in ("single", "double", "triple", "home_run")]
    tb_values = {
        "single": 1, "double": 2, "triple": 3, "home_run": 4,
    }
    for row_index, (probability, raw_slot) in enumerate(zip(probabilities, lineup_slots)):
        if isinstance(raw_slot, (bool, np.bool_)):
            raise ValueError(f"invalid lineup slot at row {row_index}: {raw_slot!r}")
        try:
            slot_number = int(raw_slot)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"invalid lineup slot at row {row_index}: {raw_slot!r}") from exc
        try:
            exact_slot = float(raw_slot)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"invalid lineup slot at row {row_index}: {raw_slot!r}") from exc
        if not np.isfinite(exact_slot) or exact_slot != slot_number or not 1 <= slot_number <= 9:
            raise ValueError(f"invalid lineup slot at row {row_index}: {raw_slot!r}")
        slot = str(slot_number)
        distribution = validated_distributions[slot]
        q_hit = float(probability[hit_indices].sum())
        q_hr = float(probability[class_index["home_run"]])
        one_pa_tb = np.zeros(5, dtype=float)
        one_pa_tb[0] = 1.0 - sum(float(probability[class_index[name]]) for name in tb_values)
        for name, bases in tb_values.items():
            one_pa_tb[bases] = float(probability[class_index[name]])
        for raw_pa, weight in distribution.items():
            pa = raw_pa
            mixture_weight = weight
            output["hits_0.5"][row_index] += mixture_weight * (1.0 - (1.0 - q_hit) ** pa)
            p0 = (1.0 - q_hit) ** pa
            p1 = pa * q_hit * (1.0 - q_hit) ** max(pa - 1, 0)
            output["hits_1.5"][row_index] += mixture_weight * (1.0 - p0 - p1)
            output["home_runs_0.5"][row_index] += mixture_weight * (1.0 - (1.0 - q_hr) ** pa)
            tb_pmf = np.array([1.0])
            for _ in range(pa):
                tb_pmf = np.convolve(tb_pmf, one_pa_tb)
            for line in (0.5, 1.5, 2.5, 3.5, 4.5, 5.5):
                threshold = int(line + 0.5)
                output[f"total_bases_{line}"][row_index] += mixture_weight * float(tb_pmf[threshold:].sum())
    for name, values in output.items():
        if not np.isfinite(values).all() or ((values < 0.0) | (values > 1.0)).any():
            raise ValueError(f"derived market probability outside [0,1]: {name}")
    return output
