#!/usr/bin/env python3
"""Network-free state-machine and schedule mutations for the service tick."""

from __future__ import annotations

import sys
import tempfile
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from run_shadow_collector_tick import (  # noqa: E402
    ShadowCollectorServiceError,
    _prepare_predictions,
    ensure_plan,
    load_runtime_config,
    run_tick,
    target_action,
)
import run_shadow_collector_tick as collector_tick  # noqa: E402
from src.evaluation.forward_evidence_era import ForwardEvidenceEraError  # noqa: E402
from src.evaluation.shadow_capture_plan import CaptureTarget  # noqa: E402


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
    except (OSError, ValueError, ShadowCollectorServiceError):
        return True
    return False


def schedule(start: str, *, presentation: str = "one") -> list[dict]:
    return [{
        "gamePk": 901,
        "officialDate": "2099-07-18",
        "gameDate": start,
        "status": {"abstractGameState": presentation},
        "teams": {
            "home": {"team": {"name": "Los Angeles Dodgers"}},
            "away": {"team": {"name": "San Francisco Giants"}},
        },
    }]


def prediction(captured_at: str) -> dict:
    return {
        "game_date": "2099-07-18",
        "prediction_provenance": {
            "schema_version": "daily-prediction-provenance-v1",
            "captured_at_utc": captured_at,
            "model_version": "synthetic-tick-v1",
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
    runtime = load_runtime_config(ROOT / "config/shadow_collector_runtime.json")
    check(
        runtime.prediction_lead_seconds == 1200
        and runtime.capture_max_early_seconds == 120
        and len(runtime.sha256) == 64,
        "locked research-only runtime loads with a content hash",
    )
    check(
        raises(lambda: run_tick(
            official_date="2026-05-15",
            service_root=Path("unused"),
            policy_path=ROOT / "config/shadow_hits_research_policy.json",
            runtime_path=ROOT / "config/shadow_collector_runtime.json",
            model_config=ROOT / "config/config.kbb.json",
            now_utc="2026-05-15T00:00:00Z",
            schedule_loader=lambda _: (_ for _ in ()).throw(AssertionError("May network access")),
            project_root=ROOT,
        )),
        "MUTATION May 2026 is blocked before schedule, model, provider, or outcome access",
    )

    with tempfile.TemporaryDirectory(prefix="shadow_tick_scope_") as temporary:
        scope_root = Path(temporary)

        class ProbeAPI:
            constructed = 0
            schedule_calls = 0

            def __init__(self) -> None:
                type(self).constructed += 1

            def get_schedule(self, _: str) -> list[dict]:
                type(self).schedule_calls += 1
                raise AssertionError("sealed May schedule must not be read")

        with patch.object(collector_tick, "MLBStatsAPI", ProbeAPI):
            check(
                collector_tick.main([
                    "--date", "2026-05-15",
                    "--service-root", str(scope_root / "missing"),
                    "--policy", str(ROOT / "config/shadow_hits_research_policy.json"),
                    "--runtime", str(ROOT / "config/shadow_collector_runtime.json"),
                    "--model-config", str(ROOT / "config/config.kbb.json"),
                ]) == 2
                and ProbeAPI.constructed == 0
                and ProbeAPI.schedule_calls == 0,
                "MUTATION missing evidence scope fails before MLB client initialization",
            )

        ProbeAPI.constructed = 0
        ProbeAPI.schedule_calls = 0
        calls: list[Path] = []

        def reject_runtime(path: str | Path, *, root: str | Path) -> dict:
            calls.append(Path(path))
            raise ForwardEvidenceEraError("running Python/runtime differs from the frozen manifest")

        with (
            patch.object(collector_tick, "validate_evidence_scope", reject_runtime),
            patch.object(collector_tick, "MLBStatsAPI", ProbeAPI),
        ):
            check(
                collector_tick.main([
                    "--date", "2026-05-15",
                    "--service-root", str(scope_root / "runtime_drift"),
                    "--policy", str(ROOT / "config/shadow_hits_research_policy.json"),
                    "--runtime", str(ROOT / "config/shadow_collector_runtime.json"),
                    "--model-config", str(ROOT / "config/config.kbb.json"),
                ]) == 2
                and len(calls) == 1
                and ProbeAPI.constructed == 0
                and ProbeAPI.schedule_calls == 0,
                "MUTATION runtime-manifest rejection fails before MLB client initialization",
            )

        ProbeAPI.constructed = 0
        ProbeAPI.schedule_calls = 0
        calls.clear()

        def accept_scope(path: str | Path, *, root: str | Path) -> dict:
            calls.append(Path(path))
            return {
                "mode": "operational_smoke",
                "economic_evidence_eligible": False,
            }

        with (
            patch.object(collector_tick, "validate_evidence_scope", accept_scope),
            patch.object(collector_tick, "MLBStatsAPI", ProbeAPI),
        ):
            check(
                collector_tick.main([
                    "--date", "2026-05-15",
                    "--service-root", str(scope_root / "valid"),
                    "--policy", str(ROOT / "config/shadow_hits_research_policy.json"),
                    "--runtime", str(ROOT / "config/shadow_collector_runtime.json"),
                    "--model-config", str(ROOT / "config/config.kbb.json"),
                ]) == 2
                and len(calls) == 1
                and ProbeAPI.constructed == 1
                and ProbeAPI.schedule_calls == 0,
                "a validated excluded-smoke scope reaches the sealed-May guard without schedule access",
            )
    target = CaptureTarget(
        mlb_game_pk=901,
        official_game_date="2099-07-18",
        official_start_time_utc="2099-07-18T23:10:00Z",
        entry_target_at_utc="2099-07-18T19:10:00Z",
        entry_hours=4,
    )
    cases = [
        ("2099-07-18T18:49:59Z", False, False, "future"),
        ("2099-07-18T18:50:00Z", False, False, "prepare"),
        ("2099-07-18T19:07:59Z", True, False, "wait_capture"),
        ("2099-07-18T19:08:00Z", True, False, "capture"),
        ("2099-07-18T19:11:00Z", True, False, "missed"),
        ("2099-07-18T19:11:00Z", True, True, "complete"),
    ]
    for now, prepared, terminal, expected in cases:
        check(
            target_action(
                target,
                now_utc=now,
                prediction_ready=prepared,
                terminal_exists=terminal,
                prediction_lead_seconds=runtime.prediction_lead_seconds,
                capture_max_early_seconds=runtime.capture_max_early_seconds,
            ) == expected,
            f"state machine emits {expected} at its exact boundary",
        )

    with tempfile.TemporaryDirectory(prefix="shadow_tick_") as temporary:
        root = Path(temporary)
        current = schedule("2099-07-18T23:10:00Z")

        def loader(_: str) -> list[dict]:
            return current

        plan, plan_path, schedule_path = ensure_plan(
            official_date="2099-07-18",
            service_root=root,
            policy_path=ROOT / "config/shadow_hits_research_policy.json",
            schedule_loader=loader,
            now_utc="2099-07-18T18:00:00Z",
        )
        check(
            plan_path.is_file() and schedule_path.is_file() and len(plan.targets) == 1,
            "future plan and canonical schedule are published before T-4h",
        )
        original_sha = plan.plan_sha256
        current = schedule("2099-07-18T23:10:00Z", presentation="mutable-display-change")
        unchanged, _, _ = ensure_plan(
            official_date="2099-07-18",
            service_root=root,
            policy_path=ROOT / "config/shadow_hits_research_policy.json",
            schedule_loader=loader,
            now_utc="2099-07-18T18:01:00Z",
        )
        check(
            unchanged.plan_sha256 == original_sha,
            "unrelated mutable MLB presentation fields do not supersede identity",
        )

        current = schedule("2099-07-18T23:20:00Z")
        replacement, _, _ = ensure_plan(
            official_date="2099-07-18",
            service_root=root,
            policy_path=ROOT / "config/shadow_hits_research_policy.json",
            schedule_loader=loader,
            now_utc="2099-07-18T18:02:00Z",
        )
        check(
            replacement.plan_sha256 != original_sha
            and (root / "plans/2099-07-18/superseded" / original_sha / "supersession.json").is_file(),
            "pre-target official start drift creates a hash-bound superseding plan",
        )

        current = schedule("2099-07-18T23:30:00Z")
        check(
            raises(lambda: ensure_plan(
                official_date="2099-07-18",
                service_root=root,
                policy_path=ROOT / "config/shadow_hits_research_policy.json",
                schedule_loader=loader,
                now_utc="2099-07-18T19:21:00Z",
            )),
            "MUTATION schedule drift after the old target is due blocks instead of retiming history",
        )
        check(
            (root / "plans/2099-07-18/schedule_drift.json").is_file(),
            "blocked schedule drift remains visible for the independent verifier",
        )

        project = root / "synthetic_project"
        archive = project / "data/learning/predictions/predictions_2099-07-18.json"
        archive.parent.mkdir(parents=True, exist_ok=True)
        model_config = project / "config.json"
        model_config.parent.mkdir(parents=True, exist_ok=True)
        model_config.write_text("{}\n", encoding="utf-8")
        fake_runner = lambda *args, **kwargs: subprocess.CompletedProcess([], 0, "ok\n", "")
        archive.write_text(json.dumps(prediction("2099-07-18T18:49:59Z")), encoding="utf-8")
        check(
            raises(lambda: _prepare_predictions(
                official_date="2099-07-18",
                targets=[target],
                service_root=root / "stale_prediction_service",
                model_config=model_config,
                runtime=runtime,
                project_root=project,
                runner=fake_runner,
                clock=lambda: "2099-07-18T18:50:00Z",
            )),
            "MUTATION a successful command cannot reuse a stale prediction archive",
        )
        archive.write_text(json.dumps(prediction("2099-07-18T18:50:00Z")), encoding="utf-8")
        _prepare_predictions(
            official_date="2099-07-18",
            targets=[target],
            service_root=root / "fresh_prediction_service",
            model_config=model_config,
            runtime=runtime,
            project_root=project,
            runner=fake_runner,
            clock=lambda: "2099-07-18T18:50:00Z",
        )
        check(
            (root / "fresh_prediction_service/prepared/2099-07-18" / target.target_id / "receipt.json").is_file(),
            "a newly published provenance timestamp creates a target-specific immutable snapshot",
        )

    print(f"\n{PASS}/{PASS + FAIL} checks passed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
