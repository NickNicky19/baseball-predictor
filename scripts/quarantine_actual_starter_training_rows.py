#!/usr/bin/env python3
"""Create a time-safe hitter training derivative without realized starters.

``opp_sp_source='actual_starter'`` is post-game knowledge.  This tool never
edits the certified source; it creates one new, hash-bound derivative and
refuses to overwrite either output.  A future fitted challenger must consume
the derivative, not silently filter rows in memory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--expected-schema", default="a3.2")
    args = parser.parse_args(argv)

    source = Path(args.input)
    output = Path(args.output)
    manifest = Path(args.manifest)
    if not source.exists():
        raise FileNotFoundError(source)
    if output.exists() or manifest.exists():
        raise FileExistsError("refusing to overwrite a quarantined derivative or manifest")

    frame = pd.read_csv(source, low_memory=False)
    required = {"builder_schema", "opp_sp_source", "game_date", "game_pk", "player_id"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"source missing required columns: {missing}")
    schemas = set(frame["builder_schema"].dropna().astype(str))
    if schemas != {args.expected_schema}:
        raise ValueError(f"source schema {sorted(schemas)} != {[args.expected_schema]}")

    source_name = frame["opp_sp_source"].astype(str)
    actual = source_name.eq("actual_starter")
    unknown = ~source_name.isin({"probable", "actual_starter"})
    if unknown.any():
        values = sorted(source_name.loc[unknown].unique())
        raise ValueError(f"unknown opposing-starter provenance labels: {values}")
    if not actual.any():
        raise ValueError("source contains no actual_starter rows; refusing no-op derivative")

    kept = frame.loc[~actual].copy()
    key = ["game_pk", "player_id"]
    if kept[key].isna().any().any() or kept.duplicated(key).any():
        raise ValueError("quarantined derivative has null or duplicate game/player identity")
    if not kept["opp_sp_source"].astype(str).eq("probable").all():
        raise ValueError("non-probable starter rows survived quarantine")

    output.parent.mkdir(parents=True, exist_ok=True)
    kept.to_csv(output, index=False, compression="gzip" if output.suffix == ".gz" else None)
    payload = {
        "schema_version": "time-safe-training-quarantine-v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": str(source),
        "source_sha256": sha256_file(source),
        "output": str(output),
        "output_sha256": sha256_file(output),
        "builder_schema": args.expected_schema,
        "source_rows": int(len(frame)),
        "quarantined_rows": int(actual.sum()),
        "output_rows": int(len(kept)),
        "quarantine_rule": "opp_sp_source == actual_starter",
        "remaining_opp_sp_source_counts": {
            str(k): int(v) for k, v in kept["opp_sp_source"].value_counts().items()
        },
        "date_min": str(kept["game_date"].min()),
        "date_max": str(kept["game_date"].max()),
    }
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # fail closed with a useful terminal reason
        print(f"FATAL: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2)
