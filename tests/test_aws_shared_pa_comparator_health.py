import json
from datetime import datetime, timezone
from pathlib import Path

from scripts.report_aws_shared_pa_comparator_health import build_report
from src.evaluation.shadow_capture_plan import canonical_schedule_records, plan_from_schedule
from src.evaluation.shared_pa_comparator_runtime import canonical_bytes


def test_may_health_does_not_read_or_write_plan_tree(tmp_path: Path) -> None:
    report = build_report(
        plan_dir=tmp_path / "missing", evidence_root=tmp_path / "missing-evidence",
        assessed_at=datetime(2026, 5, 10, 12, tzinfo=timezone.utc),
    )
    assert report["state"] == "sealed_may_no_access"
    assert report["report_written"] is False
    assert not any(tmp_path.iterdir())


def test_health_fails_attention_closed_when_provider_credential_is_missing(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    day = "2026-07-30"
    game = {
        "gamePk": 901, "officialDate": day, "gameDate": "2026-07-30T23:10:00Z",
        "teams": {
            "home": {"team": {"name": "Home Club"}},
            "away": {"team": {"name": "Away Club"}},
        },
    }
    plan = plan_from_schedule(
        official_game_date=day, entry_hours=4, policy_sha256="a" * 64,
        schedule_snapshot=canonical_schedule_records([game]),
    )
    plans = tmp_path / "plans"
    plans.mkdir()
    (plans / f"{day}.plan.json").write_bytes(canonical_bytes(plan.to_dict()))
    report = build_report(
        plan_dir=plans, evidence_root=tmp_path / "evidence",
        assessed_at=datetime(2026, 7, 30, 12, tzinfo=timezone.utc),
    )
    assert report["state"] == "attention_required"
    assert report["provider_credential_configured"] is False
    assert report["outcomes_or_settlement_accessed"] is False
