#!/usr/bin/env python3
"""
Retrain corrections from historical prediction-outcome pairs.

Examples:
    python run_retrain.py
    python run_retrain.py --pairs data/learning/prediction_outcomes.csv
    python run_retrain.py --lookback 14 --min-pairs 50 --verbose
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from src.learning.retrain_runner import RetrainRunner
from src.utils.errors import ConfigError, RetrainError
from src.utils.logging import setup_logging

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_CONFIG = 3


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fit and save bias corrections from prediction-outcome pairs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        help="Path to config.json (default: config/config.json)",
    )
    parser.add_argument(
        "--pairs",
        metavar="PATH",
        help="CSV with columns: player_id, game_date, category, predicted_value, actual_value",
    )
    parser.add_argument(
        "--lookback",
        type=int,
        metavar="DAYS",
        help="Only use pairs from the last N days",
    )
    parser.add_argument(
        "--min-pairs",
        type=int,
        metavar="N",
        help="Minimum aligned pairs required to fit",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="Fit corrections but do not persist state",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fit and report without saving correction state",
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

    try:
        config = RetrainRunner.load_config(args.config)
        runner = RetrainRunner.from_config(config)
        report = runner.run(
            pairs_path=args.pairs,
            lookback_days=args.lookback,
            min_pairs=args.min_pairs,
            save_state=not args.no_save and not args.dry_run,
            dry_run=args.dry_run,
        )

        if report.skipped:
            print(f"Retrain skipped: {report.skip_reason}", file=sys.stderr)
            return EXIT_ERROR

        print(json.dumps(report.to_dict(), indent=2))
        print(
            f"\nRetrain complete: {report.sample_size} pairs, "
            f"confidence={report.confidence:.2f}, MAE={report.weighted_mae:.3f}"
        )
        if report.state_path:
            print(f"Correction state saved to {report.state_path}")
            print("Apply with: python run_daily.py --apply-corrections")
        return EXIT_OK

    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    except RetrainError as exc:
        print(f"Retrain error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())