"""Deterministic Statcast batted-ball rate derivations.

The functions in this module contain no fitted coefficients.  They translate
public Statcast classifications into rates over batted balls and return
``None`` when the required evidence is unavailable.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from src.data.statcast_integrity import derive_batted_ball_evidence


def hard_hit_rate(batted_balls: pd.DataFrame) -> Optional[float]:
    """Return hard-hit rate over the shared measured-EV population."""
    if batted_balls.empty or "launch_speed" not in batted_balls.columns:
        return None
    exit_velocity = pd.to_numeric(batted_balls["launch_speed"], errors="coerce")
    measured = exit_velocity.notna() & (exit_velocity > 0.0)
    if not bool(measured.any()):
        return None
    return float(exit_velocity[measured].ge(95.0).mean())


def barrel_rate(batted_balls: pd.DataFrame) -> Optional[float]:
    """Return barrel rate over the shared measured-EV population.

    Sparse ``barrel`` columns and geometric approximations are intentionally
    not accepted as authoritative Statcast labels.
    """
    evidence = derive_batted_ball_evidence(batted_balls)
    return None if evidence is None else evidence.barrel_rate
