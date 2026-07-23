from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.evaluation.multi_market_foundation import PA_OUTCOMES
from src.learning.eb_residual_pa_model import fit_eb_residual_pa_model


NUMERIC = ["process_signal"]
LOGGED = ["process_count"]


def _frame() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for row_index in range(32):
        outcome_index = row_index % len(PA_OUTCOMES)
        history = np.full(len(PA_OUTCOMES), 2, dtype=int)
        history[outcome_index] += 3
        row: dict[str, object] = {
            "out_pa": 1,
            "out_ab": 1,
            "out_hits": 0,
            "out_doubles": 0,
            "out_triples": 0,
            "out_hr": 0,
            "out_bb": 0,
            "out_k": 0,
            "history_pa": int(history.sum()),
            "process_signal": float(row_index % 5),
            "process_count": int(row_index % 7),
        }
        for index, outcome in enumerate(PA_OUTCOMES):
            row[f"history_{outcome}_count"] = int(history[index])
        if PA_OUTCOMES[outcome_index] == "strikeout":
            row["out_k"] = 1
        elif PA_OUTCOMES[outcome_index] == "walk":
            row["out_ab"] = 0
            row["out_bb"] = 1
        elif PA_OUTCOMES[outcome_index] == "single":
            row["out_hits"] = 1
        elif PA_OUTCOMES[outcome_index] == "double":
            row["out_hits"] = 1
            row["out_doubles"] = 1
        elif PA_OUTCOMES[outcome_index] == "triple":
            row["out_hits"] = 1
            row["out_triples"] = 1
        elif PA_OUTCOMES[outcome_index] == "home_run":
            row["out_hits"] = 1
            row["out_hr"] = 1
        elif PA_OUTCOMES[outcome_index] == "other_non_ab":
            row["out_ab"] = 0
        rows.append(row)
    return pd.DataFrame(rows)


def _fit(frame: pd.DataFrame):
    return fit_eb_residual_pa_model(
        frame,
        prior_strength_pa=50.0,
        regularization_c=0.1,
        numeric_features=NUMERIC,
        log1p_features=LOGGED,
        seed=20260723,
    )


def test_fit_preserves_eight_class_unit_mass_with_raw_feature_name_overlap():
    frame = _frame()
    model = _fit(frame)
    probabilities = model.predict_proba(frame.drop(columns=[column for column in frame if column.startswith("out_")]))
    assert probabilities.shape == (len(frame), len(PA_OUTCOMES))
    assert np.isfinite(probabilities).all()
    assert np.allclose(probabilities.sum(axis=1), 1.0, atol=1e-9, rtol=0.0)
    # Regression for the old bug: process_signal existed in both the raw frame
    # and prepared frame, creating duplicate labels and an ambiguous feature map.
    assert len(model.pipeline.feature_names_in_) == len(set(model.pipeline.feature_names_in_))
    assert "numeric__process_signal" in model.pipeline.feature_names_in_
    assert "process_signal" not in model.pipeline.feature_names_in_


def test_history_count_denominator_mismatch_fails_closed():
    frame = _frame()
    frame.loc[0, "history_pa"] += 1
    with pytest.raises(ValueError, match="do not equal history PA exposure"):
        _fit(frame)


def test_nonnumeric_process_value_fails_closed():
    frame = _frame()
    frame["process_signal"] = frame["process_signal"].astype(object)
    frame.loc[0, "process_signal"] = "not-a-number"
    with pytest.raises(ValueError, match="nonnumeric"):
        _fit(frame)


def test_negative_log_transform_source_fails_closed():
    frame = _frame()
    frame.loc[0, "process_count"] = -1
    with pytest.raises(ValueError, match="must be nonnegative"):
        _fit(frame)


def test_prediction_does_not_require_or_consume_terminal_outcomes():
    frame = _frame()
    model = _fit(frame)
    prediction_frame = frame.drop(columns=[column for column in frame if column.startswith("out_")])
    before = model.predict_proba(prediction_frame)
    # Target columns are absent, proving the source-to-probability path only
    # consumes the contracted strict-prior histories and process features.
    after = model.predict_proba(prediction_frame.copy())
    assert np.array_equal(before, after)
