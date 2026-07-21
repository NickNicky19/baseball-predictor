from __future__ import annotations

import numpy as np
import pytest

from scripts.audit_repaired_hr_required_comparators import high_probability_tail


def test_high_probability_tail_is_exposure_weighted() -> None:
    counts = np.array([[9, 1], [1, 1]], dtype=float)
    probability = np.array([[0.95, 0.05], [0.40, 0.60]], dtype=float)
    result = high_probability_tail(counts, probability)
    assert result["row_count"] == 1
    assert result["pa"] == 2
    assert result["mean_predicted"] == pytest.approx(0.60)
    assert result["mean_observed"] == pytest.approx(0.50)


def test_high_probability_tail_empty_exposure_fails_closed() -> None:
    with pytest.raises(ValueError, match="no exposure"):
        high_probability_tail(np.zeros((2, 2)), np.array([[0.9, 0.1], [0.8, 0.2]]))
