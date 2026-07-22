#!/usr/bin/env python3
"""Offline/mutation harness for the hard-keyed forward shadow ledger."""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.evaluation.shadow_ledger import (
    ForwardShadowLedger,
    ShadowEntry,
    ShadowLedgerError,
    ShadowResolution,
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


def entry(**overrides) -> ShadowEntry:
    payload = {
        "mlb_game_pk": 777001,
        "player_id": 123,
        "game_date": "2026-07-13",
        "official_start_time_utc": "2026-07-13T23:10:00Z",
        "sportsbook": "DraftKings",
        "category": "hits",
        "line": 0.5,
        "entry_target_at_utc": "2026-07-13T19:10:00Z",
        "entry_quote_at_utc": "2026-07-13T19:08:00Z",
        "entry_over_odds_american": -120,
        "entry_under_odds_american": 100,
        "model_p_over": 0.62,
        "selection_side": "over",
        "selection_policy_id": "shadow-policy-v1",
        "selection_policy_sha256": "f" * 64,
        "model_version": "deadbeefcafe",
        "config_sha256": H,
        "code_sha256": "b" * 64,
        "prediction_artifact_sha256": "c" * 64,
        "quote_artifact_sha256": "d" * 64,
    }
    payload.update(overrides)
    return ShadowEntry(**payload)


def must_raise(fn, label: str) -> None:
    try:
        fn()
    except ShadowLedgerError:
        check(True, label)
    else:
        check(False, label)


def main() -> int:
    tmp = Path(tempfile.mkdtemp())
    ledger_path = tmp / "ledger.jsonl"
    ledger = ForwardShadowLedger(
        ledger_path,
        clock=lambda: datetime(2026, 7, 13, 19, 0, tzinfo=timezone.utc),
    )
    first = entry()

    report = ledger.append_entries([first])
    check(report.added == 1 and report.total_records == 1, "first hard-keyed entry appends")
    check(ledger.verify().head_hash is not None, "hash chain verifies after entry append")

    replay = ledger.append_entries([first])
    check(replay.added == 0 and replay.idempotent == 1 and replay.total_records == 1,
          "identical rerun is idempotent, not a duplicate append")
    must_raise(lambda: ledger.append_entries([entry(model_p_over=0.63)]),
               "mutation: changed decision evidence hard-fails")
    late_ledger = ForwardShadowLedger(
        tmp / "late.jsonl",
        clock=lambda: datetime(2026, 7, 13, 19, 11, tzinfo=timezone.utc),
    )
    must_raise(lambda: late_ledger.append_entries([entry()]),
               "mutation: post-horizon entry cannot become forward evidence")
    must_raise(lambda: entry(entry_quote_at_utc="2026-07-13T19:11:00Z"),
               "mutation: post-horizon quote cannot become entry evidence")
    must_raise(lambda: entry(selection_side="under"),
               "mutation: selected side without positive de-vigged edge hard-fails")
    must_raise(
        lambda: entry(model_p_over=0.53),
        "mutation: positive de-vig edge but negative posted-price EV hard-fails",
    )
    check(first.expected_profit_per_unit > 0.0,
          "accepted entry exposes a positive exact posted-price expected profit")
    must_raise(lambda: ShadowEntry.from_mapping({"mlb_game_pk": 1}),
               "missing hard identity/provenance cannot become an entry")

    resolution = ShadowResolution(
        entry_id=first.entry_id,
        settlement_status="graded",
        settled_at_utc="2026-07-14T10:00:00Z",
        close_quote_at_utc="2026-07-13T23:05:00Z",
        close_over_odds_american=-130,
        close_under_odds_american=110,
        close_quote_artifact_sha256="d" * 64,
        official_actual_value=1.0,
        official_outcome_artifact_sha256="e" * 64,
    )
    report = ledger.append_resolutions([resolution])
    check(report.added == 1 and report.total_records == 2,
          "close/outcome is a separate immutable linked record")
    check(len(ledger.graded_pairs()) == 1,
          "verified ledger exposes only linked graded pairs for scoring")
    must_raise(
        lambda: ledger.append_resolutions([
            ShadowResolution(
                entry_id="f" * 64,
                settlement_status="void",
                settled_at_utc="2026-07-14T10:00:00Z",
                reason="official no-action",
            )
        ]),
        "resolution cannot reference a missing entry",
    )
    must_raise(
        lambda: ShadowResolution(
            entry_id=first.entry_id,
            settlement_status="graded",
            settled_at_utc="2026-07-14T10:00:00Z",
            close_quote_at_utc="2026-07-13T23:05:00Z",
            close_over_odds_american=-130,
            close_under_odds_american=110,
            official_actual_value=1.0,
            official_outcome_artifact_sha256="e" * 64,
        ),
        "mutation: close prices without their retained artifact hash hard-fail",
    )
    must_raise(
        lambda: ShadowResolution(
            entry_id=first.entry_id,
            settlement_status="graded",
            settled_at_utc="2026-07-14T10:00:00Z",
            official_actual_value=1.0,
            official_outcome_artifact_sha256="e" * 64,
        ),
        "mutation: graded record without a close hard-fails",
    )
    must_raise(
        lambda: ledger.append_resolutions([
            ShadowResolution(
                entry_id=first.entry_id,
                settlement_status="graded",
                settled_at_utc="2026-07-14T10:00:00Z",
                close_quote_at_utc="2026-07-13T19:00:00Z",
                close_over_odds_american=-130,
                close_under_odds_american=110,
                close_quote_artifact_sha256="d" * 64,
                official_actual_value=1.0,
                official_outcome_artifact_sha256="e" * 64,
            )
        ]),
        "mutation: close before entry quote hard-fails",
    )

    records = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines()]
    records[0]["model_p_over"] = 0.01  # mutate after recording; leave hash intact
    ledger_path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
    must_raise(ledger.verify, "mutation: post-hoc edit breaks hash-chain verification")

    print(f"\n{PASS}/{PASS + FAIL} checks passed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
