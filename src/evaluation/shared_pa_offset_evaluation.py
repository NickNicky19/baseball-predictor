"""Market-separated scoring for the 2023 shared-PA offset experiment."""

from __future__ import annotations

from typing import Any, Mapping

import numpy as np
import pandas as pd

from src.evaluation.shared_pa_eb_offset_catboost import PA_OUTCOMES, target_counts


EPSILON = 1e-12
INDEX = {name: index for index, name in enumerate(PA_OUTCOMES)}


def _matrix(probability: np.ndarray, columns: int) -> np.ndarray:
    value = np.asarray(probability, dtype=float)
    if value.ndim != 2 or value.shape[1] != columns:
        raise ValueError("probability matrix has the wrong shape")
    if not np.isfinite(value).all() or (value < 0.0).any() or (value > 1.0).any():
        raise ValueError("probability matrix lies outside [0,1]")
    if not np.allclose(value.sum(axis=1), 1.0, rtol=0.0, atol=1e-9):
        raise ValueError("probability matrix does not sum to one")
    return value


def component(
    frame: pd.DataFrame,
    pa_probability: np.ndarray,
    name: str,
) -> tuple[np.ndarray, np.ndarray]:
    counts = target_counts(frame)
    probability = _matrix(pa_probability, len(PA_OUTCOMES))
    if len(counts) != len(probability):
        raise ValueError("PA count/probability row mismatch")
    if name == "per_pa_hit_event":
        members = [INDEX[value] for value in ("single", "double", "triple", "home_run")]
        positive = counts[:, members].sum(axis=1)
        p = probability[:, members].sum(axis=1)
        return np.column_stack([counts.sum(axis=1) - positive, positive]), np.column_stack([1.0 - p, p])
    if name == "per_pa_home_run_event":
        positive = counts[:, INDEX["home_run"]]
        p = probability[:, INDEX["home_run"]]
        return np.column_stack([counts.sum(axis=1) - positive, positive]), np.column_stack([1.0 - p, p])
    if name == "per_pa_total_bases_distribution":
        zero_members = [INDEX[value] for value in ("strikeout", "walk", "bip_out", "other_non_ab")]
        zero = counts[:, zero_members].sum(axis=1)
        zero_p = probability[:, zero_members].sum(axis=1)
        return (
            np.column_stack([
                zero,
                counts[:, INDEX["single"]],
                counts[:, INDEX["double"]],
                counts[:, INDEX["triple"]],
                counts[:, INDEX["home_run"]],
            ]),
            np.column_stack([
                zero_p,
                probability[:, INDEX["single"]],
                probability[:, INDEX["double"]],
                probability[:, INDEX["triple"]],
                probability[:, INDEX["home_run"]],
            ]),
        )
    raise ValueError(f"unknown per-PA component: {name}")


def weighted_auc(positive: np.ndarray, negative: np.ndarray, probability: np.ndarray) -> float:
    positive = np.asarray(positive, dtype=float)
    negative = np.asarray(negative, dtype=float)
    probability = np.asarray(probability, dtype=float)
    if not (len(positive) == len(negative) == len(probability)):
        raise ValueError("AUC inputs differ in length")
    if not np.isfinite(np.r_[positive, negative, probability]).all():
        raise ValueError("AUC input is nonfinite")
    if (positive < 0.0).any() or (negative < 0.0).any():
        raise ValueError("AUC weights are negative")
    total_positive, total_negative = positive.sum(), negative.sum()
    if total_positive <= 0.0 or total_negative <= 0.0:
        return float("nan")
    order = np.argsort(probability, kind="mergesort")
    score = probability[order]
    pos = positive[order]
    neg = negative[order]
    concordant = 0.0
    cumulative_negative = 0.0
    start = 0
    while start < len(score):
        end = start + 1
        while end < len(score) and score[end] == score[start]:
            end += 1
        group_positive = pos[start:end].sum()
        group_negative = neg[start:end].sum()
        concordant += group_positive * (cumulative_negative + 0.5 * group_negative)
        cumulative_negative += group_negative
        start = end
    return float(concordant / (total_positive * total_negative))


def _binary_calibration(
    positive: np.ndarray,
    exposure: np.ndarray,
    probability: np.ndarray,
) -> tuple[float, float]:
    total = float(exposure.sum())
    bias = float((probability * exposure).sum() / total - positive.sum() / total)
    order = np.argsort(probability, kind="mergesort")
    ece = 0.0
    for group in np.array_split(order, min(10, len(order))):
        group_exposure = float(exposure[group].sum())
        if group_exposure <= 0.0:
            continue
        predicted = float((probability[group] * exposure[group]).sum() / group_exposure)
        observed = float(positive[group].sum() / group_exposure)
        ece += group_exposure / total * abs(predicted - observed)
    return bias, float(ece)


