#!/usr/bin/env python3
"""Prove an operational prestart failure still reaches official unscored resolution."""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import run_shadow_collector_tick as tick_module  # noqa: E402
from run_shadow_primary_collector import capture_target  # noqa: E402
from scripts.check_shadow_primary_collector_offline import (  # noqa: E402
    FakeClient,
    prediction,
    write,
)
from scripts.check_shadow_lifecycle_tree_offline import FakeFinalAPI  # noqa: E402
from src.evaluation.shadow_capture_plan import plan_from_schedule  # noqa: E402
from src.evaluation.shadow_lifecycle import commit_target_entries  # noqa: E402
from src.evaluation.shadow_ledger import ForwardShadowLedger  # noqa: E402
from src.utils.provenance import sha256_file  # noqa: E402


PASS = 0
FAIL = 0


def check(value: bool, label: str) -> None:
    global PASS, FAIL
    if value:
        PASS += 1
        print(f"  [OK] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}")


def main() -> int:
    official_date = "2099-07-18"
    schedule_row = {
        "gamePk": 901,
        "officialDate": official_date,
        "gameDate": "2099-07-18T23:10:00Z",
        "status": {"abstractGameState": "Final"},
        "teams": {
            "home": {"team": {"name": "Los Angeles Dodgers"}},
            "away": {"team": {"name": "San Francisco Giants"}},
        },
    }
    policy = ROOT / "config/shadow_hits_research_policy.json"
    runtime = ROOT / "config/shadow_collector_runtime.json"
    plan = plan_from_schedule(
        official_game_date=official_date,
        entry_hours=4,
        policy_sha256=sha256_file(policy),
        schedule_snapshot=tick_module.canonical_schedule_records([schedule_row]),
    )
    target = plan.targets[0]

    with tempfile.TemporaryDirectory(prefix="shadow_failure_lifecycle_") as temporary:
        service = Path(temporary) / "service"
        plan_path = service / "plans" / official_date / "plan.json"
        schedule_path = service / "plans" / official_date / "schedule.json"
        prediction_path = service / "prepared" / official_date / target.target_id / "prediction.json"
        plan.write(plan_path)
        write(
            schedule_path,
            {"schedule": tick_module.canonical_schedule_records([schedule_row])},
        )
        write(prediction_path, prediction())
        bundle_path = capture_target(
            plan_path=plan_path,
            target_id=target.target_id,
            schedule_snapshot_path=schedule_path,
            prediction_archive=prediction_path,
            output_root=service / "live",
            client=FakeClient(),  # type: ignore[arg-type]
            api_key_env="SYNTHETIC_KEY",
            max_early_seconds=600,
            max_event_start_delta_seconds=60,
            clock=lambda: "2099-07-18T19:05:00Z",
        )
        ledger_path = service / "ledger/forward_ledger.jsonl"
        commit_target_entries(
            bundle_path=bundle_path,
            selection_policy_path=policy,
            ledger_path=ledger_path,
            artifact_root=service / "lifecycle",
            clock=lambda: "2099-07-18T19:06:00Z",
            ledger_clock=lambda: datetime(2099, 7, 18, 19, 6, tzinfo=timezone.utc),
        )

        original_now = tick_module._now
        tick_module._now = lambda: "2099-07-19T04:00:00Z"
        try:
            failed = False
            try:
                tick_module.run_tick(
                    official_date=official_date,
                    service_root=service,
                    policy_path=policy,
                    runtime_path=runtime,
                    model_config=ROOT / "config/config.kbb.json",
                    now_utc="2099-07-19T04:00:00Z",
                    schedule_loader=lambda _: [schedule_row],
                    project_root=ROOT,
                    official_api=FakeFinalAPI(),
                )
            except tick_module.ShadowCollectorServiceError as exc:
                failed = "no backfill is permitted" in str(exc)
        finally:
            tick_module._now = original_now

        records = ForwardShadowLedger(ledger_path).verified_records()
        resolution = records[-1]
        check(
            failed
            and len(records) == 2
            and resolution["record_type"] == "resolution"
            and resolution["settlement_status"] == "unscored"
            and resolution["reason"] == "missing_prestart_reference_for_officially_graded_entry",
            "missed prestart stays a hard failure but still receives official unscored resolution",
        )
        disposition = (
            service
            / "official"
            / official_date
            / "901"
            / "entries"
            / f"{resolution['entry_id']}.json"
        )
        check(
            disposition.is_file()
            and json.loads(disposition.read_text(encoding="utf-8"))["actual_value"] == 1,
            "official MLB truth is retained even when economic scoring is disallowed",
        )

    print(f"\n{PASS}/{PASS + FAIL} checks passed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
