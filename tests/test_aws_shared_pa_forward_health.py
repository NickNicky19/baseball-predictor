"""Health reporting must distinguish future, complete, and missed evidence."""

from __future__ import annotations

import json
import os
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.report_aws_shared_pa_forward_health import _atomic_publish_once, build_report
from src.evaluation.shadow_capture_plan import plan_from_schedule


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "config/shared_pa_forward_runtime_v1.json"
START = datetime(2026, 7, 30, tzinfo=timezone.utc)
HORIZON = START - timedelta(hours=4)


def test_health_publication_sets_group_read_only_mode_before_link(tmp_path: Path) -> None:
    path = tmp_path / "health" / "report.json"
    with patch(
        "scripts.report_aws_shared_pa_forward_health.os.fchmod",
        wraps=os.fchmod,
    ) as chmod:
        _atomic_publish_once(path, b"immutable\n")
    chmod.assert_called_once()
    assert chmod.call_args.args[1] == 0o640
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o640


def _write_plan(path: Path) -> None:
    plan = plan_from_schedule(
        official_game_date="2026-07-29",
        entry_hours=4,
        policy_sha256="a" * 64,
        schedule_snapshot=[{
            "gamePk": 123456,
            "officialDate": "2026-07-29",
            "gameDate": START.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "teams": {
                "home": {"team": {"name": "Home"}},
                "away": {"team": {"name": "Away"}},
            },
        }],
    )
    path.write_text(json.dumps(plan.to_dict()), encoding="utf-8")


def test_future_plan_without_ledger_is_healthy_and_read_only(tmp_path: Path) -> None:
    plans = tmp_path / "plans"
    plans.mkdir()
    _write_plan(plans / "2026-07-29.plan.json")
    ledger = tmp_path / "ledger"
    report = build_report(
        plan_dir=plans,
        ledger_root=ledger,
        runtime_path=RUNTIME,
        assessed_at=HORIZON - timedelta(minutes=30),
    )
    assert report["state"] == "healthy"
    assert report["plans"][0]["due_sides"] == 0
    assert not ledger.exists()


def test_due_plan_without_terminal_records_alerts(tmp_path: Path) -> None:
    plans = tmp_path / "plans"
    plans.mkdir()
    _write_plan(plans / "2026-07-29.plan.json")
    report = build_report(
        plan_dir=plans,
        ledger_root=tmp_path / "ledger",
        runtime_path=RUNTIME,
        assessed_at=HORIZON + timedelta(seconds=1),
    )
    assert report["state"] == "alert"
    assert report["plans"][0]["missing_due_sides"] == 2


def test_health_skips_may_without_opening_plan_or_writing_ledger(tmp_path: Path) -> None:
    plans = tmp_path / "plans"
    plans.mkdir()
    (plans / "2026-05-12.plan.json").write_bytes(b"must-never-be-opened")
    ledger = tmp_path / "ledger"
    report = build_report(
        plan_dir=plans,
        ledger_root=ledger,
        runtime_path=RUNTIME,
        assessed_at=datetime(2026, 5, 12, 12, tzinfo=timezone.utc),
    )
    assert report["state"] == "sealed_may_no_access"
    assert not ledger.exists()
