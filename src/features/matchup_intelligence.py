"""
Matchup intelligence — hitter-vs-pitcher context for HR/HRR modeling.

Populates MatchupContext with platoon splits, BvP history, recency form,
and pitcher-archetype similarity. All adjustments are shrinkage-based and
config-driven (pitcher_matchup + recency blocks).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Optional, Protocol

from src.data.mlb_api import HittingStatsSnapshot
from src.models.dataclasses import (
    HitterGameContext,
    LeagueBaselines,
    MatchupContext,
    PitcherStatcastProfile,
    PlayerFeatureBundle,
    StatcastProfile,
)
from src.utils.logging import get_logger

logger = get_logger(__name__)


class MatchupDataProvider(Protocol):
    """Protocol for fetching matchup-specific stats (MLB API or mocks)."""

    def get_hitting_stats(self, player_id: int) -> tuple[HittingStatsSnapshot, HittingStatsSnapshot]:
        ...

    def get_platoon_splits(
        self, player_id: int
    ) -> tuple[Optional[HittingStatsSnapshot], Optional[HittingStatsSnapshot]]:
        ...

    def get_bvp_stats(
        self, hitter_id: int, pitcher_id: int
    ) -> Optional[HittingStatsSnapshot]:
        ...


@dataclass(frozen=True)
class MatchupIntelligenceSettings:
    """Configurable weights from pitcher_matchup and recency config blocks."""

    season_weight: float
    recent_weight: float
    platoon_weight: float
    league_weight: float
    recent_ip_target: float
    platoon_ip_target: float
    recent_form_exponent: float
    min_bvp_pa: int
    min_platoon_pa: int
    recency_min_pa: int
    recency_target_pa: int
    recency_max_boost: float
    recency_max_penalty: float
    league_ops: float

    @classmethod
    def from_config(cls, config: dict[str, Any], league: LeagueBaselines) -> MatchupIntelligenceSettings:
        pm = config.get("pitcher_matchup", {})
        rec = config.get("recency", {})
        league_cfg = config.get("league_avg", {})
        league_ops = float(league_cfg.get("obp", 0.32)) + float(league_cfg.get("slg", 0.41))
        return cls(
            season_weight=float(pm.get("season_weight", 0.30)),
            recent_weight=float(pm.get("recent_weight", 0.35)),
            platoon_weight=float(pm.get("platoon_weight", 0.25)),
            league_weight=float(pm.get("league_weight", 0.10)),
            recent_ip_target=float(pm.get("recent_ip_target", 20)),
            platoon_ip_target=float(pm.get("platoon_ip_target", 25)),
            recent_form_exponent=float(pm.get("recent_form_exponent", 0.35)),
            min_bvp_pa=int(pm.get("min_bvp_pa", 8)),
            min_platoon_pa=int(pm.get("min_platoon_pa", 15)),
            recency_min_pa=int(rec.get("min_recent_pa", 20)),
            recency_target_pa=int(rec.get("target_pa", 80)),
            recency_max_boost=float(rec.get("max_boost", 0.12)),
            recency_max_penalty=float(rec.get("max_penalty", 0.10)),
            league_ops=league_ops,
        )


class MatchupIntelligence:
    """
    Builds shrinkage-weighted matchup context for a hitter–pitcher pair.

    Follows the LineupIntelligence pattern: settings + build + apply_to_bundle.
    """

    def __init__(
        self,
        settings: Optional[MatchupIntelligenceSettings] = None,
        league_baselines: Optional[LeagueBaselines] = None,
        config: Optional[dict[str, Any]] = None,
        data_provider: Optional[MatchupDataProvider] = None,
    ):
        self.config = config or {}
        self.league = league_baselines or LeagueBaselines.from_config(self.config)
        self.settings = settings or MatchupIntelligenceSettings.from_config(
            self.config, self.league
        )
        self.data_provider = data_provider

    @classmethod
    def from_config(
        cls,
        config: dict[str, Any],
        league_baselines: Optional[LeagueBaselines] = None,
        data_provider: Optional[MatchupDataProvider] = None,
    ) -> MatchupIntelligence:
        league = league_baselines or LeagueBaselines.from_config(config)
        return cls(
            settings=MatchupIntelligenceSettings.from_config(config, league),
            league_baselines=league,
            config=config,
            data_provider=data_provider,
        )

    def build_matchup_context(
        self,
        hitter: HitterGameContext,
        pitcher_statcast: Optional[PitcherStatcastProfile] = None,
        hitter_statcast: Optional[StatcastProfile] = None,
    ) -> MatchupContext:
        """Compute full matchup context for a hitter against their opposing pitcher."""
        platoon_adv, platoon_ops_z, platoon_slg_z, split_ops = self._platoon_context(hitter)
        bvp_pa, bvp_ops_factor, bvp_hr_factor = self._bvp_context(hitter)
        recent_form = self._recency_multiplier(hitter)
        archetype_sim = self._pitcher_archetype_similarity(
            hitter_statcast, pitcher_statcast
        )

        return MatchupContext(
            platoon_advantage=platoon_adv,
            bvp_pa=bvp_pa,
            bvp_ops_factor=bvp_ops_factor,
            recent_form_multiplier=recent_form,
            platoon_ops_z=platoon_ops_z,
            platoon_slg_z=platoon_slg_z,
            bvp_hr_factor=bvp_hr_factor,
            pitcher_archetype_similarity=archetype_sim,
            handedness_split_ops=split_ops,
        )

    def apply_to_bundle(self, bundle: PlayerFeatureBundle) -> PlayerFeatureBundle:
        """Return bundle with enriched matchup context and metadata."""
        matchup = self.build_matchup_context(
            bundle.hitter,
            pitcher_statcast=bundle.pitcher_statcast,
            hitter_statcast=bundle.statcast,
        )
        metadata = dict(bundle.metadata)
        metadata.update(
            {
                "matchup_bvp_pa": matchup.bvp_pa,
                "matchup_bvp_ops_factor": round(matchup.bvp_ops_factor, 4),
                "matchup_recent_form": round(matchup.recent_form_multiplier, 4),
                "matchup_archetype_sim": round(matchup.pitcher_archetype_similarity, 4),
            }
        )
        return PlayerFeatureBundle(
            hitter=bundle.hitter,
            statcast=bundle.statcast,
            park=bundle.park,
            weather=bundle.weather,
            matchup=matchup,
            umpire=bundle.umpire,
            injury=bundle.injury,
            pitcher_statcast=bundle.pitcher_statcast,
            expected_pa=bundle.expected_pa,
            features=bundle.features,
            metadata=metadata,
        )

    def _platoon_context(
        self, hitter: HitterGameContext
    ) -> tuple[float, float, float, float]:
        bats = hitter.player.bats
        throws = hitter.opposing_pitcher_throws
        s = self.settings

        platoon_adv = 0.0
        if bats in ("L", "R") and throws in ("L", "R"):
            if bats != throws:
                platoon_adv = s.platoon_weight
            else:
                platoon_adv = -s.platoon_weight * 0.5

        platoon_ops_z = 0.0
        platoon_slg_z = 0.0
        split_ops = s.league_ops

        if self.data_provider and bats in ("L", "R") and throws in ("L", "R"):
            vs_lhp, vs_rhp = self.data_provider.get_platoon_splits(hitter.player.mlb_id)
            target = vs_lhp if throws == "L" else vs_rhp
            if target and target.pa >= s.min_platoon_pa:
                split_ops = target.obp + target.slg
                shrink = min(1.0, target.pa / max(s.platoon_ip_target * 4.2, 1.0))
                ops_z = (split_ops - s.league_ops) / max(s.league_ops * 0.08, 0.001)
                slg_z = (target.slg - self.league.xslg) / max(self.league.xslg * 0.08, 0.001)
                platoon_ops_z = ops_z * shrink
                platoon_slg_z = slg_z * shrink
                platoon_adv += platoon_ops_z * s.platoon_weight * shrink

        return platoon_adv, platoon_ops_z, platoon_slg_z, split_ops

    def _bvp_context(self, hitter: HitterGameContext) -> tuple[int, float, float]:
        s = self.settings
        if not self.data_provider or not hitter.opposing_pitcher_id:
            return 0, 1.0, 1.0

        bvp = self.data_provider.get_bvp_stats(
            hitter.player.mlb_id, hitter.opposing_pitcher_id
        )
        if not bvp or bvp.pa < s.min_bvp_pa:
            return bvp.pa if bvp else 0, 1.0, 1.0

        bvp_ops = bvp.obp + bvp.slg
        shrink = min(1.0, bvp.pa / max(s.min_bvp_pa * 3, 1))
        ops_factor = 1.0 + shrink * ((bvp_ops / max(s.league_ops, 0.001)) - 1.0) * s.recent_weight

        season_hr = bvp.home_runs / max(bvp.pa, 1)
        league_hr = (self.league.hr_per_9 / 9.0) / 4.2
        hr_factor = 1.0 + shrink * ((season_hr / max(league_hr, 0.001)) - 1.0) * s.season_weight

        return bvp.pa, max(0.7, min(1.4, ops_factor)), max(0.6, min(1.5, hr_factor))

    def _recency_multiplier(self, hitter: HitterGameContext) -> float:
        s = self.settings
        if not self.data_provider:
            return 1.0

        try:
            season, recent = self.data_provider.get_hitting_stats(hitter.player.mlb_id)
        except Exception as exc:
            logger.debug("Recency fetch failed for %s: %s", hitter.player.name, exc)
            return 1.0

        if recent.pa < s.recency_min_pa or season.pa <= 0:
            return 1.0

        season_ops = season.obp + season.slg
        recent_ops = recent.obp + recent.slg
        if season_ops <= 0:
            return 1.0

        ratio = recent_ops / season_ops
        shrink = min(1.0, recent.pa / max(s.recency_target_pa, 1))
        delta = (ratio - 1.0) * shrink * s.recent_form_exponent
        delta = max(-s.recency_max_penalty, min(s.recency_max_boost, delta))
        return 1.0 + delta

    def _pitcher_archetype_similarity(
        self,
        hitter_statcast: Optional[StatcastProfile],
        pitcher: Optional[PitcherStatcastProfile],
    ) -> float:
        """Cosine-like similarity between hitter skill vector and pitcher archetype."""
        if not pitcher:
            return 0.0

        lg = self.league
        pitcher_vec = [
            (pitcher.k_rate or lg.k_pct / 100.0) - lg.k_pct / 100.0,
            (pitcher.bb_rate or lg.bb_pct / 100.0) - lg.bb_pct / 100.0,
            (pitcher.hr_per_9 or lg.hr_per_9) - lg.hr_per_9,
        ]
        contact = (
            hitter_statcast.contact_rate
            if hitter_statcast and hitter_statcast.contact_rate is not None
            else lg.contact_rate
        )
        power = (
            hitter_statcast.barrel_rate
            if hitter_statcast and hitter_statcast.barrel_rate is not None
            else lg.barrel_rate
        )
        quality = (
            hitter_statcast.xwoba
            if hitter_statcast and hitter_statcast.xwoba is not None
            else lg.xwoba
        )
        hitter_vec = [
            contact - lg.contact_rate,
            power - lg.barrel_rate,
            quality - lg.xwoba,
        ]

        dot = sum(a * b for a, b in zip(pitcher_vec, hitter_vec))
        norm_p = math.sqrt(sum(v * v for v in pitcher_vec)) or 1.0
        norm_h = math.sqrt(sum(v * v for v in hitter_vec)) or 1.0
        return round(dot / (norm_p * norm_h), 6)
