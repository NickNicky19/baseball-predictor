#!/usr/bin/env python3
"""Credential-free mutations for the prestart reference collector."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from run_shadow_close_collector import capture_prestart_reference  # noqa: E402
from run_shadow_primary_collector import capture_target  # noqa: E402
from scripts.check_shadow_primary_collector_offline import (  # noqa: E402
    FakeClient,
    prediction,
    write,
)
from src.evaluation.shadow_capture_plan import plan_from_schedule  # noqa: E402
from src.evaluation.shadow_live_provider import ProviderResponse  # noqa: E402


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


class CloseClient:
    def __init__(self, *, quote_at: str = "2099-07-18T23:08:00Z") -> None:
        self.calls = 0
        self.quote_at = quote_at

    def fetch_hits(self, event_id: str) -> ProviderResponse:
        self.calls += 1
        body = json.dumps({
            "id": event_id,
            "sport_key": "baseball_mlb",
            "commence_time": "2099-07-18T23:10:00Z",
            "home_team": "Los Angeles Dodgers",
            "away_team": "San Francisco Giants",
            "bookmakers": [{
                "key": "draftkings",
                "markets": [{
                    "key": "batter_hits",
                    "last_update": self.quote_at,
                    "outcomes": [
                        {"name": "Over", "description": "Mookie Betts", "point": 0.5, "price": -145, "sid": "over-close"},
                        {"name": "Under", "description": "Mookie Betts", "point": 0.5, "price": 120, "sid": "under-close"},
                    ],
                }],
            }],
        }).encode()
        return ProviderResponse(body, "2099-07-18T23:08:30Z", 200, {
            "x-requests-remaining": "497",
            "x-requests-used": "3",
            "x-requests-last": "1",
        })


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
    plan = plan_from_schedule(
        official_game_date="2099-07-18",
        entry_hours=4,
        policy_sha256="c" * 64,
        schedule_snapshot=[schedule_row],
    )
    target = plan.targets[0]
    with tempfile.TemporaryDirectory(prefix="shadow_close_") as temporary:
        root = Path(temporary)
        plan_path = root / "plan.json"
        schedule_path = root / "schedule.json"
        prediction_path = root / "prediction.json"
        plan.write(plan_path)
        write(schedule_path, {"schedule": [schedule_row]})
        write(prediction_path, prediction())
        entry_bundle = capture_target(
            plan_path=plan_path,
            target_id=target.target_id,
            schedule_snapshot_path=schedule_path,
            prediction_archive=prediction_path,
            output_root=root / "entry",
            client=FakeClient(),  # type: ignore[arg-type]
            api_key_env="SYNTHETIC_KEY",
            max_early_seconds=600,
            max_event_start_delta_seconds=60,
            clock=lambda: "2099-07-18T19:05:00Z",
        )

        client = CloseClient()
        close_bundle = capture_prestart_reference(
            entry_bundle_path=entry_bundle,
            output_root=root / "close",
            client=client,  # type: ignore[arg-type]
            api_key_env="SYNTHETIC_KEY",
            max_early_seconds=120,
            clock=lambda: "2099-07-18T23:08:30Z",
        )
        payload = json.loads(close_bundle.read_text(encoding="utf-8"))
        check(
            client.calls == 1
            and payload["entry_market_count"] == 1
            and payload["resolved_prestart_market_count"] == 1
            and payload["coverage_rows"][0]["status"] == "resolved_prestart_reference"
            and payload["actual_fill"] is False,
            "entry MARKET_KEY receives one exact retained prestart reference, never an inferred fill",
        )
        again = capture_prestart_reference(
            entry_bundle_path=entry_bundle,
            output_root=root / "close",
            client=client,  # type: ignore[arg-type]
            api_key_env="SYNTHETIC_KEY",
            max_early_seconds=120,
            clock=lambda: "2099-07-18T23:09:00Z",
        )
        check(again == close_bundle and client.calls == 1, "restart is idempotent and makes no second provider call")

        check(
            raises(lambda: capture_prestart_reference(
                entry_bundle_path=entry_bundle,
                output_root=root / "late",
                client=CloseClient(),  # type: ignore[arg-type]
                api_key_env="SYNTHETIC_KEY",
                max_early_seconds=120,
                clock=lambda: "2099-07-18T23:10:00Z",
            )),
            "MUTATION first-pitch or later capture cannot be backfilled",
        )
        check(
            raises(lambda: capture_prestart_reference(
                entry_bundle_path=entry_bundle,
                output_root=root / "early",
                client=CloseClient(),  # type: ignore[arg-type]
                api_key_env="SYNTHETIC_KEY",
                max_early_seconds=120,
                clock=lambda: "2099-07-18T23:07:59Z",
            )),
            "MUTATION capture before the locked prestart window fails before provider access",
        )
        at_start = CloseClient(quote_at="2099-07-18T23:10:00Z")
        boundary = capture_prestart_reference(
            entry_bundle_path=entry_bundle,
            output_root=root / "market_boundary",
            client=at_start,  # type: ignore[arg-type]
            api_key_env="SYNTHETIC_KEY",
            max_early_seconds=120,
            clock=lambda: "2099-07-18T23:08:30Z",
        )
        boundary_payload = json.loads(boundary.read_text(encoding="utf-8"))
        check(
            boundary_payload["resolved_prestart_market_count"] == 0
            and boundary_payload["coverage_rows"][0]["status"] == "missing_prestart_reference",
            "MUTATION a market timestamp at first pitch cannot become a prestart quote",
        )

        raw = Path(payload["raw_provider_artifact_path"])
        raw.write_bytes(b'{"tampered":true}\n')
        check(
            raises(lambda: capture_prestart_reference(
                entry_bundle_path=entry_bundle,
                output_root=root / "close",
                client=client,  # type: ignore[arg-type]
                api_key_env="SYNTHETIC_KEY",
                max_early_seconds=120,
                clock=lambda: "2099-07-18T23:09:00Z",
            )),
            "MUTATION retained raw prestart evidence tamper fails verification",
        )

    print(f"\n{PASS}/{PASS + FAIL} checks passed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
