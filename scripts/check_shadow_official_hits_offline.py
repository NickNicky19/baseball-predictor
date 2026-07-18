#!/usr/bin/env python3
"""Outcome-blind unit mutations for official Hits reference settlement."""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.shadow_ledger import ShadowEntry, ShadowResolution  # noqa: E402
from src.evaluation.shadow_official_hits import official_hits_disposition  # noqa: E402


PASS = 0
FAIL = 0
RULE = ROOT / "config/shadow_draftkings_hits_reference_settlement.json"


def check(value: bool, label: str) -> None:
    global PASS, FAIL
    if value:
        PASS += 1
        print(f"  [OK] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}")


def raises(fn) -> bool:
    try:
        fn()
    except ValueError:
        return True
    return False


def entry(**updates) -> ShadowEntry:
    values = {
        "mlb_game_pk": 901,
        "player_id": 123,
        "game_date": "2026-07-16",
        "official_start_time_utc": "2026-07-16T23:10:00Z",
        "sportsbook": "draftkings",
        "category": "hits",
        "line": 0.5,
        "entry_target_at_utc": "2026-07-16T19:10:00Z",
        "entry_quote_at_utc": "2026-07-16T19:05:00Z",
        "entry_over_odds_american": -115,
        "entry_under_odds_american": -105,
        "model_p_over": 0.70,
        "selection_side": "over",
        "selection_policy_id": "synthetic",
        "selection_policy_sha256": "a" * 64,
        "model_version": "synthetic",
        "config_sha256": "b" * 64,
        "code_sha256": "c" * 64,
        "prediction_artifact_sha256": "d" * 64,
        "quote_artifact_sha256": "e" * 64,
    }
    values.update(updates)
    return ShadowEntry(**values)


def outcome(value: ShadowEntry, role, pa, hits, regular=True):
    return official_hits_disposition(
        entry=value,
        official_role=role,
        official_pa=pa,
        official_hits=hits,
        regular_game_completed=regular,
        settlement_rule_artifact=RULE,
        official_game_feed_artifact_sha256="f" * 64,
    )


def main() -> int:
    starter = {
        "is_starter": True,
        "starter_replaced_in_slot": False,
        "lineup_slot": 1,
        "team_side": "away",
    }
    substitute = {**starter, "is_starter": False, "starter_replaced_in_slot": False}
    replaced = {**starter, "starter_replaced_in_slot": True}
    check(outcome(entry(), starter, 4, 1)["settlement_status"] == "graded", "starter with PA is graded")
    check(outcome(entry(), substitute, 2, 1)["settlement_status"] == "void", "official substitute is void")
    check(outcome(entry(), starter, 0, 0)["settlement_status"] == "void", "starter with zero PA is void")
    check(
        outcome(entry(), replaced, 1, 0)["settlement_status"] == "void",
        "Over starter replaced after one PA without winning is void",
    )
    check(
        outcome(entry(), replaced, 1, 1)["settlement_status"] == "graded",
        "Over already unconditionally won remains graded",
    )
    under = replace(entry(), selection_side="under", model_p_over=0.30)
    check(
        outcome(under, replaced, 1, 0)["settlement_status"] == "graded",
        "Under is exempt from the one-PA early-exit void clause",
    )
    check(
        outcome(entry(), starter, 4, 1, regular=False)["settlement_status"] == "unscored",
        "unsupported game completion is unscored rather than guessed",
    )
    check(
        outcome(entry(), None, None, None)["settlement_status"] == "unscored",
        "missing official role/stat facts are unscored rather than zero",
    )
    check(
        raises(lambda: ShadowResolution(
            entry_id=entry().entry_id,
            settlement_status="void",
            settled_at_utc="2026-07-17T04:00:00Z",
            reason="synthetic void",
        )),
        "MUTATION void cannot enter the ledger without retained settlement evidence",
    )
    valid_void = ShadowResolution(
        entry_id=entry().entry_id,
        settlement_status="void",
        settled_at_utc="2026-07-17T04:00:00Z",
        settlement_evidence_artifact_sha256="1" * 64,
        reason="synthetic void",
    )
    check(valid_void.official_actual_value is None, "void carries no fabricated official actual")

    print(f"\n{PASS}/{PASS + FAIL} checks passed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
