#!/usr/bin/env python3
"""Network-free regression and mutation checks for AWS pitcher receipts."""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_aws_pitcher_receipt_plan import AWSReceiptPlanError, build_plan
from scripts.report_aws_pitcher_receipt_health import build_report, main as health_main
from scripts.run_aws_pitcher_receipt_tick import AWSReceiptTickError, run_all
from scripts.verify_aws_pitcher_receipt_tree import verify_tree
import scripts.build_forward_pitcher_context_plan as legacy_plan_builder
from src.evaluation.forward_pitcher_context_collector import RawScheduleResponse
from src.evaluation.shadow_capture_plan import ShadowCapturePlanError


def at(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def game() -> dict:
    return {
        "gamePk": 901,
        "officialDate": "2026-07-30",
        "gameDate": "2026-07-30T23:10:00Z",
        "gameType": "R",
        "teams": {
            "home": {"team": {"name": "Home"}, "probablePitcher": {"id": 111}},
            "away": {"team": {"name": "Away"}, "probablePitcher": {"id": 222}},
        },
    }


def response() -> RawScheduleResponse:
    return RawScheduleResponse(
        body=json.dumps({"dates": [{"games": [game()]}]}, sort_keys=True).encode("utf-8"),
        received_at_utc="2026-07-30T00:15:00Z",
    )


def response_for(*, official_date: str, game_pk: int, start: str, received: str) -> RawScheduleResponse:
    row = game()
    row["gamePk"] = game_pk
    row["officialDate"] = official_date
    row["gameDate"] = start
    return RawScheduleResponse(
        body=json.dumps({"dates": [{"games": [row]}]}, sort_keys=True).encode("utf-8"),
        received_at_utc=received,
    )


def empty_response(*, received: str) -> RawScheduleResponse:
    return RawScheduleResponse(
        body=b'{"dates": []}',
        received_at_utc=received,
    )


def runtime() -> Path:
    return ROOT / "config/forward_pitcher_context_runtime_v1.json"


def fails(fn) -> bool:
    try:
        fn()
    except (OSError, ValueError, RuntimeError, AWSReceiptPlanError, AWSReceiptTickError, ShadowCapturePlanError):
        return True
    return False


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="aws_pitcher_receipts_") as temporary:
        base = Path(temporary)
        plans, receipts, ledgers = base / "plans", base / "receipts", base / "ledgers"
        plans.mkdir()
        with patch("scripts.run_aws_pitcher_receipt_tick.fetcher", lambda _: (_ for _ in ()).throw(AssertionError("no plan must not fetch"))):
            waiting = run_all(plan_dir=plans, ledger_root=ledgers, runtime_path=runtime(), now=at("2026-07-30T00:00:00Z"))
        assert waiting["plans"] == [] and waiting["collector_state"] == "awaiting_published_plan"
        print("[OK] clean AWS startup waits for the scheduled plan without inventing a capture")
        calls: list[str] = []

        def fake_fetcher(_runtime: dict):
            def fetch(date: str) -> RawScheduleResponse:
                calls.append(date)
                return response()
            return fetch

        with patch("scripts.build_aws_pitcher_receipt_plan.fetcher", fake_fetcher):
            published = build_plan(
                official_date="2026-07-30", plan_dir=plans, receipt_dir=receipts,
                runtime_path=runtime(), now=at("2026-07-30T00:16:00Z"),
            )
        assert calls == ["2026-07-30"] and published["targets"] == 1 and published["published"]["plan"]
        plan_path = plans / "2026-07-30.plan.json"
        raw = response().body
        assert hashlib.sha256(raw).hexdigest() == published["source_payload_sha256"]
        assert (receipts / "raw" / f"2026-07-30.{published['source_payload_sha256']}.json").read_bytes() == raw
        print("[OK] exact raw official schedule, plan hash, and source receipt publish together")

        calls.clear()
        with patch("scripts.build_aws_pitcher_receipt_plan.fetcher", fake_fetcher):
            retry = build_plan(
                official_date="2026-07-30", plan_dir=plans, receipt_dir=receipts,
                runtime_path=runtime(), now=at("2026-07-30T00:16:00Z"),
            )
        assert calls == ["2026-07-30"] and not any(retry["published"].values())
        print("[OK] exact retry verifies rather than overwriting immutable plan evidence")

        def no_fetch(_runtime: dict):
            raise AssertionError("May boundary must precede schedule source access")

        with patch("scripts.build_aws_pitcher_receipt_plan.fetcher", no_fetch):
            assert fails(lambda: build_plan(
                official_date="2026-05-15", plan_dir=plans, receipt_dir=receipts,
                runtime_path=runtime(), now=at("2026-05-01T00:00:00Z"),
            ))
        print("[OK] MUTATION May plan request fails before source construction or access")

        class ProbeAPI:
            constructed = 0

            def __init__(self):
                type(self).constructed += 1
                raise AssertionError("legacy plan builder must not construct MLB client for sealed May")

        with patch.object(legacy_plan_builder, "mlb_api_factory", ProbeAPI):
            assert legacy_plan_builder.main([
                "--date", "2026-05-15", "--out", str(base / "forbidden.plan.json"),
            ]) == 2 and ProbeAPI.constructed == 0
        print("[OK] MUTATION legacy plan command rejects May before MLB client construction")

        assert fails(lambda: build_plan(
            official_date="2026-07-30", plan_dir=plans, receipt_dir=receipts,
            runtime_path=runtime(), now=at("2026-07-30T19:11:00Z"),
        ))
        print("[OK] MUTATION post-T-4 plan publication is refused")

        with patch("scripts.run_aws_pitcher_receipt_tick.fetcher", fake_fetcher):
            tick = run_all(
                plan_dir=plans, ledger_root=ledgers, runtime_path=runtime(), now=at("2026-07-30T19:09:20Z"),
            )
        assert tick["plans"][0]["captured"] == 1 and tick["model_or_market_accessed"] is False
        print("[OK] tick captures only target-bound official probable-starter context")

        health = build_report(
            plan_dir=plans, ledger_root=ledgers, runtime_path=runtime(), assessed_at=at("2026-07-30T19:10:00Z"),
        )
        assert health["status"] == "healthy" and health["plans"][0]["ledger"]["state_counts"] == {"captured": 1, "source_error": 0, "missed": 0}
        assert health["plans"][0]["ledger"]["candidate_input_eligible_captured"] == 1
        print("[OK] health report proves complete terminal receipt coverage without outcomes")

        with patch("scripts.build_aws_pitcher_receipt_plan.fetcher", lambda _: lambda _date: response_for(
            official_date="2026-07-31", game_pk=902, start="2026-07-31T23:10:00Z",
            received="2026-07-31T19:10:01Z",
        )):
            assert fails(lambda: build_plan(
                official_date="2026-07-31", plan_dir=base / "late-plans", receipt_dir=base / "late-receipts",
                runtime_path=runtime(), now=at("2026-07-31T00:16:00Z"),
            ))
        print("[OK] MUTATION a plan fetch completing after T-4 cannot publish stale evidence")

        off_root = base / "off-day"
        (off_root / "plans").mkdir(parents=True)
        with patch("scripts.build_aws_pitcher_receipt_plan.fetcher", lambda _: lambda _date: empty_response(received="2026-07-31T04:15:00Z")):
            off_plan = build_plan(
                official_date="2026-07-31", plan_dir=off_root / "plans",
                receipt_dir=off_root / "plan-receipts", runtime_path=runtime(),
                now=at("2026-07-31T04:16:00Z"),
            )
        assert off_plan["targets"] == 0
        with patch("scripts.run_aws_pitcher_receipt_tick.fetcher", lambda _: (_ for _ in ()).throw(AssertionError("off-day must not fetch"))):
            off_tick = run_all(
                plan_dir=off_root / "plans", ledger_root=off_root / "ledgers",
                runtime_path=runtime(), now=at("2026-07-31T19:10:00Z"),
            )
        assert off_tick["plans"][0]["no_scheduled_targets"] is True
        off_health = build_report(
            plan_dir=off_root / "plans", ledger_root=off_root / "ledgers",
            runtime_path=runtime(), assessed_at=at("2026-07-31T19:10:00Z"),
        )
        assert off_health["status"] == "healthy" and off_health["plans"][0]["targets"] == 0
        off_verified = verify_tree(
            evidence_root=off_root, official_date="2026-07-31", runtime_path=runtime(),
            assessed_at=at("2026-07-31T19:10:00Z"),
        )
        assert off_verified["targets"] == 0 and off_verified["health"]["status"] == "healthy"
        print("[OK] official dates with no scheduled games receive a verified zero-target receipt")

        raw_off_path = next((off_root / "plan-receipts" / "raw").glob("*.json"))
        raw_off_original = raw_off_path.read_bytes()
        raw_off_path.write_bytes(raw_off_original + b" ")
        assert fails(lambda: verify_tree(
            evidence_root=off_root, official_date="2026-07-31", runtime_path=runtime(),
            assessed_at=at("2026-07-31T19:10:00Z"),
        ))
        raw_off_path.write_bytes(raw_off_original)
        print("[OK] MUTATION independent verification rejects altered plan-source bytes")

        failure_root = base / "source-failure"
        (failure_root / "plans").mkdir(parents=True)
        failure_response = response_for(
            official_date="2026-08-01", game_pk=903, start="2026-08-01T23:10:00Z",
            received="2026-08-01T04:15:00Z",
        )
        with patch("scripts.build_aws_pitcher_receipt_plan.fetcher", lambda _: lambda _date: failure_response):
            build_plan(
                official_date="2026-08-01", plan_dir=failure_root / "plans",
                receipt_dir=failure_root / "plan-receipts", runtime_path=runtime(),
                now=at("2026-08-01T04:16:00Z"),
            )
        with patch("scripts.run_aws_pitcher_receipt_tick.fetcher", lambda _: lambda _date: (_ for _ in ()).throw(OSError("network unavailable"))):
            failed_tick = run_all(
                plan_dir=failure_root / "plans", ledger_root=failure_root / "ledgers",
                runtime_path=runtime(), now=at("2026-08-01T19:09:20Z"),
            )
        assert failed_tick["plans"][0]["source_error"] == 1
        failure_health = build_report(
            plan_dir=failure_root / "plans", ledger_root=failure_root / "ledgers",
            runtime_path=runtime(), assessed_at=at("2026-08-01T19:10:00Z"),
        )
        assert failure_health["status"] == "alert"
        assert failure_health["plans"][0]["status"] == "terminal_collection_failure"
        assert health_main([
            "--plan-dir", str(failure_root / "plans"),
            "--ledger-root", str(failure_root / "ledgers"),
            "--report-dir", str(failure_root / "health"),
            "--runtime", str(runtime()),
        ]) == 2
        print("[OK] MUTATION terminal source failure writes evidence and exits nonzero for alerting")

        timer = (ROOT / "deploy/forward_pitcher_receipts/baseball-pitcher-receipt-tick.timer").read_text(encoding="utf-8")
        assert "OnUnitActiveSec=15s" in timer and "AccuracySec=1s" in timer
        for service_name in (
            "baseball-pitcher-receipt-plan.service",
            "baseball-pitcher-receipt-tick.service",
            "baseball-pitcher-receipt-health.service",
        ):
            service = (ROOT / "deploy/forward_pitcher_receipts" / service_name).read_text(encoding="utf-8")
            assert "WorkingDirectory=/opt/baseball-predictor-pitcher-receipts/current" in service
            assert "run_slate.py" not in service and "odds-api" not in service.lower()
        workflow = (ROOT / ".github/workflows/pitcher-receipt-verifier.yml").read_text(encoding="utf-8")
        assert "Prove verifier identity is read-only" in workflow
        assert "verify_aws_pitcher_receipt_tree.py" in workflow
        assert "replacement receipt was fetched" in workflow
        assert "Invalid plan hash" in workflow and "Invalid source hash" in workflow
        assert "check_tracked_secrets.py" in workflow
        installer = (ROOT / "deploy/forward_pitcher_receipts/install_exact_release.sh").read_text(encoding="utf-8")
        assert "/opt/baseball-predictor-pitcher-receipts" in installer
        assert "^[0-9a-f]{40}$" in installer
        assert "github_host_key_sha256=\"6233fddbb0a29afc8c4e8c699733c1a188c3a41f2fb63a2640653dc4aea624ce\"" in installer
        assert 'env GIT_SSH_COMMAND="$ssh_command" \\\n    git -C "$temporary/repo" checkout --detach "$commit"' in installer
        assert "run_slate.py" not in installer and "odds-api" not in installer.lower()
        known_hosts = (ROOT / "deploy/forward_pitcher_receipts/github.com_known_hosts").read_bytes()
        assert hashlib.sha256(known_hosts).hexdigest() == "6233fddbb0a29afc8c4e8c699733c1a188c3a41f2fb63a2640653dc4aea624ce"
        print("[OK] scheduler cadence matches the locked runtime and GitHub remains read-only")

        original = plan_path.read_bytes()
        mutated = json.loads(original)
        mutated["official_game_date"] = "2026-05-15"
        plan_path.write_text(json.dumps(mutated), encoding="utf-8")
        assert fails(lambda: run_all(plan_dir=plans, ledger_root=ledgers, runtime_path=runtime(), now=at("2026-07-30T19:09:20Z")))
        plan_path.write_bytes(original)
        print("[OK] MUTATION sealed-May plan cannot reach the AWS receipt runner")

        conflict = json.loads(original)
        conflict["policy_sha256"] = "c" * 64
        plan_path.write_text(json.dumps(conflict), encoding="utf-8")
        with patch("scripts.build_aws_pitcher_receipt_plan.fetcher", fake_fetcher):
            assert fails(lambda: build_plan(
                official_date="2026-07-30", plan_dir=plans, receipt_dir=receipts,
                runtime_path=runtime(), now=at("2026-07-30T00:16:00Z"),
            ))
        plan_path.write_bytes(original)
        print("[OK] MUTATION conflicting plan retry fails without replacing the published plan")

    print("16/16")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
