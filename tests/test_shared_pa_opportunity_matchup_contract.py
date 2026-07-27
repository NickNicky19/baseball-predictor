from __future__ import annotations

import hashlib
from copy import deepcopy
from pathlib import Path

import pytest

from src.evaluation.forward_pitcher_context import ProbablePitcher
from src.evaluation.forward_pitcher_context_v2 import ForwardPitcherContextV2, ForwardPitcherContextV2Error, context_v2_from_schedule
from src.evaluation.projected_lineup_contract import sha256_value
from src.evaluation.projected_lineup_contract_v2 import ProjectedLineupContractV2Error
from src.evaluation.prospective_batter_opportunity import ProspectiveBatterOpportunityError
from src.evaluation.shadow_capture_plan import CaptureTarget, ShadowCapturePlan
from src.evaluation.shared_pa_opportunity_matchup_contract import (
    SharedPAOpportunityMatchupError,
    build_candidate_input_bundle,
    load_protocol,
)


ROOT = Path(__file__).resolve().parents[1]


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _target() -> CaptureTarget:
    return CaptureTarget(123, "2026-07-28", "2026-07-28T23:00:00Z", "2026-07-28T19:00:00Z", 4)


def _plan(target: CaptureTarget) -> ShadowCapturePlan:
    return ShadowCapturePlan(target.official_game_date, 4, _sha("policy"), _sha("schedule"), (target,))


def _core() -> dict:
    return {
        "schema_version": "time-safe-batter-core-receipt-v1", "source_kind": "internal_point_in_time_batter_history",
        "player_id": 1, "observed_at_utc": "2026-07-28T18:59:00Z", "max_source_game_date": "2026-07-27",
        "feature_payload_sha256": _sha("features"), "feature_schema_sha256": _sha("schema"),
    }


def _opportunity() -> dict:
    features = []
    for player_id in range(1, 10):
        features.append({
            "player_id": player_id, "descriptive_start_share_over_captured_games": 0.8,
            "descriptive_mean_pa_given_start": 4.1, "fit_eligible": True,
        })
    row = {
        "schema_version": "prospective-batter-opportunity-snapshot-v1", "terminal_state": "captured_complete",
        "research_only": True, "betting_authorized": False, "production_probability_consumption_authorized": False,
        "official_game_date": "2026-07-28", "mlb_game_pk": 123, "side": "home", "team_id": 147,
        "target_horizon_utc": "2026-07-28T19:00:00.000000Z", "assembled_at_utc": "2026-07-28T18:59:00.000000Z",
        "history_coverage": {"complete_coverage": True, "denominator_receipt_bound": True, "captured_game_count": 1, "captured_prior_game_dates": ["2026-07-27"]},
        "features": features,
    }
    return {**row, "snapshot_sha256": sha256_value(row)}


def _lineup() -> dict:
    history = {"source_kind": "internal_historical_lineup_feature_store", "source_record_id": "history", "received_at_utc": "2026-07-28T18:58:00Z", "payload_sha256": _sha("history"), "input_surface_sha256": _sha("surface")}
    roster = {**history, "source_kind": "official_mlb_active_roster_t4", "source_record_id": "roster"}
    row = {
        "schema_version": "projected-lineup-projection-v1", "terminal_state": "projected_complete", "research_only": True,
        "betting_authorized": False, "official_game_date": "2026-07-28", "mlb_game_pk": 123, "team_id": 147,
        "target_horizon_utc": "2026-07-28T19:00:00Z", "projection_receipt_utc": "2026-07-28T18:59:00Z",
        "input_receipts": {"active_roster": roster, "historical_lineup_features": history},
        "active_roster_player_ids": list(range(1, 12)), "fitted_candidate_id": "lineup-v1",
        "fitted_artifact_sha256": _sha("artifact"), "model_code_sha256": _sha("code"),
        "scenarios": [{"probability": 1.0, "lineup": [{"player_id": player_id, "slot": player_id} for player_id in range(1, 10)]}],
    }
    return {**row, "projection_content_sha256": sha256_value(row)}


def _pitcher() -> dict:
    target = _target()
    context = ForwardPitcherContextV2(
        target.target_id, _plan(target).plan_sha256, "2026-07-28T18:59:00Z", "mlb_statsapi_schedule", _sha("raw"),
        123, "2026-07-28", "2026-07-28T23:00:00Z", "R", 147, "New York Yankees", 111, "Boston Red Sox",
        ProbablePitcher("resolved", 9001), ProbablePitcher("resolved", 9002),
    )
    return context.to_dict()


def _build(**overrides):
    values = dict(
        target=_target(), batting_side="home", batting_team_id=147, player_id=1, batter_core_receipt=_core(),
        opportunity_snapshot=_opportunity(), projected_lineup_record=_lineup(), pitcher_context_record=_pitcher(),
        lineup_contract_path=ROOT / "config/projected_lineup_contract_v1.json",
    )
    values.update(overrides)
    return build_candidate_input_bundle(**values)


