"""
B1b — benchmark GBM predictions against the simulator baseline on held-out
DATES, using the existing A2 reconstruction verbatim as the baseline source
(so there is exactly one bundle-reconstruction path, not a drifting second one).

Flow per held-out date:
  1. run_reconstruct_date.reconstruct(date) -> simulator PropProjections +
     OutcomeRecords (the honest, leakage-safe baseline the roadmap defines).
  2. GBM predicts on the A3(+A4) rows for that same date -> PropProjections.
  3. Both scored via BacktestEngine.evaluate_predictions against the SAME
     OutcomeRecords, then compare_reports(baseline, candidate).

GATE GUARD (the important part): compare_reports silently skips any category
missing from either side (`if not b or not c: continue`), so a candidate could
appear to "win overall" merely because its worst category was absent from the
comparison. Before trusting `overall_improved`, we assert the compared category
set is exactly the expected gated set and is non-empty. A missing category is a
hard failure, not a silent pass.

This module imports run_reconstruct_date lazily: the offline harness exercises
the guard logic with synthetic reports and needs neither the MLB API nor a GBM.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from src.evaluation.backtest_engine import BacktestEngine, BacktestReport, OutcomeRecord
from src.models.dataclasses import PropProjection


@dataclass
class BenchmarkResult:
    expected_categories: tuple[str, ...]
    compared_categories: list[str]
    comparison: dict[str, Any]
    per_date: dict[str, int] = field(default_factory=dict)
    ok: bool = False
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "expected_categories": list(self.expected_categories),
            "compared_categories": self.compared_categories,
            "comparison": self.comparison,
            "per_date_pairs": self.per_date,
            "ok": self.ok,
            "error": self.error,
        }


def assert_comparable(
    comparison: dict[str, Any],
    expected: tuple[str, ...],
    baseline: Optional[BacktestReport] = None,
    candidate: Optional[BacktestReport] = None,
    min_samples: int = 1,
) -> list[str]:
    """Raise unless every expected category was compared with real samples on
    BOTH sides.

    Guards against two holes in compare_reports:
      (a) a category missing entirely from one side (the `if not b or not c`
          skip) — never enters `categories`; caught by the key check.
      (b) a category present but with ZERO matched pairs on a side. This is the
          dangerous one: evaluate_predictions(..., categories=cats) still emits
          a zero-sample metric with mae=0.0, so compare_reports shows it as the
          candidate's *best* category (mae_delta hugely negative) — a phantom
          win on a category the model never actually predicted. Presence of the
          key is NOT enough; we require n_samples >= min_samples on both sides.

    Pass baseline/candidate reports to enable the (b) check (strongly
    recommended). Returns the sorted list of validly-compared categories.
    """
    compared = sorted(comparison.get("categories", {}).keys())
    exp = sorted(expected)
    if not compared:
        raise ValueError(
            "No categories were compared — baseline and candidate share no "
            "category. `overall_improved` is meaningless; refusing to report a "
            "win. Check that the GBM emits the same PropCategory labels the "
            "simulator uses."
        )
    missing = set(exp) - set(compared)
    extra = set(compared) - set(exp)
    if missing or extra:
        raise ValueError(
            f"Compared categories {compared} != expected {exp} "
            f"(missing={sorted(missing)}, unexpected={sorted(extra)}). "
            "A category dropping out of the comparison would let a model pass "
            "the gate without being scored on it."
        )
    if baseline is not None and candidate is not None:
        thin: list[str] = []
        for cat in exp:
            b = baseline.metrics_by_category.get(cat)
            c = candidate.metrics_by_category.get(cat)
            bn = b.n_samples if b else 0
            cn = c.n_samples if c else 0
            if bn < min_samples or cn < min_samples:
                thin.append(f"{cat}(baseline_n={bn}, candidate_n={cn})")
        if thin:
            raise ValueError(
                "Zero/insufficient samples on a compared category — a phantom "
                f"win risk: {thin}. compare_reports scores an unpredicted "
                "category as mae=0.0, which would flatter the model. Refusing "
                "to trust overall_improved until both sides have >= "
                f"{min_samples} matched pairs per category."
            )
    return compared


def score_reports(
    baseline_projections: list[PropProjection],
    candidate_projections: list[PropProjection],
    outcomes: list[OutcomeRecord],
    categories: tuple[str, ...],
    engine: Optional[BacktestEngine] = None,
) -> tuple[BacktestReport, BacktestReport, dict[str, Any]]:
    """Score both sets against the same outcomes and compare. Pure/testable."""
    engine = engine or BacktestEngine()
    baseline = engine.evaluate_predictions(baseline_projections, outcomes, categories=categories)
    candidate = engine.evaluate_predictions(candidate_projections, outcomes, categories=categories)
    comparison = engine.compare_reports(baseline, candidate)
    return baseline, candidate, comparison


def run_benchmark(
    holdout_dates: list[str],
    gbm_predict_for_date: Callable[[str], list[PropProjection]],
    *,
    config: dict[str, Any],
    expected_categories: tuple[str, ...] = ("hits", "hrr", "home_runs", "strikeouts"),
    reconstruct_fn: Optional[Callable[..., Any]] = None,
) -> BenchmarkResult:
    """Full benchmark over held-out dates.

    gbm_predict_for_date(date) -> list[PropProjection] for that date across all
    expected categories (hitter categories from the hitter models, strikeouts
    from the pitcher model). reconstruct_fn is injectable for tests; in
    production it defaults to A2's reconstruct(), which returns simulator
    projections + outcomes for the date.
    """
    engine = BacktestEngine()
    all_baseline: list[PropProjection] = []
    all_candidate: list[PropProjection] = []
    all_outcomes: list[OutcomeRecord] = []
    per_date: dict[str, int] = {}

    recon = reconstruct_fn or _default_reconstruct

    try:
        for d in holdout_dates:
            sim_projs, outcomes = recon(d, config)
            gbm_projs = gbm_predict_for_date(d)
            all_baseline.extend(sim_projs)
            all_candidate.extend(gbm_projs)
            all_outcomes.extend(outcomes)
            per_date[d] = len(outcomes)

        baseline, candidate, comparison = score_reports(
            all_baseline, all_candidate, all_outcomes, expected_categories, engine
        )
        compared = assert_comparable(
            comparison, expected_categories, baseline=baseline, candidate=candidate
        )
        return BenchmarkResult(
            expected_categories=expected_categories,
            compared_categories=compared,
            comparison=comparison,
            per_date=per_date,
            ok=True,
        )
    except Exception as exc:
        return BenchmarkResult(
            expected_categories=expected_categories,
            compared_categories=[],
            comparison={},
            per_date=per_date,
            ok=False,
            error=str(exc),
        )


def _default_reconstruct(
    game_date: str, config: dict[str, Any]
) -> tuple[list[PropProjection], list[OutcomeRecord]]:
    """Production baseline: call A2 to get simulator projections + outcomes.

    A2's reconstruct() builds both the simulator PropProjections (hitter via
    evaluate_bundles, pitcher via project_pitcher_strikeouts) and the
    OutcomeRecords internally, but as written returns only a report dict. B1b
    needs the raw objects to score the GBM against the SAME outcomes.

    Two supported wirings, tried in order:
      1. If reconstruct() has been given the small additive change to collect
         its projections/outcomes (recommended — see A2_PATCH below), call it
         with collect=True and read them back.
      2. Otherwise fall back to reconstruct_objects() — a thin sibling that
         reuses A2's own bundle/outcome construction and returns objects. This
         keeps a single reconstruction path (it imports and calls the same
         FeatureFactory + PropEngine + OutcomeRecord logic).
    """
    from src import run_reconstruct_date as a2  # entry-point import path

    if hasattr(a2, "reconstruct_objects"):
        return a2.reconstruct_objects(game_date, config)

    raise NotImplementedError(
        "A2 does not yet expose raw projections/outcomes. Apply the small "
        "additive change in gbm_benchmark.A2_PATCH (add reconstruct_objects "
        "to run_reconstruct_date.py) so the simulator baseline and the GBM are "
        "scored on identical OutcomeRecords via one reconstruction path."
    )


# The minimal, additive A2 change B1b relies on. Additive-only (collection-safe,
# discipline #1): it factors the projection/outcome building A2 already does into
# a function that RETURNS the objects, and leaves reconstruct()'s report output
# untouched. Paste into run_reconstruct_date.py.
A2_PATCH = '''
def reconstruct_objects(game_date, config):
    """Return (simulator_projections, outcome_records) for one historical date.

    Reuses the SAME leakage-safe construction reconstruct() uses (AsOfMLBAPI +
    PointInTimeStats + FeatureFactory + PropEngine), so B1's benchmark scores
    the GBM against exactly the simulator baseline the roadmap defines. Additive:
    does not modify reconstruct() or touch the live model.
    """
    from datetime import date as _date
    season = _date.fromisoformat(game_date).year
    reslog = ResolutionLog()
    pit = PointInTimeStats(mlb_api=MLBStatsAPI(season=season), season=season)
    api = AsOfMLBAPI(as_of_date=game_date, pit=pit, reslog=reslog, season=season)

    league = LeagueBaselines.from_config(config)
    fantasy = FantasyScoring.from_config(config)
    factory = FeatureFactory(
        config=config, league_baselines=league, mlb_api=api,
        weather_client=InstrumentedWeatherClient(reslog),
        umpire_client=InstrumentedUmpireClient(reslog),
        injury_client=HistoricalInjuryClient(reslog),
        rolling_stats_provider=pit,
    )
    prop_engine = PropEngine(league_baselines=league, config=config)

    bundles = factory.build_bundles(game_date, use_projected_lineups=False, savant_csv_path=None)
    projections, outcomes = [], []
    actual_hitting, actual_pitching = api.get_actuals_for_date(game_date)

    for b in bundles:
        projections.extend(prop_engine.project_hitter(b, categories=HITTER_CATEGORIES))
        pid = b.hitter.player.mlb_id
        stats = actual_hitting.get(pid)
        if stats is None:
            continue
        for cat in HITTER_CATEGORIES:
            outcomes.append(OutcomeRecord(
                player_id=pid, player_name=b.hitter.player.name, game_date=game_date,
                category=cat, actual_value=compute_actual_value(stats, cat, fantasy)))

    for pctx in api.get_pitchers_for_date(game_date):
        pid = pctx.player.mlb_id
        season_s, recent_s = api.get_pitching_stats(pid)
        projections.append(prop_engine.project_pitcher_strikeouts(pctx, season_s, recent_s))
        p_stats = actual_pitching.get(pid)
        if p_stats is not None:
            outcomes.append(OutcomeRecord(
                player_id=pid, player_name=pctx.player.name, game_date=game_date,
                category="strikeouts", actual_value=float(p_stats.strikeouts)))

    return projections, outcomes
'''
