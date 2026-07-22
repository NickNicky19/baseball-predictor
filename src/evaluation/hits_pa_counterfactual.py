"""Pure helpers for the open-period hits PA counterfactual.

This module does not load files, select a policy, or authorize a model change.
It only transforms an already verified per-slot PA distribution into exact
binomial-mixture hit probabilities.  Keeping the math here makes the offline
mutation harness exercise the same functions as the artifact-producing audit.
"""
from __future__ import annotations

from math import comb
from math import lcm
from fractions import Fraction
from typing import Mapping

import numpy as np


PADistribution = dict[int, float]


def require_exact_key_set(observed: set[tuple], expected: set[tuple], label: str) -> None:
    if observed != expected:
        missing = list(expected - observed)[:5]
        extra = list(observed - expected)[:5]
        raise ValueError(f"{label} key mismatch; missing={missing}, extra={extra}")


def require_locked_float(observed: float, expected: float, label: str) -> float:
    value = float(observed)
    locked = float(expected)
    if not np.isfinite(value) or not np.isfinite(locked) or value != locked:
        raise ValueError(f"{label} differs from its predeclared locked value")
    return value


def infer_probability_lattice_denominator(
    probabilities: list[float] | np.ndarray,
    *,
    maximum_denominator: int = 100_000,
) -> int:
    """Infer the smallest shared empirical-probability draw denominator.

    Reconstruction clips exact zero/one to 1e-6/1-1e-6, so those two sentinel
    values are excluded.  Every remaining probability must lie exactly on the
    inferred lattice; otherwise the draw count is not recoverable from the
    immutable artifact and the caller must fail rather than consult a changed
    current config.
    """

    values = np.asarray(probabilities, dtype=float)
    if values.size == 0 or (~np.isfinite(values)).any() or (
        (values < 0.0) | (values > 1.0)
    ).any():
        raise ValueError("probability lattice input is empty or invalid")
    interior = values[(values > 1e-6) & (values < 1.0 - 1e-6)]
    if interior.size == 0:
        raise ValueError("probability lattice has no unclipped interior values")
    denominator = 1
    for value in interior:
        fraction = Fraction(str(float(value))).limit_denominator(maximum_denominator)
        if abs(float(fraction) - float(value)) > 1e-12:
            raise ValueError("probability is not recoverable on an exact finite lattice")
        denominator = lcm(denominator, fraction.denominator)
        if denominator > maximum_denominator:
            raise ValueError("probability lattice denominator exceeds the declared limit")
    scaled = interior * denominator
    if not np.allclose(scaled, np.rint(scaled), atol=1e-9, rtol=0.0):
        raise ValueError("probabilities do not share one exact empirical lattice")
    return int(denominator)


def normalise_pa_distribution(raw: Mapping[int | str, float]) -> PADistribution:
    """Return a finite, positive-mass discrete PA distribution."""

    if not raw:
        raise ValueError("PA distribution is empty")
    parsed: PADistribution = {}
    for raw_pa, raw_weight in raw.items():
        pa = int(raw_pa)
        weight = float(raw_weight)
        if pa < 0 or not np.isfinite(weight) or weight < 0.0:
            raise ValueError("PA distribution has an invalid state or weight")
        parsed[pa] = parsed.get(pa, 0.0) + weight
    total = float(sum(parsed.values()))
    if not np.isfinite(total) or total <= 0.0:
        raise ValueError("PA distribution has no positive finite mass")
    return {pa: weight / total for pa, weight in sorted(parsed.items()) if weight > 0.0}


def pa_mean(distribution: Mapping[int | str, float]) -> float:
    dist = normalise_pa_distribution(distribution)
    return float(sum(pa * weight for pa, weight in dist.items()))


def probability_at_least_hits(
    per_pa_hit_probability: float,
    distribution: Mapping[int | str, float],
    threshold: int,
) -> float:
    """Exact P(H >= threshold) for H | PA=n ~ Binomial(n, q)."""

    q = float(per_pa_hit_probability)
    if not np.isfinite(q) or not 0.0 <= q <= 1.0:
        raise ValueError("per-PA hit probability must be finite in [0, 1]")
    if isinstance(threshold, bool) or int(threshold) != threshold or threshold < 1:
        raise ValueError("hit threshold must be a positive integer")
    k = int(threshold)
    dist = normalise_pa_distribution(distribution)
    probability = 0.0
    for pa, weight in dist.items():
        if pa < k:
            continue
        below = sum(
            comb(pa, hits) * q**hits * (1.0 - q) ** (pa - hits)
            for hits in range(k)
        )
        probability += weight * (1.0 - below)
    return float(np.clip(probability, 0.0, 1.0))


