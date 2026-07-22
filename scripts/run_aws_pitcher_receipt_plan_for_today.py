#!/usr/bin/env python3
"""Publish the next America/New_York official-date receipt plan on AWS."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_aws_pitcher_receipt_plan import (
    AWSReceiptPlanError,
    AWSReceiptPlanTooLateError,
    build_plan,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan-dir", type=Path, required=True)
    parser.add_argument("--receipt-dir", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, default=ROOT / "config/forward_pitcher_context_runtime_v1.json")
    args = parser.parse_args(argv)
    try:
        # MLB schedule dates are organized in the league's Eastern time zone,
        # not by the host's UTC date near midnight.
        official_date = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
        print(json.dumps(build_plan(
            official_date=official_date,
            plan_dir=args.plan_dir,
            receipt_dir=args.receipt_dir,
            runtime_path=args.runtime,
        ), sort_keys=True))
    except AWSReceiptPlanTooLateError as exc:
        # The exact status is part of the deployment boundary: the installer
        # may defer a genuinely late plan until the next non-persistent timer
        # boundary, but must still reject every other failure.
        print(f"[FAIL] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3
    except (OSError, ValueError, RuntimeError, AWSReceiptPlanError) as exc:
        print(f"[FAIL] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
