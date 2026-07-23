"""The GitHub backup must run early; AWS owns per-game T-4 collection."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_daily_prediction_backup_is_early_and_explicitly_non_primary() -> None:
    workflow = (ROOT / ".github" / "workflows" / "daily-predictions.yml").read_text(
        encoding="utf-8"
    )

    assert '- cron: "0 12 * * *"' in workflow
    assert "NOT the primary T-4h" in workflow
    assert "whole-slate boundary in run_slate.py remains authoritative" in workflow
    assert 'cron: "0 20 * * *"' not in workflow
