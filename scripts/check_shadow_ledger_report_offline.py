#!/usr/bin/env python3
"""Offline/mutation harness for forward-only shadow capture reporting."""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import run_shadow_ledger_report as report
from src.evaluation.shadow_ledger import ForwardShadowLedger, ShadowEntry, ShadowResolution


PASS = 0
FAIL = 0


def check(condition: bool, label: str) -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"[PASS] {label}")
    else:
        FAIL += 1
        print(f"[FAIL] {label}")


def must_fail(argv: list[str], label: str) -> None:
    try:
        report.main(argv)
    except SystemExit as exc:
        check(exc.code == 2, label)
    else:
        check(False, label)


def entry(day: int) -> ShadowEntry:
    date = f"2026-07-0{day}"
    return ShadowEntry(
        mlb_game_pk=810000 + day,
        player_id=700 + day,
        game_date=date,
        official_start_time_utc=f"{date}T20:00:00Z",
        sportsbook="draftkings",
        category="hits",
        line=0.5,
        entry_target_at_utc=f"{date}T16:00:00Z",
        entry_quote_at_utc=f"{date}T15:59:00Z",
        entry_over_odds_american=-110,
        entry_under_odds_american=-110,
        model_p_over=0.65,
        selection_side="over",
        selection_policy_id="shadow-policy-v1",
        selection_policy_sha256="a" * 64,
        model_version="model-v1",
        config_sha256="b" * 64,
        code_sha256="c" * 64,
        prediction_artifact_sha256="d" * 64,
        quote_artifact_sha256="e" * 64,
    )


def main() -> int:
    tmp = Path(tempfile.mkdtemp())
    ledger_path = tmp / "forward.jsonl"
    ledger = ForwardShadowLedger(
        ledger_path,
        clock=lambda: datetime(2026, 6, 30, 0, 0, tzinfo=timezone.utc),
    )
    entries = [entry(day) for day in range(1, 5)]
    ledger.append_entries(entries)
    ledger.append_resolutions([
        ShadowResolution(
            entry_id=value.entry_id,
            settlement_status="graded",
            settled_at_utc=f"2026-07-0{day}T23:00:00Z",
            close_quote_at_utc=f"2026-07-0{day}T19:00:00Z",
            close_over_odds_american=-120,
            close_under_odds_american=100,
            close_quote_artifact_sha256="e" * 64,
            official_actual_value=1.0,
            official_outcome_artifact_sha256="f" * 64,
        )
        for day, value in enumerate(entries, 1)
    ])

    output = tmp / "shadow_capture.csv"
    rc = report.main([
        "--ledger", str(ledger_path), "--capture-bar", "0.10", "--b", "100",
        "--out", str(output),
    ])
    got = pd.read_csv(output)
    market = got[got["row_type"] == "market_capture"].iloc[0]
    check(
        rc == 0
        and len(got) == 2
        and market["verdict"] == "SHADOW_ONLY_NOT_PROMOTED"
        and bool(market["capture_ci_lower_exceeds_bar"]),
        "per-market capture is bootstrap-scored but remains shadow-only",
    )
    check(
        set(got["category"]) == {"hits"} and set(got["sportsbook"]) == {"draftkings"},
        "report does not pool markets or sportsbooks",
    )

    empty_ledger = tmp / "empty.jsonl"
    must_fail(
        ["--ledger", str(empty_ledger), "--capture-bar", "0.10", "--out", str(tmp / "empty.csv")],
        "mutation: an empty/non-forward ledger cannot manufacture capture evidence",
    )

    print(f"\n{PASS}/{PASS + FAIL} checks passed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
