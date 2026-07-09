#!/usr/bin/env python3
"""
Record actual outcomes and append prediction-outcome pairs for retraining.

Run after a slate's games are final:
    python run_record_outcomes.py --date 2026-07-01
    python run_record_outcomes.py --date 2026-07-01 --verbose
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from src.learning.outcome_recorder import OutcomeRecorder
from src.learning.retrain_runner import RetrainRunner
from src.utils.errors import ConfigError, RetrainError
from src.utils.logging import setup_logging

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_CONFIG = 3


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pair archived predictions with MLB actuals for retraining.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--date", required=True, metavar="YYYY-MM-DD", help="Game date to record")
    parser.add_argument("--config", metavar="PATH", help="Path to config.json")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable DEBUG logging")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging(level=logging.DEBUG if args.verbose else logging.INFO)

    try:
        config = RetrainRunner.load_config(args.config)
        recorder = OutcomeRecorder.from_config(config)
        report = recorder.record_for_date(args.date)

        print(json.dumps(report.to_dict(), indent=2))
        if report.pairs_appended == 0 and report.final_games == 0:
            print(
                f"\nNo final games for {args.date} yet. Re-run after the slate completes.",
                file=sys.stderr,
            )
            return EXIT_ERROR

        print(
            f"\nRecorded {report.pairs_appended} pairs "
            f"({report.pairs_skipped_duplicate} duplicates skipped)"
        )
        print(f"Pairs file: {recorder._pairs_path()}")
        print("Next: python run_retrain.py")
        return EXIT_OK

    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    except RetrainError as exc:
        print(f"Recording error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())