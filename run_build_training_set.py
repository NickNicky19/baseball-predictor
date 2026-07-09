#!/usr/bin/env python3
"""
A3 — Build the 2023-2025 point-in-time training set (Phase-B input).

Iterates every regular-season date, emitting per-date shards of hitter and
pitcher rows (raw as-of features + observed outcomes) under data/training/,
with disk-cached, rate-limited MLB API access. Fully resumable: completed
dates are recorded in a manifest and skipped on re-run; every HTTP response
is cached to disk, so a crash or Ctrl-C costs nothing.

Usage:
    python run_build_training_set.py --seasons 2024 --limit-dates 3   # smoke test
    python run_build_training_set.py --seasons 2023 2024 2025          # the real run
    python run_build_training_set.py --assemble-only --seasons 2023 2024 2025

Expect roughly 5-6k API calls per season cold (schedules + one feed per
game + one game log per player-season + identities). At the default 0.4s
interval that is ~40-50 min per season the first time and near-instant on
re-runs. Leave it running; interrupt freely.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from src.data.http_cache import RateLimiter
from src.data.point_in_time import PointInTimeStats
from src.learning.retrain_runner import RetrainRunner
from src.learning.training_set_builder import (
    CachedMLBAPI,
    TrainingSetBuilder,
    season_dates,
)
from src.utils.logging import setup_logging

EXIT_OK = 0
EXIT_ERROR = 1


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the point-in-time training set (A3).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--seasons", nargs="+", type=int, default=[2023, 2024, 2025])
    parser.add_argument("--start", metavar="YYYY-MM-DD", help="Override window start (single-season runs)")
    parser.add_argument("--end", metavar="YYYY-MM-DD", help="Override window end (single-season runs)")
    parser.add_argument("--limit-dates", type=int, metavar="N", help="Build at most N new dates per season (smoke tests)")
    parser.add_argument("--rebuild", action="store_true", help="Ignore the manifest and rebuild dates")
    parser.add_argument("--rate-interval", type=float, default=0.4, metavar="SECONDS", help="Min seconds between real API calls (default 0.4)")
    parser.add_argument("--cache-dir", default="data/cache/http", metavar="PATH")
    parser.add_argument("--out-dir", default="data/training", metavar="PATH")
    parser.add_argument("--config", metavar="PATH", help="Path to config.json")
    parser.add_argument("--no-assemble", action="store_true", help="Skip final shard assembly")
    parser.add_argument("--assemble-only", action="store_true", help="Only assemble existing shards")
    parser.add_argument("--verbose", "-v", action="store_true")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    setup_logging(level=logging.DEBUG if args.verbose else logging.INFO)
    log = logging.getLogger("build_training_set")

    if (args.start or args.end) and len(args.seasons) > 1:
        print("--start/--end only make sense with a single --seasons value.", file=sys.stderr)
        return EXIT_ERROR

    config = RetrainRunner.load_config(args.config)
    limiter = RateLimiter(min_interval_seconds=args.rate_interval)
    last_builder = None

    try:
        for season in args.seasons:
            api = CachedMLBAPI(
                season=season,
                cache_dir=args.cache_dir,
                rate_limiter=limiter,
            )
            pit = PointInTimeStats(mlb_api=api, season=season)
            builder = TrainingSetBuilder(api, pit, config, out_dir=args.out_dir)
            last_builder = builder

            if not args.assemble_only:
                dates = season_dates(season, args.start, args.end)
                log.info("Season %d: %d dates in window", season, len(dates))
                builder.build_dates(dates, rebuild=args.rebuild, limit=args.limit_dates)
                done = builder.manifest.get("dates", {})
                total_h = sum(d.get("hitter_rows", 0) for d in done.values())
                total_p = sum(d.get("pitcher_rows", 0) for d in done.values())
                print(
                    f"Season {season}: {len(done)} dates recorded, "
                    f"{total_h} hitter rows, {total_p} pitcher rows "
                    f"(cache {api.cache_stats()})"
                )

        if last_builder is not None and not args.no_assemble:
            outputs = last_builder.assemble(args.seasons)
            for kind, path in outputs.items():
                print(f"Assembled {kind}: {path}")

        print(f"Manifests + shards: {Path(args.out_dir).resolve()}")
        return EXIT_OK

    except KeyboardInterrupt:
        print(
            "\nInterrupted — progress is saved. Re-run the same command to resume.",
            file=sys.stderr,
        )
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
