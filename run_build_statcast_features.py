#!/usr/bin/env python3
"""
A4 — Join rolling Statcast features onto the A3 hitter training set.

Reads A3 hitter shards (or an assembled hitters CSV), attaches per-row
roll15/roll30 xwOBA/EV/barrel/hard-hit/whiff computed strictly from games
BEFORE each row's date, and writes an enriched copy. Pitchers are unchanged
(A4 is a hitter batted-ball signal; pitcher K modeling is B3/B4).

Usage:
    # smoke test: enrich a single A3 shard
    python run_build_statcast_features.py --shard data/training/2024/hitters_2024-04-05.csv

    # enrich an assembled set (writes *_statcast.csv.gz alongside)
    python run_build_statcast_features.py --assembled data/training/training_hitters_2024.csv.gz

    # enrich every hitter shard for a season, in place-adjacent files
    python run_build_statcast_features.py --season 2024

Requires pybaseball (pip install pybaseball). Every player-season Statcast
pull is disk-cached under data/cache/statcast/, so re-runs are near-free and
the job is fully resumable (already-enriched files are skipped unless
--overwrite).
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

from src.data.statcast_roller import (
    ROLLER_SCHEMA,
    RollerConfig,
    StatcastRoller,
    rolling_feature_columns,
)
from src.utils.logging import setup_logging

EXIT_OK = 0
EXIT_ERROR = 1


def enrich_frame(df: pd.DataFrame, roller: StatcastRoller) -> pd.DataFrame:
    rows = df.to_dict("records")
    roller.join_onto_rows(rows)
    enriched = pd.DataFrame(rows)
    enriched["roller_schema"] = ROLLER_SCHEMA
    return enriched


def _enriched_path(src: Path) -> Path:
    if src.name.endswith(".csv.gz"):
        return src.with_name(src.name[:-7] + "_statcast.csv.gz")
    return src.with_name(src.stem + "_statcast" + src.suffix)


def enrich_file(src: Path, roller: StatcastRoller, overwrite: bool, log) -> Path | None:
    out = _enriched_path(src)
    if out.exists() and not overwrite:
        log.info("Skip (exists): %s", out.name)
        return out
    df = pd.read_csv(src)
    if df.empty:
        log.info("Skip (empty): %s", src.name)
        return None
    enriched = enrich_frame(df, roller)
    tmp = out.with_suffix(out.suffix + ".tmp")
    enriched.to_csv(tmp, index=False)
    tmp.replace(out)
    added = len(rolling_feature_columns(roller.config.windows))
    log.info(
        "%s -> %s (+%d cols, %d rows, cache %s)",
        src.name, out.name, added, len(enriched), roller.cache_stats(),
    )
    return out


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Join rolling Statcast features onto A3 hitter rows (A4).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--shard", metavar="PATH", help="One A3 hitter shard CSV")
    src.add_argument("--assembled", metavar="PATH", help="An assembled hitters CSV(.gz)")
    src.add_argument("--season", type=int, metavar="YYYY", help="Enrich all hitter shards for a season")
    parser.add_argument("--training-dir", default="data/training")
    parser.add_argument("--cache-dir", default="data/cache/statcast")
    parser.add_argument("--windows", type=int, nargs="+", default=[15, 30])
    parser.add_argument("--rate-limit", type=float, default=2.0, metavar="SECONDS")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--verbose", "-v", action="store_true")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    setup_logging(level=logging.DEBUG if args.verbose else logging.INFO)
    log = logging.getLogger("build_statcast")

    roller = StatcastRoller(
        cache_dir=args.cache_dir,
        config=RollerConfig(windows=tuple(args.windows), rate_limit_seconds=args.rate_limit),
    )

    try:
        if args.shard:
            enrich_file(Path(args.shard), roller, args.overwrite, log)
        elif args.assembled:
            enrich_file(Path(args.assembled), roller, args.overwrite, log)
        else:
            shard_dir = Path(args.training_dir) / str(args.season)
            shards = sorted(shard_dir.glob("hitters_*.csv"))
            shards = [s for s in shards if not s.stem.endswith("_statcast")]
            if not shards:
                print(f"No hitter shards in {shard_dir}", file=sys.stderr)
                return EXIT_ERROR
            log.info("Enriching %d shards for %d", len(shards), args.season)
            for shard in shards:
                enrich_file(shard, roller, args.overwrite, log)

        print(f"Done. Statcast cache: {roller.cache_stats()}")
        return EXIT_OK
    except KeyboardInterrupt:
        print("\nInterrupted — cache is saved; re-run to resume.", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
