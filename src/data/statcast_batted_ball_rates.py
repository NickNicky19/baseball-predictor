"""Deterministic Statcast batted-ball rate derivations.

The functions in this module contain no fitted coefficients.  They translate
public Statcast classifications into rates over batted balls and return
``None`` when the required evidence is unavailable.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd


def hard_hit_rate(batted_balls: pd.DataFrame) -> Optional[float]:
    """Return the share of measured batted balls hit at least 95 mph."""
    if batted_balls.empty or "launch_speed" not in batted_balls.columns:
        return None
    exit_velocity = pd.to_numeric(batted_balls["launch_speed"], errors="coerce")
    valid = exit_velocity.notna()
    if not valid.any():
        return None
    return float((exit_velocity[valid] >= 95.0).mean())


def barrel_rate(batted_balls: pd.DataFrame) -> Optional[float]:
    """Return Statcast barrel rate over batted balls.

    Prefer Statcast's explicit classification, then its launch-speed-angle
    bucket 6.  The final branch preserves the project's existing public
    EV/launch-angle approximation for older exports lacking both fields.
    """
    if batted_balls.empty:
        return None

    denominator = len(batted_balls)
    if "barrel" in batted_balls.columns:
        explicit = pd.to_numeric(batted_balls["barrel"], errors="coerce")
        if explicit.notna().any():
            return float(explicit.fillna(0).astype(bool).sum() / denominator)

    if "launch_speed_angle" in batted_balls.columns:
        bucket = pd.to_numeric(
            batted_balls["launch_speed_angle"], errors="coerce"
        )
        if bucket.notna().any():
            return float((bucket == 6).sum() / denominator)

    required = {"launch_speed", "launch_angle"}
    if required.issubset(batted_balls.columns):
        exit_velocity = pd.to_numeric(
            batted_balls["launch_speed"], errors="coerce"
        )
        launch_angle = pd.to_numeric(
            batted_balls["launch_angle"], errors="coerce"
        )
        valid = exit_velocity.notna() & launch_angle.notna()
        if not valid.any():
            return None
        barrel = (
            valid
            & (exit_velocity >= 98.0)
            & (launch_angle >= 26.0 - (exit_velocity - 98.0))
            & (launch_angle <= 30.0 + (exit_velocity - 98.0))
        )
        return float(barrel.sum() / valid.sum())

    return None
