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
from scripts.report_aws_pitcher_receipt_health import build_report
from scripts.run_aws_pitcher_receipt_tick import AWSReceiptTickError, run_all
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

        with patch.object(legacy_plan_builder, "MLBStatsAPI", ProbeAPI):
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
        print("[OK] health report proves complete terminal receipt coverage without outcomes")

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

    print("10/10")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