def metrics(counts: np.ndarray, probability: np.ndarray) -> dict[str, float | int | None]:
    counts = np.asarray(counts, dtype=float)
    probability = _matrix(probability, counts.shape[1])
    if counts.shape != probability.shape or (counts < 0.0).any() or not np.isfinite(counts).all():
        raise ValueError("score counts are invalid")
    exposure = counts.sum(axis=1)
    total = float(exposure.sum())
    if total <= 0.0 or (exposure <= 0.0).any():
        raise ValueError("score exposure is not positive")
    clipped = np.clip(probability, EPSILON, 1.0)
    row_brier = exposure * (1.0 + np.square(probability).sum(axis=1)) - 2.0 * (counts * probability).sum(axis=1)
    row_log = -(counts * np.log(clipped)).sum(axis=1)
    aucs: list[float] = []
    biases: list[float] = []
    eces: list[float] = []
    for index in range(counts.shape[1]):
        positive = counts[:, index]
        aucs.append(weighted_auc(positive, exposure - positive, probability[:, index]))
        bias, ece = _binary_calibration(positive, exposure, probability[:, index])
        biases.append(bias)
        eces.append(ece)
    if not np.isfinite(aucs).all():
        auc = None
    else:
        auc = float(aucs[1] if counts.shape[1] == 2 else np.mean(aucs))
    selected_biases = [biases[1]] if counts.shape[1] == 2 else biases
    selected_eces = [eces[1]] if counts.shape[1] == 2 else eces
    return {
        "rows": int(len(counts)),
        "exposure": int(total),
        "brier": float(row_brier.sum() / total),
        "log_loss": float(row_log.sum() / total),
        "auc": auc,
        "max_absolute_calibration_bias": float(max(abs(value) for value in selected_biases)),
        "mean_ece_10": float(np.mean(selected_eces)),
    }


def row_losses(counts: np.ndarray, probability: np.ndarray) -> dict[str, np.ndarray]:
    counts = np.asarray(counts, dtype=float)
    probability = _matrix(probability, counts.shape[1])
    exposure = counts.sum(axis=1)
    return {
        "exposure": exposure,
        "brier": exposure * (1.0 + np.square(probability).sum(axis=1)) - 2.0 * (counts * probability).sum(axis=1),
        "log_loss": -(counts * np.log(np.clip(probability, EPSILON, 1.0))).sum(axis=1),
    }


def paired_date_interval(
    counts: np.ndarray,
    candidate: np.ndarray,
    comparator: np.ndarray,
    dates: pd.Series,
    *,
    draws: int,
    seed: int,
) -> dict[str, Any]:
    candidate_loss = row_losses(counts, candidate)
    comparator_loss = row_losses(counts, comparator)
    block = pd.DataFrame({
        "date": pd.to_datetime(dates, format="%Y-%m-%d", errors="raise").dt.date,
        "exposure": candidate_loss["exposure"],
        "brier": candidate_loss["brier"] - comparator_loss["brier"],
        "log_loss": candidate_loss["log_loss"] - comparator_loss["log_loss"],
    }).groupby("date", sort=True).sum()
    if len(block) < 2 or draws <= 0:
        raise ValueError("date-block interval needs two dates and positive draws")
    rng = np.random.default_rng(seed)
    result: dict[str, Any] = {"date_blocks": int(len(block)), "draws": int(draws)}
    exposure = block["exposure"].to_numpy(float)
    for metric in ("brier", "log_loss"):
        delta = block[metric].to_numpy(float)
        samples = np.empty(draws, dtype=float)
        for draw in range(draws):
            selected = rng.integers(0, len(block), size=len(block))
            samples[draw] = delta[selected].sum() / exposure[selected].sum()
        result[metric] = {
            "point": float(delta.sum() / exposure.sum()),
            "lower": float(np.quantile(samples, 0.025)),
            "upper": float(np.quantile(samples, 0.975)),
        }
    return result


def fit_pooled_pa_distribution(frame: pd.DataFrame) -> dict[str, float]:
    """Fit P(PA) on an outer-training fold without target-game lineup data."""

    if "out_pa" not in frame:
        raise ValueError("PA opportunity outcome is missing")
    pa = pd.to_numeric(frame["out_pa"], errors="coerce")
    if pa.isna().any() or not np.equal(pa, np.floor(pa)).all() or (pa <= 0).any():
        raise ValueError("PA opportunity requires positive integer PA")
    counts = pa.astype(int).value_counts().sort_index()
    if counts.empty:
        raise ValueError("outer training data has no PA support")
    return {str(int(key)): float(count / counts.sum()) for key, count in counts.items()}


