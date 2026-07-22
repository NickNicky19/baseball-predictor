#!/usr/bin/env python3
"""Record or verify the hard-keyed, tamper-evident forward shadow ledger.

This is intentionally *not* a name-matching adapter.  Entry input must already
have ``mlb_game_pk`` and ``player_id`` from an independently auditable identity
chain.  A raw odds CSV or the legacy name-keyed shadow comparison is rejected
by the required-column contract rather than silently becoming CLV evidence.
``verify`` prints the chain head hash; retain that hash outside this filesystem
if the record must be independently tamper-evident.

Examples:
  python run_shadow_ledger.py entries --input data/analysis/shadow/entries.csv
  python run_shadow_ledger.py resolutions --input data/analysis/shadow/resolutions.csv
  python run_shadow_ledger.py verify
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

from src.evaluation.shadow_ledger import (
    ForwardShadowLedger,
    ShadowEntry,
    ShadowLedgerError,
    ShadowResolution,
)


def _read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise ShadowLedgerError(f"input does not exist: {path}")
    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8-sig") as handle:
            return list(csv.DictReader(handle))
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            payload = payload.get("rows")
        if not isinstance(payload, list) or not all(isinstance(row, dict) for row in payload):
            raise ShadowLedgerError("JSON input must be a row list or {'rows': [...]} ")
        return payload
    raise ShadowLedgerError("input must be .csv or .json")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Hard-keyed tamper-evident forward shadow ledger")
    parser.add_argument(
        "--ledger",
        default="data/learning/shadow/forward_ledger.jsonl",
        help="append-only JSONL ledger path",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("entries", "resolutions"):
        child = sub.add_parser(name)
        child.add_argument("--input", required=True, help="strict .csv or .json input")
    sub.add_parser("verify")
    args = parser.parse_args(argv)

    ledger = ForwardShadowLedger(args.ledger)
    try:
        if args.command == "entries":
            report = ledger.append_entries(
                ShadowEntry.from_mapping(row) for row in _read_rows(Path(args.input))
            )
        elif args.command == "resolutions":
            report = ledger.append_resolutions(
                ShadowResolution.from_mapping(row) for row in _read_rows(Path(args.input))
            )
        else:
            report = ledger.verify()
    except (ShadowLedgerError, OSError, json.JSONDecodeError) as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return 2
    print(
        f"ledger={args.ledger} added={report.added} idempotent={report.idempotent} "
        f"total_records={report.total_records} head_hash={report.head_hash or 'EMPTY'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
