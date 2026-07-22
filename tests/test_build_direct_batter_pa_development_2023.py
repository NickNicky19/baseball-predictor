from __future__ import annotations

import pandas as pd
import pytest

from scripts.build_direct_batter_pa_development_2023 import add_composition


def frame(**updates) -> pd.DataFrame:
    row = {
        "history_batted_ball_denominator": 100,
        "history_barrel_count": 8,
        "history_hard_hit_count": 42,
        "history_pa": 250,
    }
    row.update(updates)
    return pd.DataFrame([row])


def test_add_composition_preserves_independent_count_partition() -> None:
    out = add_composition(frame()).iloc[0]
    assert out.history_hard_hit_non_barrel_count == 34
    assert out.history_other_measured_bbe_count == 58
    assert out.history_barrel_share_bbe == 0.08
    assert out.history_measured_bbe_per_pa == 0.4
    assert out.history_barrel_count + out.history_hard_hit_non_barrel_count + out.history_other_measured_bbe_count == out.history_batted_ball_denominator


def test_add_composition_rejects_kwan_shaped_mutation() -> None:
    with pytest.raises(ValueError, match="ordering"):
        add_composition(frame(history_barrel_count=50, history_hard_hit_count=9))


def test_add_composition_preserves_all_missing_as_missing() -> None:
    out = add_composition(frame(
        history_batted_ball_denominator=None,
        history_barrel_count=None,
        history_hard_hit_count=None,
        history_pa=0,
    )).iloc[0]
    assert pd.isna(out.history_barrel_share_bbe)
