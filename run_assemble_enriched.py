#!/usr/bin/env python3
"""
Assemble the A4-enriched training set into single files for B1.

TrainingSetBuilder.assemble() globs 'hitters_*.csv', which after A4 would sweep
up BOTH the raw A3 shards AND their *_statcast enriched siblings and double the
rows. This assembler instead:

  - hitters:  concatenates ONLY the *_statcast.csv shards (the enriched ones)
  - pitchers: concatenates the raw shards (A4 is hitter-only; pitchers unchanged)

and enforces a COMPLETENESS GUARD: for every raw hitter shard there must be a
matching _statcast shard, and every assembled hitter row must carry
roller_schema. If any date wasn't enriched, it fails loudly rather than
silently training on a partially-enriched set (exactly the half-built-set
footgun the provenance gate exists to stop).

Output (gzipped, matching what run_train_gbm expects):
    data/training/training_hitters_<span>_statcast.csv.gz
    data/training/training_pitchers_<span>.csv.gz

Usage:
    python run_assemble_enriched.py --seasons 2023 2024 2025
    python run_assemble_enriched.py --seasons 2023 2024 2025 --training-dir data/training
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROLLER_SCHEMA = "a4.1"
BUILDER_SCHEMA = "a3.1"


def _season_shards(season_dir: Path) -> tuple[list[Path], list[Path], list[Path]]:
    """Return (enriched_hitter_shards, raw_hitter_shards, pitcher_shards)."""
    all_hitters = sorted(season_dir.glob("hitters_*.csv"))
    enriched = [p for p in all_hitters if p.stem.endswith("_statcast")]
    raw_hitters = [p for p in all_hitters if not p.stem.endswith("_statcast")]
    pitchers = sorted(
        p for p in season_dir.glob("pitchers_*.csv")
        if not p.stem.endswith("_statcast")
    )
    return enriched, raw_hitters, pitchers


def _date_of(shard: Path) -> str:
    # hitters_2024-08-25_statcast.csv -> 2024-08-25 ; pitchers_2024-08-25.csv -> 2024-08-25
    stem = shard.stem.replace("_statcast", "")
    return stem.split("_", 1)[1]


def assemble(seasons: list[int], training_dir: Path) -> dict[str, Path]:
    span = f"{min(seasons)}_{max(seasons)}" if len(seasons) > 1 else str(seasons[0])
    hitter_frames: list[pd.DataFrame] = []
    pitcher_frames: list[pd.DataFrame] = []
    problems: list[str] = []

    for season in seasons:
        sdir = training_dir / str(season)
        if not sdir.exists():
            problems.append(f"[{season}] directory missing: {sdir}")
            continue
        enriched, raw_hitters, pitchers = _season_shards(sdir)

        # Completeness guard: every raw hitter date must have an enriched twin.
        enriched_dates = {_date_of(p) for p in enriched}
        raw_dates = {_date_of(p) for p in raw_hitters}
        missing = sorted(raw_dates - enriched_dates)
        if missing:
            problems.append(
                f"[{season}] {len(missing)} hitter date(s) not enriched "
                f"(first few: {missing[:5]}). Run run_build_statcast_features "
                f"--season {season} before assembling."
            )
        if not enriched:
            problems.append(f"[{season}] no *_statcast hitter shards found.")

        for shard in enriched:
            df = pd.read_csv(shard, low_memory=False)
            hitter_frames.append(df)
        for shard in pitchers:
            pitcher_frames.append(pd.read_csv(shard, low_memory=False))

    if problems:
        for p in problems:
            print(f"REFUSING TO ASSEMBLE: {p}", file=sys.stderr)
        raise SystemExit(2)

    outputs: dict[str, Path] = {}

    h = pd.concat(hitter_frames, ignore_index=True)
    _verify_enriched(h)
    hpath = training_dir / f"training_hitters_{span}_statcast.csv.gz"
    h.to_csv(hpath, index=False)
    outputs["hitters"] = hpath

    p = pd.concat(pitcher_frames, ignore_index=True)
    _verify_pitcher(p)
    ppath = training_dir / f"training_pitchers_{span}.csv.gz"
    p.to_csv(ppath, index=False)
    outputs["pitchers"] = ppath

    print(f"Hitters : {len(h):>7} rows -> {hpath.name}")
    print(f"Pitchers: {len(p):>7} rows -> {ppath.name}")
    return outputs


def _verify_enriched(df: pd.DataFrame) -> None:
    if "roller_schema" not in df.columns:
        raise SystemExit("Assembled hitters missing roller_schema — not enriched.")
    bad = set(df["roller_schema"].dropna().unique()) - {ROLLER_SCHEMA}
    if bad:
        raise SystemExit(f"Assembled hitters have roller_schema {sorted(bad)}.")
    if df["roller_schema"].isna().any():
        n = int(df["roller_schema"].isna().sum())
        raise SystemExit(f"{n} assembled hitter rows have blank roller_schema.")
    if not any(c.startswith("roll15_") or c.startswith("roll30_") for c in df.columns):
        raise SystemExit("Assembled hitters have no roll15_/roll30_ columns.")


def _verify_pitcher(df: pd.DataFrame) -> None:
    if "builder_schema" not in df.columns:
        raise SystemExit("Assembled pitchers missing builder_schema.")
    bad = set(df["builder_schema"].dropna().unique()) - {BUILDER_SCHEMA}
    if bad:
        raise SystemExit(f"Assembled pitchers have builder_schema {sorted(bad)}.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Assemble A4-enriched training set for B1.")
    ap.add_argument("--seasons", type=int, nargs="+", required=True)
    ap.add_argument("--training-dir", default="data/training")
    args = ap.parse_args(argv)
    assemble(args.seasons, Path(args.training_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
