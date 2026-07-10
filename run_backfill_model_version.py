#!/usr/bin/env python3
"""
One-time backfill of blank model_version on existing prediction->outcome pairs.

BUG 2: every row in data/learning/prediction_outcomes.csv has a blank
model_version. This is safe to backfill to a SINGLE hash ONLY because the live
model did not change over the entire collection window (the collection freeze):
every historical pair was produced by the same model, so they all belong to one
version. The target hash is DERIVED from the current model config, not
hardcoded, so it stays correct if the config's canonical hash is ever recomputed.

Safety properties:
  * Only fills rows whose model_version is blank. Rows already carrying a
    version are left untouched.
  * REFUSES to run if any row already carries a NON-blank version DIFFERENT
    from the target — that would mean the freeze assumption is violated and a
    blind single-hash backfill would corrupt provenance. Resolve by hand.
  * Writes atomically via a temp file + replace, and takes a .bak first.
  * Idempotent: a second run finds nothing blank and is a no-op.

Usage:
    python run_backfill_model_version.py --config config/config.json
    python run_backfill_model_version.py --config config/config.json --dry-run
    python run_backfill_model_version.py --config config/config.json \
        --pairs data/learning/prediction_outcomes.csv
"""

from __future__ import annotations

import argparse
import csv
import shutil
import sys
from pathlib import Path

from src.learning.retrain_runner import RetrainRunner
from src.utils.model_version import model_version

DEFAULT_PAIRS = "data/learning/prediction_outcomes.csv"
COL = "model_version"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", metavar="PATH", help="Path to config.json (default resolution if omitted)")
    p.add_argument("--pairs", metavar="PATH", default=DEFAULT_PAIRS, help="Pairs CSV to backfill")
    p.add_argument("--dry-run", action="store_true", help="Report what would change; write nothing")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    config = RetrainRunner.load_config(args.config)
    target = model_version(config)
    if not target:
        print("Refusing to backfill: model_version(config) is empty.", file=sys.stderr)
        return 1

    path = Path(args.pairs)
    if not path.exists():
        print(f"Pairs file not found: {path}", file=sys.stderr)
        return 1

    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    if COL not in fieldnames:
        print(f"Column '{COL}' not present in {path}; run the recorder once to migrate schema first.", file=sys.stderr)
        return 1

    # Guard the freeze assumption: no row may already carry a *different* version.
    conflicting = {
        (r.get(COL) or "").strip()
        for r in rows
        if (r.get(COL) or "").strip() and (r.get(COL) or "").strip() != target
    }
    if conflicting:
        print(
            "Refusing to backfill: rows already carry non-blank version(s) "
            f"{sorted(conflicting)} != target {target}. The single-hash "
            "freeze assumption does not hold; resolve manually.",
            file=sys.stderr,
        )
        return 1

    blanks = [r for r in rows if not (r.get(COL) or "").strip()]
    print(f"Target model_version : {target}")
    print(f"Total rows           : {len(rows)}")
    print(f"Blank rows to fill    : {len(blanks)}")

    if not blanks:
        print("Nothing to backfill (already stamped). No-op.")
        return 0

    if args.dry_run:
        print("Dry run: no changes written.")
        return 0

    for r in blanks:
        r[COL] = target

    backup = path.with_suffix(path.suffix + ".bak")
    shutil.copy2(path, backup)
    tmp = path.with_suffix(".backfill.tmp")
    with tmp.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, restval="")
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(path)

    print(f"Backed up original to {backup}")
    print(f"Backfilled {len(blanks)} rows to {target} in {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
