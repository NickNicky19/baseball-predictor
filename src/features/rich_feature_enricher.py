"""
RichFeatureEnricher — bridge from production bundles to the src/features/ml/
feature layer.

FIX (this revision): the enricher previously received only
{"game_date": ...} plus six StatcastProfile copies, so nearly every field the
ml/ engineers name resolved to None. It now copies the full StatcastProfile
and merges the real weather / park / matchup / lineup context computed by
FeatureFactory, so ContextFeatureEngineer and StatcastFeatureEngineer emit
real values. RollingFeatureEngineer fields populate when a rolling-stats
provider is supplied (see PointInTimeStats.rolling_features()).
"""

from __future__ import annotations

from typing import Any, Optional

from src.features.ml.feature_pipeline import FeaturePipeline
from src.features.ml.statcast_features import StatcastFeatureEngineer
from src.features.ml.context_features import ContextFeatureEngineer
from src.features.ml.rolling_features import RollingFeatureEngineer
from src.models.dataclasses import StatcastProfile
from src.data.statcast_integrity import validate_rate_pair


class RichFeatureEnricher:
    """
    Enriches existing player data + StatcastProfile with rich features
    from the modular feature layer (src/features/ml/).

    This is additive — it does not modify the current StatcastProfile.
    """

    def __init__(self):
        self.pipeline = FeaturePipeline([
            StatcastFeatureEngineer(),
            ContextFeatureEngineer(),
            RollingFeatureEngineer(),
        ])

    def enrich(
        self,
        data: dict[str, Any],
        profile: StatcastProfile,
        rolling: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """
        Returns a dictionary of rich features.

        Parameters
        ----------
        data : dict
            Real per-game context assembled by FeatureFactory (weather, park,
            matchup, lineup slot, etc.). Keys should match the names the ml/
            engineers read (see ContextFeatureEngineer.get_feature_names()).
        profile : StatcastProfile
            The player's current Statcast profile.
        rolling : dict, optional
            Rolling-window metrics (roll15_xwoba, recent_pa_15, ...) from a
            point-in-time stats provider. Optional until historical per-game
            data is wired in; fields stay None when absent.
        """
        validate_rate_pair(
            profile.barrel_rate,
            profile.hard_hit_rate,
            context=f"RichFeatureEnricher[{profile.player_id}]",
        )
        input_data: dict[str, Any] = {
            **data,
            # Full StatcastProfile pass-through (was: only 6 fields)
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
            "sample_pa": profile.sample_pa,
        }
        if rolling:
            input_data.update(rolling)

        return self.pipeline.compute(input_data)