def infer_per_pa_hit_probability(
    probability_ge_one: float,
    distribution: Mapping[int | str, float],
) -> float:
    """Invert exact P(H >= 1) under a fixed PA distribution."""

    target = float(probability_ge_one)
    if not np.isfinite(target) or not 0.0 <= target <= 1.0:
        raise ValueError("P(H >= 1) must be finite in [0, 1]")
    dist = normalise_pa_distribution(distribution)
    if target == 0.0:
        return 0.0
    attainable = probability_at_least_hits(1.0, dist, 1)
    if target > attainable + 1e-12:
        raise ValueError("P(H >= 1) is outside the PA distribution's support")
    if target >= attainable:
        return 1.0
    lo, hi = 0.0, 1.0
    for _ in range(80):
        mid = (lo + hi) / 2.0
        if probability_at_least_hits(mid, dist, 1) < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def exponential_tilt_to_mean(
    distribution: Mapping[int | str, float], target_mean: float
) -> PADistribution:
    """KL-minimum exponential tilt whose mean equals ``target_mean``.

    The support and relative ordering of the fitted distribution are retained.
    Only the one sufficient statistic named by this counterfactual--mean PA--
    is changed.
    """

    base = normalise_pa_distribution(distribution)
    target = float(target_mean)
    states = np.asarray(list(base), dtype=float)
    weights = np.asarray([base[int(pa)] for pa in states], dtype=float)
    if not np.isfinite(target) or target < states.min() or target > states.max():
        raise ValueError("target PA mean lies outside the fitted support")
    current = float(np.dot(states, weights))
    if abs(target - current) <= 1e-13:
        return base

    def tilted(lam: float) -> tuple[np.ndarray, float]:
        logits = np.log(weights) + lam * states
        logits -= logits.max()
        out = np.exp(logits)
        out /= out.sum()
        return out, float(np.dot(states, out))

    lo, hi = -1.0, 1.0
    while tilted(lo)[1] > target:
        lo *= 2.0
    while tilted(hi)[1] < target:
        hi *= 2.0
    for _ in range(100):
        mid = (lo + hi) / 2.0
        if tilted(mid)[1] < target:
            lo = mid
        else:
            hi = mid
    out, achieved = tilted((lo + hi) / 2.0)
    result = {int(pa): float(weight) for pa, weight in zip(states, out, strict=True)}
    validate_mean_aligned_distribution(base, result, target)
    return result


def validate_mean_aligned_distribution(
    base: Mapping[int | str, float],
    aligned: Mapping[int | str, float],
    target_mean: float,
) -> None:
    """Validate the output without silently normalizing a broken mutation."""

    base_dist = normalise_pa_distribution(base)
    raw = {int(pa): float(weight) for pa, weight in aligned.items()}
    if set(raw) != set(base_dist):
        raise ValueError("mean-aligned PA distribution changed support")
    if any(not np.isfinite(weight) or weight < 0.0 for weight in raw.values()):
        raise ValueError("mean-aligned PA distribution has invalid weight")
    if abs(sum(raw.values()) - 1.0) > 1e-12:
        raise ValueError("mean-aligned PA distribution is not normalized")
    achieved = sum(pa * weight for pa, weight in raw.items())
    if abs(achieved - float(target_mean)) > 1e-10:
        raise ValueError("mean-aligned PA distribution missed its target")


def realised_pa_distribution(official_pa: int | float) -> PADistribution:
    value = float(official_pa)
    if not np.isfinite(value) or value < 0.0 or not value.is_integer():
        raise ValueError("official PA must be a non-negative integer")
    return {int(value): 1.0}


def reject_holdout_dates(dates: list[str] | tuple[str, ...], holdout_start: str) -> None:
    rendered = [str(value) for value in dates]
    if not rendered or any(value >= str(holdout_start) for value in rendered):
        raise ValueError("sealed holdout date entered the PA counterfactual")
