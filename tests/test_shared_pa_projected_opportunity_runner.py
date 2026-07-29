"""Population, abstention, and chronology tests for the opportunity runner."""

from __future__ import annotations

import copy

import pytest

from src.evaluation.shared_pa_forward_runner import StatsBatch
from src.evaluation.shared_pa_projected_opportunity_runner import (
    ProjectedOpportunityRunnerError,
    build_side_candidate_bundle,
)
from tests.test_shared_pa_projected_opportunity_candidate import _inputs, _stats_response
from tests.test_shared_pa_forward_evidence import _snapshot


def _batch(projection: dict, *, error_player: int | None = None) -> StatsBatch:
    responses = {}
    errors = {}
    for player_id in projection["active_roster_player_ids"]:
        if not any(
            entry["player_id"] == player_id
            for scenario in projection["scenarios"]
            for entry in scenario["lineup"]
        ):
            continue
        if player_id == error_player:
            errors[player_id] = "redacted_source_failure"
            continue
        source = copy.deepcopy(_snapshot())
        source["player_id"] = player_id
        responses[player_id] = _stats_response(source)
    return StatsBatch(responses=responses, errors=errors)


def _run(*, batch: StatsBatch | None = None, generated: str | None = None) -> dict:
    _, projection, lineup_contract, forward, protocol = _inputs()
    return build_side_candidate_bundle(
        projected_lineup_record=projection,
        lineup_contract=lineup_contract,
        stats_batch=batch or _batch(projection),
        loaded_forward_contract=forward,
        candidate_protocol=protocol,
        side="home",
        research_epoch_utc="2026-07-27T00:00:00Z",
        prediction_generated_at_utc=generated or projection["target_horizon_utc"],
        collector_instance_id="synthetic-opportunity-runner",
        collector_code_sha256="d" * 64,
        runtime_manifest_sha256="e" * 64,
    )


def test_side_runner_accounts_for_every_projected_support_player() -> None:
    bundle = _run()
    assert bundle["terminal_state"] == "candidate_side_complete"
    assert bundle["coverage"] == {
        "projected_support_players": 10,
        "predicted_players": 10,
        "abstained_players": 0,
    }
    assert len({record["player_id"] for record in bundle["candidate_records"]}) == 10
    assert all(record["research_only"] for record in bundle["candidate_records"])
    assert all(not record["betting_authorized"] for record in bundle["candidate_records"])


def test_one_missing_stats_source_is_an_explicit_abstention_without_substitution() -> None:
    _, projection, _, _, _ = _inputs()
    player_id = projection["scenarios"][0]["lineup"][0]["player_id"]
    bundle = _run(batch=_batch(projection, error_player=player_id))
    assert bundle["terminal_state"] == "candidate_side_partial_abstentions"
    assert bundle["coverage"]["predicted_players"] == 9
    assert bundle["coverage"]["abstained_players"] == 1
    assert bundle["abstentions"][0]["player_id"] == player_id
    assert bundle["abstentions"][0]["probability_substituted"] is False
    assert all(record["player_id"] != player_id for record in bundle["candidate_records"])


def test_unaccounted_projected_player_fails_the_whole_side() -> None:
    _, projection, _, _, _ = _inputs()
    batch = _batch(projection)
    responses = dict(batch.responses)
    responses.pop(next(iter(responses)))
    with pytest.raises(ProjectedOpportunityRunnerError, match="account for every"):
        _run(batch=StatsBatch(responses=responses, errors={}))


def test_invalid_stats_identity_is_retained_as_abstention() -> None:
    _, projection, _, _, _ = _inputs()
    batch = _batch(projection)
    responses = dict(batch.responses)
    player_id = next(iter(responses))
    wrong = copy.deepcopy(_snapshot())
    wrong["player_id"] = 999999
    responses[player_id] = _stats_response(wrong)
    bundle = _run(batch=StatsBatch(responses=responses, errors={}))
    assert bundle["coverage"]["abstained_players"] == 1
    assert bundle["abstentions"][0]["player_id"] == player_id
    assert bundle["abstentions"][0]["reason"].startswith("invalid_player_input_")


def test_post_horizon_side_bundle_cannot_be_backfilled() -> None:
    with pytest.raises(ProjectedOpportunityRunnerError, match="after T-4"):
        _run(generated="2026-07-29T20:00:00.000001Z")
