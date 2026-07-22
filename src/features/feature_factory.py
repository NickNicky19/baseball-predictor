"""
Feature factory — orchestrates rich feature bundle assembly for daily slates.

FIX (this revision): RichFeatureEnricher.enrich() previously received only
{"game_date": ...}, so the ml/ feature layer emitted None/default for nearly
every field. The enrichment call now passes the real weather, park, matchup,
umpire, and lineup context that this factory already computes, plus optional
leakage-safe rolling stats from a PointInTimeStats provider when one is
injected.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from pathlib import Path
from typing import Any, Mapping, Optional, Protocol

from src.data.injury_client import InjuryClient
from src.data.mlb_api import MLBStatsAPI
from src.data.savant import SavantClient
from src.data.umpire_client import UmpireClient
from src.data.weather_client import WeatherClient
from src.evaluation.park_factor_estimator import ParkFactorEstimator, ParkFactorSettings
from src.features.feature_vector import FeatureVectorBuilder
from src.features.lineup_intelligence import LineupIntelligence
from src.features.matchup_intelligence import MatchupIntelligence
from src.features.pitcher_matchup_gate import (
    LEGACY_MODE,
    STRICT_MODE,
    PitcherMatchupAuthorization,
    validate_mode,
    validate_pitcher_profile_lineage,
)
from src.features.pa_volume_gate import (
    LEGACY_MODE as PA_VOLUME_LEGACY_MODE,
    STRICT_MODE as PA_VOLUME_STRICT_MODE,
    PAVolumeAuthorization,
    validate_mode as validate_pa_volume_mode,
)
from src.features.legacy_statcast_features import StatcastFeatureEngine
from src.features.rich_feature_enricher import RichFeatureEnricher
from src.data.statcast_integrity import (
    RICH_FEATURE_LINEAGE_KEY,
    sha256_feature_value,
)
from src.models.dataclasses import (
    HitterGameContext,
    LeagueBaselines,
    MatchupContext,
    ParkFactors,
    PitcherStatcastProfile,
    PlayerFeatureBundle,
)
from src.utils.logging import get_logger

logger = get_logger(__name__)


class HitterProvider(Protocol):
    def get_hitters_for_date(
        self, game_date: str, include_projected: bool = False
    ) -> list[HitterGameContext]:
        ...

    def get_pitching_stats(self, player_id: int):
        ...


class RollingStatsProvider(Protocol):
    """Anything that can produce leakage-safe rolling features (see PointInTimeStats)."""

    def rolling_features(self, player_id: int, as_of_date: str) -> dict[str, Any]:
        ...


class PitcherAuthorizationProvider(Protocol):
    """Future-only provider of validated T-minus-4 starter authorizations."""

    def authorization_for(
        self, hitter: HitterGameContext, game_date: str
    ) -> PitcherMatchupAuthorization | Mapping[str, Any] | None:
        ...


class CertifiedPitcherProfileProvider(Protocol):
    """Point-in-time profile provider with exact count/date/source lineage."""

    def profile_for(
        self, authorization: PitcherMatchupAuthorization
    ) -> tuple[PitcherStatcastProfile, Mapping[str, Any]] | None:
        ...


class PAVolumeAuthorizationProvider(Protocol):
    """Future-only provider of T-minus-4 lineup/PA-volume receipts."""

    def authorization_for(
        self, hitter: HitterGameContext, game_date: str
    ) -> PAVolumeAuthorization | Mapping[str, Any] | None:
        ...


class FeatureFactory:
    """
    Assembles PlayerFeatureBundle rows with rich features and matchup context.
    """

    def __init__(
        self,
        config: Optional[dict[str, Any]] = None,
        league_baselines: Optional[LeagueBaselines] = None,
        mlb_api: Optional[HitterProvider] = None,
        statcast_engine: Optional[StatcastFeatureEngine] = None,
        lineup_intelligence: Optional[LineupIntelligence] = None,
        matchup_intelligence: Optional[MatchupIntelligence] = None,
        feature_vector_builder: Optional[FeatureVectorBuilder] = None,
        weather_client: Optional[WeatherClient] = None,
        umpire_client: Optional[UmpireClient] = None,
        injury_client: Optional[InjuryClient] = None,
        savant_client: Optional[SavantClient] = None,
        park_estimator: Optional[ParkFactorEstimator] = None,
        estimated_park_factors: Optional[dict[str, ParkFactors]] = None,
        rolling_stats_provider: Optional[RollingStatsProvider] = None,
        pitcher_authorization_provider: Optional[PitcherAuthorizationProvider] = None,
        certified_pitcher_profile_provider: Optional[CertifiedPitcherProfileProvider] = None,
        pa_volume_authorization_provider: Optional[PAVolumeAuthorizationProvider] = None,
    ):
        self.config = config or {}
        self.league = league_baselines or LeagueBaselines.from_config(self.config)
        self.mlb_api = mlb_api
        feature_config = self.config.get("feature_factory", {}) or {}
        self.derive_batted_ball_rates = bool(
            feature_config.get("derive_batted_ball_rates", False)
        )
        self.hitter_rate_source_mode = str(
            feature_config.get("hitter_rate_source_mode", "legacy_frozen")
        )
        if self.hitter_rate_source_mode not in {
            "legacy_frozen",
            "point_in_time_required",
        }:
            raise ValueError(
                "feature_factory.hitter_rate_source_mode must be "
                "'legacy_frozen' or 'point_in_time_required'"
            )
        self.statcast_engine = statcast_engine or StatcastFeatureEngine(
            league_baselines=self.league,
            derive_batted_ball_rates=self.derive_batted_ball_rates,
            config=self.config,
        )
        self.lineup_intelligence = lineup_intelligence or LineupIntelligence.from_config(
            self.config, league_baselines=self.league
        )
        self.matchup_intelligence = matchup_intelligence
        self.feature_vector_builder = feature_vector_builder or FeatureVectorBuilder(
            league_baselines=self.league,
            config=self.config,
        )
        self.weather_client = weather_client or WeatherClient()
        self.umpire_client = umpire_client or UmpireClient()
        self.injury_client = injury_client or InjuryClient()
        self.savant_client = savant_client or SavantClient(
            league_baselines=self.league,
            derive_batted_ball_rates=self.derive_batted_ball_rates,
        )
        self._park_estimator = park_estimator or ParkFactorEstimator(
            ParkFactorSettings.from_config(self.config)
        )
        self._estimated_park_factors = estimated_park_factors or {}

        # Rich feature layer + optional leakage-safe rolling stats source.
        self.rich_feature_enricher = RichFeatureEnricher()
        self.rolling_stats_provider = rolling_stats_provider
        self.pitcher_authorization_provider = pitcher_authorization_provider
        self.certified_pitcher_profile_provider = certified_pitcher_profile_provider
        self.pa_volume_authorization_provider = pa_volume_authorization_provider
        self.pitcher_context_identity_mode = validate_mode(
            (self.config.get("pa_simulator") or {}).get(
                "pitcher_context_identity_mode", LEGACY_MODE
            )
        )
        self.pa_volume_identity_mode = validate_pa_volume_mode(
            (self.config.get("base_running") or {}).get(
                "pa_volume_identity_mode", PA_VOLUME_LEGACY_MODE
            )
        )

        if self.matchup_intelligence is None and isinstance(self.mlb_api, MLBStatsAPI):
            self.matchup_intelligence = MatchupIntelligence.from_config(
                self.config,
                league_baselines=self.league,
                data_provider=self.mlb_api,
            )
        elif self.matchup_intelligence is None:
            self.matchup_intelligence = MatchupIntelligence.from_config(
                self.config,
                league_baselines=self.league,
            )

    @classmethod
    def from_config(
        cls,
        config: dict[str, Any],
        league_baselines: Optional[LeagueBaselines] = None,
        mlb_api: Optional[HitterProvider] = None,
    ) -> FeatureFactory:
        return cls(config=config, league_baselines=league_baselines, mlb_api=mlb_api)

    def build_bundles(
        self,
        game_date: str,
        hitters: Optional[list[HitterGameContext]] = None,
        use_projected_lineups: bool = False,
        savant_csv_path: Optional[str] = None,
    ) -> list[PlayerFeatureBundle]:
        """Build enriched feature bundles for all active hitters on a slate."""
        target_date = date.fromisoformat(game_date)
        if (
            (
                self.pitcher_context_identity_mode == STRICT_MODE
                or self.pa_volume_identity_mode == PA_VOLUME_STRICT_MODE
            )
            and target_date.year == 2026
            and target_date.month == 5
        ):
            raise ValueError("May 2026 is sealed; refusing all strict feature-source access")
        if hitters is None:
            if self.mlb_api is None:
                raise ValueError("mlb_api required when hitters are not provided")
            hitters = self.mlb_api.get_hitters_for_date(
                game_date,
                include_projected=use_projected_lineups,
            )

        if not hitters:
            return []

        profiles = self.statcast_engine.build_profiles_for_hitters(
            hitters,
            game_date=game_date,
            savant_csv_path=savant_csv_path,
        )

        bundles: list[PlayerFeatureBundle] = []
        for hitter in hitters:
            if not self.injury_client.is_available(hitter.player.mlb_id):
                logger.debug("Skipping injured/inactive player %s", hitter.player.name)
                continue

            statcast = profiles[hitter.player.mlb_id]
            pitcher_metadata: dict[str, Any] = {}
            if self.pitcher_context_identity_mode == STRICT_MODE:
                hitter, pitcher_statcast, pitcher_metadata = self._strict_pitcher_inputs(
                    hitter, game_date
                )
            else:
                pitcher_statcast = self._resolve_opposing_pitcher_statcast(hitter)
            pa_volume_metadata = self._pa_volume_metadata(hitter, game_date)
            weather = self.weather_client.get_weather_for_game(
                hitter.game.game_pk,
                hitter.game.venue,
                game_date,
            )
            umpire = self.umpire_client.get_umpire_for_game(hitter.game.game_pk)
            injury = self.injury_client.get_injury_status(hitter.player.mlb_id)
            weather_hr = self.weather_client.hr_factor_from_weather(weather)
            park = self._park_factors(hitter.game.venue)

            base_bundle = PlayerFeatureBundle(
                hitter=hitter,
                statcast=statcast,
                park=park,
                weather=weather,
                matchup=(
                    MatchupContext()
                    if self.pitcher_context_identity_mode == STRICT_MODE
                    else self.matchup_intelligence.build_matchup_context(
                        hitter,
                        pitcher_statcast=pitcher_statcast,
                        hitter_statcast=statcast,
                    )
                ),
                umpire=umpire,
                injury=injury,
                pitcher_statcast=pitcher_statcast,
                expected_pa=self.league.pa_per_game,
                metadata={
                    "lineup_status": hitter.game.lineup_status,
                    "weather_hr_factor": weather_hr,
                    **pitcher_metadata,
                    **pa_volume_metadata,
                },
            )

            enriched = (
                base_bundle
                if self.pitcher_context_identity_mode == STRICT_MODE
                else self.matchup_intelligence.apply_to_bundle(base_bundle)
            )
            season_hitting, recent_hitting, hitter_rate_lineage = self._hitting_stats(
                hitter.player.mlb_id, game_date
            )

            # ================================================================
            # FEED THE HITTER'S OWN K/BB RATES TO THE SIMULATOR.
            # ================================================================
            # These snapshots were ALREADY being fetched here -- and handed ONLY
            # to the GBM feature vector, then DROPPED. The simulator never saw
            # them, so StatcastProfile.k_rate fell back to the league constant on
            # every hitter (MEASURED: sd = 0.0000 across 270 hitters) and the K
            # logit had to use `contact_rate`, a per-swing whiff PROXY that is
            # mis-centred by +0.52 and over-scaled 1.7x.
            #
            # That single defect inflated bip_prob by 9.9%, which carried 91.7%
            # of the model's ENTIRE hits excess.
            #
            # The data and the consumer were both already here. They were simply
            # not connected. This connects them.
            #
            # ADDITIVE and SAFE: if a snapshot is missing or has pa == 0, the
            # rate is None, StatcastProfile keeps its existing value, and the
            # simulator's fitted path stays INERT (it requires BOTH the config
            # flag AND the rates).
            enriched = self._attach_hitter_rates(
                enriched,
                season_hitting,
                recent_hitting,
                hitter_rate_lineage,
            )
            statcast = enriched.statcast

            features = self.feature_vector_builder.build(
                enriched,
                season_hitting=season_hitting,
                recent_hitting=recent_hitting,
            )

            # === FIX: feed the enricher the REAL context this factory just
            # computed, instead of only {"game_date": ...}. Matchup fields
            # come from the post-matchup-intelligence bundle.
            context_data: dict[str, Any] = {
                "game_date": game_date,
                # Park
                "park_hits_factor": park.hits_factor,
                "park_hr_factor": park.hr_factor,
                "park_runs_factor": park.runs_factor,
                # Weather
                "weather_temp": weather.temperature_f,
                "weather_wind_speed": weather.wind_mph,
                "weather_wind_direction": weather.wind_direction_deg,
                "weather_humidity": weather.precip_probability,
                "weather_is_dome": weather.is_dome,
                "weather_hr_factor": weather_hr,
                # Umpire / venue side
                "umpire_k_bias": umpire.k_bias if umpire else None,
                "is_home": getattr(hitter.game, "is_home", None),
                # Matchup / form
                "platoon_advantage": enriched.matchup.platoon_advantage,
                "bvp_ops_factor": enriched.matchup.bvp_ops_factor,
                "bvp_hr_factor": enriched.matchup.bvp_hr_factor,
                "recent_form_mult": enriched.matchup.recent_form_multiplier,
                # Lineup
                "lineup_slot": (
                    hitter.lineup_slot
                    if pa_volume_metadata.get("pa_volume_status")
                    in {"legacy_frozen", "receipt_confirmed_slot"}
                    else None
                ),
            }

            rolling: Optional[dict[str, Any]] = None
            if self.rolling_stats_provider is not None:
                try:
                    rolling = self.rolling_stats_provider.rolling_features(
                        hitter.player.mlb_id, game_date
                    )
                except Exception as exc:
                    logger.debug(
                        "Rolling features unavailable for %s: %s",
                        hitter.player.name,
                        exc,
                    )

            rich_features = self.rich_feature_enricher.enrich(
                data=context_data,
                profile=statcast,
                rolling=rolling,
            )
            # The fitted contact adapter is optional and config-gated.  Custom
            # engines used by tests and downstream callers may implement only
            # the long-standing profile interface; absence must remain the
            # explicit inert path, not turn an optional candidate seam into a
            # slate-wide failure.
            contact_evidence = getattr(
                self.statcast_engine, "hits_contact_evidence_for", None
            )
            contact_adapter = (
                contact_evidence(hitter.player.mlb_id)
                if callable(contact_evidence)
                else None
            )
            if contact_adapter is not None:
                rich_features = {
                    **rich_features,
                    "hits_contact_adapter_status": contact_adapter["status"],
                    "hits_contact_adapter_reason": contact_adapter.get("reason"),
                }
                if contact_adapter["status"] == "adapter_applied":
                    rich_features["contact_xba_fitted"] = contact_adapter[
                        "fitted_contact_xba"
                    ]
                    lineage = rich_features.get(RICH_FEATURE_LINEAGE_KEY)
                    if not isinstance(lineage, dict) or not isinstance(
                        lineage.get("fields"), dict
                    ):
                        raise ValueError(
                            "contact adapter cannot enter probabilities without rich lineage"
                        )
                    lineage["fields"]["contact_xba_fitted"] = {
                        "source": "hits_contact_adapter",
                        "player_id": hitter.player.mlb_id,
                        "target_date": contact_adapter["target_date"],
                        "source_cutoff_date": contact_adapter["source_cutoff_date"],
                        "source_max_game_date": contact_adapter[
                            "last_evidence_date"
                        ],
                        "source_hash": contact_adapter["source_hash"],
                        "sample_count": int(contact_adapter["player_bip"]),
                        "denominator": int(contact_adapter["player_bip"]),
                        "numerator": None,
                        "fallback_reason": None,
                        "value_sha256": sha256_feature_value(
                            rich_features["contact_xba_fitted"]
                        ),
                        "selection_evidence_sha256": contact_adapter[
                            "selection_evidence_sha256"
                        ],
                    }

            enriched = PlayerFeatureBundle(
                hitter=enriched.hitter,
                statcast=enriched.statcast,
                park=enriched.park,
                weather=enriched.weather,
                matchup=enriched.matchup,
                umpire=enriched.umpire,
                injury=enriched.injury,
                pitcher_statcast=enriched.pitcher_statcast,
                expected_pa=enriched.expected_pa,
                features=features,
                metadata={
                    **enriched.metadata,
                    "feature_count": features.count(),
                    "rich_features": rich_features,
                    **(
                        {"hits_contact_adapter": contact_adapter}
                        if contact_adapter is not None
                        else {}
                    ),
                },
            )
            if (
                self.pa_volume_identity_mode == PA_VOLUME_STRICT_MODE
                and pa_volume_metadata.get("pa_volume_status")
                != "receipt_confirmed_slot"
            ):
                # A postgame/final slot may still be present on the schedule
                # object, but without a T-4 receipt it cannot alter expected PA
                # or any rich probability input. The strict simulator consumes
                # the fitted pooled distribution instead.
                bundles.append(enriched)
            else:
                bundles.append(self.lineup_intelligence.apply_to_bundle(enriched))

        logger.info("Built %d feature bundles for %s", len(bundles), game_date)
        return bundles

    def set_estimated_park_factors(self, factors: dict[str, ParkFactors]) -> None:
        """Set externally estimated park factors (used during validation/backtesting)."""
        self._estimated_park_factors = factors

    def sync_league(self, league: LeagueBaselines) -> None:
        """Keep sub-engines aligned when league baselines are corrected."""
        self.league = league
        self.statcast_engine.league = league
        self.statcast_engine.savant.league = league
        self.savant_client.league = league
        self.lineup_intelligence.league = league
        self.matchup_intelligence.league = league
        self.feature_vector_builder.league = league

    def _resolve_opposing_pitcher_statcast(
        self, hitter: HitterGameContext
    ) -> Optional[PitcherStatcastProfile]:
        pitcher_id = hitter.opposing_pitcher_id
        if not pitcher_id or self.mlb_api is None:
            return None

        try:
            season, recent = self.mlb_api.get_pitching_stats(pitcher_id)
        except Exception:
            logger.debug(
                "Could not fetch stats for opposing pitcher %s",
                hitter.opposing_pitcher_name,
            )
            return None

        k_pct = recent.k_pct or season.k_pct or self.league.k_pct
        bb_pct = recent.bb_pct or season.bb_pct or self.league.bb_pct
        hr_per_9 = recent.hr_per_9 or season.hr_per_9 or self.league.hr_per_9

        return self.savant_client.build_pitcher_profile_from_rates(
            player_id=pitcher_id,
            player_name=hitter.opposing_pitcher_name,
            k_pct=k_pct,
            bb_pct=bb_pct,
            hr_per_9=hr_per_9,
            sample_pa=int(recent.innings_pitched * 4.2),
        )

    def _strict_pitcher_inputs(
        self, hitter: HitterGameContext, game_date: str
    ) -> tuple[HitterGameContext, Optional[PitcherStatcastProfile], dict[str, Any]]:
        """Resolve only receipt-proven identity and certified pregame rates.

        Missing evidence is an explicit neutral exclusion.  Present-but-
        contradictory evidence raises; no guessed/actual starter or legacy
        current-season API fallback is allowed to reach the probability path.
        """
        provider = self.pitcher_authorization_provider
        raw = provider.authorization_for(hitter, game_date) if provider is not None else None
        if raw is None:
            sanitized = replace(
                hitter,
                opposing_pitcher_id=None,
                opposing_pitcher_name="",
                opposing_pitcher_throws="U",
            )
            return sanitized, None, {"pitcher_matchup_status": "excluded_missing_receipt"}

        authorization = (
            raw
            if isinstance(raw, PitcherMatchupAuthorization)
            else PitcherMatchupAuthorization.from_mapping(raw)
        )
        authorization.validate_hitter(hitter)
        sanitized = replace(
            hitter,
            opposing_pitcher_id=authorization.expected_pitcher_id,
            opposing_pitcher_name="",
            opposing_pitcher_throws="U",
        )
        metadata: dict[str, Any] = {
            "pitcher_matchup_authorization": authorization.to_dict(),
            "pitcher_matchup_status": "excluded_profile_unavailable",
        }
        profile_provider = self.certified_pitcher_profile_provider
        supplied = profile_provider.profile_for(authorization) if profile_provider is not None else None
        if supplied is None:
            return sanitized, None, metadata
        if not isinstance(supplied, tuple) or len(supplied) != 2:
            raise ValueError("certified pitcher provider must return (profile, lineage)")
        profile, lineage = supplied
        if not isinstance(profile, PitcherStatcastProfile) or not isinstance(lineage, Mapping):
            raise ValueError("certified pitcher provider returned invalid types")
        validate_pitcher_profile_lineage(
            profile=profile,
            lineage=lineage,
            authorization=authorization,
        )
        metadata.update(
            {
                "pitcher_profile_lineage": dict(lineage),
                "pitcher_matchup_status": "receipt_and_profile_verified",
            }
        )
        return sanitized, profile, metadata

    def _pa_volume_metadata(
        self, hitter: HitterGameContext, game_date: str
    ) -> dict[str, Any]:
        if self.pa_volume_identity_mode == PA_VOLUME_LEGACY_MODE:
            return {"pa_volume_status": "legacy_frozen"}
        provider = self.pa_volume_authorization_provider
        raw = provider.authorization_for(hitter, game_date) if provider is not None else None
        if raw is None:
            return {"pa_volume_status": "pooled_missing_receipt"}
        authorization = (
            raw
            if isinstance(raw, PAVolumeAuthorization)
            else PAVolumeAuthorization.from_mapping(raw)
        )
        authorization.validate_hitter(hitter)
        status = (
            "receipt_confirmed_slot"
            if authorization.lineup_state == "confirmed"
            else f"pooled_lineup_{authorization.lineup_state}"
        )
        if (
            authorization.lineup_state == "confirmed"
            and hitter.lineup_slot != authorization.lineup_slot
        ):
            raise ValueError("hitter lineup slot contradicts its T-minus-4 receipt")
        return {
            "pa_volume_authorization": authorization.to_dict(),
            "pa_volume_status": status,
        }

    def _attach_hitter_rates(self, bundle, season, recent, lineage=None):
        """Put the hitter's OWN season/recent K and BB rates on his StatcastProfile.

        RULE 8 -- what these quantities MEAN:
          season.k_rate = strikeouts / PA over the season TO DATE (~208 PA)
          recent.k_rate = strikeouts / PA over the API's lastXGames (~51 PA)
        Both are FRACTIONS in [0, 1], matching StatcastProfile's contract
        ("rates are fractions (0-1) unless noted").

        A missing snapshot, or one with pa == 0, yields None -- and None leaves
        the existing profile value untouched, which keeps the simulator on its
        legacy path. We NEVER fabricate a rate.
        """
        if bundle.statcast is None:
            return bundle
        k_s = season.k_rate if season is not None else None
        bb_s = season.bb_rate if season is not None else None
        k_r = recent.k_rate if recent is not None else None
        bb_r = recent.bb_rate if recent is not None else None
        if k_s is None and bb_s is None and k_r is None and bb_r is None:
            return bundle
        if self.hitter_rate_source_mode == "point_in_time_required":
            if not isinstance(lineage, dict) or lineage.get(
                "schema_version"
            ) != "hitter-rate-lineage-v1":
                raise ValueError(
                    "point-in-time hitter rates require bound source lineage"
                )
            lineage_fields = lineage.get("fields")
            if not isinstance(lineage_fields, dict):
                raise ValueError("point-in-time hitter-rate lineage fields are missing")
        else:
            lineage_fields = {}

        field_lineage = dict(bundle.statcast.field_lineage)
        fallback_fields = set(bundle.statcast.fallback_fields)
        for field_name, value in (
            ("k_rate", k_s),
            ("bb_rate", bb_s),
            ("k_rate_recent", k_r),
            ("bb_rate_recent", bb_r),
        ):
            if value is None or not lineage_fields:
                continue
            entry = lineage_fields.get(field_name)
            if not isinstance(entry, dict):
                raise ValueError(
                    f"point-in-time hitter-rate lineage is missing {field_name}"
                )
            field_lineage[field_name] = dict(entry)
            fallback_fields.discard(field_name)
        return replace(
            bundle,
            statcast=replace(
                bundle.statcast,
                k_rate=k_s if k_s is not None else bundle.statcast.k_rate,
                bb_rate=bb_s if bb_s is not None else bundle.statcast.bb_rate,
                k_rate_recent=k_r,
                bb_rate_recent=bb_r,
                fallback_fields=tuple(sorted(fallback_fields)),
                field_lineage=field_lineage,
            ),
        )

    def _hitting_stats(self, player_id: int, game_date: str):
        if self.hitter_rate_source_mode == "point_in_time_required":
            provider = self.rolling_stats_provider
            method = getattr(provider, "get_hitting_stats_with_lineage", None)
            if not callable(method):
                raise ValueError(
                    "point-in-time hitter-rate mode requires a provenance-capable provider"
                )
            return method(player_id, game_date)
        if self.mlb_api is None:
            return None, None, None
        try:
            season, recent = self.mlb_api.get_hitting_stats(player_id)
            return season, recent, None
        except Exception:
            return None, None, None

    def _park_factors(self, venue: str) -> ParkFactors:
        if self._estimated_park_factors:
            venue_lower = venue.lower()
            for name, factors in self._estimated_park_factors.items():
                if name.lower() in venue_lower:
                    return factors

        park_config = self.config.get("park_factors", {})
        venue_lower = venue.lower()
        for park_name, factors in park_config.items():
            if isinstance(factors, dict) and park_name.lower() in venue_lower:
                return ParkFactors(
                    venue=venue,
                    hits_factor=float(factors.get("hits", 1.0)),
                    hr_factor=float(factors.get("hr", 1.0)),
                    runs_factor=float(factors.get("runs", 1.0)),
                )
        return ParkFactors(venue=venue)

    def resolve_savant_csv_path(self, config_path: Optional[Path] = None) -> Optional[str]:
        savant_cfg = self.config.get("savant", {})
        csv_path = savant_cfg.get("csv_path")
        if not csv_path:
            return None
        path = Path(csv_path)
        if not path.is_absolute():
            root = config_path or Path(__file__).resolve().parents[2]
            path = root / csv_path
        return str(path) if path.exists() else None
