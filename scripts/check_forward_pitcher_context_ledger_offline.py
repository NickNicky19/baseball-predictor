#!/usr/bin/env python3
"""Offline end-to-end and mutation checks for pitcher-context terminal evidence."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.evaluation.forward_pitcher_context import context_from_schedule  # noqa: E402
from src.evaluation.forward_pitcher_context_ledger import (  # noqa: E402
    ForwardPitcherContextLedger,
    ForwardPitcherContextLedgerError,
)
from src.evaluation.shadow_capture_plan import plan_from_schedule  # noqa: E402


H = "a" * 64
R = "b" * 64


def fails(fn) -> bool:
    try:
        fn()
    except (OSError, ValueError, ForwardPitcherContextLedgerError):
        return True
    return False


def unique_record_for_state(root: Path, state: str) -> Path:
    """Select a mutation target by content, never filesystem enumeration order."""
    matches = []
    for path in (root / "records").glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("terminal_state") == state:
            matches.append(path)
    assert len(matches) == 1, f"expected exactly one {state!r} record, found {len(matches)}"
    return matches[0]


def game(pk: int, start: str, home_pitcher: int, away_pitcher: int) -> dict:
    return {
        "gamePk": pk, "officialDate": "2026-07-20", "gameDate": start, "gameType": "R",
        "teams": {
            "home": {"team": {"name": f"Home {pk}"}, "probablePitcher": {"id": home_pitcher}},
            "away": {"team": {"name": f"Away {pk}"}, "probablePitcher": {"id": away_pitcher}},
        },
    }


def main() -> int:
    games = [
        game(901, "2026-07-20T23:10:00Z", 111, 222),
        game(902, "2026-07-21T00:10:00Z", 333, 444),
        game(903, "2026-07-21T01:10:00Z", 555, 666),
    ]
    plan = plan_from_schedule(
        official_game_date="2026-07-20", entry_hours=4, policy_sha256=H, schedule_snapshot=games,
    )
    first, second, third = sorted(plan.targets, key=lambda item: item.official_start_time_utc)
    raw = json.dumps({"dates": [{"games": games}]}, sort_keys=True).encode("utf-8")
    first_context = context_from_schedule(
        target=first, plan=plan, captured_at_utc="2026-07-20T19:05:00Z",
        source_payload_sha256=__import__("hashlib").sha256(raw).hexdigest(), schedule_games=games,
    )
    with tempfile.TemporaryDirectory(prefix="pitcher_context_ledger_") as temporary:
        root = Path(temporary)
        ledger = ForwardPitcherContextLedger(root, plan, R)
        ledger.initialize()
        ledger.append_captured(target=first, context=first_context, raw_payload=raw)
        ledger.append_exclusion(
            target=second, state="source_error", observed_at_utc="2026-07-20T20:09:00Z",
            detail="official schedule request failed; payload unavailable",
        )
        ledger.append_exclusion(
            target=third, state="missed", observed_at_utc="2026-07-20T21:11:00Z",
            detail="scheduler did not produce a schedule response by the T-horizon",
        )
        report = ledger.verify(assessed_at_utc="2026-07-20T21:11:00Z")
        assert report["complete_due_targets"] and report["state_counts"] == {"captured": 1, "source_error": 1, "missed": 1}
        print("[OK] complete due target ledger preserves capture, source failure, and missed horizon")

        assert fails(lambda: ledger.append_captured(target=first, context=first_context, raw_payload=raw))
        print("[OK] MUTATION terminal target cannot be retried or backfilled")

        captured_record = unique_record_for_state(root, "captured")
        backup = captured_record.read_bytes()
        changed = json.loads(backup)
        changed["terminal_state"] = "missed"
        captured_record.write_text(json.dumps(changed), encoding="utf-8")
        assert fails(lambda: ledger.verify(assessed_at_utc="2026-07-20T21:11:00Z"))
        captured_record.write_bytes(backup)
        print("[OK] MUTATION terminal state edit breaks hash chain")

        raw_path = next((root / "raw").glob("*.json"))
        raw_backup = raw_path.read_bytes()
        raw_path.write_bytes(b'{"changed":true}')
        assert fails(lambda: ledger.verify(assessed_at_utc="2026-07-20T21:11:00Z"))
        raw_path.write_bytes(raw_backup)
        print("[OK] MUTATION raw source payload edit fails")

        context_path = next((root / "contexts").glob("*.json"))
        context_backup = context_path.read_bytes()
        changed = json.loads(context_backup)
        changed["home_probable_pitcher"]["player_id"] = 999
        context_path.write_text(json.dumps(changed), encoding="utf-8")
        assert fails(lambda: ledger.verify(assessed_at_utc="2026-07-20T21:11:00Z"))
        context_path.write_bytes(context_backup)
        print("[OK] MUTATION pitcher identity edit fails")

        third_plan = plan_from_schedule(
            official_game_date="2026-07-20", entry_hours=4, policy_sha256="b" * 64, schedule_snapshot=games,
        )
        assert fails(lambda: ForwardPitcherContextLedger(root, third_plan, R).verify(assessed_at_utc="2026-07-20T21:11:00Z"))
        print("[OK] MUTATION different capture plan fails")

        assert fails(lambda: ForwardPitcherContextLedger(root, plan, "c" * 64).verify(assessed_at_utc="2026-07-20T21:11:00Z"))
        print("[OK] MUTATION runtime-config hash differs from ledger binding")

    print("7/7")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
