from __future__ import annotations

from datetime import date
import json
from pathlib import Path

import pandas as pd
import pytest

from scripts.backfill_permissible_statcast_inputs import (
    permissible_chunks,
    run,
)
from src.data.historical_backfill_contract import BackfillContractError


def _frame(start: date) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "game_date": [start.isoformat(), start.isoformat()],
            "batter": [1, 1],
            "player_name": ["Pitcher, Not Batter", "Pitcher, Not Batter"],
            "pitcher": [2, 2],
            "at_bat_number": [1, 1],
            "pitch_number": [1, 2],
            "sv_id": ["a", "b"],
            "launch_speed": [100.0, 90.0],
            "launch_speed_angle": [6, 1],
            "events": ["home_run", "field_out"],
            "postgame_result": ["must disappear", "must disappear"],
        }
    )


def test_planner_skips_may_without_emitting_a_may_query() -> None:
    chunks = permissible_chunks(date(2026, 4, 29), date(2026, 6, 3), days=7)
    assert chunks == [
        (date(2026, 4, 29), date(2026, 4, 30)),
        (date(2026, 6, 1), date(2026, 6, 3)),
    ]


def test_runner_never_calls_fetcher_for_may_and_writes_input_only(tmp_path: Path) -> None:
    calls: list[tuple[date, date]] = []

    def fetcher(start: date, end: date) -> pd.DataFrame:
        calls.append((start, end))
        return _frame(start)

    config = tmp_path / "contract.json"
    config.write_text("{}", encoding="utf-8")
    outputs = run(
        start=date(2026, 4, 29),
        end=date(2026, 6, 3),
        out_root=tmp_path / "permissible",
        config_path=config,
        chunk_days=7,
        fetcher=fetcher,
    )
    assert calls == [
        (date(2026, 4, 29), date(2026, 4, 30)),
        (date(2026, 6, 1), date(2026, 6, 3)),
    ]
    assert len(outputs) == 2
    for manifest_path in outputs:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["outcome_fields"] == "EXCLUDED"
        frame = pd.read_csv(manifest_path.parent / manifest["artifact"]["path"])
        assert "events" not in frame
        assert "postgame_result" not in frame
        assert "player_name" not in frame


def test_provider_returning_may_row_fails_before_write(tmp_path: Path) -> None:
    config = tmp_path / "contract.json"
    config.write_text("{}", encoding="utf-8")

    def bad_fetcher(start: date, end: date) -> pd.DataFrame:
        frame = _frame(start)
        frame.loc[0, "game_date"] = "2026-05-01"
        return frame

    with pytest.raises(BackfillContractError, match="sealed May"):
        run(
            start=date(2026, 6, 1),
            end=date(2026, 6, 1),
            out_root=tmp_path / "permissible",
            config_path=config,
            chunk_days=7,
            fetcher=bad_fetcher,
        )
    assert not (tmp_path / "permissible").exists()
