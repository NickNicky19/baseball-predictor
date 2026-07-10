"""
B1 — per-category GBM trainer.

CatBoost is the code-default primary (native NaN handling + ordered target
encoding for the real categorical features here: opposing pitcher context,
venue, umpire, team — ordered encoding is specifically leakage-resistant,
which matches this project's obsession). LightGBM is available behind
model="lightgbm" as a fast second opinion. IMPORTANT: which model gets
PROMOTED is decided by walk-forward on held-out dates + calibration
(discipline #4), NOT by which one is the code default.

GBM libraries are imported lazily inside the fit path so gbm_dataset.py and
the offline harness stay importable with neither library installed.

Predictions are emitted as PropProjection objects carrying the EXACT
PropCategory label the simulator uses, so they drop straight into
BacktestEngine.evaluate_predictions and compare_reports against the A2
simulator baseline on the same held-out dates.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import numpy as np
import pandas as pd

from src.learning.gbm_dataset import CategoryData
from src.models.dataclasses import PropProjection

DEFAULT_MODEL = "catboost"


@dataclass
class TrainedCategoryModel:
    category: str
    model: Any
    model_kind: str
    feature_names: list[str]
    categorical_features: list[str]
    label_maps: Optional[dict[str, dict[str, int]]] = None  # lightgbm only

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        if self.model_kind == "catboost":
            from catboost import Pool

            pool = Pool(
                X[self.feature_names],
                cat_features=self.categorical_features or None,
            )
            preds = self.model.predict(pool)
        else:  # lightgbm
            Xenc = _apply_label_maps(X[self.feature_names], self.label_maps or {})
            preds = self.model.predict(Xenc)
        return np.clip(np.asarray(preds, dtype=float), 0.0, None)

    def emit_projections(self, data: CategoryData) -> list[PropProjection]:
        """Predict on the holdout and wrap as PropProjection (keyed for
        evaluate_predictions)."""
        if len(data.y_holdout) == 0:
            return []
        preds = self.predict(data.X_holdout)
        ids = data.holdout_ids
        out: list[PropProjection] = []
        for i, pred in enumerate(preds):
            row = ids.iloc[i]
            out.append(
                PropProjection(
                    player_id=int(row["player_id"]),
                    player_name=str(row.get("player_name", "")),
                    category=self.category,  # exact PropCategory literal
                    game_date=str(row["game_date"]),
                    projected_value=round(float(pred), 4),
                    confidence=0.0,  # B2 owns calibrated confidence; not faked here
                    team=str(row.get("team", "")),
                    opponent=str(row.get("opponent", "")),
                )
            )
        return out


def train_category(
    data: CategoryData,
    *,
    model: str = DEFAULT_MODEL,
    params: Optional[dict[str, Any]] = None,
    seed: int = 13,
) -> TrainedCategoryModel:
    if len(data.y_train) == 0:
        raise ValueError(f"[{data.category}] no training rows after split.")
    if model == "catboost":
        return _train_catboost(data, params=params, seed=seed)
    if model == "lightgbm":
        return _train_lightgbm(data, params=params, seed=seed)
    raise ValueError(f"Unknown model '{model}' (want 'catboost' or 'lightgbm').")


# ---------------------------------------------------------------------------
# CatBoost
# ---------------------------------------------------------------------------


def _train_catboost(data: CategoryData, *, params, seed: int) -> TrainedCategoryModel:
    from catboost import CatBoostRegressor, Pool

    p = {
        "loss_function": "RMSE",
        "iterations": 800,
        "learning_rate": 0.03,
        "depth": 6,
        "random_seed": seed,
        "verbose": False,
        # Count outcomes (hits, K) are small non-negative integers; RMSE is a
        # fine default. Poisson is a reasonable alternative worth a walk-forward
        # bake-off later — left as a documented knob, not a hidden default.
    }
    if params:
        p.update(params)
    pool = Pool(
        data.X_train[data.feature_names],
        label=data.y_train,
        cat_features=data.categorical_features or None,
    )
    reg = CatBoostRegressor(**p)
    reg.fit(pool)
    return TrainedCategoryModel(
        category=data.category,
        model=reg,
        model_kind="catboost",
        feature_names=data.feature_names,
        categorical_features=data.categorical_features,
    )


# ---------------------------------------------------------------------------
# LightGBM (categoricals label-encoded from TRAIN only, then applied to holdout)
# ---------------------------------------------------------------------------


def _train_lightgbm(data: CategoryData, *, params, seed: int) -> TrainedCategoryModel:
    import lightgbm as lgb

    label_maps = _fit_label_maps(data.X_train, data.categorical_features)
    Xtr = _apply_label_maps(data.X_train[data.feature_names], label_maps)

    p = {
        "objective": "regression",
        "metric": "rmse",
        "num_leaves": 63,
        "learning_rate": 0.03,
        "n_estimators": 800,
        "min_child_samples": 40,
        "random_state": seed,
        "verbose": -1,
    }
    if params:
        p.update(params)
    reg = lgb.LGBMRegressor(**p)
    cat_idx = [Xtr.columns.get_loc(c) for c in data.categorical_features if c in Xtr.columns]
    reg.fit(Xtr, data.y_train, categorical_feature=cat_idx or "auto")
    return TrainedCategoryModel(
        category=data.category,
        model=reg,
        model_kind="lightgbm",
        feature_names=data.feature_names,
        categorical_features=data.categorical_features,
        label_maps=label_maps,
    )


def _fit_label_maps(X: pd.DataFrame, categorical: list[str]) -> dict[str, dict[str, int]]:
    maps: dict[str, dict[str, int]] = {}
    for c in categorical:
        if c not in X.columns:
            continue
        cats = pd.Index(X[c].astype(str).unique())
        maps[c] = {v: i for i, v in enumerate(sorted(cats))}
    return maps


def _apply_label_maps(X: pd.DataFrame, maps: dict[str, dict[str, int]]) -> pd.DataFrame:
    if not maps:
        return X
    out = X.copy()
    for c, m in maps.items():
        if c in out.columns:
            # Unseen holdout category -> -1 (its own bucket, LightGBM-safe).
            out[c] = out[c].astype(str).map(m).fillna(-1).astype("int64")
    return out
