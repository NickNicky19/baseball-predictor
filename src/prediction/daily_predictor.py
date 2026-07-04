"""
Daily prediction orchestrator.

Coordinates data and feature layers, delegates prop math to PropEngine and
simulation to the simulation layer. Designed for dependency injection so
prediction logic is testable without live API calls.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from src.data.injury_client import InjuryClient
from src.data.mlb_api import MLBStatsAPI
from src.data.odds import CompositeOddsProvider
from src.data.savant import SavantClient
from src.data.umpire_client import UmpireClient
from src.data.weather_client import WeatherClient
from src.evaluation.park_factor_estimator import ParkFactorEstimator, ParkFactorSettings
from src.learning.outcome_recorder import OutcomeRecorder
from src.features.feature_factory import FeatureFactory
from src.features.feature_store import FeatureStore
from src.features.lineup_intelligence import LineupIntelligence
from src.features.legacy_statcast_features import StatcastFeatureEngine
from src.models.dataclasses import (
    DailyPrediction,
    EdgeResult,
    HitterGameContext,
    LeagueBaselines,
    MatchupContext,
    OddsLine,
    ParkFactors,
    PitcherGameContext,
    PitcherStatcastProfile,
    PlayerFeatureBundle,
    PropCategory,
    PropProjection,
    WeatherContext,
)
from src.prediction.correction_manager import CorrectionManager
from src.prediction.edge_calculator import EdgeCalculator
from src.prediction.prop_engine import PropEngine
from src.utils.errors import ConfigError, DataFetchError, OddsLoadError, PredictionPipelineError
from src.utils.logging import get_logger

logger = get_logger(__name__)


class DailyPredictor:
    """
    End-to-end daily prediction pipeline.

    Dependencies are injectable for unit tests and backtests. When omitted,
    sensible defaults are constructed from config.json.
    """

    def __init__(
        self,
        config: Optional[dict[str, Any]] = None,
        config_path: Optional[str | Path] = None,
        mlb_api: Optional[MLBStatsAPI] = None,
        statcast_engine: Optional[StatcastFeatureEngine] = None,
        prop_engine: Optional[PropEngine] = None,
        edge_calculator: Optional[EdgeCalculator] = None,
        odds_loader: Optional[CompositeOddsProvider] = None,
        outcome_recorder: Optional[OutcomeRecorder] = None,
        feature_store: Optional[FeatureStore] = None,
        league_baselines: Optional[LeagueBaselines] = None,
        correction_manager: Optional[CorrectionManager] = None,
        weather_client: Optional[WeatherClient] = None,
        umpire_client: Optional[UmpireClient] = None,
        injury_client: Optional[InjuryClient] = None,
        feature_factory: Optional[FeatureFactory] = None,
    ):
        self.config = config or self._load_config(config_path)
        self.config_path = config_path
        self.league = league_baselines or LeagueBaselines.from_config(self.config)
        season = int(self.config.get("season", self.league.season))

        self.mlb_api = mlb_api or MLBStatsAPI(season=season)
        self.statcast_engine = statcast_engine or StatcastFeatureEngine(
            league_baselines=self.league,
        )
        self.prop_engine = prop_engine or PropEngine(
            league_baselines=self.league,
            config=self.config,
        )
        self.edge_calculator = edge_calculator or EdgeCalculator(config=self.config)
        self.odds_loader = odds_loader or CompositeOddsProvider.from_config(self.config)
        self.outcome_recorder = outcome_recorder or OutcomeRecorder.from_config(self.config)
        self.feature_store = feature_store or FeatureStore()
        self.lineup_intelligence = LineupIntelligence.from_config(
            self.config,
            league_baselines=self.league,
        )
        self._savant_client = SavantClient(league_baselines=self.league)
        self.correction_manager = correction_manager or CorrectionManager.from_config(
            self.config,
        )
        self.weather_client = weather_client or WeatherClient()
        self.umpire_client = umpire_client or UmpireClient()
        self.injury_client = injury_client or InjuryClient()
        self._park_estimator = ParkFactorEstimator(ParkFactorSettings.from_config(self.config))
        self._estimated_park_factors: dict[str, ParkFactors] = {}
        self.feature_factory = feature_factory or FeatureFactory(
            config=self.config,
            league_baselines=self.league,
            mlb_api=self.mlb_api,
            statcast_engine=self.statcast_engine,
            lineup_intelligence=self.lineup_intelligence,
            weather_client=self.weather_client,
            umpire_client=self.umpire_client,
            injury_client=self.injury_client,
            savant_client=self._savant_client,
            park_estimator=self._park_estimator,
            estimated_park_factors=self._estimated_park_factors,
        )

    def predict(
        self,
        game_date: Optional[str] = None,
        hitter_categories: Optional[tuple[PropCategory, ...]] = None,
        include_pitchers: bool = True,
        persist_features: bool = False,
        archive_predictions: Optional[bool] = None,
        apply_corrections: Optional[bool] = None,
        include_edges: Optional[bool] = None,
        odds_lines: Optional[list[OddsLine]] = None,
        min_edge_pct: Optional[float] = None,
        use_projected_lineups: bool = False,
    ) -> DailyPrediction:
        """
        Run the full daily prediction pipeline for a slate date.

        Returns DailyPrediction with hitter/pitcher projections and value_plays
        when odds data is available.

        apply_corrections: when True, applies learned corrections from
        CorrectionManager. Defaults to config learning.apply_corrections (False).

        include_edges: when True, computes value_plays from odds. Defaults to
        odds.enabled in config when None. Pass odds_lines to override file loading.
        """
        game_date = game_date or date.today().isoformat()
        use_corrections = (
            apply_corrections
            if apply_corrections is not None
            else self.correction_manager.settings.enabled
        )
        compute_edges = (
            include_edges if include_edges is not None else self.odds_loader.is_enabled()
        )

        logger.info(
            "Running daily predictions for %s (corrections=%s, edges=%s, projected_lineups=%s)",
            game_date,
            use_corrections,
            compute_edges,
            use_projected_lineups,
        )

        try:
            correction_ctx = self._prepare_corrections(use_corrections)

            bundles = self.build_feature_bundles(
                game_date,
                use_projected_lineups=use_projected_lineups,
            )
            if persist_features and bundles:
                self.feature_store.save(bundles, game_date)

            hitter_projections: list[PropProjection] = []
            for bundle in bundles:
                hitter_projections.extend(
                    self.prop_engine.project_hitter(bundle, categories=hitter_categories)
                )

            pitcher_projections: list[PropProjection] = []
            if include_pitchers:
                pitcher_projections = self._project_pitchers(game_date)

            if correction_ctx:
                hitter_projections = self.correction_manager.apply_projections(
                    hitter_projections, force=True
                )
                pitcher_projections = self.correction_manager.apply_projections(
                    pitcher_projections, force=True
                )

            value_plays = self._compute_value_plays(
                hitter_projections,
                pitcher_projections,
                game_date,
                compute_edges,
                odds_lines,
                min_edge_pct,
            )

            result = DailyPrediction(
                game_date=date.fromisoformat(game_date),
                hitter_projections=hitter_projections,
                pitcher_projections=pitcher_projections,
                value_plays=value_plays,
            )

            should_archive = (
                archive_predictions
                if archive_predictions is not None
                else self.outcome_recorder.should_archive_predictions()
            )
            if should_archive:
                self.outcome_recorder.archive_prediction(result)

            return result
        except (DataFetchError, OddsLoadError):
            raise
        except Exception as exc:
            raise PredictionPipelineError(
                f"Prediction failed for {game_date}: {exc}",
                hint="Check logs with --verbose; verify MLB API connectivity and config paths",
            ) from exc

    def build_feature_bundles(
        self,
        game_date: str,
        use_projected_lineups: bool = False,
    ) -> list[PlayerFeatureBundle]:
        """Assemble PlayerFeatureBundle rows for hitters on a date."""
        savant_csv = self._savant_csv_path()
        if savant_csv is None:
            logger.info(
                "No Savant CSV at configured path; using league baselines for Statcast fallbacks"
            )

        self.feature_factory.set_estimated_park_factors(self._estimated_park_factors)
        try:
            return self.feature_factory.build_bundles(
                game_date,
                use_projected_lineups=use_projected_lineups,
                savant_csv_path=savant_csv,
            )
        except DataFetchError:
            raise
        except Exception as exc:
            raise DataFetchError(
                f"Failed to build feature bundles for {game_date}",
                hint="Verify date format (YYYY-MM-DD) and network connectivity",
            ) from exc

    def enable_corrections(self, load_state: bool = True) -> None:
        """Explicitly enable learned corrections for subsequent predict() calls."""
        if load_state:
            loaded = self.correction_manager.load_state_if_exists()
            if not loaded:
                logger.warning(
                    "Corrections enabled but no state file at %s",
                    self.correction_manager.settings.state_path,
                )
        self.correction_manager.enable()

    def disable_corrections(self) -> None:
        """Disable corrections (default behavior)."""
        self.correction_manager.disable()

    def correction_status(self) -> dict[str, Any]:
        """Return current correction manager status."""
        return self.correction_manager.status()

    def load_latest_corrections(self) -> bool:
        """Load the latest persisted correction state if available."""
        return self.correction_manager.load_state_if_exists()

    def _compute_value_plays(
        self,
        hitter_projections: list[PropProjection],
        pitcher_projections: list[PropProjection],
        game_date: str,
        compute_edges: bool,
        odds_lines: Optional[list[OddsLine]],
        min_edge_pct: Optional[float],
    ) -> list[EdgeResult]:
        if not compute_edges:
            return []

        try:
            lines = odds_lines if odds_lines is not None else self.odds_loader.load(game_date)
        except OddsLoadError:
            raise
        except Exception as exc:
            logger.warning("Odds load failed; skipping edge calculation: %s", exc)
            return []

        if not lines:
            logger.debug("No odds lines available for %s", game_date)
            return []

        all_projections = hitter_projections + pitcher_projections
        edge_cfg = self.config.get("odds", {})
        min_edge = min_edge_pct
        if min_edge is None and "min_edge_pct" in edge_cfg:
            min_edge = float(edge_cfg["min_edge_pct"])

        value_plays = self.edge_calculator.find_value_plays(
            all_projections,
            lines,
            min_edge_pct=min_edge,
        )
        logger.info("Found %d value plays for %s", len(value_plays), game_date)
        return value_plays

    def _prepare_corrections(self, use_corrections: bool) -> bool:
        """
        Optionally apply model-parameter corrections before simulation.

        Returns True when output projection corrections should also be applied.
        """
        if not use_corrections:
            return False

        if not self.correction_manager.corrector.is_active():
            if not self.correction_manager.load_state_if_exists():
                logger.info(
                    "Corrections requested but no active state at %s; running without corrections",
                    self.correction_manager.settings.state_path,
                )
                return False

        if not self.correction_manager.corrector.is_active():
            return False

        pa_config = self.prop_engine.pa_config
        effective_league, _ = self.correction_manager.prepare(
            self.league,
            pa_config,
            self.prop_engine,
            force=True,
        )
        self._sync_league_context(effective_league)
        return True

    def _sync_league_context(self, league: LeagueBaselines) -> None:
        """Keep feature/statcast layers aligned with corrected league baselines."""
        self.league = league
        self.statcast_engine.league = league
        self.statcast_engine.savant.league = league
        self._savant_client.league = league
        self.lineup_intelligence.league = league
        self.feature_factory.sync_league(league)

    def rank_hitter_projections(
        self,
        projections: list[PropProjection],
        category: PropCategory,
        top_n: int = 25,
    ) -> list[PropProjection]:
        """Sort hitter projections for a category by projected value."""
        filtered = [p for p in projections if p.category == category]
        return sorted(filtered, key=lambda p: p.projected_value, reverse=True)[:top_n]

    def rank_pitcher_projections(
        self,
        projections: list[PropProjection],
        top_n: int = 15,
    ) -> list[PropProjection]:
        """Sort pitcher strikeout projections."""
        filtered = [p for p in projections if p.category == "strikeouts"]
        return sorted(filtered, key=lambda p: p.projected_value, reverse=True)[:top_n]

    def _project_pitchers(self, game_date: str) -> list[PropProjection]:
        try:
            pitchers = self.mlb_api.get_pitchers_for_date(game_date)
        except DataFetchError:
            raise
        except Exception as exc:
            raise DataFetchError(
                f"Failed to fetch pitchers for {game_date}",
            ) from exc

        results: list[PropProjection] = []
        for pitcher in pitchers:
            try:
                season, recent = self.mlb_api.get_pitching_stats(pitcher.player.mlb_id)
            except DataFetchError as exc:
                logger.warning(
                    "Skipping pitcher %s (id=%d): %s",
                    pitcher.player.name,
                    pitcher.player.mlb_id,
                    exc,
                )
                continue
            results.append(
                self.prop_engine.project_pitcher_strikeouts(pitcher, season, recent)
            )
        return results

    def _resolve_opposing_pitcher_statcast(
        self, hitter: HitterGameContext
    ) -> Optional[PitcherStatcastProfile]:
        pitcher_id = hitter.opposing_pitcher_id
        if not pitcher_id:
            return None

        try:
            season, recent = self.mlb_api.get_pitching_stats(pitcher_id)
        except DataFetchError:
            logger.debug(
                "Could not fetch stats for opposing pitcher %s; using league rates",
                hitter.opposing_pitcher_name,
            )
            return None

        k_pct = recent.k_pct or season.k_pct or self.league.k_pct
        bb_pct = recent.bb_pct or season.bb_pct or self.league.bb_pct
        hr_per_9 = recent.hr_per_9 or season.hr_per_9 or self.league.hr_per_9

        return self._savant_client.build_pitcher_profile_from_rates(
            player_id=pitcher_id,
            player_name=hitter.opposing_pitcher_name,
            k_pct=k_pct,
            bb_pct=bb_pct,
            hr_per_9=hr_per_9,
            sample_pa=int(recent.innings_pitched * 4.2),
        )

    def _matchup_context(self, hitter: HitterGameContext) -> MatchupContext:
        platoon_weight = float(
            self.config.get("pitcher_matchup", {}).get("platoon_weight", 0.25)
        )
        bats = hitter.player.bats
        throws = hitter.opposing_pitcher_throws

        platoon_advantage = 0.0
        if bats in ("L", "R") and throws in ("L", "R"):
            if bats != throws:
                platoon_advantage = platoon_weight
            else:
                platoon_advantage = -platoon_weight * 0.5

        return MatchupContext(
            platoon_advantage=platoon_advantage,
            recent_form_multiplier=1.0,
        )

    def load_estimated_park_factors(self, pairs_path: str | Path) -> None:
        """
        Fit dynamic park factors from a historical pairs CSV.

        Requires columns: ``venue``, ``category``, ``actual_value``.
        Respects ``park_factors.estimation_shrinkage``, ``min_games``, and
        ``blend_with_static`` from config.
        """
        path = Path(pairs_path)
        if not path.exists():
            logger.warning("Pairs file not found for park estimation: %s", path)
            return
        pairs = pd.read_csv(path)
        static = self.config.get("park_factors", {})
        static_venues = {k: v for k, v in static.items() if isinstance(v, dict)}
        estimated = self._park_estimator.estimate_from_pairs(pairs)
        self._estimated_park_factors = self._park_estimator.blend_with_static(
            estimated,
            static_venues,
        )
        self.feature_factory.set_estimated_park_factors(self._estimated_park_factors)
        logger.info("Loaded %d estimated park factor venues", len(self._estimated_park_factors))

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

    def _savant_csv_path(self) -> Optional[str]:
        savant_cfg = self.config.get("savant", {})
        csv_path = savant_cfg.get("csv_path")
        if not csv_path:
            return None
        path = Path(csv_path)
        if not path.is_absolute():
            root = Path(__file__).resolve().parents[2]
            path = root / csv_path
        return str(path) if path.exists() else None

    @staticmethod
    def _load_config(config_path: Optional[str | Path]) -> dict[str, Any]:
        if config_path is None:
            root = Path(__file__).resolve().parents[2]
            for candidate in (root / "config" / "config.json",):
                if candidate.exists():
                    config_path = candidate
                    break
        if config_path is None:
            return {}
        path = Path(config_path)
        if not path.exists():
            raise ConfigError(f"Config file not found: {path}")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigError(f"Invalid JSON in config: {path}") from exc