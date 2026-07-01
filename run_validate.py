#!/usr/bin/env python3
"""
End-to-end pipeline validation on historical dates.

Examples:
    python run_validate.py --from-pairs
    python run_validate.py --dates 2026-06-25,2026-06-26 --apply-corrections
    python run_validate.py --from-pairs --retrain --min-pairs 25
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from src.evaluation.pipeline_validator import PipelineValidator
from src.learning.retrain_runner import RetrainRunner
from src.utils.errors import ConfigError, RetrainError
from src.utils.logging import setup_logging

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_CONFIG = 3


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate prediction pipeline on historical slates.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--config", metavar="PATH", help="Path to config.json")
    parser.add_argument(
        "--dates",
        metavar="DATE,DATE,...",
        help="Comma-separated dates to validate",
    )
    parser.add_argument(
        "--from-pairs",
        action="store_true",
        help="Derive dates from prediction_outcomes.csv (respects lookback)",
    )
    parser.add_argument(
        "--pairs",
        metavar="PATH",
        help="Override pairs CSV path",
    )
    parser.add_argument("--lookback", type=int, metavar="DAYS", help="Lookback window for --from-pairs")
    parser.add_argument(
        "--apply-corrections",
        action="store_true",
        help="Validate with corrections enabled (only with --rerun)",
    )
    parser.add_argument(
        "--with-edges",
        action="store_true",
        help="Include edge calculation when re-running predictions",
    )
    parser.add_argument(
        "--rerun",
        action="store_true",
        help="Re-run live predictions instead of using archives",
    )
    parser.add_argument(
        "--retrain",
        action="store_true",
        help="After validation, run RetrainRunner if enough pairs exist",
    )
    parser.add_argument("--min-pairs", type=int, metavar="N", help="Min pairs for --retrain")
    parser.add_argument(
        "--output",
        "-o",
        metavar="PATH",
        default="data/learning/validation_report.json",
        help="Save JSON report to this path",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable DEBUG logging")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging(level=logging.DEBUG if args.verbose else logging.INFO)

    try:
        config = RetrainRunner.load_config(args.config)
        validator = PipelineValidator(config=config)

        if args.retrain:
            report = validator.validate_and_retrain(
                pairs_path=args.pairs,
                min_pairs=args.min_pairs,
            )
        elif args.from_pairs:
            report = validator.validate_from_pairs_csv(
                pairs_path=args.pairs,
                lookback_days=args.lookback,
                apply_corrections=args.apply_corrections,
            )
        elif args.dates:
            dates = [d.strip() for d in args.dates.split(",") if d.strip()]
            report = validator.validate_dates(
                dates,
                apply_corrections=args.apply_corrections,
                include_edges=args.with_edges,
                rerun_predictions=args.rerun,
            )
        else:
            print("Specify --from-pairs, --dates, or --retrain", file=sys.stderr)
            return EXIT_ERROR

        validator.save_report(report, args.output)
        print(json.dumps(report.to_dict(), indent=2))
        print(
            f"\nValidated {report.dates_evaluated} dates, "
            f"{report.total_matched_pairs} pairs, "
            f"mean MAE={report.mean_weighted_mae:.4f}"
        )
        print(f"Report saved to {args.output}")
        return EXIT_OK

    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    except RetrainError as exc:
        print(f"Validation error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())