def derive_market_probabilities(
    pa_probability: np.ndarray,
    pa_distribution: Mapping[str, float],
) -> dict[str, np.ndarray]:
    probability = _matrix(pa_probability, len(PA_OUTCOMES))
    distribution = {int(pa): float(weight) for pa, weight in pa_distribution.items()}
    if any(pa <= 0 or not np.isfinite(weight) or weight < 0.0 for pa, weight in distribution.items()):
        raise ValueError("PA distribution has invalid support")
    if not np.isclose(sum(distribution.values()), 1.0, rtol=0.0, atol=1e-12):
        raise ValueError("PA distribution does not sum to one")
    output = {
        "hits_over_0_5": np.zeros(len(probability)),
        "hits_over_1_5": np.zeros(len(probability)),
        "home_runs_over_0_5": np.zeros(len(probability)),
    }
    for line in (0.5, 1.5, 2.5, 3.5, 4.5, 5.5):
        output[f"total_bases_over_{str(line).replace('.', '_')}"] = np.zeros(len(probability))
    hit_indices = [INDEX[value] for value in ("single", "double", "triple", "home_run")]
    for row, values in enumerate(probability):
        q_hit = float(values[hit_indices].sum())
        q_hr = float(values[INDEX["home_run"]])
        one_pa_tb = np.zeros(5)
        one_pa_tb[1] = values[INDEX["single"]]
        one_pa_tb[2] = values[INDEX["double"]]
        one_pa_tb[3] = values[INDEX["triple"]]
        one_pa_tb[4] = values[INDEX["home_run"]]
        one_pa_tb[0] = 1.0 - one_pa_tb[1:].sum()
        for pa, weight in distribution.items():
            output["hits_over_0_5"][row] += weight * (1.0 - (1.0 - q_hit) ** pa)
            p0 = (1.0 - q_hit) ** pa
            p1 = pa * q_hit * (1.0 - q_hit) ** (pa - 1)
            output["hits_over_1_5"][row] += weight * (1.0 - p0 - p1)
            output["home_runs_over_0_5"][row] += weight * (1.0 - (1.0 - q_hr) ** pa)
            pmf = np.array([1.0])
            for _ in range(pa):
                pmf = np.convolve(pmf, one_pa_tb)
            for line in (0.5, 1.5, 2.5, 3.5, 4.5, 5.5):
                threshold = int(line + 0.5)
                key = f"total_bases_over_{str(line).replace('.', '_')}"
                output[key][row] += weight * float(pmf[threshold:].sum())
    for name, values in output.items():
        if not np.isfinite(values).all() or ((values < 0.0) | (values > 1.0)).any():
            raise ValueError(f"derived market probability is invalid: {name}")
    return output


def market_target(frame: pd.DataFrame, name: str) -> np.ndarray:
    if name.startswith("hits_over_"):
        value = pd.to_numeric(frame["out_hits"], errors="raise").to_numpy(float)
    elif name == "home_runs_over_0_5":
        value = pd.to_numeric(frame["out_hr"], errors="raise").to_numpy(float)
    elif name.startswith("total_bases_over_"):
        hits = pd.to_numeric(frame["out_hits"], errors="raise").to_numpy(float)
        doubles = pd.to_numeric(frame["out_doubles"], errors="raise").to_numpy(float)
        triples = pd.to_numeric(frame["out_triples"], errors="raise").to_numpy(float)
        home_runs = pd.to_numeric(frame["out_hr"], errors="raise").to_numpy(float)
        value = hits + doubles + 2.0 * triples + 3.0 * home_runs
    else:
        raise ValueError(f"unknown market target: {name}")
    token = name.rsplit("_over_", 1)[1]
    threshold = float(token.replace("_", "."))
    return (value > threshold).astype(float)


def binary_market_counts(frame: pd.DataFrame, name: str) -> np.ndarray:
    positive = market_target(frame, name)
    return np.column_stack([1.0 - positive, positive])


def binary_market_probability(values: np.ndarray) -> np.ndarray:
    probability = np.asarray(values, dtype=float)
    if probability.ndim != 1:
        raise ValueError("binary market probability must be one-dimensional")
    return _matrix(np.column_stack([1.0 - probability, probability]), 2)
