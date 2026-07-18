#!/usr/bin/env python3
"""Mutation checks for the open-period hits PA counterfactual."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.evaluation.hits_pa_counterfactual import (  # noqa: E402
    exponential_tilt_to_mean,
    infer_probability_lattice_denominator,
    infer_per_pa_hit_probability,
    pa_mean,
    probability_at_least_hits,
    realised_pa_distribution,
    require_exact_key_set,
    require_locked_float,
    reject_holdout_dates,
    validate_mean_aligned_distribution,
)


def main() -> int:
    dist = {1: 0.1, 3: 0.3, 4: 0.6}
    q = 0.247
    p1 = probability_at_least_hits(q, dist, 1)
    p2 = probability_at_least_hits(q, dist, 2)
    recovered = infer_per_pa_hit_probability(p1, dist)
    assert abs(recovered - q) < 1e-10
    assert abs(probability_at_least_hits(recovered, dist, 2) - p2) < 1e-10
    print("[OK] per-PA rate inversion recovers the generating rate and P(H>=2)")

    # M1: the common mutation that treats the 1.5 line as P(H>=1) must move
    # the answer.  If this does not fire, the line-specific experiment is fake.
    assert abs(p2 - p1) > 0.1
    print("[OK] MUTATION P(H>=2) replaced by P(H>=1) is caught")

    target = 3.25
    tilted = exponential_tilt_to_mean(dist, target)
    assert abs(sum(tilted.values()) - 1.0) < 1e-12
    assert set(tilted) == set(dist)
    assert abs(pa_mean(tilted) - target) < 1e-10
    print("[OK] mean-aligned tilt preserves support, normalizes, and reaches target")

    # M2: an unnormalised or wrong-mean output would violate both invariants.
    broken = dict(tilted)
    broken[4] += 0.1
    caught = False
    try:
        validate_mean_aligned_distribution(dist, broken, target)
    except ValueError as exc:
        caught = "normalized" in str(exc) or "target" in str(exc)
    assert caught
    print("[OK] MUTATION broken mean-aligned weights are caught")

    oracle = realised_pa_distribution(5)
    assert oracle == {5: 1.0}
    assert probability_at_least_hits(q, oracle, 2) > 0.0
    print("[OK] realised-PA oracle is a single postgame PA state")

    lattice = np.asarray([1 / 8000, 123 / 8000, 4000 / 8000, 7999 / 8000])
    assert infer_probability_lattice_denominator(lattice) == 8000
    caught = False
    try:
        infer_probability_lattice_denominator(np.append(lattice, 0.123456789))
    except ValueError as exc:
        caught = "lattice" in str(exc) or "denominator" in str(exc)
    assert caught
    print("[OK] immutable probability lattice recovers 8,000 draws")
    print("[OK] MUTATION off-lattice probability hard-fails")

    reject_holdout_dates(["2026-03-25", "2026-04-30"], "2026-05-01")
    caught = False
    try:
        reject_holdout_dates(["2026-04-30", "2026-05-01"], "2026-05-01")
    except ValueError as exc:
        caught = "holdout" in str(exc)
    assert caught
    print("[OK] MUTATION a May date hard-fails")

    # M3/M4 are artifact-boundary invariants: exact key count and fixed policy
    # value are equality checks, not tolerant comparisons.
    expected_keys = {(1, 10, "hits", 0.5), (1, 10, "hits", 1.5)}
    missing_key_mutation = {next(iter(expected_keys))}
    caught = False
    try:
        require_exact_key_set(missing_key_mutation, expected_keys, "fixture")
    except ValueError as exc:
        caught = "key mismatch" in str(exc)
    assert caught
    locked_threshold = 0.032116815573421054
    caught = False
    try:
        require_locked_float(locked_threshold + 0.001, locked_threshold, "threshold")
    except ValueError as exc:
        caught = "locked" in str(exc)
    assert caught
    print("[OK] MUTATION dropping a key fails exact-set equality")
    print("[OK] MUTATION changing the locked policy threshold fails exact equality")
    print("10/10")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
