"""Locked hierarchical shrinkage for source-bound PA opportunity.

This module only combines a time-safe pooled PA PMF and nine time-safe slot
PMFs. It never loads outcomes, chooses a weight, or mutates the v1 artifact.
"""
from __future__ import annotations

import math
from typing import Any, Mapping


class HierarchicalPAVolumeError(ValueError):
    """A PA distribution or locked shrinkage weight is invalid."""


SUPPORT = tuple(range(0, 8))
SLOTS = tuple(range(1, 10))


def _probability_map(value: Any, label: str) -> dict[int, float]:
    if not isinstance(value, Mapping):
        raise HierarchicalPAVolumeError(f"{label} must be a probability map")
    try:
        parsed = {int(state): float(probability) for state, probability in value.items()}
    except (TypeError, ValueError) as exc:
        raise HierarchicalPAVolumeError(f"{label} is malformed") from exc
    if not parsed or not set(parsed) <= set(SUPPORT):
        raise HierarchicalPAVolumeError(f"{label} support must be a nonempty subset of PA 0 through 7")
    if any(not math.isfinite(item) or item < 0.0 for item in parsed.values()):
        raise HierarchicalPAVolumeError(f"{label} has invalid mass")
    if not math.isclose(sum(parsed.values()), 1.0, rel_tol=0.0, abs_tol=1e-12):
        raise HierarchicalPAVolumeError(f"{label} does not sum to one")
    return {state: parsed.get(state, 0.0) for state in SUPPORT}


def shrink_slot_distributions(
    *,
    pooled: Mapping[int | str, float],
    by_slot: Mapping[int | str, Mapping[int | str, float]],
    weight: float,
) -> dict[int, dict[int, float]]:
    """Apply one global convex weight to all nine complete slot PMFs."""
    if isinstance(weight, bool) or not isinstance(weight, (int, float)):
        raise HierarchicalPAVolumeError("weight must be numeric")
    parsed_weight = float(weight)
    if not math.isfinite(parsed_weight) or not 0.0 <= parsed_weight <= 1.0:
        raise HierarchicalPAVolumeError("weight must be in [0,1]")
    pooled_probability = _probability_map(pooled, "pooled")
    if not isinstance(by_slot, Mapping):
        raise HierarchicalPAVolumeError("by_slot must be a mapping")
    parsed_slots: dict[int, Mapping[int | str, float]] = {}
    for slot, probability in by_slot.items():
        try:
            parsed_slot = int(slot)
        except (TypeError, ValueError) as exc:
            raise HierarchicalPAVolumeError("slot identity is malformed") from exc
        if parsed_slot in parsed_slots:
            raise HierarchicalPAVolumeError("duplicate slot identity")
        parsed_slots[parsed_slot] = probability
    if set(parsed_slots) != set(SLOTS):
        raise HierarchicalPAVolumeError("all and only slots 1 through 9 are required")
    result = {}
    for slot in SLOTS:
        raw = _probability_map(parsed_slots[slot], f"slot {slot}")
        result[slot] = {
            state: parsed_weight * raw[state] + (1.0 - parsed_weight) * pooled_probability[state]
            for state in SUPPORT
        }
        _probability_map(result[slot], f"hierarchical slot {slot}")
    return result
