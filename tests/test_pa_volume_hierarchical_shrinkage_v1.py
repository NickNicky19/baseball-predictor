from __future__ import annotations

import math

import pytest

from src.evaluation.pa_volume_hierarchical_shrinkage_v1 import (
    HierarchicalPAVolumeError,
    shrink_slot_distributions,
)


def fixture():
    pooled = {state: (1.0 if state == 4 else 0.0) for state in range(8)}
    by_slot = {
        slot: {state: (1.0 if state == (5 if slot == 1 else 4) else 0.0) for state in range(8)}
        for slot in range(1, 10)
    }
    return pooled, by_slot


def test_endpoints_and_convex_mean_are_exact():
    pooled, by_slot = fixture()
    at_zero = shrink_slot_distributions(pooled=pooled, by_slot=by_slot, weight=0.0)
    at_one = shrink_slot_distributions(pooled=pooled, by_slot=by_slot, weight=1.0)
    middle = shrink_slot_distributions(pooled=pooled, by_slot=by_slot, weight=0.2)
    assert at_zero[1] == pooled
    assert at_one[1] == by_slot[1]
    assert math.isclose(sum(state * mass for state, mass in middle[1].items()), 4.2)
    assert 0 in middle[1] and math.isclose(sum(middle[1].values()), 1.0)


def test_one_weight_governs_every_slot_and_invalid_shapes_fail_closed():
    pooled, by_slot = fixture()
    result = shrink_slot_distributions(pooled=pooled, by_slot=by_slot, weight=0.2)
    assert result[1][5] == pytest.approx(0.2)
    assert result[2][4] == pytest.approx(1.0)
    with pytest.raises(HierarchicalPAVolumeError):
        shrink_slot_distributions(pooled=pooled, by_slot={1: by_slot[1]}, weight=0.2)
    with pytest.raises(HierarchicalPAVolumeError):
        shrink_slot_distributions(pooled=pooled, by_slot=by_slot, weight=1.01)
