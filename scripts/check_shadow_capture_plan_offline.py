#!/usr/bin/env python3
"""Offline/mutation checks for forward-capture accountability."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.evaluation.shadow_capture_plan import (
    CaptureAttempt,
    ShadowCapturePlanError,
    assess_capture_attempts,
    load_capture_plan,
    plan_from_schedule,
)


PASS = 0
FAIL = 0
H = "a" * 64


def check(condition: bool, label: str) -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"[PASS] {label}")
    else:
        FAIL += 1
        print(f"[FAIL] {label}")


def must_raise(fn, label: str) -> None:
    try:
        fn()
    except ShadowCapturePlanError:
        check(True, label)
    else:
        check(False, label)


def schedule() -> list[dict]:
    return [
        {"gamePk": 901, "officialDate": "2026-07-16", "gameDate": "2026-07-16T23:10:00Z"},
        {"gamePk": 902, "officialDate": "2026-07-16", "gameDate": "2026-07-17T00:40:00Z"},
    ]


def attempt(plan, target, outcome: str, count: int, detail: str = "") -> CaptureAttempt:
    return CaptureAttempt(
        target_id=target.target_id,
        plan_sha256=plan.plan_sha256,
        started_at_utc="2026-07-16T19:08:00Z",
        completed_at_utc="2026-07-16T19:09:00Z",
        source_name="provider",
        source_payload_sha256=H,
        outcome=outcome,
        resolved_two_sided_quote_count=count,
        detail=detail,
    )


def main() -> int:
    plan = plan_from_schedule(
        official_game_date="2026-07-16", entry_hours=4,
        policy_sha256="b" * 64, schedule_snapshot=schedule(),
    )
    targets = {target.mlb_game_pk: target for target in plan.targets}
    first, second = targets[901], targets[902]
    check(first.entry_target_at_utc == "2026-07-16T19:10:00Z",
          "T-4 target is derived from the official game start")
    check(second.entry_target_at_utc == "2026-07-16T20:40:00Z",
          "UTC midnight game remains on its official MLB slate date")

    moved = schedule()
    moved[0]["gameDate"] = "2026-07-17T00:10:00Z"
    moved_plan = plan_from_schedule(
        official_game_date="2026-07-16", entry_hours=4,
        policy_sha256="b" * 64, schedule_snapshot=moved,
    )
    check(moved_plan.plan_sha256 != plan.plan_sha256,
          "mutation: changed official start changes the content-addressed plan")

    payload = plan.to_dict()
    check(load_capture_plan_fixture(payload).plan_sha256 == plan.plan_sha256,
          "persisted plan round-trip verifies its content hash")
    tampered = dict(payload)
    tampered["entry_hours"] = 5
    must_raise(lambda: load_capture_plan_fixture(tampered),
               "mutation: tampered plan field cannot retain its hash")

    duplicate = schedule() + [dict(schedule()[0])]
    must_raise(
        lambda: plan_from_schedule(
            official_game_date="2026-07-16", entry_hours=4,
            policy_sha256="b" * 64, schedule_snapshot=duplicate,
        ),
        "mutation: duplicate MLB game identity hard-fails",
    )

    report = assess_capture_attempts(
        plan,
        [attempt(plan, first, "captured", 12), attempt(plan, second, "no_eligible_market", 0)],
        assessed_at_utc="2026-07-16T21:00:00Z",
    )
    check(report.complete and report.captured_targets == 1 and report.no_eligible_market_targets == 1,
          "captured and no-market outcomes are distinct observed coverage")

    persisted_attempt = attempt(plan, first, "captured", 12).to_dict()
    check(CaptureAttempt.from_mapping(persisted_attempt).target_id == first.target_id,
          "persisted terminal receipt round-trip preserves its hard target")
    bad_schema = dict(persisted_attempt)
    bad_schema["schema_version"] = "unknown"
    must_raise(lambda: CaptureAttempt.from_mapping(bad_schema),
               "mutation: unknown receipt schema fails closed")

    missing = assess_capture_attempts(
        plan, [attempt(plan, first, "captured", 12)],
        assessed_at_utc="2026-07-16T21:00:00Z",
    )
    check(not missing.complete and len(missing.missing_target_ids) == 1,
          "mutation: an unattempted due target is missing, not quietly zero")

    failed = assess_capture_attempts(
        plan,
        [attempt(plan, first, "captured", 12), attempt(plan, second, "source_error", 0, "timeout")],
        assessed_at_utc="2026-07-16T21:00:00Z",
    )
    check(not failed.complete and failed.source_error_targets == 1,
          "source error is a failed capture, never observed coverage")

    must_raise(
        lambda: assess_capture_attempts(
            plan,
            [attempt(plan, first, "captured", 12), attempt(plan, first, "captured", 12)],
            assessed_at_utc="2026-07-16T21:00:00Z",
        ),
        "mutation: duplicate terminal receipts hard-fail",
    )
    wrong_plan = CaptureAttempt(
        target_id=first.target_id, plan_sha256="c" * 64,
        started_at_utc="2026-07-16T19:08:00Z",
        completed_at_utc="2026-07-16T19:09:00Z", source_name="provider",
        source_payload_sha256=H, outcome="captured", resolved_two_sided_quote_count=1,
    )
    must_raise(
        lambda: assess_capture_attempts(plan, [wrong_plan], assessed_at_utc="2026-07-16T21:00:00Z"),
        "mutation: receipt from another plan cannot satisfy this schedule",
    )

    late = CaptureAttempt(
        target_id=first.target_id, plan_sha256=plan.plan_sha256,
        started_at_utc="2026-07-16T19:10:01Z",
        completed_at_utc="2026-07-16T19:10:02Z", source_name="provider",
        source_payload_sha256=H, outcome="captured", resolved_two_sided_quote_count=1,
    )
    must_raise(
        lambda: assess_capture_attempts(plan, [late], assessed_at_utc="2026-07-16T21:00:00Z"),
        "mutation: a post-target provider poll cannot satisfy capture coverage",
    )
    must_raise(
        lambda: CaptureAttempt(
            target_id=first.target_id, plan_sha256=plan.plan_sha256,
            started_at_utc="2026-07-16T19:09:00Z",
            completed_at_utc="2026-07-16T19:08:00Z", source_name="provider",
            source_payload_sha256=H, outcome="captured", resolved_two_sided_quote_count=1,
        ),
        "mutation: attempt completion cannot predate its start",
    )

    print(f"\n{PASS}/{PASS + FAIL} checks passed")
    return 0 if FAIL == 0 else 1


def load_capture_plan_fixture(payload: dict):
    """Exercise the real mapping verifier without writing a fake parser."""
    from src.evaluation.shadow_capture_plan import ShadowCapturePlan
    return ShadowCapturePlan.from_mapping(payload)


if __name__ == "__main__":
    raise SystemExit(main())
