#!/usr/bin/env python3
"""Credential-free end-to-end mutations for the independent lifecycle verifier."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from run_shadow_close_collector import capture_prestart_reference  # noqa: E402
from run_shadow_official_settlement import settle_final_entries  # noqa: E402
from run_shadow_primary_collector import capture_target  # noqa: E402
from scripts.check_shadow_close_collector_offline import CloseClient  # noqa: E402
from scripts.check_shadow_primary_collector_offline import (  # noqa: E402
    FakeClient,
    prediction,
    write,
)
from scripts.verify_shadow_lifecycle_tree import verify_lifecycle_tree  # noqa: E402
from src.data.mlb_api import MLBStatsAPI  # noqa: E402
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


def raises(fn) -> bool:
    try:
        fn()
    except (OSError, ValueError):
        return True
    return False


def final_feed() -> dict:
    return {
        "gameData": {
            "datetime": {"officialDate": "2099-07-18"},
            "status": {"codedGameState": "F"},
        },
        "liveData": {
            "linescore": {"scheduledInnings": 9, "currentInning": 9},
            "boxscore": {
                "teams": {
                    "away": {"players": {}},
                    "home": {
                        "players": {
                            "ID605141": {
                                "battingOrder": "100",
                                "stats": {
                                    "batting": {
                                        "plateAppearances": 4,
                                        "atBats": 4,
                                        "hits": 1,
                                    }
                                },
                            }
                        }
                    },
                }
            },
        },
    }


class FakeFinalAPI(MLBStatsAPI):
    def get_final_game_pks(self, game_date=None):
        return [901]

    def get_completed_game_feed(self, game_pk: int):
        if game_pk != 901:
            raise AssertionError("unexpected synthetic game")
        return final_feed()


def main() -> int:
    schedule_row = {
        "gamePk": 901,
        "officialDate": "2099-07-18",
        "gameDate": "2099-07-18T23:10:00Z",
        "teams": {
            "home": {"team": {"name": "Los Angeles Dodgers"}},
            "away": {"team": {"name": "San Francisco Giants"}},
        },
    }
    policy = ROOT / "config/shadow_hits_research_policy.json"
    plan = plan_from_schedule(
        official_game_date="2099-07-18",
        entry_hours=4,
        policy_sha256=sha256_file(policy),
        schedule_snapshot=[schedule_row],
    )
    target = plan.targets[0]

    with tempfile.TemporaryDirectory(prefix="shadow_lifecycle_tree_") as temporary:
        root = Path(temporary) / "primary"
        plan_path = root / "plans/2099-07-18/capture_plan.json"
        schedule_path = root / "plans/2099-07-18/mlb_schedule.json"
        prediction_path = root / "prepared/2099-07-18/prediction.json"
        ledger_path = root / "ledger/forward_ledger.jsonl"
        plan.write(plan_path)
        write(schedule_path, {"schedule": [schedule_row]})
        write(prediction_path, prediction())

        bundle_path = capture_target(
            plan_path=plan_path,
            target_id=target.target_id,
            schedule_snapshot_path=schedule_path,
            prediction_archive=prediction_path,
            output_root=root / "live",
            client=FakeClient(),  # type: ignore[arg-type]
            api_key_env="SYNTHETIC_KEY",
            max_early_seconds=600,
            clock=lambda: "2099-07-18T19:05:00Z",
        )
        commit_target_entries(
            bundle_path=bundle_path,
            selection_policy_path=policy,
            ledger_path=ledger_path,
            artifact_root=root / "lifecycle",
            clock=lambda: "2099-07-18T19:06:00Z",
            ledger_clock=lambda: datetime(2099, 7, 18, 19, 6, tzinfo=timezone.utc),
        )
        capture_prestart_reference(
            entry_bundle_path=bundle_path,
            output_root=root / "close",
            client=CloseClient(),  # type: ignore[arg-type]
            api_key_env="SYNTHETIC_KEY",
            max_early_seconds=120,
            clock=lambda: "2099-07-18T23:08:30Z",
        )
        settled = settle_final_entries(
            official_date="2099-07-18",
            ledger_path=ledger_path,
            close_root=root / "close",
            official_root=root / "official",
            settlement_rule_artifact=(
                ROOT / "config/shadow_draftkings_hits_reference_settlement.json"
            ),
            api=FakeFinalAPI(),
            clock=lambda: "2099-07-19T04:00:00Z",
        )
        check(
            settled.get("resolution_graded") == 1
            and ForwardShadowLedger(ledger_path).verify().total_records == 2,
            "synthetic target reaches a graded immutable lifecycle before verification",
        )

        relocated = Path(temporary) / "independent_copy"
        shutil.copytree(root, relocated)
        verify_args = {
            "plan_path": relocated / "plans/2099-07-18/capture_plan.json",
            "live_root": relocated / "live",
            "lifecycle_root": relocated / "lifecycle",
            "close_root": relocated / "close",
            "official_root": relocated / "official",
            "ledger_path": relocated / "ledger/forward_ledger.jsonl",
            "assessed_at_utc": "2099-07-19T04:00:00Z",
        }
        report = verify_lifecycle_tree(**verify_args)
        check(
            report["complete_due_entry_and_prestart_phases"] is True
            and report["settlement_complete"] is True
            and report["verified_resolution_artifacts"] == 1
            and report["replacement_odds_fetched"] is False
            and report["betting_authorized"] is False,
            "relocated independent verifier proves the complete research-only lifecycle",
        )

        funnel = next((relocated / "lifecycle").rglob("selection_funnel.json"))
        original_funnel = funnel.read_bytes()
        payload = json.loads(original_funnel)
        payload["betting_authorized"] = True
        funnel.write_text(json.dumps(payload), encoding="utf-8")
        check(
            raises(lambda: verify_lifecycle_tree(**verify_args)),
            "MUTATION selection-funnel scope tamper fails independent verification",
        )
        funnel.write_bytes(original_funnel)

        close_bundle = next((relocated / "close").rglob("prestart_reference_bundle.json"))
        close_backup = close_bundle.read_bytes()
        close_bundle.unlink()
        check(
            raises(lambda: verify_lifecycle_tree(**verify_args)),
            "MUTATION missing due prestart evidence fails independent verification",
        )
        close_bundle.write_bytes(close_backup)

        ledger = relocated / "ledger/forward_ledger.jsonl"
        ledger_backup = ledger.read_bytes()
        rows = ledger.read_text(encoding="utf-8").splitlines()
        changed = json.loads(rows[0])
        changed["model_p_over"] = 0.01
        rows[0] = json.dumps(changed)
        ledger.write_text("\n".join(rows) + "\n", encoding="utf-8")
        check(
            raises(lambda: verify_lifecycle_tree(**verify_args)),
            "MUTATION ledger edit fails hash-chain verification",
        )
        ledger.write_bytes(ledger_backup)

        disposition = next((relocated / "official").rglob("entries/*.json"))
        disposition_backup = disposition.read_bytes()
        disposition.unlink()
        check(
            raises(lambda: verify_lifecycle_tree(**verify_args)),
            "MUTATION missing official disposition fails independent verification",
        )
        disposition.write_bytes(disposition_backup)

    print(f"\n{PASS}/{PASS + FAIL} checks passed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
