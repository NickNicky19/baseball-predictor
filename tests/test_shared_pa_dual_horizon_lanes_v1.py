from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.evaluation.shared_pa_dual_horizon_lanes_v1 import (
    SharedPADualHorizonError,
    archive_relative_path,
    bind_prediction_archive_to_lane,
    build_dual_horizon_plans,
)


ROOT = Path(__file__).resolve().parents[1]


def schedule():
    return [{"gamePk": 123, "officialDate": "2026-07-31", "gameDate": "2026-08-01T00:10:00Z"}]


def test_two_lanes_share_game_clock_but_have_distinct_immutable_targets():
    plans = build_dual_horizon_plans(
        official_game_date="2026-07-31",
        schedule_snapshot=schedule(),
        contract_path=ROOT / "config/shared_pa_dual_horizon_lanes_v1.json",
    )
    projected = plans["projected_t4"].targets[0]
    confirmed = plans["confirmed_t1"].targets[0]
    assert projected.entry_hours == 4 and confirmed.entry_hours == 1
    assert projected.official_start_time_utc == confirmed.official_start_time_utc
    assert projected.target_id != confirmed.target_id
    assert datetime.fromisoformat(projected.entry_target_at_utc.replace("Z", "+00:00")) == datetime(2026, 7, 31, 20, 10, tzinfo=timezone.utc)
    assert datetime.fromisoformat(confirmed.entry_target_at_utc.replace("Z", "+00:00")) == datetime(2026, 7, 31, 23, 10, tzinfo=timezone.utc)


def test_archive_namespaces_cannot_overwrite_or_cross_model_boundaries():
    projected = archive_relative_path(
        lane="projected_t4", official_game_date="2026-07-31", mlb_game_pk=123,
        side="home", player_id=456, model_id="shared_pa_candidate_v1",
    )
    confirmed = archive_relative_path(
        lane="confirmed_t1", official_game_date="2026-07-31", mlb_game_pk=123,
        side="home", player_id=456, model_id="shared_pa_candidate_v1",
    )
    assert projected != confirmed
    with pytest.raises(SharedPADualHorizonError):
        archive_relative_path(
            lane="confirmed_t1", official_game_date="2026-05-10", mlb_game_pk=123,
            side="home", player_id=456, model_id="shared_pa_candidate_v1",
        )
    with pytest.raises(SharedPADualHorizonError):
        archive_relative_path(
            lane="projected_t4", official_game_date="2026-07-31", mlb_game_pk=123,
            side="home", player_id=456, model_id="frozen_baseline",
        )


def test_lane_binding_enforces_exact_horizon_and_confirmed_source_state():
    plans = build_dual_horizon_plans(
        official_game_date="2026-07-31", schedule_snapshot=schedule(),
        contract_path=ROOT / "config/shared_pa_dual_horizon_lanes_v1.json",
    )
    projected_archive = {
        "model_id": "shared_pa_candidate_v1", "game_date": "2026-07-31",
        "research_only": True, "betting_authorized": False,
        "predictions": [{
            "mlb_game_pk": 123,
            "decision_horizon_utc": "2026-07-31T20:10:00Z",
            "input_health": {"lineup_state": "projected_probability_distribution"},
        }],
    }
    bound = bind_prediction_archive_to_lane(
        lane="projected_t4", plan=plans["projected_t4"], archive=projected_archive
    )
    assert bound["lane_id"] == "projected_t4"
    with pytest.raises(SharedPADualHorizonError, match="confirmed-lineup"):
        bind_prediction_archive_to_lane(
            lane="confirmed_t1",
            plan=plans["confirmed_t1"],
            archive={
                **projected_archive,
                "predictions": [{
                    **projected_archive["predictions"][0],
                    "decision_horizon_utc": "2026-07-31T23:10:00Z",
                }],
            },
        )
