"""
Legacy Statcast profile builder for hitters (production path).

Builds ``StatcastProfile`` objects from Savant/Statcast data for DailyPredictor
and FeatureFactory. The modular ML feature layer lives in ``src/features/ml/``;
see ``RichFeatureEnricher`` for additive rich features on top of profiles.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from typing import Optional

import pandas as pd

from src.data.savant import SavantClient
from src.data.statcast_source_contract import validate_statcast_source_lineage
from src.data.statcast_distributions import StatcastDistributionBuilder
from src.evaluation.hits_contact_adapter import (
    HitsContactAdapterSettings,
    build_contact_adapter_evidence,
)
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
        derive_batted_ball_rates: bool = False,
        config: Optional[dict] = None,
    ):
        self.league = league_baselines or LeagueBaselines()
        self.derive_batted_ball_rates = bool(derive_batted_ball_rates)
        self.savant = SavantClient(
            league_baselines=self.league,
            lookback_days=lookback_days,
            min_pa=min_pa,
            derive_batted_ball_rates=self.derive_batted_ball_rates,
        )
        self.hits_contact_adapter = HitsContactAdapterSettings.from_config(
            config or {}
        )
        self._hits_contact_evidence: dict[int, dict] = {}

    def build_profiles_from_statcast_df(
        self, statcast_df: pd.DataFrame
    ) -> dict[int, StatcastProfile]:
        """Aggregate a Statcast DataFrame into player profiles with distributions."""
        # Self-calibrate the league contact-conditional baselines from the same
        # pull that builds the profiles. StatcastProfile.xwoba/xslg are averaged
        # over batted balls, so the simulator must center them against the
        # league's batted-ball average — not the season-level xwoba/xslg. This
        # derives that baseline from data instead of a hand-picked constant.
        self._calibrate_contact_baselines(statcast_df)

        raw = self.savant.build_hitter_profiles_from_statcast(statcast_df)
        dist_builder = StatcastDistributionBuilder(
            derive_batted_ball_rates=self.derive_batted_ball_rates
        )
        distributions = dist_builder.build_from_statcast_df(statcast_df)
        merged = dist_builder.attach_to_profiles(raw, distributions)
        return {pid: self.enrich_profile(p) for pid, p in merged.items()}

    def _calibrate_contact_baselines(self, statcast_df: pd.DataFrame) -> None:
        """Derive league contact-conditional xwOBA/xSLG from the Statcast pull
        and update self.league in place (immutably via replace).

        Falls back to the existing league values if the columns are missing or
        empty, so this never degrades behavior when data is sparse.
        """
        col_woba = "estimated_woba_using_speedangle"
        col_slg = "estimated_slg_using_speedangle"
        col_ba = "estimated_ba_using_speedangle"
        if col_woba not in statcast_df.columns or col_slg not in statcast_df.columns:
            return

        woba = pd.to_numeric(statcast_df[col_woba], errors="coerce").dropna()
        slg = pd.to_numeric(statcast_df[col_slg], errors="coerce").dropna()
        # Require a meaningful sample before overriding the configured baseline.
        if len(woba) < 500 or len(slg) < 500:
            return

        updates = {
            "xwoba_on_contact": float(woba.mean()),
            "xslg_on_contact": float(slg.mean()),
        }
        if col_ba in statcast_df.columns:
            ba = pd.to_numeric(statcast_df[col_ba], errors="coerce").dropna()
            if len(ba) >= 500:
                updates["xba_on_contact"] = float(ba.mean())

        new_league = replace(self.league, **updates)
        self.league = new_league
        self.savant.league = new_league
        logger.info(
            "Calibrated contact baselines from %d batted balls: "
            "xwoba_on_contact=%.3f xslg_on_contact=%.3f xba_on_contact=%.3f",
            len(slg),
            new_league.xwoba_on_contact,
            new_league.xslg_on_contact,
            new_league.xba_on_contact,
        )

    def build_profiles_for_date(
        self,
        game_date: Optional[str] = None,
        savant_csv_path: Optional[str] = None,
        active_player_ids: Optional[list[int]] = None,
    ) -> dict[int, StatcastProfile]:
        """
        Build pregame profiles from Statcast rows strictly before ``game_date``.

        CSV profiles override live Statcast when both exist for the same player.

        ``pybaseball.statcast`` treats its end date as inclusive. Passing the
        target game date therefore admits that game's completed batted balls
        into a historical reconstruction. Use the preceding calendar date so
        every profile is available before the target date begins. When no date
        is supplied, today's live slate follows the same fail-closed boundary.
        """
        profiles: dict[int, StatcastProfile] = {}

        target_date = date.fromisoformat(game_date) if game_date else date.today()
        if self.hits_contact_adapter is not None and game_date is None:
            raise ValueError("hits-contact candidate requires an explicit target date")
        if self.hits_contact_adapter is not None and savant_csv_path:
            raise ValueError(
                "hits-contact candidate cannot mix an unbound Savant CSV override"
            )
        last_completed_date = (target_date - timedelta(days=1)).isoformat()
        statcast_df = self.savant.fetch_statcast_range(end_date=last_completed_date)
        profiles.update(
            {
                player_id: replace(
                    profile,
                    source_window_end=last_completed_date,
                )
                for player_id, profile in self.build_profiles_from_statcast_df(
                    statcast_df
                ).items()
            }
        )
        for profile in profiles.values():
            validate_statcast_source_lineage(
                profile,
                context=f"StatcastFeatureEngine.build_profiles_for_date[{profile.player_id}]",
            )
        self._hits_contact_evidence = {}
        if self.hits_contact_adapter is not None:
            self._hits_contact_evidence = build_contact_adapter_evidence(
                statcast_df,
                active_player_ids=active_player_ids or [],
                target_date=target_date.isoformat(),
                settings=self.hits_contact_adapter,
            )

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
            if profile.player_id != player_id:
                raise ValueError(
                    f"Statcast profile identity mismatch: key={player_id}, "
                    f"profile.player_id={profile.player_id}"
                )
            if profile.player_name != hitter.player.name:
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
        active_player_ids = [h.player.mlb_id for h in hitters]
        pool = self.build_profiles_for_date(
            game_date,
            savant_csv_path,
            active_player_ids=active_player_ids,
        )
        resolved: dict[int, StatcastProfile] = {}
        for hitter in hitters:
            player_id = hitter.player.mlb_id
            profile = self.resolve_profile_for_hitter(hitter, pool)
            adapter = self._hits_contact_evidence.get(player_id)
            if adapter and adapter["status"] == "adapter_applied":
                profile = replace(profile, xba=float(adapter["fitted_contact_xba"]))
            resolved[player_id] = profile
        return resolved

    def hits_contact_evidence_for(self, player_id: int) -> Optional[dict]:
        """Return the factual candidate status for one active hitter, if enabled."""
        evidence = self._hits_contact_evidence.get(int(player_id))
        return dict(evidence) if evidence is not None else None

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
                    "source_kind": profile.source_kind,
                    "source_status": profile.source_status,
                    "source_window_end": profile.source_window_end,
                    "source_row_count": profile.source_row_count,
                    "fallback_fields": list(profile.fallback_fields),
                }
            )
        return pd.DataFrame(rows)
