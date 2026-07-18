#!/usr/bin/env python3
"""Credential-free end-to-end mutations for the restart-safe primary worker."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from run_shadow_primary_collector import capture_target  # noqa: E402
from src.evaluation.shadow_capture_plan import plan_from_schedule  # noqa: E402
from src.evaluation.shadow_live_provider import ProviderResponse, artifact_bytes  # noqa: E402
from src.evaluation.shadow_target_capture import load_target_capture_bundle  # noqa: E402
from scripts.verify_shadow_capture_tree import verify_capture_tree  # noqa: E402


PASS = 0
FAIL = 0
SECRET = "synthetic-secret-must-never-land"


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


class FakeClient:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    def fetch_events(self, game_date: str) -> ProviderResponse:
        self.calls += 1
        if self.fail:
            raise ValueError(f"provider rejected {SECRET}")
        body = json.dumps([{
            "id": "event-901",
            "commence_time": "2099-07-18T23:10:00Z",
            "home_team": "Los Angeles Dodgers",
            "away_team": "San Francisco Giants",
        }]).encode()
        return ProviderResponse(body, "2099-07-18T19:05:00Z", 200, {
            "x-requests-remaining": "499",
            "x-requests-used": "1",
            "x-requests-last": "0",
        })

    def fetch_hits(self, event_id: str) -> ProviderResponse:
        self.calls += 1
        body = json.dumps({
            "id": "event-901",
            "sport_key": "baseball_mlb",
            "commence_time": "2099-07-18T23:10:00Z",
            "home_team": "Los Angeles Dodgers",
            "away_team": "San Francisco Giants",
            "bookmakers": [{
                "key": "draftkings",
                "title": "DraftKings",
                "markets": [{
                    "key": "batter_hits",
                    "last_update": "2099-07-18T19:05:00Z",
                    "outcomes": [
                        {"name": "Over", "description": "Mookie Betts", "point": 0.5, "price": -120, "sid": "over-1"},
                        {"name": "Under", "description": "Mookie Betts", "point": 0.5, "price": 100, "sid": "under-1"},
                    ],
                }],
            }],
        }).encode()
        return ProviderResponse(body, "2099-07-18T19:06:00Z", 200, {
            "x-requests-remaining": "498",
            "x-requests-used": "2",
            "x-requests-last": "1",
        })


def write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(artifact_bytes(value))


def prediction(captured: str = "2099-07-18T18:55:00Z") -> dict:
    return {
        "game_date": "2099-07-18",
        "prediction_provenance": {
            "schema_version": "daily-prediction-provenance-v1",
            "captured_at_utc": captured,
            "model_version": "synthetic-primary-v1",
            "effective_config_sha256": "a" * 64,
            "code": {"status": "available", "snapshot_sha256": "b" * 64},
        },
        "hitter_projections": [{
            "mlb_game_pk": 901,
            "player_id": 605141,
            "player_name": "Mookie Betts",
            "game_date": "2099-07-18",
            "category": "hits",
            "simulation": {"p_ge_threshold": {"1": 0.70, "2": 0.31}},
        }],
        "pitcher_projections": [],
    }


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
    prior = os.environ.get("SYNTHETIC_SHADOW_KEY")
    os.environ["SYNTHETIC_SHADOW_KEY"] = SECRET
    try:
        with tempfile.TemporaryDirectory(prefix="shadow_primary_") as tmp:
            root = Path(tmp)
            plan_path = root / "plan.json"
            schedule_path = root / "schedule.json"
            prediction_path = root / "prediction.json"
            plan.write(plan_path)
            write(schedule_path, {"schedule": [schedule_row]})
            write(prediction_path, prediction())

            client = FakeClient()
            bundle_path = capture_target(
                plan_path=plan_path,
                target_id=target.target_id,
                schedule_snapshot_path=schedule_path,
                prediction_archive=prediction_path,
                output_root=root / "live",
                client=client,  # type: ignore[arg-type]
                api_key_env="SYNTHETIC_SHADOW_KEY",
                max_early_seconds=600,
                clock=lambda: "2099-07-18T19:05:00Z",
            )
            bundle = load_target_capture_bundle(bundle_path)
            check(
                bundle.attempt["outcome"] == "captured"
                and len(bundle.quote_sha256) == 1
                and client.calls == 2,
                "one future target publishes a verified bundle from two provider calls",
            )
            target_root = root / "live" / target.official_game_date / target.target_id
            (target_root / "terminal_attempt.json").unlink()
            (target_root / "capture_status.json").unlink()
            recovery_calls = client.calls
            recovered = capture_target(
                plan_path=plan_path,
                target_id=target.target_id,
                schedule_snapshot_path=schedule_path,
                prediction_archive=prediction_path,
                output_root=root / "live",
                client=client,  # type: ignore[arg-type]
                api_key_env="SYNTHETIC_SHADOW_KEY",
                max_early_seconds=600,
                clock=lambda: "2099-07-18T19:05:00Z",
            )
            check(
                recovered == bundle_path
                and (target_root / "terminal_attempt.json").is_file()
                and (target_root / "capture_status.json").is_file()
                and client.calls == recovery_calls,
                "restart repairs receipts after bundle publication without another provider call",
            )
            before = client.calls
            again = capture_target(
                plan_path=plan_path,
                target_id=target.target_id,
                schedule_snapshot_path=schedule_path,
                prediction_archive=prediction_path,
                output_root=root / "live",
                client=client,  # type: ignore[arg-type]
                api_key_env="SYNTHETIC_SHADOW_KEY",
                max_early_seconds=600,
                clock=lambda: "2099-07-18T19:05:00Z",
            )
            check(
                again == bundle_path and client.calls == before,
                "restart after terminal success is idempotent and makes no provider call",
            )
            all_text = "\n".join(
                path.read_text(encoding="utf-8", errors="ignore")
                for path in (root / "live").rglob("*") if path.is_file()
            )
            check(SECRET not in all_text, "credential never enters success artifacts")

            too_early_client = FakeClient()
            check(
                raises(lambda: capture_target(
                    plan_path=plan_path,
                    target_id=target.target_id,
                    schedule_snapshot_path=schedule_path,
                    prediction_archive=prediction_path,
                    output_root=root / "too-early-live",
                    client=too_early_client,  # type: ignore[arg-type]
                    api_key_env="SYNTHETIC_SHADOW_KEY",
                    max_early_seconds=600,
                    clock=lambda: "2099-07-18T18:59:00Z",
                )) and too_early_client.calls == 0,
                "MUTATION capture before the locked operational window fails before provider access",
            )

            backup_root = root / "independent-copy"
            shutil.copytree(root / "live", backup_root)
            verified = verify_capture_tree(
                plan_path=plan_path,
                artifact_root=backup_root,
                assessed_at_utc=target.entry_target_at_utc,
            )
            check(
                verified["complete"] is True
                and verified["verified_target_bundles"] == 1,
                "independent verifier accepts a relocated copy of the complete target tree",
            )
            missing_verification = verify_capture_tree(
                plan_path=plan_path,
                artifact_root=root / "missing-live",
                assessed_at_utc=target.entry_target_at_utc,
            )
            check(
                missing_verification["complete"] is False
                and len(missing_verification["coverage"]["missing_target_ids"]) == 1,
                "MUTATION a missing due target is visible and incomplete",
            )

            raw_path = Path(bundle.raw_provider_artifact_path)
            original_raw = raw_path.read_bytes()
            raw_path.write_bytes(b'{"tampered":true}\n')
            check(
                raises(lambda: verify_capture_tree(
                    plan_path=plan_path,
                    artifact_root=root / "live",
                    assessed_at_utc=target.entry_target_at_utc,
                )),
                "MUTATION retained provider artifact tamper fails independent verification",
            )
            raw_path.write_bytes(original_raw)

            bad_schedule = root / "bad_schedule.json"
            changed = json.loads(json.dumps(schedule_row))
            changed["teams"]["home"]["team"]["name"] = "Other Team"
            write(bad_schedule, {"schedule": [changed]})
            check(
                raises(lambda: capture_target(
                    plan_path=plan_path,
                    target_id=target.target_id,
                    schedule_snapshot_path=bad_schedule,
                    prediction_archive=prediction_path,
                    output_root=root / "bad-schedule-live",
                    client=FakeClient(),  # type: ignore[arg-type]
                    api_key_env="SYNTHETIC_SHADOW_KEY",
                    max_early_seconds=600,
                    clock=lambda: "2099-07-18T19:05:00Z",
                )),
                "MUTATION schedule snapshot differing from plan hard-fails",
            )

            failing = FakeClient(fail=True)
            error_root = root / "error-live"
            check(
                raises(lambda: capture_target(
                    plan_path=plan_path,
                    target_id=target.target_id,
                    schedule_snapshot_path=schedule_path,
                    prediction_archive=prediction_path,
                    output_root=error_root,
                    client=failing,  # type: ignore[arg-type]
                    api_key_env="SYNTHETIC_SHADOW_KEY",
                    max_early_seconds=600,
                    clock=lambda: "2099-07-18T19:05:00Z",
                )),
                "provider failure publishes no successful bundle",
            )
            terminal = error_root / target.official_game_date / target.target_id / "terminal_attempt.json"
            error_text = "\n".join(
                path.read_text(encoding="utf-8", errors="ignore")
                for path in error_root.rglob("*") if path.is_file()
            )
            check(
                terminal.is_file() and SECRET not in error_text,
                "source-error receipt exists and redacts the credential",
            )
            error_verification = verify_capture_tree(
                plan_path=plan_path,
                artifact_root=error_root,
                assessed_at_utc=target.entry_target_at_utc,
            )
            check(
                error_verification["complete"] is False
                and error_verification["verified_source_error_artifacts"] == 1,
                "independent verifier retains but rejects a source-error target",
            )
            calls = failing.calls
            check(
                raises(lambda: capture_target(
                    plan_path=plan_path,
                    target_id=target.target_id,
                    schedule_snapshot_path=schedule_path,
                    prediction_archive=prediction_path,
                    output_root=error_root,
                    client=failing,  # type: ignore[arg-type]
                    api_key_env="SYNTHETIC_SHADOW_KEY",
                    max_early_seconds=600,
                    clock=lambda: "2099-07-18T19:05:00Z",
                )) and failing.calls == calls,
                "MUTATION terminal source error cannot be overwritten by retry",
            )

            late_prediction = root / "late_prediction.json"
            write(late_prediction, prediction("2099-07-18T19:11:00Z"))
            check(
                raises(lambda: capture_target(
                    plan_path=plan_path,
                    target_id=target.target_id,
                    schedule_snapshot_path=schedule_path,
                    prediction_archive=late_prediction,
                    output_root=root / "late-live",
                    client=FakeClient(),  # type: ignore[arg-type]
                    api_key_env="SYNTHETIC_SHADOW_KEY",
                    max_early_seconds=600,
                    clock=lambda: "2099-07-18T19:05:00Z",
                )),
                "MUTATION post-target prediction cannot enter forward evidence",
            )
    finally:
        if prior is None:
            os.environ.pop("SYNTHETIC_SHADOW_KEY", None)
        else:
            os.environ["SYNTHETIC_SHADOW_KEY"] = prior

    print(f"\n{PASS}/{PASS + FAIL} checks passed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
