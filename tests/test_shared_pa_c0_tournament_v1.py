from __future__ import annotations

import math

import pytest

from src.evaluation.shared_pa_c0_tournament_v1 import (
    C0TournamentError,
    MARKET_INCREMENTS,
    _market_pmf,
    _market_record,
    _mix,
)


def test_exact_market_pmf_reconciles_to_one_and_to_its_mean() -> None:
    per_pa = {
        "strikeout": 0.20, "non_intentional_walk": 0.08, "intentional_walk": 0.01,
        "hit_by_pitch": 0.01, "home_run": 0.04, "single": 0.16, "double": 0.05,
        "triple": 0.01, "bip_out": 0.40, "sac_fly": 0.02, "sac_bunt": 0.01,
        "catcher_interference": 0.0, "other_official_pa": 0.01,
    }
    pa = {0: 0.05, 1: 0.10, 2: 0.20, 3: 0.25, 4: 0.25, 5: 0.10, 6: 0.04, 7: 0.01}
    for increments in MARKET_INCREMENTS.values():
        pmf = _market_pmf(per_pa, pa, increments)
        assert math.isclose(sum(pmf), 1.0, rel_tol=0.0, abs_tol=1e-12)
        assert all(value >= 0.0 for value in pmf)


def test_opportunity_mixture_rejects_mass_loss() -> None:
    with pytest.raises(C0TournamentError, match="does not sum"):
        _mix([(0.5, {0: 1.0, 1: 0.0})], (0, 1))


def test_every_serialized_market_keeps_its_identity_and_pmf_route() -> None:
    per_pa = {name: 0.0 for name in (
        "strikeout", "non_intentional_walk", "intentional_walk", "hit_by_pitch", "home_run",
        "single", "double", "triple", "bip_out", "sac_fly", "sac_bunt", "catcher_interference", "other_official_pa",
    )}
    per_pa["bip_out"] = 1.0
    record = _market_record(per_pa, {0: 0.0, 1: 1.0})
    assert set(record) == set(MARKET_INCREMENTS)
    for market, value in record.items():
        assert value["market"] == market
        assert math.isclose(value["projected_value"], 0.0, abs_tol=1e-12)
        assert math.isclose(value["threshold_probabilities"]["over_0_5"], 0.0, abs_tol=1e-12)
