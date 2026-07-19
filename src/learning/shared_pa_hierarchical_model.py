"""Two-stage coherent PA model for measured contact/non-contact separation."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from src.evaluation.multi_market_foundation import PA_OUTCOMES
from src.evaluation.shared_pa_training_data import outcome_counts


STAGE_1 = ["strikeout", "walk", "other_non_ab", "contact"]
STAGE_2 = ["single", "double", "triple", "home_run", "bip_out"]


def hierarchical_counts(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    counts = outcome_counts(frame).loc[:, PA_OUTCOMES].astype(float)
    stage_1 = counts[["strikeout", "walk", "other_non_ab"]].copy()
    stage_1["contact"] = counts[STAGE_2].sum(axis=1)
    stage_2 = counts[STAGE_2].copy()
    if not np.allclose(stage_1.sum(axis=1), counts.sum(axis=1), atol=0.0):
        raise ValueError("hierarchical stage 1 does not preserve PA exposure")
    if (stage_2.sum(axis=1) < 0).any() or float(stage_2.to_numpy().sum()) <= 0:
        raise ValueError("hierarchical stage 2 has invalid contact exposure")
    return stage_1, stage_2


def _weighted_rows(frame: pd.DataFrame, features: list[str], counts: pd.DataFrame) -> tuple[pd.DataFrame, np.ndarray, np.ndarray]:
    parts: list[pd.DataFrame] = []
    labels: list[int] = []
    weights: list[float] = []
    for index, column in enumerate(counts.columns):
        mask = counts[column].to_numpy(float) > 0
        if not mask.any():
            continue
        parts.append(frame.loc[mask, features])
        labels.extend([index] * int(mask.sum()))
        weights.extend(counts.loc[mask, column].astype(float).tolist())
    if not parts:
        raise ValueError("hierarchical stage has no positive outcome counts")
    return pd.concat(parts, ignore_index=True), np.asarray(labels, dtype=int), np.asarray(weights, dtype=float)


@dataclass
class StageModel:
    model: Any
    features: list[str]
    classes: list[str]

    def predict_proba(self, frame: pd.DataFrame) -> np.ndarray:
        from catboost import Pool
        probability = np.asarray(self.model.predict_proba(Pool(frame[self.features])), dtype=float)
        learned = [int(value) for value in self.model.classes_]
        if set(learned) != set(range(len(self.classes))):
            raise ValueError("hierarchical CatBoost class set changed")
        ordered = probability[:, [learned.index(index) for index in range(len(self.classes))]]
        if not np.isfinite(ordered).all() or (ordered < 0).any() or not np.allclose(ordered.sum(axis=1), 1.0, atol=1e-9):
            raise ValueError("hierarchical stage probabilities are invalid")
        return ordered


def fit_stage(
    frame: pd.DataFrame,
    *,
    features: list[str],
    counts: pd.DataFrame,
    classes: list[str],
    params: dict[str, Any],
    seed: int,
    validation_frame: pd.DataFrame,
    validation_counts: pd.DataFrame,
    early_stopping_rounds: int,
) -> StageModel:
    from catboost import CatBoostClassifier, Pool
    if list(counts.columns) != classes or list(validation_counts.columns) != classes:
        raise ValueError("hierarchical stage count columns changed")
    x, y, weight = _weighted_rows(frame, features, counts)
    vx, vy, vweight = _weighted_rows(validation_frame, features, validation_counts)
    model = CatBoostClassifier(loss_function="MultiClass", random_seed=int(seed), verbose=False, **params)
    model.fit(
        Pool(x, label=y, weight=weight),
        eval_set=Pool(vx, label=vy, weight=vweight),
        use_best_model=True,
        early_stopping_rounds=int(early_stopping_rounds),
    )
    return StageModel(model=model, features=list(features), classes=list(classes))


def fit_stage_full(
    frame: pd.DataFrame,
    *,
    features: list[str],
    counts: pd.DataFrame,
    classes: list[str],
    params: dict[str, Any],
    seed: int,
) -> StageModel:
    from catboost import CatBoostClassifier, Pool
    if list(counts.columns) != classes:
        raise ValueError("hierarchical final-stage count columns changed")
    x, y, weight = _weighted_rows(frame, features, counts)
    model = CatBoostClassifier(loss_function="MultiClass", random_seed=int(seed), verbose=False, **params)
    model.fit(Pool(x, label=y, weight=weight))
    return StageModel(model=model, features=list(features), classes=list(classes))


def combine(stage_1: np.ndarray, stage_2: np.ndarray) -> np.ndarray:
    first = np.asarray(stage_1, dtype=float)
    second = np.asarray(stage_2, dtype=float)
    if first.ndim != 2 or first.shape[1] != len(STAGE_1):
        raise ValueError("hierarchical stage 1 shape changed")
    if second.shape != (len(first), len(STAGE_2)):
        raise ValueError("hierarchical stage 2 shape changed")
    if not np.allclose(first.sum(axis=1), 1.0, atol=1e-9) or not np.allclose(second.sum(axis=1), 1.0, atol=1e-9):
        raise ValueError("hierarchical stages do not sum to one")
    output = np.zeros((len(first), len(PA_OUTCOMES)), dtype=float)
    index = {name: position for position, name in enumerate(PA_OUTCOMES)}
    for name in ("strikeout", "walk", "other_non_ab"):
        output[:, index[name]] = first[:, STAGE_1.index(name)]
    contact = first[:, STAGE_1.index("contact")]
    for name in STAGE_2:
        output[:, index[name]] = contact * second[:, STAGE_2.index(name)]
    if not np.isfinite(output).all() or (output < 0).any() or not np.allclose(output.sum(axis=1), 1.0, atol=1e-9):
        raise ValueError("combined hierarchical PA probabilities are invalid")
    return output
