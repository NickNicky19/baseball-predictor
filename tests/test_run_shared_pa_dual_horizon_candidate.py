from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.run_shared_pa_dual_horizon_candidate import run_lane
from src.evaluation.shared_pa_dual_horizon_lanes_v1 import (
    SharedPADualHorizonError,
    build_dual_horizon_plans,
    publish_lane_archive_once,
)


ROOT = Path(__file__).resolve().parents[1]


def _plan(tmp_path: Path, lane: str) -> Path:
    plans = build_dual_horizon_plans(
        official_game_date="2026-07-31",
        schedule_snapshot=[{
            "gamePk": 123, "officialDate": "2026-07-31",
            "gameDate": "2026-08-01T00:10:00Z",
        }],
        contract_path=ROOT / "config/shared_pa_dual_horizon_lanes_v1.json",
    )
    path = tmp_path / f"{lane}.json"
    plans[lane].write(path)
    return path


def test_missing_evidence_freezes_typed_lane_abstention(tmp_path: Path):
    result = run_lane(
        lane="projected_t4", game_date="2026-07-31", game_pk=123,
        plan_path=_plan(tmp_path, "projected_t4"), candidate_evidence=None,
        output_root=tmp_path / "archives",
    )
    output = Path(result["output_path"])
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert result["predicted_players"] == 0
    assert payload["candidate_archive"]["abstentions"][0]["reason_code"] == (
        "PROJECTED_T4_QUALIFIED_EVIDENCE_UNAVAILABLE"
    )
    assert payload["target_game_pk"] == 123
    assert payload["research_only"] is True
    assert payload["betting_authorized"] is False


def test_publish_once_accepts_exact_replay_and_rejects_replacement(tmp_path: Path):
    path = tmp_path / "archive.json"
    publish_lane_archive_once({"value": 1}, path)
    publish_lane_archive_once({"value": 1}, path)
    with pytest.raises(SharedPADualHorizonError, match="different bytes"):
        publish_lane_archive_once({"value": 2}, path)


def test_plan_lane_mismatch_fails_closed(tmp_path: Path):
    with pytest.raises(SharedPADualHorizonError, match="plan date or horizon"):
        run_lane(
            lane="confirmed_t1", game_date="2026-07-31", game_pk=123,
            plan_path=_plan(tmp_path, "projected_t4"), candidate_evidence=None,
            output_root=tmp_path / "archives",
        )
