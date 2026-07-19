#!/usr/bin/env python3
"""Create one future-only official-MLB T−4 pitcher-context capture plan."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.run_forward_pitcher_context_collector import load_runtime  # noqa: E402
from src.data.mlb_api import MLBStatsAPI  # noqa: E402
from src.evaluation.shadow_capture_plan import (  # noqa: E402
    ShadowCapturePlanError,
    canonical_schedule_records,
    plan_from_schedule,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True)
    parser.add_argument("--runtime", type=Path, default=ROOT / "config/forward_pitcher_context_runtime_v1.json")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        runtime, runtime_sha = load_runtime(args.runtime)
        records = canonical_schedule_records(MLBStatsAPI().get_schedule(args.date))
        plan = plan_from_schedule(
            official_game_date=args.date,
            entry_hours=runtime["scheduler"]["entry_hours"],
            policy_sha256=runtime_sha,
            schedule_snapshot=records,
        )
        now = datetime.now(timezone.utc)
        if any(datetime.fromisoformat(target.entry_target_at_utc.replace("Z", "+00:00")) <= now for target in plan.targets):
            raise ShadowCapturePlanError("refusing to publish a pitcher-context plan after any T-horizon is due")
        schedule_path = args.out.with_name(args.out.stem + ".schedule.json")
        payload = {"schedule": records}
        schedule_path.parent.mkdir(parents=True, exist_ok=True)
        schedule_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        actual = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")).hexdigest()
        if actual != plan.schedule_snapshot_sha256:
            raise AssertionError("plan schedule hash drifted before publication")
        plan.write(args.out)
    except (OSError, ValueError, ShadowCapturePlanError) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 2
    print(f"PITCHER CONTEXT PLAN {args.date}")
    print(f"  targets: {len(plan.targets)}  T-{plan.entry_hours}h")
    print(f"  plan_sha256: {plan.plan_sha256}")
    print(f"  runtime_sha256: {runtime_sha}")
    print("  research-only; no pitcher prediction, selection, or betting decision follows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
