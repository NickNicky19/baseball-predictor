#!/usr/bin/env python3
"""Build a future-only, hard-keyed T-horizon capture plan from MLB's schedule.

This does not collect odds or create shadow-ledger entries. It records exactly
which official MLB games an external collector must account for. The plan is
content-addressed and carries the exact policy hash that supplied entry_hours.

Run before the earliest T-horizon on a future slate. It refuses a plan with an
already due target; creating a plan after the fact cannot manufacture forward
evidence for a missed capture.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.data.mlb_api import MLBStatsAPI
from src.evaluation.shadow_provider_adapter import load_research_selection_policy
from src.evaluation.shadow_capture_plan import (
    ShadowCapturePlanError,
    canonical_schedule_records,
    plan_from_schedule,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, help="Official MLB slate date (YYYY-MM-DD)")
    parser.add_argument("--policy", default="config/shadow_hits_research_policy.json")
    parser.add_argument(
        "--out",
        help="Plan path (default: data/learning/shadow/plans/capture_plan_<date>.json)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    policy_path = Path(args.policy)
    policy_full_sha = hashlib.sha256(Path(args.policy).read_bytes()).hexdigest()
    policy = load_research_selection_policy(
        policy_path,
        expected_sha256=policy_full_sha,
    )
    records = canonical_schedule_records(MLBStatsAPI().get_schedule(args.date))
    plan = plan_from_schedule(
        official_game_date=args.date,
        entry_hours=policy.entry_hours,
        # ``load_policy`` exposes a short diagnostic fingerprint for existing
        # reports. A capture plan is an immutable evidence boundary and must
        # bind the actual full file digest, never a padded look-alike.
        policy_sha256=policy_full_sha,
        schedule_snapshot=records,
    )
    now = datetime.now(timezone.utc)
    due = [target for target in plan.targets if datetime.fromisoformat(
        target.entry_target_at_utc.replace("Z", "+00:00")
    ) <= now]
    if due:
        raise ShadowCapturePlanError(
            "refusing to create a forward capture plan after its T-horizon is due: "
            + ", ".join(str(target.mlb_game_pk) for target in due[:10])
        )

    output = Path(args.out or f"data/learning/shadow/plans/capture_plan_{args.date}.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    snapshot = output.with_name(output.stem + ".schedule.json")
    snapshot_payload = {"schedule": records}
    snapshot.write_text(json.dumps(snapshot_payload, indent=2) + "\n", encoding="utf-8")
    digest = hashlib.sha256(json.dumps(snapshot_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")).hexdigest()
    if digest != plan.schedule_snapshot_sha256:
        raise AssertionError("schedule snapshot hash drifted before plan publication")
    plan.write(output)

    print(f"CAPTURE PLAN  {args.date}")
    print(f"  targets: {len(plan.targets)}  T-{plan.entry_hours}h")
    print(f"  plan sha: {plan.plan_sha256}")
    print(f"  schedule snapshot: {snapshot} sha {plan.schedule_snapshot_sha256}")
    print(f"  policy sha: {policy_full_sha}  status=RESEARCH_ONLY")
    print("  no betting decision follows; this is capture-accountability evidence only")
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ShadowCapturePlanError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        raise SystemExit(2)
