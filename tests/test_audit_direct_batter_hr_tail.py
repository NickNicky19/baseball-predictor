from __future__ import annotations

import numpy as np
import pytest

from scripts.audit_direct_batter_hr_tail import calibration_summary


def test_tail_calibration_uses_exposure_weighting() -> None:
    result = calibration_summary(
        np.asarray([0.02, 0.10]), np.asarray([0, 1]), np.asarray([4, 6])
    )
    assert result["top_decile"]["rows"] == 1
    assert result["top_decile"]["predicted"] == pytest.approx(0.10)
    assert result["top_decile"]["observed"] == pytest.approx(1 / 6)


@pytest.mark.parametrize("probability", [np.asarray([1.2]), np.asarray([np.nan])])
def test_tail_calibration_rejects_invalid_probability(probability) -> None:
    with pytest.raises(ValueError, match="invalid HR probabilities"):
        calibration_summary(probability, np.asarray([0]), np.asarray([4]))
