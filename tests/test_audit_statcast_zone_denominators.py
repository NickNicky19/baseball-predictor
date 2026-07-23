from __future__ import annotations

import pandas as pd
import pytest

from scripts.audit_statcast_zone_denominators import audit


def test_zone_audit_reads_only_locked_development_seasons(tmp_path) -> None:
    for season in (2023, 2024):
        folder = tmp_path / str(season)
        folder.mkdir()
        pd.DataFrame({"zone": [1, None, 14]}).to_csv(folder / "batter_7.csv", index=False)
    result = audit(tmp_path, (2023, 2024))

    assert result["rows"] == 6
    assert result["missing_zone"] == 2
    assert result["nonnumeric_nonmissing"] == 0
    assert result["numeric_outside_1_14"] == 0
    assert result["outcome_fields_read"] is False
    assert result["may_2026_touched"] is False


def test_zone_audit_records_zero_byte_file_without_inventing_rows(tmp_path) -> None:
    folder = tmp_path / "2023"
    folder.mkdir()
    (folder / "batter_7.csv").write_bytes(b"")
    result = audit(tmp_path, (2023,))

    assert result["files"] == 1
    assert result["empty_files"] == 1
    assert result["rows"] == 0


@pytest.mark.parametrize("seasons", [(2026,), (2023, 2026), ()])
def test_zone_audit_refuses_unlocked_or_empty_season_scope(tmp_path, seasons) -> None:
    with pytest.raises(ValueError, match="2023/2024"):
        audit(tmp_path, seasons)
