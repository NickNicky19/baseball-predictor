"""
Statcast distribution builder — launch angle and exit velocity distributions per hitter.
"""

from __future__ import annotations

from dataclasses import replace

import pandas as pd

from src.data.statcast_batted_ball_rates import (
    barrel_rate as derive_barrel_rate,
    hard_hit_rate as derive_hard_hit_rate,
)
from src.data.statcast_integrity import derive_batted_ball_evidence, validate_rate_pair
from src.models.dataclasses import StatcastDistributionProfile, StatcastProfile


class StatcastDistributionBuilder:
    """Aggregates pitch-level Statcast into distribution profiles."""

    def __init__(self, derive_batted_ball_rates: bool = False):
        self.derive_batted_ball_rates = bool(derive_batted_ball_rates)

    def build_from_statcast_df(self, statcast_df: pd.DataFrame) -> dict[int, StatcastDistributionProfile]:
        if statcast_df.empty or "batter" not in statcast_df.columns:
            return {}

        df = statcast_df.copy()
        if "launch_speed" in df.columns:
            bip = df[df["launch_speed"].notna() & (df["launch_speed"] > 0)]
        else:
            bip = df

        profiles: dict[int, StatcastDistributionProfile] = {}
        for batter_id, group in bip.groupby("batter"):
            profiles[int(batter_id)] = self._aggregate_group(group)
        return profiles

    def attach_to_profiles(
        self,
        profiles: dict[int, StatcastProfile],
        distributions: dict[int, StatcastDistributionProfile],
    ) -> dict[int, StatcastProfile]:
        """Merge distribution data into existing StatcastProfile objects."""
        merged: dict[int, StatcastProfile] = {}
        for player_id, profile in profiles.items():
            dist = distributions.get(player_id)
            merged[player_id] = replace(profile, distribution=dist) if dist else profile
        return merged

    def _aggregate_group(self, group: pd.DataFrame) -> StatcastDistributionProfile:
        launch_speed = pd.to_numeric(group.get("launch_speed"), errors="coerce").dropna()
        launch_angle = pd.to_numeric(group.get("launch_angle"), errors="coerce").dropna()

        sample_bip = len(launch_speed)
        ev_mean = float(launch_speed.mean()) if not launch_speed.empty else 88.0
        ev_std = float(launch_speed.std(ddof=0)) if len(launch_speed) > 1 else 10.0
        la_mean = float(launch_angle.mean()) if not launch_angle.empty else 12.0
        la_std = float(launch_angle.std(ddof=0)) if len(launch_angle) > 1 else 18.0
        max_ev = float(launch_speed.max()) if not launch_speed.empty else 105.0

        evidence = derive_batted_ball_evidence(group)
        barrel_rate = evidence.barrel_rate if evidence is not None else None
        if barrel_rate is None:
            barrel_rate = 0.085
        sweet_spot = _sweet_spot_rate(group)
        hard_hit = evidence.hard_hit_rate if evidence is not None else None
        if hard_hit is None:
            hard_hit = 0.39
        validate_rate_pair(
            barrel_rate,
            hard_hit,
            context="StatcastDistributionProfile",
        )

        return StatcastDistributionProfile(
            launch_angle_mean=la_mean,
            launch_angle_std=la_std,
            exit_velocity_mean=ev_mean,
            exit_velocity_std=ev_std,
            max_exit_velocity=max_ev,
            sweet_spot_rate=sweet_spot,
            barrel_rate=barrel_rate,
            hard_hit_rate=hard_hit,
            sample_bip=sample_bip,
            batted_ball_denominator=(
                evidence.measured_batted_balls if evidence is not None else None
            ),
            barrel_count=evidence.barrel_count if evidence is not None else None,
            hard_hit_count=evidence.hard_hit_count if evidence is not None else None,
            batted_ball_rate_definition=(
                evidence.classification if evidence is not None else None
            ),
        )


def _rate_from_column(group: pd.DataFrame, col: str) -> float:
    if col not in group.columns:
        return 0.085
    series = pd.to_numeric(group[col], errors="coerce").dropna()
    if series.empty:
        return 0.085
    val = float(series.mean())
    return val / 100.0 if val > 1.0 else val


def _sweet_spot_rate(group: pd.DataFrame) -> float:
    if "launch_angle" not in group.columns:
        return 0.34
    la = pd.to_numeric(group["launch_angle"], errors="coerce")
    mask = la.between(8, 32)
    return float(mask.sum() / max(len(la.dropna()), 1))


def _hard_hit_rate(group: pd.DataFrame, launch_speed: pd.Series) -> float:
    if launch_speed.empty:
        return 0.39
    return float((launch_speed >= 95.0).sum() / len(launch_speed))
