#!/usr/bin/env python3
"""
A2 — Reconstruct ONE historical date end-to-end, leakage-safe.

PointInTimeStats -> FeatureFactory bundles -> PropEngine -> evaluate_bundles,
with per-feature RESOLUTION LOGGING: what genuinely resolves for a past date
(as-of stats, actual lineups, park, umpire identity) vs what falls back to
defaults or is neutralized by policy (Statcast profiles, weather, platoon
splits, BvP, injuries). The resolution report IS the A3 scoping document.

Usage:
    python run_reconstruct_date.py --date 2025-06-15
    python run_reconstruct_date.py --date 2024-08-01 --no-pitchers --verbose

Leakage policy (strict by default):
- Player season/recent stats: PointInTimeStats game logs, strictly < date.
- Statcast profiles: savant_csv_path forced to None. A current-season Savant
  CSV is an "as of now" snapshot -> leaks for any past date. Profiles fall
  back to contact-conditional league baselines. A4 (per-game pybaseball
  Statcast) is where honest historical xwOBA/EV/barrel comes from.
- Platoon splits + BvP: MLB API returns season-to-NOW splits -> leaky.
  Neutralized (handedness-only platoon). Override with --allow-leaky-splits
  only to measure how much they'd matter; never for training data.
- Injury feed: reflects TODAY's IL -> wrong for a past date, and the actual
  lineup already proves availability. Bypassed.
- Lineups/umpire/weather from the game feed are pre-game-knowable facts of
  that day; using the persisted values is not leakage.

This script is ADDITIVE: no live module is modified. The live model stays
frozen (collection discipline #1); corrections are never applied here.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from src.data.injury_client import InjuryClient
from src.data.mlb_api import HittingStatsSnapshot, MLBStatsAPI, PitchingStatsSnapshot
from src.data.point_in_time import PointInTimeStats
from src.data.umpire_client import UmpireClient
from src.data.weather_client import WeatherClient
from src.evaluation.backtest_engine import BacktestEngine, OutcomeRecord
from src.features.feature_factory import FeatureFactory
from src.learning.outcome_recorder import compute_actual_value
from src.learning.retrain_runner import RetrainRunner
from src.models.dataclasses import (
    InjuryStatus,
    LeagueBaselines,
    WeatherContext,
)
from src.prediction.prop_engine import PropEngine
from src.simulation.monte_carlo import FantasyScoring
from src.utils.logging import setup_logging
from src.utils.model_version import model_version

HITTER_CATEGORIES = ("hits", "hrr", "home_runs")

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NO_DATA = 2
EXIT_CONFIG = 3

logger = logging.getLogger("reconstruct")


# ---------------------------------------------------------------------------
# Resolution logging — the core A2 deliverable
# ---------------------------------------------------------------------------


class ResolutionLog:
    """
    Counts, per feature source, how each lookup resolved for the target date.

    Statuses:
      resolved     — real point-in-time-valid data came back
      empty        — source answered but had no pre-date data (e.g. April
                     call-up with no games before the as-of date)
      default      — source unavailable; neutral default used
      neutralized  — deliberately disabled by leakage policy
      bypassed     — deliberately skipped (injury feed on past dates)
    """

    def __init__(self) -> None:
        self._counts: dict[str, Counter] = {}
        self._notes: dict[str, str] = {}

    def count(self, source: str, status: str) -> None:
        self._counts.setdefault(source, Counter())[status] += 1

    def note(self, source: str, text: str) -> None:
        self._notes[source] = text

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for source in sorted(set(self._counts) | set(self._notes)):
            counts = dict(self._counts.get(source, {}))
            entry: dict[str, Any] = {"counts": counts}
            if source in self._notes:
                entry["note"] = self._notes[source]
            out[source] = entry
        return out

    def print_table(self) -> None:
        print("\nFEATURE RESOLUTION (what a historical date can actually provide)")
        print("-" * 72)
        for source, entry in self.to_dict().items():
            counts = entry["counts"]
            summary = ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "-"
            print(f"  {source:<28} {summary}")
            if "note" in entry:
                print(f"  {'':<28} ^ {entry['note']}")
        print("-" * 72)


# ---------------------------------------------------------------------------
# As-of adapter — routes stat lookups through PointInTimeStats
# ---------------------------------------------------------------------------


class AsOfMLBAPI(MLBStatsAPI):
    """
    MLBStatsAPI whose player-stat lookups answer "as of" a historical date.

    Inherits schedule/lineup/identity/boxscore behavior unchanged (those are
    per-date or immutable lookups). Overrides exactly the four methods that
    would otherwise answer with season-to-NOW data:

      get_hitting_stats / get_pitching_stats -> PointInTimeStats (strict <)
      get_platoon_splits / get_bvp_stats     -> neutralized (policy)

    Because FeatureFactory builds MatchupIntelligence with this object as its
    data_provider (isinstance check passes), the matchup layer's recent-form
    lookups become as-of automatically. Likewise get_pitchers_for_date's
    expected-IP estimate uses the overridden get_pitching_stats, so the
    pitcher-K opportunity input is leakage-safe with zero live-code changes.
    """

    def __init__(
        self,
        as_of_date: str,
        pit: PointInTimeStats,
        reslog: ResolutionLog,
        allow_leaky_splits: bool = False,
        **kwargs: Any,
    ):
        super().__init__(**kwargs)
        self.as_of_date = as_of_date
        self.pit = pit
        self.reslog = reslog
        self.allow_leaky_splits = allow_leaky_splits

    def get_hitting_stats(
        self, player_id: int
    ) -> tuple[HittingStatsSnapshot, HittingStatsSnapshot]:
        season, recent = self.pit.get_hitting_stats_as_of(player_id, self.as_of_date)
        self.reslog.count(
            "hitting_stats_as_of", "resolved" if season.pa > 0 else "empty"
        )
        return season, recent

    def get_pitching_stats(
        self, player_id: int
    ) -> tuple[PitchingStatsSnapshot, PitchingStatsSnapshot]:
        season, recent = self.pit.get_pitching_stats_as_of(player_id, self.as_of_date)
        self.reslog.count(
            "pitching_stats_as_of",
            "resolved" if season.innings_pitched > 0 else "empty",
        )
        return season, recent

    def get_platoon_splits(
        self, player_id: int
    ) -> tuple[Optional[HittingStatsSnapshot], Optional[HittingStatsSnapshot]]:
        if self.allow_leaky_splits:
            self.reslog.count("platoon_splits", "leaky_allowed")
            return super().get_platoon_splits(player_id)
        self.reslog.count("platoon_splits", "neutralized")
        return None, None

    def get_bvp_stats(
        self, hitter_id: int, pitcher_id: int
    ) -> Optional[HittingStatsSnapshot]:
        if self.allow_leaky_splits:
            self.reslog.count("bvp_stats", "leaky_allowed")
            return super().get_bvp_stats(hitter_id, pitcher_id)
        self.reslog.count("bvp_stats", "neutralized")
        return None


# ---------------------------------------------------------------------------
# Instrumented / policy clients
# ---------------------------------------------------------------------------


class HistoricalInjuryClient(InjuryClient):
    """Past-date policy: actual lineup implies availability; feed is today's."""

    def __init__(self, reslog: ResolutionLog, **kwargs: Any):
        super().__init__(**kwargs)
        self.reslog = reslog

    def is_available(self, player_id: int) -> bool:
        self.reslog.count("injury_feed", "bypassed")
        return True

    def get_injury_status(self, player_id: int) -> InjuryStatus:
        return InjuryStatus(player_id=player_id, status="Active", is_active=True)