def test_complete_bundle_is_hash_bound_but_does_not_authorize_prediction_or_betting():
    result = _build()
    assert result["terminal_state"] == "eligible_complete"
    assert result["feature_values"]["opposing_probable_pitcher_id"] == 9002
    assert result["prediction_authorized"] is False
    assert result["betting_authorized"] is False


def test_missing_receipted_block_is_terminal_abstention_without_feature_fallback():
    result = _build(pitcher_context_record=None)
    assert result["terminal_state"] == "terminal_missing_required_input"
    assert result["feature_values"] is None
    assert result["prediction_authorized"] is False


def test_swapped_official_team_identity_cannot_reach_matchup_features():
    row = _pitcher()
    row["home_team_id"], row["away_team_id"] = row["away_team_id"], row["home_team_id"]
    rebuilt = ForwardPitcherContextV2.from_mapping({**row, "context_sha256": ForwardPitcherContextV2(
        row["target_id"], row["plan_sha256"], row["captured_at_utc"], row["source_name"], row["source_payload_sha256"],
        row["mlb_game_pk"], row["official_game_date"], row["official_start_time_utc"], row["game_type"],
        row["home_team_id"], row["home_team_name"], row["away_team_id"], row["away_team_name"],
        ProbablePitcher(**row["home_probable_pitcher"]), ProbablePitcher(**row["away_probable_pitcher"]),
    ).context_sha256}).to_dict()
    with pytest.raises(SharedPAOpportunityMatchupError, match="team-side"):
        _build(pitcher_context_record=rebuilt)


def test_late_opportunity_snapshot_fails_even_when_attacker_recomputes_hash():
    row = _opportunity()
    row["assembled_at_utc"] = "2026-07-28T19:00:01Z"
    row["snapshot_sha256"] = sha256_value({key: value for key, value in row.items() if key != "snapshot_sha256"})
    with pytest.raises(ProspectiveBatterOpportunityError, match="after T-minus-4"):
        _build(opportunity_snapshot=row)


def test_same_day_opportunity_history_fails_even_when_hash_is_recomputed():
    row = _opportunity()
    row["history_coverage"]["captured_prior_game_dates"] = ["2026-07-28"]
    row["snapshot_sha256"] = sha256_value({key: value for key, value in row.items() if key != "snapshot_sha256"})
    with pytest.raises(ProspectiveBatterOpportunityError, match="same-day or future"):
        _build(opportunity_snapshot=row)


def test_v2_pitcher_receipt_rejects_sealed_or_noncanonical_dates():
    row = _pitcher()
    for bad_date in ("2026-05-14", "2026-5-14"):
        changed = deepcopy(row)
        changed["official_game_date"] = bad_date
        with pytest.raises(ForwardPitcherContextV2Error):
            ForwardPitcherContextV2.from_mapping(changed)


def test_v2_source_parser_retains_official_team_ids_and_probable_pitchers():
    target = _target()
    game = {
        "gamePk": 123, "officialDate": "2026-07-28", "gameDate": "2026-07-28T23:00:00Z", "gameType": "R",
        "teams": {
            "home": {"team": {"id": 147, "name": "New York Yankees"}, "probablePitcher": {"id": 9001}},
            "away": {"team": {"id": 111, "name": "Boston Red Sox"}, "probablePitcher": {"id": 9002}},
        },
    }
    context = context_v2_from_schedule(target=target, plan=_plan(target), captured_at_utc="2026-07-28T18:59:00Z", source_payload_sha256=_sha("raw"), schedule_games=[game])
    assert (context.home_team_id, context.away_team_id) == (147, 111)
    assert context.away_probable_pitcher.player_id == 9002


def test_v2_source_parser_fails_closed_when_official_team_id_is_missing():
    target = _target()
    game = {
        "gamePk": 123, "officialDate": "2026-07-28", "gameDate": "2026-07-28T23:00:00Z", "gameType": "R",
        "teams": {
            "home": {"team": {"name": "New York Yankees"}, "probablePitcher": {"id": 9001}},
            "away": {"team": {"id": 111, "name": "Boston Red Sox"}, "probablePitcher": {"id": 9002}},
        },
    }
    with pytest.raises(ForwardPitcherContextV2Error, match="official team identity"):
        context_v2_from_schedule(target=target, plan=_plan(target), captured_at_utc="2026-07-28T18:59:00Z", source_payload_sha256=_sha("raw"), schedule_games=[game])


@pytest.mark.parametrize("bad_date", ["2026-05-14", "2026-5-14", "05/14/2026"])
def test_v2_lineup_boundary_rejects_sealed_and_noncanonical_dates_after_rehash(bad_date: str):
    row = _lineup()
    row["official_game_date"] = bad_date
    row["projection_content_sha256"] = sha256_value({key: value for key, value in row.items() if key != "projection_content_sha256"})
    with pytest.raises(ProjectedLineupContractV2Error):
        _build(projected_lineup_record=row)


def test_protocol_cannot_be_mistaken_for_active_or_betting_authority():
    protocol = load_protocol(ROOT / "config/shared_pa_opportunity_matchup_protocol_v1.json")
    assert protocol["status"] == "PREDECLARED_RESEARCH_ONLY_NOT_ACTIVE"
    assert protocol["protected_boundaries"]["betting_authorized"] is False
