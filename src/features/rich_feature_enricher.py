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
from src.data.statcast_integrity import (
    RICH_FEATURE_LINEAGE_KEY,
    RICH_FEATURE_LINEAGE_SCHEMA,
    RICH_PROFILE_OVERRIDE_FIELDS,
    StatcastIntegrityError,
    sha256_feature_value,
    validate_rate_pair,
)


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

        features = self.pipeline.compute(input_data)
        if profile.source_status == "unverified":
            return features
        target_date = data.get("game_date")
        if not isinstance(target_date, str) or not target_date:
            raise StatcastIntegrityError(
                "source-built rich features require an explicit target date"
            )
        if not profile.source_cutoff_date:
            raise StatcastIntegrityError(
                "source-built rich features require a bound source cutoff"
            )
        if profile.source_status != "league_fallback" and not profile.source_hash:
            raise StatcastIntegrityError(
                "source-built rich features require an immutable source hash"
            )

        lineage_fields: dict[str, Any] = {}
        for field in sorted(RICH_PROFILE_OVERRIDE_FIELDS):
            value = features.get(field)
            if value is None:
                continue
            fallback_reason: Optional[str] = None
            if profile.source_status == "league_fallback":
                fallback_reason = "player_missing_from_valid_source"
            elif field in profile.fallback_fields:
                fallback_reason = "source_field_missing_league_substitution"
            denominator = None
            numerator = None
            if field in {"barrel_rate", "hard_hit_rate"} and fallback_reason is None:
                denominator = profile.batted_ball_denominator
                numerator = (
                    profile.barrel_count
                    if field == "barrel_rate"
                    else profile.hard_hit_count
                )
            lineage_fields[field] = {
                "source": (
                    "league_baseline"
                    if profile.source_status == "league_fallback"
                    else "statcast_profile"
                ),
                "player_id": profile.player_id,
                "target_date": target_date,
                "source_cutoff_date": profile.source_cutoff_date,
                "source_max_game_date": profile.source_max_game_date,
                "source_hash": profile.source_hash,
                "sample_count": profile.sample_pa,
                "denominator": denominator,
                "numerator": numerator,
                "fallback_reason": fallback_reason,
                "value_sha256": sha256_feature_value(value),
            }

        if features.get("roll15_xwoba") is not None and float(
            features.get("recent_pa_15") or 0.0
        ) > 0.0:
            rolling_lineage = (
                rolling.get(RICH_FEATURE_LINEAGE_KEY)
                if isinstance(rolling, dict)
                else None
            )
            rolling_fields = (
                rolling_lineage.get("fields")
                if isinstance(rolling_lineage, dict)
                else None
            )
            if not isinstance(rolling_fields, dict):
                raise StatcastIntegrityError(
                    "rolling xwOBA cannot enter probabilities without source lineage"
                )
            for field in ("roll15_xwoba", "recent_pa_15"):
                if field not in rolling_fields:
                    raise StatcastIntegrityError(
                        f"rolling probability feature lacks lineage: {field}"
                    )
                lineage_fields[field] = dict(rolling_fields[field])

        features[RICH_FEATURE_LINEAGE_KEY] = {
            "schema_version": RICH_FEATURE_LINEAGE_SCHEMA,
            "fields": lineage_fields,
        }
        return features
