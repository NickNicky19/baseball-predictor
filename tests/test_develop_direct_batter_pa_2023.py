from __future__ import annotations

import numpy as np
import pytest

from scripts.develop_direct_batter_pa_2023 import (
    NEW_COMPOSITION, OVERLAPPING_REMOVED, calibration_gate, feature_sets, top_decile,
)


def test_feature_sets_replace_overlap_without_changing_other_features() -> None:
    columns = ["player_id", "history_pa", "days_since_pa", *OVERLAPPING_REMOVED, *NEW_COMPOSITION]
    legacy, composition = feature_sets(columns)
    assert set(OVERLAPPING_REMOVED).issubset(legacy)
    assert not set(NEW_COMPOSITION).intersection(legacy)
    assert not set(OVERLAPPING_REMOVED).intersection(composition)
    assert set(NEW_COMPOSITION).issubset(composition)
    assert {"player_id", "history_pa", "days_since_pa"}.issubset(composition)


def test_top_decile_threshold_depends_only_on_predictions() -> None:
    counts = np.asarray([[4, 0], [3, 1], [2, 2]], dtype=float)
    probability = np.asarray([[0.99, 0.01], [0.8, 0.2], [0.6, 0.4]], dtype=float)
    result = top_decile(counts, probability)
    assert result["threshold_from_predictions_only"] == pytest.approx(0.36)
    assert result["rows"] == 1


def test_calibration_gate_requires_no_worse_diagnostics() -> None:
    counts = np.asarray([[99, 1], [90, 10], [80, 20], [70, 30]], dtype=float)
    candidate = np.asarray([[.99,.01],[.90,.10],[.80,.20],[.70,.30]])
    legacy = np.asarray([[.98,.02],[.85,.15],[.75,.25],[.65,.35]])
    result = calibration_gate(counts, candidate, legacy)
    assert result["passed"] is True
