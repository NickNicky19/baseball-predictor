#!/usr/bin/env python3
"""Fail-closed audit of expected vs terminal forward capture attempts."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.evaluation.shadow_capture_plan import (
    ShadowCapturePlanError,
    assess_capture_attempts,
    load_capture_attempts,
    load_capture_plan,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--attempts", required=True, help="Append-only terminal-receipt JSONL")
    parser.add_argument("--as-of", help="UTC timestamp; default is now")
    parser.add_argument("--out", help="Optional JSON report path")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    plan = load_capture_plan(args.plan)
    attempts = load_capture_attempts(args.attempts)
    as_of = args.as_of or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    report = assess_capture_attempts(plan, attempts, assessed_at_utc=as_of)
    payload = report.to_dict()
    print("FORWARD CAPTURE COVERAGE")
    print(f"  plan: {report.plan_sha256}")
    print(f"  due: {report.due_targets}  future: {report.future_targets}")
    print(f"  captured: {report.captured_targets}")
    print(f"  no eligible market: {report.no_eligible_market_targets}")
    print(f"  source error: {report.source_error_targets}")
    print(f"  missing: {len(report.missing_target_ids)}")
    if args.out:
        output = Path(args.out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {output}")
    if not report.complete:
        print("[FAIL] capture accountability is incomplete; this slate cannot support forward evidence")
        return 2
    print("[OK] every due game has one terminal capture observation")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, ShadowCapturePlanError) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        raise SystemExit(2)
