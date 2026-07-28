#!/usr/bin/env python3
"""Observe existing T-4 batter ledgers and publish exact-byte inventories."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.shared_pa_batter_evidence_inventory import (
    BatterEvidenceInventoryError,
    assemble_side_inventory,
    parse_json_object_bytes,
    record_missing_plan_attempt,
    stable_read_bytes,
    unlinked_regular_file_exists,
)
from src.evaluation.shadow_capture_plan import ShadowCapturePlan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--official-date")
    parser.add_argument("--plan-dir", type=Path, required=True)
    parser.add_argument("--roster-ledger-root", type=Path, required=True)
    parser.add_argument("--history-ledger-root", type=Path, required=True)
    parser.add_argument("--inventory-root", type=Path, required=True)
    parser.add_argument("--target-id")
    parser.add_argument("--side", choices=("away", "home"))
    parser.add_argument("--observed-at-utc")
    args = parser.parse_args(argv)
    observed = args.observed_at_utc or datetime.now(timezone.utc).isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")
    if args.official_date is None:
        try:
            observed_utc = datetime.fromisoformat(observed.replace("Z", "+00:00"))
        except ValueError:
            print("[FAIL] observed timestamp must be timezone-aware ISO", file=sys.stderr)
            return 2
        if observed_utc.tzinfo is None or observed_utc.utcoffset() is None:
            print("[FAIL] observed timestamp must be timezone-aware ISO", file=sys.stderr)
            return 2
        observed_utc = observed_utc.astimezone(timezone.utc)
        statuses = []
        for candidate in (
            observed_utc.date() - timedelta(days=2),
            observed_utc.date() - timedelta(days=1),
            observed_utc.date(),
        ):
            if candidate.year == 2026 and candidate.month == 5:
                continue
            child = [
                "--official-date", candidate.isoformat(),
                "--plan-dir", str(args.plan_dir),
                "--roster-ledger-root", str(args.roster_ledger_root),
                "--history-ledger-root", str(args.history_ledger_root),
                "--inventory-root", str(args.inventory_root),
                "--observed-at-utc", observed,
            ]
            if args.target_id is not None:
                child.extend(("--target-id", args.target_id, "--side", args.side))
            statuses.append(main(child))
        return max(statuses, default=0)
    official_date = args.official_date
    # Normalize and reject the sealed month before constructing or reading a source path.
    try:
        parsed_date = date.fromisoformat(official_date)
    except ValueError:
        print("[FAIL] official date must be canonical ISO", file=sys.stderr)
        return 2
    if parsed_date.isoformat() != official_date:
        print("[FAIL] official date must be canonical ISO", file=sys.stderr)
        return 2
    if parsed_date.year == 2026 and parsed_date.month == 5:
        print("[FAIL] sealed May 2026 is rejected before source paths", file=sys.stderr)
        return 2
    if (args.target_id is None) != (args.side is None):
        parser.error("--target-id and --side must be supplied together")
    try:
        plan_path = args.plan_dir / f"{official_date}.plan.json"
        if not unlinked_regular_file_exists(plan_path, "T-4 plan path"):
            missing = record_missing_plan_attempt(
                official_game_date=official_date,
                observed_at_utc=observed,
                output_root=args.inventory_root,
            )
            result = {
                "schema_version": "shared-pa-batter-evidence-tick-v1",
                "state": "no_immutable_plan",
                "official_game_date": official_date,
                "durable_attempt": missing,
                "research_only": True,
                "betting_authorized": False,
            }
        else:
            plan_raw = stable_read_bytes(plan_path, "T-4 plan")
            plan = ShadowCapturePlan.from_mapping(
                parse_json_object_bytes(plan_raw, "T-4 plan")
            )
            requested = (
                [(args.target_id, args.side)]
                if args.target_id is not None
                else [
                    (target.target_id, side)
                    for target in plan.targets
                    for side in ("away", "home")
                ]
            )
            records = [
                assemble_side_inventory(
                    official_game_date=official_date,
                    plan_path=plan_path,
                    roster_ledger_root=args.roster_ledger_root,
                    history_ledger_root=args.history_ledger_root,
                    output_root=args.inventory_root,
                    target_id=str(target_id),
                    side=str(side),
                    observed_at_utc=observed,
                )
                for target_id, side in requested
            ]
            if stable_read_bytes(plan_path, "T-4 plan") != plan_raw:
                raise BatterEvidenceInventoryError("T-4 plan changed during CLI tick")
            expected_coverage = {(str(target_id), str(side)) for target_id, side in requested}
            actual_coverage = {
                (str(record.get("target_id")), str(record.get("side")))
                for record in records
                if isinstance(record, dict)
            }
            if actual_coverage != expected_coverage or len(records) != len(expected_coverage):
                raise BatterEvidenceInventoryError("CLI target-side coverage differs from plan snapshot")
            result = {
                "schema_version": "shared-pa-batter-evidence-tick-v1",
                "state": "processed",
                "official_game_date": official_date,
                "records": records,
                "research_only": True,
                "betting_authorized": False,
            }
    except (BatterEvidenceInventoryError, OSError, ValueError) as exc:
        print(f"[FAIL] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
