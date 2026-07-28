"""End-to-end population and no-v1-fallback tests for the v2 side runner."""

from __future__ import annotations

import copy
from datetime import date

import pytest

from src.evaluation.shared_pa_forward_evidence import load_forward_contract
from src.evaluation.shared_pa_projected_opportunity_candidate_v2 import load_protocol_v2
from src.evaluation.shared_pa_projected_opportunity_evidence_v2 import (
    RawDateBoundedStatsResponse,
    expected_stats_requests,
)
from src.evaluation.shared_pa_projected_opportunity_runner_v2 import (
    DateBoundedStatsBatchV2,
    FailedStatsAttemptV2,
    ProjectedOpportunityRunnerV2Error,
    build_side_candidate_bundle_v2,
)
from tests.test_shared_pa_projected_opportunity_candidate_v2 import ROOT, _inputs, _stats


def _support(projection: dict) -> list[int]:
    return sorted({
        int(entry["player_id"])
        for scenario in projection["scenarios"]
        if scenario["probability"] > 0
        for entry in scenario["lineup"]
    })


def _batch(projection: dict, *, error_player: int | None = None) -> DateBoundedStatsBatchV2:
    responses = {}
    errors = {}
    for player_id in _support(projection):
        if player_id == error_player:
            urls, _ = expected_stats_requests(player_id=player_id, target_date=date.fromisoformat("2026-09-17"))
            errors[player_id] = FailedStatsAttemptV2.capture(
                player_id=player_id,
                request_urls=urls,
                attempted_at_utc="2026-09-17T15:57:00Z",
                failed_at_utc="2026-09-17T15:58:00Z",
                error_class="network_error",
            )
        else:
            responses[player_id] = _stats(player_id)
    return DateBoundedStatsBatchV2(responses=responses, errors=errors)


def _run(*, batch: DateBoundedStatsBatchV2 | None = None) -> dict:
    inputs = _inputs()
    projection = inputs["projected_lineup_record"]
    return build_side_candidate_bundle_v2(
        root=ROOT,
        protocol=load_protocol_v2(
            root=ROOT,
            path=ROOT / "config/shared_pa_projected_opportunity_forward_v2.json",
        ),
        plan=inputs["plan"],
        target=inputs["target"],
        side=inputs["side"],
        team_id=inputs["team_id"],
        schedule_response=inputs["schedule_response"],
        active_roster_receipt=inputs["active_roster_receipt"],
        active_roster_raw=inputs["active_roster_raw"],
        history_records=inputs["history_records"],
        history_raw_by_sha256=inputs["history_raw_by_sha256"],
        history_coverage=inputs["history_coverage"],
        history_schedule_raw_by_sha256=inputs["history_schedule_raw_by_sha256"],
        opportunity_snapshot=inputs["opportunity_snapshot"],
        projected_lineup_record=projection,
        stats_batch=batch or _batch(projection),
        prediction_generated_at_utc=inputs["prediction_generated_at_utc"],
        loaded_forward_contract=load_forward_contract(
            root=ROOT,
            contract_path=ROOT / "config/shared_pa_forward_evidence_contract_v1.json",
        ),
    )


def test_v2_side_runner_accounts_for_every_projected_player() -> None:
    bundle = _run()
    assert bundle["schema_version"].endswith("-v2")
    assert bundle["candidate_id"].endswith("_v2")
    assert bundle["coverage"] == {
        "projected_support_players": 10,
        "predicted_players": 10,
        "abstained_players": 0,
    }
    assert all(record["schema_version"].endswith("-v2") for record in bundle["candidate_records"])
    assert all(record["confirmation_eligible"] is False for record in bundle["candidate_records"])


def test_v2_runner_never_calls_the_v1_candidate_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.evaluation.shared_pa_projected_opportunity_candidate as legacy

    def forbidden(*args, **kwargs):
        raise AssertionError("v1 unbound candidate path was called")

    monkeypatch.setattr(legacy, "build_projected_opportunity_candidate", forbidden)
    assert _run()["coverage"]["predicted_players"] == 10


def test_one_missing_player_is_an_explicit_no_substitution_abstention() -> None:
    projection = _inputs()["projected_lineup_record"]
    player_id = _support(projection)[0]
    bundle = _run(batch=_batch(projection, error_player=player_id))
    assert bundle["terminal_state"] == "candidate_side_partial_abstentions"
    assert bundle["coverage"]["predicted_players"] == 9
    assert bundle["coverage"]["abstained_players"] == 1
    assert bundle["abstentions"][0]["player_id"] == player_id
    assert bundle["abstentions"][0]["probability_substituted"] is False
    attempt = bundle["abstentions"][0]["source_attempt_receipt"]
    assert attempt["player_id"] == player_id
    expected_urls, _ = expected_stats_requests(
        player_id=player_id, target_date=date.fromisoformat("2026-09-17")
    )
    assert attempt["request_urls"] == expected_urls
    assert attempt["attempted_at_utc"] == "2026-09-17T15:57:00Z"
    assert attempt["failed_at_utc"] == "2026-09-17T15:58:00Z"
    assert attempt["attempt_sha256"] == bundle["abstentions"][0]["source_attempt_sha256"]


