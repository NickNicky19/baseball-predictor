#!/usr/bin/env python3
"""Offline target-window and mutation checks for pitcher-context collection."""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.evaluation.forward_pitcher_context_collector import (  # noqa: E402
    ForwardPitcherContextCollectorError, RawScheduleResponse, run_tick,
)
from src.evaluation.forward_pitcher_context_ledger import ForwardPitcherContextLedger  # noqa: E402
from src.evaluation.shadow_capture_plan import plan_from_schedule  # noqa: E402


H = "a" * 64
R = "b" * 64


def when(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def fails(fn) -> bool:
    try:
        fn()
    except (ValueError, ForwardPitcherContextCollectorError):
        return True
    return False


def game(pk: int = 901) -> dict:
    return {
        "gamePk": pk, "officialDate": "2026-07-20", "gameDate": "2026-07-20T23:10:00Z", "gameType": "R",
        "teams": {
            "home": {"team": {"name": "Home"}, "probablePitcher": {"id": 111}},
            "away": {"team": {"name": "Away"}, "probablePitcher": {"id": 222}},
        },
    }


def response(payload: object, receipt: str) -> RawScheduleResponse:
    return RawScheduleResponse(json.dumps(payload).encode("utf-8"), receipt)


def main() -> int:
    plan = plan_from_schedule(
        official_game_date="2026-07-20", entry_hours=4, policy_sha256=H, schedule_snapshot=[game()],
    )
    target = plan.targets[0]
    raw = {"dates": [{"games": [game()]}]}
    with tempfile.TemporaryDirectory(prefix="pitcher_collector_") as temp:
        ledger = ForwardPitcherContextLedger(Path(temp), plan, R)
        calls = []
        result = run_tick(
            plan=plan, ledger=ledger, max_early_seconds=120,
            fetch_schedule=lambda date: (calls.append(date) or response(raw, "2026-07-20T19:09:40Z")),
            now=when("2026-07-20T19:09:40Z"),
        )
        assert result == {"future": 0, "captured": 1, "source_error": 0, "missed": 0} and calls == ["2026-07-20"]
        assert ledger.verify(assessed_at_utc="2026-07-20T19:10:00Z")["complete_due_targets"]
        print("[OK] only an in-window official response creates one target-bound capture")

        result = run_tick(
            plan=plan, ledger=ledger, max_early_seconds=120,
            fetch_schedule=lambda _: (_ for _ in ()).throw(AssertionError("already terminal")),
            now=when("2026-07-20T19:09:50Z"),
        )
        assert result["captured"] == 0 and result["future"] == 0
        print("[OK] terminal target is never requested again")

    with tempfile.TemporaryDirectory(prefix="pitcher_collector_missed_") as temp:
        ledger = ForwardPitcherContextLedger(Path(temp), plan, R)
        result = run_tick(
            plan=plan, ledger=ledger, max_early_seconds=120,
            fetch_schedule=lambda _: (_ for _ in ()).throw(AssertionError("must not backfill")),
            now=when("2026-07-20T19:10:01Z"),
        )
        assert result["missed"] == 1 and ledger.verify(assessed_at_utc="2026-07-20T19:10:01Z")["state_counts"]["missed"] == 1
        print("[OK] post-horizon target becomes permanently missed without a request")

    with tempfile.TemporaryDirectory(prefix="pitcher_collector_error_") as temp:
        ledger = ForwardPitcherContextLedger(Path(temp), plan, R)
        result = run_tick(
            plan=plan, ledger=ledger, max_early_seconds=120,
            fetch_schedule=lambda _: (_ for _ in ()).throw(RuntimeError("network unavailable")),
            now=when("2026-07-20T19:09:40Z"),
        )
        assert result["source_error"] == 1 and ledger.verify(assessed_at_utc="2026-07-20T19:10:00Z")["state_counts"]["source_error"] == 1
        print("[OK] timely source failure is explicit rather than a silent drop")

    assert fails(lambda: run_tick(
        plan=plan, ledger=ForwardPitcherContextLedger(Path("."), plan, R), max_early_seconds=0,
        fetch_schedule=lambda _: response(raw, "2026-07-20T19:09:40Z"), now=when("2026-07-20T19:09:40Z"),
    ))
    print("[OK] MUTATION nonpositive scheduler tolerance fails")
    print("4/4")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
