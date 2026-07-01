"""
Statcast feature engineering for hitters.

Transforms raw Statcast/Savant inputs into StatcastProfile objects and
enriches them with league-centered context. Keeps logic isolated from the
data ingestion and prediction layers for backtestability.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Optional

import pandas as pd

from src.data.savant import SavantClient
from src.data.statcast_distributions import StatcastDistributionBuilder
from src.models.dataclasses import (
    HitterGameContext,
    LeagueBaselines,
    StatcastProfile,
)
from src.utils.logging import get_logger

logger = get_logger(__name__)


class StatcastFeatureEngine:
    """
    Builds and enriches StatcastProfile objects for daily hitter slates.

    All player-level defaults flow through LeagueBaselines via SavantClient;
    this layer only aggregates, validates, and annotates profiles.
    """

    def __init__(
        self,
        league_baselines: Optional[LeagueBaselines] = None,
        lookback_days: int = 45,
        min_pa: int = 8,
    ):
        self.league = league_baselines or LeagueBaselines()
        self.savant = SavantClient(
            league_baselines=self.league,
            lookback_days=lookback_days,
            min_pa=min_pa,
        )

    def build_profiles_from_statcast_df(
        self, statcast_df: pd.DataFrame
    ) -> dict[int, StatcastProfile]:
        """Aggregate a Statcast DataFrame into player profiles with distributions."""
        raw = self.savant.build_hitter_profiles_from_statcast(statcast_df)
        dist_builder = StatcastDistributionBuilder()
        distributions = dist_builder.build_from_statcast_df(statcast_df)
        merged = dist_builder.attach_to_profiles(raw, distributions)
        return {pid: self.enrich_profile(p) for pid, p in merged.items()}

    def build_profiles_for_date(
        self,
        game_date: Optional[str] = None,
        savant_csv_path: Optional[str] = None,
    ) -> dict[int, StatcastProfile]:
        """
        Build profiles for a date from live Statcast and/or a local Savant CSV.

        CSV profiles override live Statcast when both exist for the same player.
        """
        profiles: dict[int, StatcastProfile] = {}

        statcast_df = self.savant.fetch_statcast_range(end_date=game_date)
        if not statcast_df.empty:
            profiles.update(self.build_profiles_from_statcast_df(statcast_df))

        if savant_csv_path:
            csv_profiles = self.savant.build_hitter_profiles_from_csv(savant_csv_path)
            profiles.update({pid: self.enrich_profile(p) for pid, p in csv_profiles.items()})

        logger.info("Built %d Statcast profiles for %s", len(profiles), game_date)
        return profiles

    def resolve_profile_for_hitter(
        self,
        hitter: HitterGameContext,
        profiles: dict[int, StatcastProfile],
    ) -> StatcastProfile:
        """Return the best available profile for a hitter (Statcast or league fallback)."""
        player_id = hitter.player.mlb_id
        if player_id in profiles:
            profile = profiles[player_id]
            if profile.player_name in ("", "Unknown"):
                profile = replace(profile, player_name=hitter.player.name)
            return self.enrich_profile(profile)
        return self.enrich_profile(
            self.savant.league_fallback_profile(player_id, hitter.player.name)
        )

    def build_profiles_for_hitters(
        self,
        hitters: list[HitterGameContext],
        game_date: Optional[str] = None,
        savant_csv_path: Optional[str] = None,
    ) -> dict[int, StatcastProfile]:
        """Build or resolve StatcastProfile for every hitter on a slate."""
        pool = self.build_profiles_for_date(game_date, savant_csv_path)
        return {
            h.player.mlb_id: self.resolve_profile_for_hitter(h, pool)
            for h in hitters
        }

    def enrich_profile(self, profile: StatcastProfile) -> StatcastProfile:
        """
        Apply league fallback for missing fields and attach derived quality flags.

        Derived fields are stored in a serializable annotation pattern via
        sample_pa and has_advanced_data(); no arbitrary coefficients added here.
        """
        enriched = self.savant.apply_league_fallback(profile)
        return replace(
            enriched,
            k_rate=enriched.k_rate if enriched.k_rate is not None else self.league.k_pct / 100.0,
            bb_rate=enriched.bb_rate if enriched.bb_rate is not None else self.league.bb_pct / 100.0,
        )

    def profiles_to_dataframe(self, profiles: dict[int, StatcastProfile]) -> pd.DataFrame:
        """Flatten profiles to a DataFrame for inspection and parquet export."""
        rows = []
        for profile in profiles.values():
            rows.append(
                {
                    "player_id": profile.player_id,
                    "player_name": profile.player_name,
                    "sample_pa": profile.sample_pa,
                    "has_advanced_data": profile.has_advanced_data(),
                    "xwoba": profile.xwoba,
                    "xba": profile.xba,
                    "xslg": profile.xslg,
                    "barrel_rate": profile.barrel_rate,
                    "sweet_spot_rate": profile.sweet_spot_rate,
                    "hard_hit_rate": profile.hard_hit_rate,
                    "avg_exit_velocity": profile.avg_exit_velocity,
                    "avg_launch_angle": profile.avg_launch_angle,
                    "chase_rate": profile.chase_rate,
                    "contact_rate": profile.contact_rate,
                    "whiff_rate": profile.whiff_rate,
                    "swing_rate": profile.swing_rate,
                    "zone_rate": profile.zone_rate,
                    "k_rate": profile.k_rate,
                    "bb_rate": profile.bb_rate,
                }
            )
        return pd.DataFrame(rows)