class InstrumentedWeatherClient(WeatherClient):
    """Counts real feed weather vs all-defaults (failed fetch / empty block)."""

    def __init__(self, reslog: ResolutionLog, **kwargs: Any):
        super().__init__(**kwargs)
        self.reslog = reslog

    def get_weather_for_game(
        self, game_pk: int, venue: str, game_date: str
    ) -> WeatherContext:
        ctx = super().get_weather_for_game(game_pk, venue, game_date)
        default = WeatherContext(venue=venue, game_date=game_date)
        self.reslog.count(
            "weather", "default" if ctx == default else "resolved"
        )
        return ctx


class InstrumentedUmpireClient(UmpireClient):
    """Counts plate-umpire identity resolution from the boxscore officials."""

    def __init__(self, reslog: ResolutionLog, **kwargs: Any):
        super().__init__(**kwargs)
        self.reslog = reslog

    def get_umpire_for_game(self, game_pk: int):
        ump = super().get_umpire_for_game(game_pk)
        self.reslog.count("umpire", "resolved" if ump is not None else "default")
        return ump


# ---------------------------------------------------------------------------
# Reconstruction pipeline
# ---------------------------------------------------------------------------


def reconstruct(
    game_date: str,
    config: dict[str, Any],
    api: AsOfMLBAPI,
    pit: PointInTimeStats,
    reslog: ResolutionLog,
    include_pitchers: bool = True,
) -> dict[str, Any]:
    """Build point-in-time bundles for a past date, project, and evaluate."""
    league = LeagueBaselines.from_config(config)
    fantasy = FantasyScoring.from_config(config)

    factory = FeatureFactory(
        config=config,
        league_baselines=league,
        mlb_api=api,
        weather_client=InstrumentedWeatherClient(reslog),
        umpire_client=InstrumentedUmpireClient(reslog),
        injury_client=HistoricalInjuryClient(reslog),
        rolling_stats_provider=pit,  # <- first real wiring of PointInTimeStats
    )
    prop_engine = PropEngine(league_baselines=league, config=config)
    backtest = BacktestEngine()

    # Policy notes surfaced in the report regardless of counts.
    reslog.note(
        "statcast_profiles",
        "savant_csv=None by policy: current CSV is as-of-NOW (leaky). All "
        "profiles fall back to contact-conditional league baselines. A4 "
        "(per-game Statcast) is the fix, and the main signal upgrade.",
    )
    reslog.note(
        "platoon_splits",
        "MLB API splits are season-to-now (leaky); neutralized. Platoon "
        "handedness itself still applies (static). A3 option: rebuild splits "
        "from PIT game logs if per-split logs prove fetchable.",
    )
    reslog.note("bvp_stats", "Career BvP queried now includes post-date PAs; neutralized.")
    reslog.note("injury_feed", "Bypassed: actual lineup implies availability on that date.")
    reslog.note(
        "umpire",
        "Identity from boxscore officials (day-of knowable). Tendencies are "
        "neutral (0.0) unless load_tendencies_from_pairs is wired — identity "
        "resolves, signal does not.",
    )

    # 1. Bundles (statcast forced to baselines; confirmed lineups only —
    # for a completed game the persisted battingOrder IS the confirmed lineup).
    bundles = factory.build_bundles(
        game_date, use_projected_lineups=False, savant_csv_path=None
    )
    if not bundles:
        return {
            "game_date": game_date,
            "error": "No bundles built — no persisted lineups for this date "
            "(future date, all postponed, or feed unavailable).",
            "resolution": reslog.to_dict(),
        }

    for b in bundles:
        reslog.count("statcast_profiles", "baseline_fallback")
        reslog.count(
            "lineup", f"status_{b.hitter.game.lineup_status}"
        )
        reslog.count(
            "opposing_pitcher",
            "resolved" if b.hitter.opposing_pitcher_id else "missing",
        )
        park_neutral = (
            b.park.hits_factor == 1.0
            and b.park.hr_factor == 1.0
            and b.park.runs_factor == 1.0
        )
        reslog.count("park_factors", "neutral_default" if park_neutral else "resolved")
        rich = b.metadata.get("rich_features", {}) or {}
        reslog.count(
            "rolling_features_pit",
            "resolved" if rich.get("recent_pa_15") else "empty",
        )

    # 2. Actual outcomes from final boxscores.
    actual_hitting, actual_pitching = api.get_actuals_for_date(game_date)

    outcomes: list[OutcomeRecord] = []
    for b in bundles:
        pid = b.hitter.player.mlb_id
        stats = actual_hitting.get(pid)
        if stats is None:
            reslog.count("actual_outcomes", "missing")
            continue
        reslog.count("actual_outcomes", "resolved")
        for cat in HITTER_CATEGORIES:
            outcomes.append(
                OutcomeRecord(
                    player_id=pid,
                    player_name=b.hitter.player.name,
                    game_date=game_date,
                    category=cat,
                    actual_value=compute_actual_value(stats, cat, fantasy),
                )
            )

    # 3. Simulate + score (this is evaluate_bundles' first real caller).
    hitter_report = backtest.evaluate_bundles(
        bundles, outcomes, prop_engine, categories=HITTER_CATEGORIES
    )

    # 4. Pitcher strikeouts (as-of stats feed both K rate and expected IP).
    pitcher_report_dict: Optional[dict[str, Any]] = None
    if include_pitchers:
        projections = []
        k_outcomes: list[OutcomeRecord] = []
        for pctx in api.get_pitchers_for_date(game_date):
            pid = pctx.player.mlb_id
            season, recent = api.get_pitching_stats(pid)
            projections.append(
                prop_engine.project_pitcher_strikeouts(pctx, season, recent)
            )
            p_stats = actual_pitching.get(pid)
            if p_stats is None:
                reslog.count("pitcher_actuals", "missing")
                continue
            reslog.count("pitcher_actuals", "resolved")
            k_outcomes.append(
                OutcomeRecord(
                    player_id=pid,
                    player_name=pctx.player.name,
                    game_date=game_date,
                    category="strikeouts",
                    actual_value=float(p_stats.strikeouts),
                )
            )
        if projections:
            pitcher_report_dict = backtest.evaluate_predictions(
                projections, k_outcomes, categories=("strikeouts",)
            ).to_dict()
        reslog.note(
            "pitcher_actuals",
            "'missing' usually means the schedule's probable never pitched "
            "(scratch) — a real-world gap the live path shares.",
        )

    return {
        "game_date": game_date,
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "model_version": model_version(config),
        "slate": {
            "bundles": len(bundles),
            "hitter_outcome_rows": len(outcomes),
        },
        "resolution": reslog.to_dict(),
        "hitter_backtest": hitter_report.to_dict(),
        "pitcher_backtest": pitcher_report_dict,
        "notes": (
            "Point-in-time reconstruction: stats strictly pre-date via "
            "PointInTimeStats; Statcast at baselines; splits/BvP neutralized; "
            "injuries bypassed. Expect hitter MAE worse than live — that is "
            "the honest historical floor A4 improves on, and the simulator "
            "baseline Phase-B models must beat."
        ),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reconstruct one historical date leakage-safe and score the simulator on it.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--date", required=True, metavar="YYYY-MM-DD", help="Historical slate date")
    parser.add_argument("--config", metavar="PATH", help="Path to config.json")
    parser.add_argument("--out", metavar="PATH", help="Report path (default: reports/reconstruction_<date>.json)")
    parser.add_argument("--no-pitchers", action="store_true", help="Skip pitcher strikeouts")
    parser.add_argument(
        "--allow-leaky-splits",
        action="store_true",
        help="Use season-to-now platoon/BvP (LEAKY — diagnostics only, never training)",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="DEBUG logging")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    setup_logging(level=logging.DEBUG if args.verbose else logging.INFO)

    try:
        target = date.fromisoformat(args.date)
    except ValueError:
        print(f"Invalid date: {args.date} (want YYYY-MM-DD)", file=sys.stderr)
        return EXIT_CONFIG
    if target >= date.today():
        print("Date must be in the past — this is a historical reconstruction.", file=sys.stderr)
        return EXIT_CONFIG

    try:
        config = RetrainRunner.load_config(args.config)
        # Season must match the TARGET date's year, not config's current
        # season — a 2024 date needs 2024 schedules and 2024 game logs.
        season = target.year
        reslog = ResolutionLog()
        pit = PointInTimeStats(mlb_api=MLBStatsAPI(season=season), season=season)
        api = AsOfMLBAPI(
            as_of_date=args.date,
            pit=pit,
            reslog=reslog,
            allow_leaky_splits=args.allow_leaky_splits,
            season=season,
        )

        report = reconstruct(
            args.date,
            config=config,
            api=api,
            pit=pit,
            reslog=reslog,
            include_pitchers=not args.no_pitchers,
        )

        out_path = Path(args.out) if args.out else Path("reports") / f"reconstruction_{args.date}.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

        reslog.print_table()
        if "error" in report:
            print(f"\n{report['error']}", file=sys.stderr)
            print(f"Report: {out_path}")
            return EXIT_NO_DATA

        hb = report["hitter_backtest"]
        print(f"\nRECONSTRUCTION {args.date}  (model_version {report['model_version']})")
        print(f"  bundles: {report['slate']['bundles']}   matched pairs: {hb['matched_pairs']}")
        for cat, m in hb["metrics"].items():
            print(f"  {cat:<10} n={m['n_samples']:<4} MAE={m['mae']:.3f}  bias={m['mean_error']:+.3f}")
        if report.get("pitcher_backtest"):
            for cat, m in report["pitcher_backtest"]["metrics"].items():
                print(f"  {cat:<10} n={m['n_samples']:<4} MAE={m['mae']:.3f}  bias={m['mean_error']:+.3f}")
        print(f"\nReport: {out_path}")
        return EXIT_OK

    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return EXIT_ERROR
    except Exception as exc:  # surface anything else with context
        logger.exception("Reconstruction failed for %s", args.date)
        print(f"Reconstruction failed: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