def test_invalid_player_stats_evidence_abstains_without_fallback() -> None:
    projection = _inputs()["projected_lineup_record"]
    batch = _batch(projection)
    responses = dict(batch.responses)
    player_id = next(iter(responses))
    bad = list(copy.deepcopy(responses[player_id]))
    original = bad[0]
    bad[0] = RawDateBoundedStatsResponse.capture(
        body=original.body,
        request_sent_at_utc=original.request_sent_at_utc,
        received_at_utc=original.received_at_utc,
        request_url=original.request_url.replace("statsapi.mlb.com", "example.invalid"),
    )
    responses[player_id] = bad
    bundle = _run(batch=DateBoundedStatsBatchV2(responses=responses, errors={}))
    assert bundle["coverage"]["abstained_players"] == 1
    assert bundle["abstentions"][0]["reason"] == "invalid_date_bounded_stats_evidence"


@pytest.mark.parametrize("late_field", ["request", "response"])
def test_malformed_stats_after_generation_cannot_create_backdated_abstention(
    late_field: str,
) -> None:
    projection = _inputs()["projected_lineup_record"]
    batch = _batch(projection)
    responses = dict(batch.responses)
    player_id = next(iter(responses))
    bad = list(copy.deepcopy(responses[player_id]))
    original = bad[0]
    request_sent_at_utc = (
        "2026-09-17T15:59:30Z"
        if late_field == "request"
        else original.request_sent_at_utc
    )
    received_at_utc = "2026-09-17T15:59:40Z" if late_field == "request" else "2026-09-17T15:59:30Z"
    bad[0] = RawDateBoundedStatsResponse.capture(
        body=original.body,
        request_sent_at_utc=request_sent_at_utc,
        received_at_utc=received_at_utc,
        request_url=original.request_url.replace("statsapi.mlb.com", "example.invalid"),
    )
    responses[player_id] = bad
    with pytest.raises(ProjectedOpportunityRunnerV2Error, match="postdates prediction generation"):
        _run(batch=DateBoundedStatsBatchV2(responses=responses, errors={}))


def test_unaccounted_projected_player_fails_the_whole_side() -> None:
    projection = _inputs()["projected_lineup_record"]
    batch = _batch(projection)
    responses = dict(batch.responses)
    responses.pop(next(iter(responses)))
    with pytest.raises(ProjectedOpportunityRunnerV2Error, match="account for every"):
        _run(batch=DateBoundedStatsBatchV2(responses=responses, errors={}))


def test_bare_stats_error_string_is_rejected() -> None:
    projection = _inputs()["projected_lineup_record"]
    player_id = _support(projection)[0]
    with pytest.raises(ProjectedOpportunityRunnerV2Error, match="failed-attempt receipt"):
        DateBoundedStatsBatchV2(responses={}, errors={player_id: "network error"})  # type: ignore[arg-type]


@pytest.mark.parametrize("mutation", ["url", "attempted_after_generation", "failed_after_generation"])
def test_failed_stats_attempt_identity_and_chronology_mutations_fail(mutation: str) -> None:
    projection = _inputs()["projected_lineup_record"]
    player_id = _support(projection)[0]
    batch = _batch(projection, error_player=player_id)
    original = batch.errors[player_id]
    urls = list(original.request_urls)
    attempted = original.attempted_at_utc
    failed = original.failed_at_utc
    if mutation == "url":
        urls[0] = urls[0].replace("statsapi.mlb.com", "example.invalid")
    elif mutation == "attempted_after_generation":
        attempted = "2026-09-17T15:59:30Z"
        failed = "2026-09-17T15:59:40Z"
    else:
        failed = "2026-09-17T15:59:30Z"
    forged = FailedStatsAttemptV2.capture(
        player_id=player_id,
        request_urls=urls,
        attempted_at_utc=attempted,
        failed_at_utc=failed,
        error_class=original.error_class,
    )
    errors = dict(batch.errors)
    errors[player_id] = forged
    with pytest.raises(ProjectedOpportunityRunnerV2Error, match="identity or chronology"):
        _run(batch=DateBoundedStatsBatchV2(responses=batch.responses, errors=errors))
