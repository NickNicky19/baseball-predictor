#!/usr/bin/env python3
"""Credential-free end-to-end checks for the official settlement worker."""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from run_shadow_official_settlement import settle_final_entries  # noqa: E402
from src.data.mlb_api import MLBStatsAPI  # noqa: E402
from src.evaluation.shadow_ledger import ForwardShadowLedger, ShadowEntry  # noqa: E402


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


def entry() -> ShadowEntry:
    return ShadowEntry(
        mlb_game_pk=901,
        player_id=123,
        game_date="2026-07-16",
        official_start_time_utc="2026-07-16T23:10:00Z",
        sportsbook="draftkings",
        category="hits",
        line=0.5,
        entry_target_at_utc="2026-07-16T19:10:00Z",
        entry_quote_at_utc="2026-07-16T19:05:00Z",
        entry_over_odds_american=-115,
        entry_under_odds_american=-105,
        model_p_over=0.70,
        selection_side="over",
        selection_policy_id="synthetic",
        selection_policy_sha256="a" * 64,
        model_version="synthetic",
        config_sha256="b" * 64,
        code_sha256="c" * 64,
        prediction_artifact_sha256="d" * 64,
        quote_artifact_sha256="e" * 64,
    )


def feed(*, batting_order: int = 100, pa: int = 4, hits: int = 1) -> dict:
    return {
        "gameData": {
            "datetime": {"officialDate": "2026-07-16"},
            "status": {"codedGameState": "F"},
        },
        "liveData": {
            "linescore": {"scheduledInnings": 9, "currentInning": 9},
            "boxscore": {
                "teams": {
                    "away": {
                        "players": {
                            "ID123": {
                                "battingOrder": str(batting_order),
                                "stats": {"batting": {
                                    "plateAppearances": pa,
                                    "atBats": pa,
                                    "hits": hits,
                                }},
                            }
                        }
                    },
                    "home": {"players": {}},
                }
            },
        },
    }


class FakeAPI(MLBStatsAPI):
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.final_queries = 0

    def get_final_game_pks(self, game_date=None):
        self.final_queries += 1
        return [901]

    def get_completed_game_feed(self, game_pk: int):
        assert game_pk == 901
        return self.payload


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="shadow_settlement_") as temporary:
        root = Path(temporary)
        ledger_path = root / "ledger.jsonl"
        ledger = ForwardShadowLedger(
            ledger_path,
            clock=lambda: datetime(2026, 7, 16, 19, 8, tzinfo=timezone.utc),
        )
        value = entry()
        ledger.append_entries([value])
        api = FakeAPI(feed())
        counts = settle_final_entries(
            official_date="2026-07-16",
            ledger_path=ledger_path,
            close_root=root / "missing_close",
            official_root=root / "official",
            settlement_rule_artifact=ROOT / "config/shadow_draftkings_hits_reference_settlement.json",
            api=api,
            clock=lambda: "2026-07-17T04:00:00Z",
        )
        records = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines()]
        resolution = records[-1]
        check(
            counts["resolution_unscored"] == 1
            and resolution["reason"] == "missing_prestart_reference_for_officially_graded_entry"
            and resolution["official_actual_value"] is None,
            "official result without a prestart reference is unscored, never a fabricated loss",
        )
        outcome_path = root / "official/2026-07-16/901/entries" / f"{value.entry_id}.json"
        outcome = json.loads(outcome_path.read_text(encoding="utf-8"))
        check(
            outcome["settlement_status"] == "graded"
            and outcome["actual_value"] == 1
            and len(outcome["official_game_feed_artifact_sha256"]) == 64,
            "official disposition remains separate and hash-bound to the retained MLB final feed",
        )
        again = settle_final_entries(
            official_date="2026-07-16",
            ledger_path=ledger_path,
            close_root=root / "missing_close",
            official_root=root / "official",
            settlement_rule_artifact=ROOT / "config/shadow_draftkings_hits_reference_settlement.json",
            api=api,
        )
        check(
            again["unresolved_input"] == 0 and len(records) == ledger.verify().total_records,
            "restart after settlement is idempotent",
        )

    with tempfile.TemporaryDirectory(prefix="shadow_void_") as temporary:
        root = Path(temporary)
        ledger_path = root / "ledger.jsonl"
        ledger = ForwardShadowLedger(
            ledger_path,
            clock=lambda: datetime(2026, 7, 16, 19, 8, tzinfo=timezone.utc),
        )
        ledger.append_entries([entry()])
        counts = settle_final_entries(
            official_date="2026-07-16",
            ledger_path=ledger_path,
            close_root=root / "missing_close",
            official_root=root / "official",
            settlement_rule_artifact=ROOT / "config/shadow_draftkings_hits_reference_settlement.json",
            api=FakeAPI(feed(batting_order=101, pa=2, hits=1)),
            clock=lambda: "2026-07-17T04:00:00Z",
        )
        records = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines()]
        resolution = records[-1]
        check(
            counts["resolution_void"] == 1
            and resolution["official_actual_value"] is None
            and len(resolution["settlement_evidence_artifact_sha256"]) == 64,
            "official nonstarter is a hash-evidenced void, not a win or loss",
        )
        check(len(ledger.graded_pairs()) == 0, "void never enters payout or capture denominators")

    print(f"\n{PASS}/{PASS + FAIL} checks passed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
