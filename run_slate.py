#!/usr/bin/env python3
"""
Full-slate daily prediction for automation (GitHub Actions).

ONE predict() call generates EVERY graded category at once — hits, hrr,
home_runs (hitters) + strikeouts (pitchers) — exactly like the GUI's
"Run all categories" button. Because config has archive_on_predict=True,
predict() automatically writes the rich JSON archive to
data/learning/predictions/predictions_<date>.json, including the per-row
`simulation` block (n_sims, mean, p10/p90, p_ge_threshold) that the flat
CSV path throws away.

Why this exists: run_daily.py takes a single --category (default hrr) and
its --format flag means output FILE FORMAT, not "all categories". Wiring
Actions to run_daily.py collected hrr-only, no simulation distributions.
This entry point is the automation-safe equivalent of the GUI.

The live model is untouched: same predict(), same math. apply_corrections
defaults to config (learning.apply_corrections=False) — frozen during the
collection window (discipline #1). Do not pass --apply-corrections until
the promotion gate (discipline #4) is cleared.

Usage:
    python run_slate.py --date 2026-07-08
    python run_slate.py                      # today (US slate date)

The default is the current K/BB research baseline.  This does not authorize
betting; the market-output policy remains fail closed.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

from src.models.dataclasses import PropCategory
from src.learning.prediction_archive import PredictionArchive
from src.prediction import DailyPredictor
from src.utils.errors import ConfigError, DataFetchError, PredictorError
from src.utils.logging import setup_logging

# The categories graded by this project, matching the GUI's TABS. Fantasy is
# intentionally excluded (not a prop being graded). If gui.py's TABS changes,
# change this to match so automation and manual runs never drift.
HITTER_CATEGORIES: tuple[PropCategory, ...] = ("hits", "hrr", "home_runs")  # type: ignore[assignment]
INCLUDE_PITCHERS: bool = True  # strikeouts
DEFAULT_RESEARCH_CONFIG = Path("config/config.kbb.json")

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NO_DATA = 2
EXIT_CONFIG = 3


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run ALL graded prop categories for a slate in one pass "
        "(automation equivalent of the GUI 'Run all categories').",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--date",
        default=date.today().isoformat(),
        metavar="YYYY-MM-DD",
        help="Slate date (default: today)",
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        help="Path to model config (default: config/config.kbb.json, current research baseline)",
    )
    parser.add_argument(
        "--archive-dir",
        metavar="PATH",
        help=(
            "Write the rich prediction archive to this directory without changing "
            "model configuration or prediction provenance. The forward collector "
            "uses a directory outside its clean release checkout."
        ),
    )
    parser.add_argument(
        "--include-projected-lineups",
        action="store_true",
        help="Include projected lineups when confirmed orders are unavailable",
    )
    parser.add_argument(
        "--apply-corrections",
        action="store_true",
        help="Apply learned corrections. LEAVE OFF during the collection "
        "window (discipline #1: model frozen). Off by default.",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Bypass MLB API day cache for this run",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable DEBUG logging",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging(level=logging.DEBUG if args.verbose else logging.INFO)
    log = logging.getLogger("run_slate")

    try:
        config_path = Path(args.config) if args.config else DEFAULT_RESEARCH_CONFIG
        predictor = DailyPredictor(config_path=config_path)
        archive_dir = (
            Path(args.archive_dir).resolve()
            if args.archive_dir
            else Path("data/learning/predictions")
        )
        if args.archive_dir:
            predictor.outcome_recorder.archive = PredictionArchive(
                archive_dir=str(archive_dir),
                project_root=Path.cwd(),
            )

        if args.refresh:
            predictor.mlb_api.clear_cache(args.date)

        # ONE call, every category. archive_predictions left as None so it
        # follows config (archive_on_predict=True) — this is what writes the
        # rich JSON archive the outcome recorder later grades.
        result = predictor.predict(
            args.date,
            hitter_categories=HITTER_CATEGORIES,
            include_pitchers=INCLUDE_PITCHERS,
            # Persist the exact feature bundles as well as the prediction
            # archive. This makes factual fallback rates auditable per slate;
            # it does not change any feature, probability, or simulation seed.
            persist_features=True,
            # The automation archive is the candidate source for future
            # hard-keyed shadow entries, so record decision-time code/config
            # provenance now. Archives without it are deliberately ineligible
            # for forward-shadow evidence.
            capture_prediction_provenance=True,
            apply_corrections=True if args.apply_corrections else None,
            use_projected_lineups=args.include_projected_lineups,
        )

        n_hit = len(result.hitter_projections)
        n_pit = len(result.pitcher_projections)
        cats = sorted({p.category for p in result.hitter_projections}
                      | {p.category for p in result.pitcher_projections})

        if n_hit == 0 and n_pit == 0:
            log.warning("No projections generated for %s (no slate / no data).", args.date)
            print(f"No projections for {args.date}.", file=sys.stderr)
            return EXIT_NO_DATA

        print(
            f"Slate {args.date}: {n_hit} hitter + {n_pit} pitcher projections "
            f"across {cats}."
        )
        print(f"Archive: {archive_dir / f'predictions_{args.date}.json'} (includes simulation blocks).")
        print(
            f"Feature snapshot: data/features/{args.date}/ "
            "(manifest-verified input-health evidence)."
        )
        return EXIT_OK

    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    except (DataFetchError, PredictorError) as exc:
        print(f"Prediction error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
