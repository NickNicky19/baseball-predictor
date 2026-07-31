"""Build separate immutable T-4 projected and T-1 confirmed plan files."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.shared_pa_dual_horizon_lanes_v1 import build_dual_horizon_plans


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--schedule-snapshot", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument(
        "--contract", type=Path,
        default=ROOT / "config/shared_pa_dual_horizon_lanes_v1.json",
    )
    args = parser.parse_args(argv)
    snapshot = json.loads(args.schedule_snapshot.read_bytes())
    records = snapshot.get("schedule", snapshot) if isinstance(snapshot, dict) else snapshot
    if not isinstance(records, list):
        raise ValueError("schedule snapshot must be a list or contain a schedule list")
    plans = build_dual_horizon_plans(
        official_game_date=args.date,
        schedule_snapshot=records,
        contract_path=args.contract,
    )
    for lane, plan in plans.items():
        plan.write(args.output_root / lane / f"{args.date}.plan.json")
    print(json.dumps({
        "schema_version": "shared-pa-dual-horizon-plan-build-v1",
        "official_game_date": args.date,
        "plans": {
            lane: {"plan_sha256": plan.plan_sha256, "targets": len(plan.targets)}
            for lane, plan in plans.items()
        },
        "research_only": True,
        "betting_authorized": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
