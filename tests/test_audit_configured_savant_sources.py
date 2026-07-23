from __future__ import annotations

import json

from scripts.audit_configured_savant_sources import audit


def test_audit_records_declared_missing_and_absent_sources_without_reading_data(tmp_path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    declared = config_dir / "declared.json"
    declared.write_text(
        json.dumps({"savant": {"csv_path": "data/savant/stats.csv"}}),
        encoding="utf-8",
    )
    absent = config_dir / "absent.json"
    absent.write_text("{}", encoding="utf-8")

    result = audit(tmp_path, [declared, absent])

    assert result["configured_count"] == 1
    assert result["configured_missing_count"] == 1
    assert result["source_rows_read"] == 0
    assert result["outcome_fields_read"] is False
    assert result["may_2026_touched"] is False
