"""
Feature vector builder — rich, interacting features for HR/HRR modeling.

All features are league-centered z-scores, ratios, or products derived from
LeagueBaselines and configurable context. No hand-tuned coefficients.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import combinations
from typing import Any, Optional

from src.data.mlb_api import HittingStatsSnapshot
from src.models.dataclasses import (
    FeatureVector,
    LeagueBaselines,
    MatchupContext,
    ParkFactors,
    PitcherStatcastProfile,
    PlayerFeatureBundle,
    StatcastDistributionProfile,
    StatcastProfile,
    WeatherContext,
)


@dataclass(frozen=True)
class FeatureVectorSettings:
    """Controls interaction expansion depth (from config feature_factory block)."""

    interaction_order: int = 2
    max_interaction_features: int = 120
    include_distribution_percentiles: bool = True

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> FeatureVectorSettings:
        block = config.get("feature_factory", {})
        return cls(
            interaction_order=int(block.get("interaction_order", 2)),
            max_interaction_features=int(block.get("max_interaction_features", 120)),
            include_distribution_percentiles=bool(
                block.get("include_distribution_percentiles", True)
            ),
        )


class FeatureVectorBuilder:
    """
    Builds a FeatureVector from a PlayerFeatureBundle.

    Produces 100+ features via systematic z-scoring, distribution metrics,
    matchup context, and pairwise interactions among top signal features.
    """

    STATCAST_Z_FIELDS: tuple[str, ...] = (
        "xwoba",
        "xba",
        "xslg",
        "barrel_rate",
        "sweet_spot_rate",
        "hard_hit_rate",
        "avg_exit_velocity",
        "avg_launch_angle",
        "chase_rate",
        "contact_rate",
        "whiff_rate",
        "swing_rate",
        "zone_rate",
        "k_rate",
        "bb_rate",
    )

    LEAGUE_ANCHORS: dict[str, tuple[str, float]] = {
        "xwoba": ("xwoba", 0.08),
        "xba": ("xwoba", 0.06),
        "xslg": ("xslg", 0.08),
        "barrel_rate": ("barrel_rate", 1.0),
        "sweet_spot_rate": ("sweet_spot_rate", 1.0),
        "hard_hit_rate": ("hard_hit_rate", 1.0),
        "avg_exit_velocity": ("hard_hit_rate", 12.0),
        "avg_launch_angle": ("sweet_spot_rate", 15.0),
        "chase_rate": ("chase_rate", 1.0),
        "contact_rate": ("contact_rate", 1.0),
        "whiff_rate": ("whiff_rate", 1.0),
        "swing_rate": ("swing_rate", 1.0),
        "zone_rate": ("zone_rate", 1.0),
        "k_rate": ("k_pct", 0.01),
        "bb_rate": ("bb_pct", 0.01),
    }

    INTERACTION_KEYS: tuple[str, ...] = (
        "z_xwoba",
        "z_xslg",
        "z_barrel_rate",
        "z_hard_hit_rate",
        "z_contact_rate",
        "z_whiff_rate",
        "m_platoon_ops_z",
        "m_bvp_ops_factor",
        "m_recent_form",
        "p_k_rate_z",
        "p_bb_rate_z",
        "ctx_park_hr",
        "ctx_park_hits",
        "ctx_weather_hr",
        "dist_quality",
    )

    def __init__(
        self,
        league_baselines: Optional[LeagueBaselines] = None,
        settings: Optional[FeatureVectorSettings] = None,
        config: Optional[dict[str, Any]] = None,
    ):
        self.config = config or {}
        self.league = league_baselines or LeagueBaselines.from_config(self.config)
        self.settings = settings or FeatureVectorSettings.from_config(self.config)

    def build(
        self,
        bundle: PlayerFeatureBundle,
        season_hitting: Optional[HittingStatsSnapshot] = None,
        recent_hitting: Optional[HittingStatsSnapshot] = None,
    ) -> FeatureVector:
        values: dict[str, float] = {}
        groups: dict[str, list[str]] = {}

        self._add_statcast_z(values, groups, bundle.statcast)
        self._add_distribution(values, groups, bundle.statcast.distribution)
        self._add_matchup(values, groups, bundle.matchup)
        self._add_pitcher(values, groups, bundle.pitcher_statcast)
        self._add_context(values, groups, bundle)
        self._add_trends(values, groups, season_hitting, recent_hitting)
        self._add_interactions(values, groups)
        self._add_pitcher_hitter_crosses(values, groups)

        return FeatureVector(values=values, groups=groups)

    def _add_statcast_z(
        self,
        values: dict[str, float],
        groups: dict[str, list[str]],
        profile: StatcastProfile,
    ) -> None:
        keys: list[str] = []
        for field in self.STATCAST_Z_FIELDS:
            raw = getattr(profile, field, None)
            if raw is None:
                continue
            anchor_name, scale_mult = self.LEAGUE_ANCHORS[field]
            anchor = getattr(self.league, anchor_name, 0.0)
            if field in ("k_rate", "bb_rate"):
                anchor = anchor / 100.0
            denom = max(abs(anchor) * scale_mult, 0.001)
            key = f"z_{field}"
            values[key] = round((raw - anchor) / denom, 6)
            keys.append(key)
        groups["statcast_z"] = keys

    def _add_distribution(
        self,
        values: dict[str, float],
        groups: dict[str, list[str]],
        dist: Optional[StatcastDistributionProfile],
    ) -> None:
        keys: list[str] = []
        if not dist or dist.sample_bip <= 0:
            groups["distribution"] = keys
            return

        lg = self.league
        ev_anchor = 88.0
        la_anchor = 12.0
        values["dist_ev_mean_z"] = round((dist.exit_velocity_mean - ev_anchor) / 12.0, 6)
        values["dist_ev_std_z"] = round((dist.exit_velocity_std - 10.0) / 8.0, 6)
        values["dist_la_mean_z"] = round((dist.launch_angle_mean - la_anchor) / 15.0, 6)
        values["dist_la_std_z"] = round((dist.launch_angle_std - 18.0) / 12.0, 6)
        values["dist_max_ev_z"] = round((dist.max_exit_velocity - 105.0) / 10.0, 6)
        values["dist_barrel_z"] = round(
            (dist.barrel_rate - lg.barrel_rate) / max(lg.barrel_rate, 0.01), 6
        )
        values["dist_hard_hit_z"] = round(
            (dist.hard_hit_rate - lg.hard_hit_rate) / max(lg.hard_hit_rate, 0.01), 6
        )
        values["dist_sweet_spot_z"] = round(
            (dist.sweet_spot_rate - lg.sweet_spot_rate) / max(lg.sweet_spot_rate, 0.01), 6
        )
        values["dist_quality"] = dist.quality_score()
        keys.extend(
            [
                "dist_ev_mean_z",
                "dist_ev_std_z",
                "dist_la_mean_z",
                "dist_la_std_z",
                "dist_max_ev_z",
                "dist_barrel_z",
                "dist_hard_hit_z",
                "dist_sweet_spot_z",
                "dist_quality",
            ]
        )

        if self.settings.include_distribution_percentiles:
            ev_p95 = dist.exit_velocity_mean + 1.645 * dist.exit_velocity_std
            ev_p75 = dist.exit_velocity_mean + 0.674 * dist.exit_velocity_std
            values["dist_ev_p95_z"] = round((ev_p95 - 100.0) / 8.0, 6)
            values["dist_ev_p75_z"] = round((ev_p75 - 95.0) / 8.0, 6)
            values["dist_ev_variance_ratio"] = round(
                dist.exit_velocity_std / max(dist.exit_velocity_mean, 1.0), 6
            )
            la_sweet_proxy = math.exp(
                -((dist.launch_angle_mean - 20.0) ** 2) / (2.0 * max(dist.launch_angle_std, 1.0) ** 2)
            )
            values["dist_la_sweet_proxy"] = round(la_sweet_proxy, 6)
            keys.extend(
                [
                    "dist_ev_p95_z",
                    "dist_ev_p75_z",
                    "dist_ev_variance_ratio",
                    "dist_la_sweet_proxy",
                ]
            )

        groups["distribution"] = keys

    def _add_matchup(
        self,
        values: dict[str, float],
        groups: dict[str, list[str]],
        matchup: MatchupContext,
    ) -> None:
        keys = [
            "m_platoon_advantage",
            "m_platoon_ops_z",
            "m_platoon_slg_z",
            "m_bvp_ops_factor",
            "m_bvp_hr_factor",
            "m_bvp_pa",
            "m_recent_form",
            "m_archetype_sim",
            "m_handedness_split_ops",
        ]
        values["m_platoon_advantage"] = round(matchup.platoon_advantage, 6)
        values["m_platoon_ops_z"] = round(matchup.platoon_ops_z, 6)
        values["m_platoon_slg_z"] = round(matchup.platoon_slg_z, 6)
        values["m_bvp_ops_factor"] = round(matchup.bvp_ops_factor, 6)
        values["m_bvp_hr_factor"] = round(matchup.bvp_hr_factor, 6)
        values["m_bvp_pa"] = round(matchup.bvp_pa / max(self.league.pa_per_game * 20, 1.0), 6)
        values["m_recent_form"] = round(matchup.recent_form_multiplier, 6)
        values["m_archetype_sim"] = round(matchup.pitcher_archetype_similarity, 6)
        values["m_handedness_split_ops"] = round(matchup.handedness_split_ops, 6)
        groups["matchup"] = keys

    def _add_pitcher(
        self,
        values: dict[str, float],
        groups: dict[str, list[str]],
        pitcher: Optional[PitcherStatcastProfile],
    ) -> None:
        keys: list[str] = []
        if not pitcher:
            groups["pitcher"] = keys
            return

        lg = self.league
        if pitcher.k_rate is not None:
            values["p_k_rate_z"] = round(
                (pitcher.k_rate - lg.k_pct / 100.0) / max(lg.k_pct / 100.0, 0.01), 6
            )
            keys.append("p_k_rate_z")
        if pitcher.bb_rate is not None:
            values["p_bb_rate_z"] = round(
                (pitcher.bb_rate - lg.bb_pct / 100.0) / max(lg.bb_pct / 100.0, 0.01), 6
            )
            keys.append("p_bb_rate_z")
        if pitcher.hr_per_9 is not None:
            values["p_hr_per_9_z"] = round(
                (pitcher.hr_per_9 - lg.hr_per_9) / max(lg.hr_per_9, 0.1), 6
            )
            keys.append("p_hr_per_9_z")
        if pitcher.whiff_rate is not None:
            values["p_whiff_z"] = round(
                (pitcher.whiff_rate - lg.whiff_rate) / max(lg.whiff_rate, 0.01), 6
            )
            keys.append("p_whiff_z")
        if pitcher.xwoba_allowed is not None:
            values["p_xwoba_allowed_z"] = round(
                (pitcher.xwoba_allowed - lg.xwoba) / max(lg.xwoba * 0.08, 0.001), 6
            )
            keys.append("p_xwoba_allowed_z")
        if pitcher.barrel_rate_allowed is not None:
            values["p_barrel_allowed_z"] = round(
                (pitcher.barrel_rate_allowed - lg.barrel_rate) / max(lg.barrel_rate, 0.01), 6
            )
            keys.append("p_barrel_allowed_z")
        groups["pitcher"] = keys

    def _add_context(
        self,
        values: dict[str, float],
        groups: dict[str, list[str]],
        bundle: PlayerFeatureBundle,
    ) -> None:
        keys = [
            "ctx_park_hr",
            "ctx_park_hits",
            "ctx_park_runs",
            "ctx_weather_hr",
            "ctx_is_home",
        ]
        values["ctx_park_hr"] = round(bundle.park.hr_factor - 1.0, 6)
        values["ctx_park_hits"] = round(bundle.park.hits_factor - 1.0, 6)
        values["ctx_park_runs"] = round(bundle.park.runs_factor - 1.0, 6)
        values["ctx_weather_hr"] = round(
            float(bundle.metadata.get("weather_hr_factor", 1.0)) - 1.0, 6
        )
        pa_volume_status = str(bundle.metadata.get("pa_volume_status", "legacy_frozen"))
        if pa_volume_status in {"legacy_frozen", "receipt_confirmed_slot"}:
            values["ctx_lineup_slot"] = round(
                (bundle.hitter.lineup_slot - 5.0) / 4.0, 6
            )
            values["ctx_expected_pa"] = round(
                (bundle.expected_pa - self.league.pa_per_game)
                / max(self.league.pa_per_game, 1.0),
                6,
            )
            keys.extend(("ctx_lineup_slot", "ctx_expected_pa"))
        values["ctx_is_home"] = 1.0 if bundle.hitter.game.is_home else 0.0
        if bundle.umpire:
            values["ctx_umpire_k_bias"] = round(bundle.umpire.k_bias, 6)
            keys.append("ctx_umpire_k_bias")
        groups["context"] = keys

    def _add_trends(
        self,
        values: dict[str, float],
        groups: dict[str, list[str]],
        season: Optional[HittingStatsSnapshot],
        recent: Optional[HittingStatsSnapshot],
    ) -> None:
        keys: list[str] = []
        if not season or not recent or season.pa <= 0:
            groups["trends"] = keys
            return

        season_ops = season.obp + season.slg
        recent_ops = recent.obp + recent.slg if recent.pa > 0 else season_ops
        season_hr_rate = season.home_runs / max(season.pa, 1)
        recent_hr_rate = recent.home_runs / max(recent.pa, 1) if recent.pa > 0 else season_hr_rate
        season_iso = season.slg - season.avg
        recent_iso = recent.slg - recent.avg if recent.pa > 0 else season_iso

        values["trend_ops_ratio"] = round(recent_ops / max(season_ops, 0.001), 6)
        values["trend_hr_rate_ratio"] = round(recent_hr_rate / max(season_hr_rate, 0.0001), 6)
        values["trend_iso_ratio"] = round(recent_iso / max(season_iso, 0.001), 6)
        values["trend_recent_pa_share"] = round(
            recent.pa / max(season.pa, 1), 6
        )
        keys.extend(
            [
                "trend_ops_ratio",
                "trend_hr_rate_ratio",
                "trend_iso_ratio",
                "trend_recent_pa_share",
            ]
        )
        groups["trends"] = keys

    def _add_interactions(self, values: dict[str, float], groups: dict[str, list[str]]) -> None:
        keys: list[str] = []
        available = [k for k in self.INTERACTION_KEYS if k in values]
        count = 0
        max_features = self.settings.max_interaction_features

        for order in range(2, self.settings.interaction_order + 2):
            for combo in combinations(available, order):
                if count >= max_features:
                    break
                name = "ix_" + "_x_".join(combo)
                product = 1.0
                for k in combo:
                    product *= values[k]
                values[name] = round(product, 6)
                keys.append(name)
                count += 1
            if count >= max_features:
                break

        groups["interactions"] = keys

    def _add_pitcher_hitter_crosses(
        self, values: dict[str, float], groups: dict[str, list[str]]
    ) -> None:
        hitter_keys = [k for k in values if k.startswith("z_")]
        pitcher_keys = [k for k in values if k.startswith("p_")]
        keys: list[str] = []
        for h in hitter_keys:
            for p in pitcher_keys:
                name = f"cross_{h}_x_{p}"
                values[name] = round(values[h] * values[p], 6)
                keys.append(name)
        groups["pitcher_hitter_crosses"] = keys
