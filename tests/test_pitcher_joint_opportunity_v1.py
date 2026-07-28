from __future__ import annotations

import hashlib
import json
import shutil
from copy import deepcopy
from pathlib import Path

import pytest

from src.evaluation.forward_pitcher_context import (
    context_from_schedule,
    games_from_raw_schedule_response,
)
from src.evaluation.forward_pitcher_context_ledger import ForwardPitcherContextLedger
from src.evaluation.pitcher_joint_opportunity_v1 import (
    OUTS_TRANSITION_SUPPORT,
    OUTCOMES,
    RawPitcherWorkloadReceipt,
    WORKLOAD_FEATURES,
    PitcherJointOpportunityError,
    _joint_opportunity_pmf,
    apply_candidate,
    build_workload_features,
    compute_market_pmfs,
    expected_workload_request,
    file_sha256,
    load_evidence_authority_contract,
    load_protocol,
    replay_synthetic_t4_pitcher_receipt_for_tests,
    replay_workload_receipts_for_offline_validation,
    sha256_value,
    state_kernel,
    validate_model_artifact,
    validate_model_authorization,
    validate_evidence_authority_contract_bytes,
    validate_runtime_release_authorization,
    validate_workload_history,
    workload_parser_code_sha256,
    workload_source_schema_sha256,
)
from src.evaluation.shadow_capture_plan import (
    CaptureTarget,
    canonical_schedule_records,
    plan_from_schedule,
)


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "config/pitcher_joint_opportunity_v1_protocol.json"
EVIDENCE_AUTHORITY = ROOT / "config/pitcher_joint_opportunity_v1_evidence_authority.json"


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _target(date: str = "2026-07-28") -> CaptureTarget:
    return CaptureTarget(123, date, f"{date}T23:00:00Z", f"{date}T19:00:00Z", 4)


def _bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _schedule_raw(*, pitcher_id: int = 9001, team_id: int = 147) -> bytes:
    return _bytes({
        "dates": [{
            "date": "2026-07-28",
            "games": [{
                "gamePk": 123,
                "officialDate": "2026-07-28",
                "gameDate": "2026-07-28T23:00:00Z",
                "gameType": "R",
                "teams": {
                    "home": {"team": {"id": team_id, "name": "Home Team"}, "probablePitcher": {"id": pitcher_id}},
                    "away": {"team": {"id": 111, "name": "Away Team"}, "probablePitcher": {"id": 9002}},
                },
            }],
        }],
    })


def _receipt_tree(root: Path, *, pitcher_id: int = 9001, team_id: int = 147) -> Path:
    raw = _schedule_raw(pitcher_id=pitcher_id, team_id=team_id)
    source = json.loads(raw)
    runtime_sha = _sha("runtime")
    plan = plan_from_schedule(
        official_game_date="2026-07-28",
        entry_hours=4,
        policy_sha256=runtime_sha,
        schedule_snapshot=canonical_schedule_records(games_from_raw_schedule_response(source)),
    )
    target = plan.targets[0]
    plan_path = root / "plans" / "2026-07-28.plan.json"
    plan.write(plan_path)
    raw_sha = hashlib.sha256(raw).hexdigest()
    plan_raw = root / "plan-receipts" / "raw" / f"2026-07-28.{raw_sha}.json"
    plan_raw.parent.mkdir(parents=True, exist_ok=True)
    plan_raw.write_bytes(raw)
    receipt = {
        "schema_version": "aws-pitcher-receipt-plan-receipt-v1",
        "official_game_date": "2026-07-28",
        "plan_sha256": plan.plan_sha256,
        "runtime_sha256": runtime_sha,
        "source_name": "mlb_statsapi_schedule",
        "source_payload_sha256": raw_sha,
        "received_at_utc": "2026-07-28T18:00:00Z",
        "targets": 1,
        "research_only": True,
        "betting_authorized": False,
        "model_or_market_accessed": False,
    }
    receipt["receipt_sha256"] = sha256_value(receipt)
    receipt_path = root / "plan-receipts" / "plans" / f"2026-07-28.{plan.plan_sha256}.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_bytes(_bytes(receipt))
    context = context_from_schedule(
        target=target,
        plan=plan,
        captured_at_utc="2026-07-28T18:59:59Z",
        source_payload_sha256=raw_sha,
        schedule_games=games_from_raw_schedule_response(source),
    )
    ledger = ForwardPitcherContextLedger(
        root / "ledgers" / "2026-07-28" / plan.plan_sha256,
        plan,
        runtime_sha,
    )
    ledger.append_captured(target=target, context=context, raw_payload=raw)
    return root


def _live_feed(game_pk: int, game_date: str, *, role: str, hr_outs: int = 0, event_override: str | None = None) -> bytes:
    events = ["strikeout", "strikeout", "walk", "single", "double", "home_run", "field_out"]
    if event_override is not None:
        events[-1] = event_override
    outs_after = [1, 2, 2, 2, 2, 2 + hr_outs, 3]
    plays = [
        {
            "about": {"inning": 1, "halfInning": "top", "isComplete": True},
            "matchup": {"pitcher": {"id": 9001}},
            "result": {"eventType": event},
            "count": {"outs": outs},
        }
        for event, outs in zip(events, outs_after)
    ]
    return _bytes({
        "gamePk": game_pk,
        "gameData": {"datetime": {"officialDate": game_date}, "game": {"type": "R"}},
        "liveData": {
            "boxscore": {"teams": {
                "home": {
                    "team": {"id": 147},
                    "pitchers": [9001, 9999] if role == "start" else [9999, 9001],
                    "players": {"ID9001": {"person": {"id": 9001}, "stats": {"pitching": {"battersFaced": 7, "numberOfPitches": 31}}}},
                },
                "away": {"team": {"id": 111}, "pitchers": [9002], "players": {}},
            }},
            "plays": {"allPlays": plays},
        },
    })


def _raw_workload_receipts(*, hr_outs: int = 0, event_override: str | None = None) -> list[RawPitcherWorkloadReceipt]:
    return [
        RawPitcherWorkloadReceipt.capture(
            body=_live_feed(game_pk, game_date, role=role, hr_outs=hr_outs, event_override=event_override),
            request_url=expected_workload_request(game_pk=game_pk),
            request_sent_at_utc="2026-07-28T18:30:00Z",
            received_at_utc="2026-07-28T18:31:00Z",
        )
        for game_pk, game_date, role in (
            (101, "2026-07-20", "relief"),
            (102, "2026-07-24", "start"),
            (103, "2026-07-27", "start"),
        )
    ]


def _workload(receipts: list[RawPitcherWorkloadReceipt] | None = None) -> dict:
    target = _target()
    retained = receipts or _raw_workload_receipts()
    rows = replay_workload_receipts_for_offline_validation(
        retained,
        pitcher_id=9001,
        pitching_team_id=147,
        target_date=__import__("datetime").date(2026, 7, 28),
        target_horizon=__import__("datetime").datetime(2026, 7, 28, 19, tzinfo=__import__("datetime").timezone.utc),
    )
    lineage = {
        "raw_receipt_manifest_sha256": sha256_value([receipt.transport_receipt_sha256 for receipt in retained]),
        "source_schema_sha256": workload_source_schema_sha256(),
        "parser_code_sha256": workload_parser_code_sha256(),
        "feature_code_sha256": workload_parser_code_sha256(),
        "history_payload_sha256": sha256_value(rows),
        "protocol_sha256": file_sha256(PROTOCOL),
    }
    unsigned = {
        "schema_version": "pitcher-pit-workload-history-v1",
        "source_kind": "official_mlb_pitching_game_log_point_in_time",
        "target_id": target.target_id,
        "official_game_date": target.official_game_date,
        "mlb_game_pk": target.mlb_game_pk,
        "pitcher_id": 9001,
        "pitching_team_id": 147,
        "target_horizon_utc": target.entry_target_at_utc,
        "assembled_at_utc": "2026-07-28T18:59:59Z",
        "max_source_game_date": "2026-07-27",
        "prior_appearances": rows,
        "lineage": lineage,
    }
    return {**unsigned, "workload_sha256": sha256_value(unsigned)}


def _rehash_workload(row: dict) -> dict:
    row["lineage"]["history_payload_sha256"] = sha256_value(row["prior_appearances"])
    row["workload_sha256"] = sha256_value({key: value for key, value in row.items() if key != "workload_sha256"})
    return row


def _linear(intercept: float = 0.0, coefficient_overrides: dict[str, float] | None = None) -> dict:
    coefficients = {name: 0.0 for name in WORKLOAD_FEATURES}
    coefficients.update(coefficient_overrides or {})
    return {
        "intercept": intercept,
        "coefficients": coefficients,
        "centers": {name: 0.0 for name in WORKLOAD_FEATURES},
        "scales": {name: 1.0 for name in WORKLOAD_FEATURES},
    }


def _artifact(*, hazard_intercept: float = 0.0) -> dict:
    unsigned = {
        "schema_version": "pitcher-joint-opportunity-model-v1",
        "candidate_id": "pitcher_joint_opportunity_v1",
        "qualification_state": "SYNTHETIC_TEST_ONLY",
        "protocol_sha256": file_sha256(PROTOCOL),
        "training_start": "2023-03-30",
        "training_end": "2023-10-01",
        "rolling_window_appearances": 3,
        "feature_names": list(WORKLOAD_FEATURES),
        "hazard_model": _linear(hazard_intercept),
        "outcome_model": {outcome: _linear(0.6931471805599453 if outcome == "K" else 0.0) for outcome in OUTCOMES},
        "outs_transition_model": {
            outcome: {str(delta): _linear(0.0) for delta in range(4)}
            for outcome in OUTCOMES
        },
        "training_data_sha256": _sha("synthetic training"),
        "fit_code_sha256": _sha("synthetic fit"),
        "fit_tests_sha256": _sha("synthetic tests"),
        "qualification_report_sha256": _sha("synthetic qualification"),
        "random_seed": 7,
    }
    return {**unsigned, "artifact_sha256": sha256_value(unsigned)}


def _apply(tmp_path: Path, **overrides) -> dict:
    values = {
        "official_game_date": "2026-07-28",
        "mlb_game_pk": 123,
        "pitching_side": "home",
        "pitching_team_id": 147,
        "pitcher_id": 9001,
        "model_artifact": None,
        "model_authorization_receipt": None,
        "runtime_release_receipt": None,
        "protocol_path": PROTOCOL,
    }
    values.update(overrides)
    return apply_candidate(**values)


def test_protocol_is_predeclared_scaffold_with_separate_market_gates() -> None:
    protocol = load_protocol(PROTOCOL)
    assert protocol["status"] == "PREDECLARED_SCAFFOLD_ONLY_NO_FIT_NO_SELECTION"
    assert protocol["development_and_selection"]["protocol_freeze_required_before_2024_access"] is True
    assert protocol["development_and_selection"]["spent_2025_hr_confirmation_reusable"] is False
    assert protocol["shared_gate_rules"]["one_market_may_rescue_another"] is False
    assert protocol["shared_gate_rules"]["capture_lower_bound_must_exceed"] == 0.10


def test_complete_inputs_still_abstain_without_a_qualified_artifact(tmp_path: Path) -> None:
    result = _apply(tmp_path)
    assert result["terminal_state"] == "TERMINAL_ABSTENTION"
    assert result["reason_code"] == "EVIDENCE_AUTHORITY_NOT_EXTERNALLY_BOUND"
    assert result["market_probabilities"] is None
    assert result["production_probability_consumption_authorized"] is False
    assert result["betting_authorized"] is False


def test_candidate_api_cannot_accept_a_caller_selected_evidence_root(tmp_path: Path) -> None:
    with pytest.raises(TypeError):
        _apply(tmp_path, receipt_evidence_root=tmp_path / "missing")


def test_candidate_api_cannot_accept_caller_plan_or_context_assertions(tmp_path: Path) -> None:
    with pytest.raises(TypeError):
        _apply(tmp_path, expected_plan_sha256=_sha("caller"))
    with pytest.raises(TypeError):
        _apply(tmp_path / "second", pitcher_context_record={"forged": True})


@pytest.mark.parametrize("argument", ["workload_record", "raw_workload_receipts"])
def test_candidate_api_rejects_caller_workload_even_with_authority_bound_temp_mutation(
    tmp_path: Path, argument: str
) -> None:
    with pytest.raises(TypeError):
        _apply(tmp_path / "ordinary", **{argument: object()})

    config_root = tmp_path / "authority-bound-copy" / "config"
    config_root.mkdir(parents=True)
    copied_protocol = config_root / PROTOCOL.name
    shutil.copyfile(PROTOCOL, copied_protocol)
    authority = json.loads(EVIDENCE_AUTHORITY.read_text(encoding="utf-8"))
    authority["status"] = "EXTERNALLY_BOUND_APPROVED_ARCHIVE_ERA"
    authority["authorized_evidence_authority_receipt_sha256"] = "a" * 64
    for field in authority["required_binding_fields"]:
        authority["binding"][field] = 123 if field in {
            "mlb_game_pk", "pitching_team_id", "probable_pitcher_mlb_id"
        } else "externally-bound-test-value"
    (config_root / EVIDENCE_AUTHORITY.name).write_bytes(_bytes(authority))
    with pytest.raises(TypeError):
        _apply(
            tmp_path / "authority-bound",
            protocol_path=copied_protocol,
            **{argument: object()},
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "raw",
        "ledger_raw",
        "ledger_context",
        "late_plan_source",
    ],
)
def test_synthetic_replay_rejects_receipt_integrity_and_chronology_mutations(
    tmp_path: Path, mutation: str
) -> None:
    if mutation == "raw":
        evidence = _receipt_tree(tmp_path / "mutated")
        path = next((evidence / "plan-receipts" / "raw").glob("*.json"))
        path.write_bytes(path.read_bytes() + b" ")
    elif mutation == "ledger_raw":
        evidence = _receipt_tree(tmp_path / "mutated")
        path = next((evidence / "ledgers").glob("**/raw/*.json"))
        path.write_bytes(path.read_bytes() + b" ")
    elif mutation == "ledger_context":
        evidence = _receipt_tree(tmp_path / "mutated")
        path = next((evidence / "ledgers").glob("**/contexts/*.json"))
        context = json.loads(path.read_text())
        context["home_probable_pitcher"]["player_id"] = 9999
        path.write_bytes(_bytes(context))
    elif mutation == "late_plan_source":
        evidence = _receipt_tree(tmp_path / "mutated")
        path = next((evidence / "plan-receipts" / "plans").glob("*.json"))
        receipt = json.loads(path.read_text())
        receipt["received_at_utc"] = "2026-07-28T19:00:00Z"
        unsigned = dict(receipt); unsigned.pop("receipt_sha256")
        receipt["receipt_sha256"] = sha256_value(unsigned)
        path.write_bytes(_bytes(receipt))
    with pytest.raises(PitcherJointOpportunityError):
        replay_synthetic_t4_pitcher_receipt_for_tests(
            evidence_root=evidence,
            official_game_date="2026-07-28",
            mlb_game_pk=123,
        )


def test_verified_ledger_raw_replay_derives_v2_team_identity(tmp_path: Path) -> None:
    evidence = _receipt_tree(tmp_path / "receipts")
    verified = replay_synthetic_t4_pitcher_receipt_for_tests(
        evidence_root=evidence, official_game_date="2026-07-28", mlb_game_pk=123
    )
    assert verified.context.home_team_id == 147
    assert verified.context.home_probable_pitcher.player_id == 9001
    assert verified.context.plan_sha256 == verified.plan.plan_sha256


def test_exact_unbound_authority_contract_is_repository_derived_and_fail_closed() -> None:
    protocol = load_protocol(PROTOCOL)
    authority = load_evidence_authority_contract(
        protocol_path=PROTOCOL, protocol=protocol
    )
    assert authority["status"] == "UNBOUND_NO_APPROVED_ARCHIVE_ERA"
    assert authority["authorized_evidence_authority_receipt_sha256"] is None
    assert all(value is None for value in authority["binding"].values())


def test_alternate_rehashed_and_copied_trees_cannot_reach_probability_application(
    tmp_path: Path,
) -> None:
    original = _receipt_tree(tmp_path / "original")
    copied = tmp_path / "copied"
    shutil.copytree(original, copied)
    alternate = _receipt_tree(tmp_path / "alternate", pitcher_id=9999, team_id=999)
    for evidence in (original, copied, alternate):
        # The byte-consistent trees remain inspectable only at the synthetic
        # test boundary; the probability API has no evidence-root parameter.
        replay_synthetic_t4_pitcher_receipt_for_tests(
            evidence_root=evidence,
            official_game_date="2026-07-28",
            mlb_game_pk=123,
        )
        with pytest.raises(TypeError):
            _apply(tmp_path / evidence.name, receipt_evidence_root=evidence)


def test_jointly_forged_tree_cannot_override_target_team_or_pitcher_authority(
    tmp_path: Path,
) -> None:
    forged = _receipt_tree(tmp_path / "joint-forgery", pitcher_id=7777, team_id=888)
    synthetic = replay_synthetic_t4_pitcher_receipt_for_tests(
        evidence_root=forged,
        official_game_date="2026-07-28",
        mlb_game_pk=123,
    )
    assert synthetic.context.home_team_id == 888
    assert synthetic.context.home_probable_pitcher.player_id == 7777
    result = _apply(tmp_path / "application")
    assert result["reason_code"] == "EVIDENCE_AUTHORITY_NOT_EXTERNALLY_BOUND"
    assert result["market_probabilities"] is None


def test_swapped_target_manifest_is_rejected_by_synthetic_ledger_replay(
    tmp_path: Path,
) -> None:
    first = _receipt_tree(tmp_path / "first", pitcher_id=9001)
    second = _receipt_tree(tmp_path / "second", pitcher_id=9009)
    first_index = next((first / "ledgers").glob("**/terminal_index.json"))
    second_index = next((second / "ledgers").glob("**/terminal_index.json"))
    first_index.write_bytes(second_index.read_bytes())
    with pytest.raises(PitcherJointOpportunityError):
        replay_synthetic_t4_pitcher_receipt_for_tests(
            evidence_root=first,
            official_game_date="2026-07-28",
            mlb_game_pk=123,
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("archive_era_id", "forged-era"),
        ("service_owned_root", "/tmp/caller-root"),
        ("archive_relative_path", "copied/archive"),
        ("collector_release_sha256", "a" * 64),
        ("collector_runtime_sha256", "b" * 64),
    ],
)
def test_archive_era_root_and_collector_identity_drift_break_fixed_authority_digest(
    field: str, value: object
) -> None:
    authority = json.loads(EVIDENCE_AUTHORITY.read_text(encoding="utf-8"))
    authority["binding"][field] = value
    with pytest.raises(PitcherJointOpportunityError, match="externally fixed digest"):
        validate_evidence_authority_contract_bytes(_bytes(authority))


def test_valid_model_and_runtime_receipts_cannot_bypass_missing_evidence_authority(
    tmp_path: Path,
) -> None:
    protocol_sha = file_sha256(PROTOCOL)
    artifact = _artifact()
    artifact["qualification_state"] = "FUTURE_CONFIRMATION_LOCKED_RESEARCH_ONLY"
    artifact["artifact_sha256"] = sha256_value(
        {key: value for key, value in artifact.items() if key != "artifact_sha256"}
    )
    model_unsigned = {
        "schema_version": "pitcher-joint-opportunity-model-authorization-v1",
        "candidate_id": "pitcher_joint_opportunity_v1",
        "qualification_state": artifact["qualification_state"],
        "protocol_sha256": protocol_sha,
        "artifact_sha256": artifact["artifact_sha256"],
        "training_data_sha256": artifact["training_data_sha256"],
        "fit_code_sha256": artifact["fit_code_sha256"],
        "fit_tests_sha256": artifact["fit_tests_sha256"],
        "qualification_report_sha256": artifact["qualification_report_sha256"],
        "research_only": True,
        "betting_authorized": False,
    }
    model_receipt = {
        **model_unsigned,
        "authorization_sha256": sha256_value(model_unsigned),
    }
    validate_model_authorization(
        model_receipt,
        artifact=artifact,
        protocol_sha256=protocol_sha,
        externally_fixed_receipt_sha256=model_receipt["authorization_sha256"],
    )
    runtime_unsigned = {
        "schema_version": "pitcher-joint-opportunity-runtime-release-v1",
        "candidate_id": "pitcher_joint_opportunity_v1",
        "protocol_sha256": protocol_sha,
        "source_release_sha256": _sha("source release"),
        "test_evidence_sha256": _sha("test evidence"),
        "research_only": True,
        "betting_authorized": False,
    }
    runtime_receipt = {**runtime_unsigned, "release_sha256": sha256_value(runtime_unsigned)}
    validate_runtime_release_authorization(
        runtime_receipt,
        protocol_sha256=protocol_sha,
        externally_fixed_receipt_sha256=runtime_receipt["release_sha256"],
    )
    result = _apply(
        tmp_path,
        model_artifact=artifact,
        model_authorization_receipt=model_receipt,
        runtime_release_receipt=runtime_receipt,
    )
    assert result["reason_code"] == "EVIDENCE_AUTHORITY_NOT_EXTERNALLY_BOUND"


def test_direct_typed_synthetic_receipt_cannot_reach_probability_consumption(
    tmp_path: Path,
) -> None:
    verified = replay_synthetic_t4_pitcher_receipt_for_tests(
        evidence_root=_receipt_tree(tmp_path / "synthetic"),
        official_game_date="2026-07-28",
        mlb_game_pk=123,
    )
    with pytest.raises(TypeError):
        _apply(tmp_path / "application", verified_t4_pitcher_receipt=verified)


def test_forged_workload_lineage_is_rejected_and_cannot_supply_evidence_authority(
    tmp_path: Path,
) -> None:
    receipts = _raw_workload_receipts()
    forged = deepcopy(_workload(receipts))
    forged["lineage"]["raw_receipt_manifest_sha256"] = _sha("jointly forged manifest")
    forged["workload_sha256"] = sha256_value(
        {key: value for key, value in forged.items() if key != "workload_sha256"}
    )
    with pytest.raises(PitcherJointOpportunityError):
        validate_workload_history(
            forged,
            target=_target(),
            pitcher_id=9001,
            pitching_team_id=147,
            protocol_sha256=file_sha256(PROTOCOL),
            raw_receipts=receipts,
        )
    with pytest.raises(TypeError):
        _apply(tmp_path, workload_record=forged)
    with pytest.raises(TypeError):
        _apply(tmp_path, raw_workload_receipts=receipts)


def test_valid_workload_derives_rest_role_and_rolling_distributions_without_defaults() -> None:
    target = _target()
    receipts = _raw_workload_receipts()
    row = validate_workload_history(
        _workload(receipts), target=target, pitcher_id=9001, pitching_team_id=147,
        protocol_sha256=file_sha256(PROTOCOL),
        raw_receipts=receipts,
    )
    features = build_workload_features(row, rolling_window_appearances=3)
    assert features["rest_days"] == 1.0
    assert features["prior_start_count"] == 2.0
    assert features["prior_relief_count"] == 1.0
    assert features["rolling_mean_batters_faced"] == 7.0
    assert features["rolling_k_per_bf"] == pytest.approx(2 / 7)


@pytest.mark.parametrize(
    "mutation",
    ["same_day", "duplicate", "bf_flag", "pitch_flag", "count_denominator", "unclassified", "history_hash", "late"],
)
def test_workload_source_truth_chronology_and_lineage_mutations_abstain(
    tmp_path: Path, mutation: str
) -> None:
    receipts = _raw_workload_receipts()
    row = deepcopy(_workload(receipts))
    if mutation == "same_day":
        row["prior_appearances"][-1]["official_game_date"] = "2026-07-28"
        row["max_source_game_date"] = "2026-07-28"
        _rehash_workload(row)
    elif mutation == "duplicate":
        row["prior_appearances"][-1]["mlb_game_pk"] = 102
        _rehash_workload(row)
    elif mutation == "bf_flag":
        row["prior_appearances"][-1]["batters_faced_source_truth_valid"] = False
        _rehash_workload(row)
    elif mutation == "pitch_flag":
        row["prior_appearances"][-1]["pitch_count_source_truth_valid"] = False
        _rehash_workload(row)
    elif mutation == "count_denominator":
        row["prior_appearances"][-1]["strikeouts"] += 1
        _rehash_workload(row)
    elif mutation == "unclassified":
        row["prior_appearances"][-1]["other_out_events"] -= 1
        row["prior_appearances"][-1]["unclassified_batters_faced"] = 1
        _rehash_workload(row)
    elif mutation == "history_hash":
        row["prior_appearances"][-1]["pitch_count"] += 1
        row["workload_sha256"] = sha256_value({key: value for key, value in row.items() if key != "workload_sha256"})
    else:
        row["assembled_at_utc"] = "2026-07-28T19:00:01Z"
        _rehash_workload(row)
    with pytest.raises(PitcherJointOpportunityError):
        validate_workload_history(
            row,
            target=_target(),
            pitcher_id=9001,
            pitching_team_id=147,
            protocol_sha256=file_sha256(PROTOCOL),
            raw_receipts=receipts,
        )


@pytest.mark.parametrize("mutation", ["body", "receipt", "parser", "schema", "row"])
def test_workload_raw_parser_and_row_binding_mutations_fail(
    tmp_path: Path, mutation: str
) -> None:
    receipts = _raw_workload_receipts()
    workload = _workload(receipts)
    if mutation == "body":
        original = receipts[-1]
        body = json.loads(original.body)
        body["liveData"]["boxscore"]["teams"]["home"]["players"]["ID9001"]["stats"]["pitching"]["numberOfPitches"] += 1
        receipts[-1] = RawPitcherWorkloadReceipt.capture(
            body=_bytes(body), request_url=original.request_url,
            request_sent_at_utc=original.request_sent_at_utc,
            received_at_utc=original.received_at_utc,
        )
    elif mutation == "receipt":
        receipts = receipts[:-1]
    elif mutation == "parser":
        workload["lineage"]["parser_code_sha256"] = _sha("forged parser")
        _rehash_workload(workload)
    elif mutation == "schema":
        workload["lineage"]["source_schema_sha256"] = _sha("forged schema")
        _rehash_workload(workload)
    else:
        workload["prior_appearances"][-1]["pitch_count"] += 1
        _rehash_workload(workload)
    with pytest.raises(PitcherJointOpportunityError):
        validate_workload_history(
            workload,
            target=_target(),
            pitcher_id=9001,
            pitching_team_id=147,
            protocol_sha256=file_sha256(PROTOCOL),
            raw_receipts=receipts,
        )


def test_unknown_live_feed_event_remains_unclassified_and_blocks(tmp_path: Path) -> None:
    receipts = _raw_workload_receipts(event_override="catcher_interference")
    rows = replay_workload_receipts_for_offline_validation(
        receipts,
        pitcher_id=9001,
        pitching_team_id=147,
        target_date=__import__("datetime").date(2026, 7, 28),
        target_horizon=__import__("datetime").datetime(2026, 7, 28, 19, tzinfo=__import__("datetime").timezone.utc),
    )
    assert rows[-1]["unclassified_batters_faced"] == 1
    workload = _workload(receipts)
    with pytest.raises(PitcherJointOpportunityError, match="unclassified"):
        validate_workload_history(
            workload,
            target=_target(),
            pitcher_id=9001,
            pitching_team_id=147,
            protocol_sha256=file_sha256(PROTOCOL),
            raw_receipts=receipts,
        )


def test_home_run_with_positive_outs_is_rejected_at_raw_event_boundary() -> None:
    receipts = _raw_workload_receipts(hr_outs=1)
    with pytest.raises(PitcherJointOpportunityError, match="HR.*structurally impossible"):
        replay_workload_receipts_for_offline_validation(
            receipts,
            pitcher_id=9001,
            pitching_team_id=147,
            target_date=__import__("datetime").date(2026, 7, 28),
            target_horizon=__import__("datetime").datetime(2026, 7, 28, 19, tzinfo=__import__("datetime").timezone.utc),
        )


def test_short_history_does_not_silently_shorten_the_fitted_rolling_window() -> None:
    row = _workload()
    with pytest.raises(PitcherJointOpportunityError, match="no shorter fallback"):
        build_workload_features(row, rolling_window_appearances=4)


def test_model_artifact_requires_2023_only_fit_exact_features_and_hash() -> None:
    artifact = _artifact()
    validate_model_artifact(artifact, protocol_sha256=file_sha256(PROTOCOL), allow_synthetic=True)
    for mutation, match in (
        ("training_end", "2023"),
        ("feature_names", "feature order"),
        ("artifact_sha256", "content hash"),
    ):
        changed = deepcopy(artifact)
        if mutation == "training_end":
            changed[mutation] = "2024-04-01"
        elif mutation == "feature_names":
            changed[mutation] = changed[mutation][:-1]
        else:
            changed[mutation] = _sha("wrong")
        with pytest.raises(PitcherJointOpportunityError, match=match):
            validate_model_artifact(changed, protocol_sha256=file_sha256(PROTOCOL), allow_synthetic=True)


def test_one_softmax_supplies_all_seven_pa_outcomes_and_no_market_specific_rates() -> None:
    artifact = validate_model_artifact(_artifact(), protocol_sha256=file_sha256(PROTOCOL), allow_synthetic=True)
    features = build_workload_features(_workload(), rolling_window_appearances=3)
    hazard, outcomes, transitions = state_kernel(artifact, features, batters_faced_before=0, outs_before=0)
    assert hazard == pytest.approx(0.5)
    assert tuple(outcomes) == OUTCOMES
    assert sum(outcomes.values()) == pytest.approx(1.0)
    assert outcomes["K"] == pytest.approx(0.25)
    assert all(sum(value.values()) == pytest.approx(1.0) for value in transitions.values())
    for outcome in OUTCOMES:
        assert tuple(transitions[outcome]) == tuple(
            delta for delta in OUTS_TRANSITION_SUPPORT[outcome] if delta <= 3
        )
    assert tuple(transitions["HR"]) == (0,)


def test_every_market_pmf_comes_from_the_same_kernel_and_conserves_probability() -> None:
    protocol = load_protocol(PROTOCOL)
    # Keep this numerical proof fast while retaining a negligible geometric tail.
    protocol = deepcopy(protocol)
    protocol["numerical_contract"]["maximum_batters_faced_iterations"] = 12
    protocol["numerical_contract"]["active_mass_tolerance"] = 1e-12
    artifact = validate_model_artifact(
        _artifact(hazard_intercept=4.59511985013459),
        protocol_sha256=file_sha256(PROTOCOL),
        allow_synthetic=True,
    )
    results = compute_market_pmfs(artifact=artifact, workload_record=_workload(), protocol=protocol)
    assert set(results) == {"joint_opportunity", "markets"}
    assert set(results["markets"]) == {
        "pitcher_strikeouts", "pitcher_walks_hbp", "pitcher_hits_allowed",
        "pitcher_home_runs_allowed", "pitcher_total_bases_allowed",
    }
    for result in results["markets"].values():
        assert sum(result["pmf"].values()) + result["unresolved_numerical_tail"] == pytest.approx(1.0, abs=1e-12)
        assert result["mean"] >= 0.0
    opportunity = results["joint_opportunity"]
    assert sum(opportunity["pmf"].values()) + opportunity["unresolved_numerical_tail"] == pytest.approx(1.0, abs=1e-12)
    assert opportunity["expected_innings_pitched"] == pytest.approx(opportunity["expected_outs_recorded"] / 3.0)


def test_unresolved_probability_tail_fails_instead_of_clipping_or_renormalizing() -> None:
    protocol = deepcopy(load_protocol(PROTOCOL))
    protocol["numerical_contract"]["maximum_batters_faced_iterations"] = 1
    artifact = validate_model_artifact(
        _artifact(hazard_intercept=0.0),
        protocol_sha256=file_sha256(PROTOCOL),
        allow_synthetic=True,
    )
    with pytest.raises(PitcherJointOpportunityError, match="unresolved active probability mass"):
        compute_market_pmfs(
            artifact=artifact,
            workload_record=_workload(),
            protocol=protocol,
        )


def test_maximum_iterations_processes_exactly_that_many_states_not_one_more() -> None:
    artifact = validate_model_artifact(
        _artifact(), protocol_sha256=file_sha256(PROTOCOL), allow_synthetic=True
    )
    base = build_workload_features(_workload(), rolling_window_appearances=3)
    pmf, unresolved = _joint_opportunity_pmf(
        artifact=artifact,
        base_features=base,
        maximum_iterations=1,
        active_mass_tolerance=1.0,
        probability_sum_tolerance=1e-12,
    )
    assert set(pmf) == {(0, 0)}
    assert unresolved == pytest.approx(0.5)


def test_synthetic_artifact_cannot_self_promote_through_application(tmp_path: Path) -> None:
    forged = _artifact()
    forged["qualification_state"] = "FUTURE_CONFIRMATION_LOCKED_RESEARCH_ONLY"
    forged["artifact_sha256"] = sha256_value(
        {key: value for key, value in forged.items() if key != "artifact_sha256"}
    )
    result = _apply(tmp_path, model_artifact=forged)
    assert result["reason_code"] == "EVIDENCE_AUTHORITY_NOT_EXTERNALLY_BOUND"
    assert result["market_probabilities"] is None


@pytest.mark.parametrize(
    "field",
    [
        "training_data_sha256",
        "fit_code_sha256",
        "fit_tests_sha256",
        "protocol_sha256",
        "qualification_report_sha256",
        "artifact_sha256",
        "qualification_state",
    ],
)
def test_independent_artifact_authorization_binds_every_qualification_input(
    field: str,
) -> None:
    artifact = _artifact()
    artifact["qualification_state"] = "FUTURE_CONFIRMATION_LOCKED_RESEARCH_ONLY"
    artifact["artifact_sha256"] = sha256_value(
        {key: value for key, value in artifact.items() if key != "artifact_sha256"}
    )
    unsigned = {
        "schema_version": "pitcher-joint-opportunity-model-authorization-v1",
        "candidate_id": "pitcher_joint_opportunity_v1",
        "qualification_state": artifact["qualification_state"],
        "protocol_sha256": file_sha256(PROTOCOL),
        "artifact_sha256": artifact["artifact_sha256"],
        "training_data_sha256": artifact["training_data_sha256"],
        "fit_code_sha256": artifact["fit_code_sha256"],
        "fit_tests_sha256": artifact["fit_tests_sha256"],
        "qualification_report_sha256": artifact["qualification_report_sha256"],
        "research_only": True,
        "betting_authorized": False,
    }
    receipt = {**unsigned, "authorization_sha256": sha256_value(unsigned)}
    validate_model_authorization(
        receipt,
        artifact=artifact,
        protocol_sha256=file_sha256(PROTOCOL),
        externally_fixed_receipt_sha256=receipt["authorization_sha256"],
    )
    changed = deepcopy(receipt)
    changed[field] = (
        "FORGED"
        if field == "qualification_state"
        else "f" * 64
    )
    changed_unsigned = dict(changed); changed_unsigned.pop("authorization_sha256")
    changed["authorization_sha256"] = sha256_value(changed_unsigned)
    with pytest.raises(PitcherJointOpportunityError):
        validate_model_authorization(
            changed,
            artifact=artifact,
            protocol_sha256=file_sha256(PROTOCOL),
            externally_fixed_receipt_sha256=changed["authorization_sha256"],
        )


def test_protocol_mutation_cannot_activate_production_or_remove_an_outcome(tmp_path: Path) -> None:
    protocol = load_protocol(PROTOCOL)
    protocol["research_boundary"]["betting_authorized"] = True
    path = tmp_path / "protocol.json"
    path.write_text(__import__("json").dumps(protocol), encoding="utf-8")
    with pytest.raises(PitcherJointOpportunityError, match="externally fixed digest"):
        load_protocol(path)
    protocol = load_protocol(PROTOCOL)
    protocol["learned_model"]["conditional_pa_outcomes"].remove("HR")
    path.write_text(json.dumps(protocol), encoding="utf-8")
    with pytest.raises(PitcherJointOpportunityError, match="externally fixed digest"):
        load_protocol(path)


@pytest.mark.parametrize(
    "path,value",
    [
        (("shared_gate_rules", "capture_lower_bound_must_exceed"), 0.09),
        (("shared_gate_rules", "each_market_adjudicated_separately"), False),
        (("shared_gate_rules", "one_market_may_rescue_another"), True),
        (("market_gates", "pitcher_strikeouts", "baselines"), []),
        (("candidate_application", "caller_supplied_plan_or_context_assertions_allowed"), True),
        (("candidate_application", "evidence_authority_contract_path"), "caller/root.json"),
        (("candidate_application", "evidence_authority_contract_sha256"), "c" * 64),
        (("candidate_application", "evidence_authority_required_for_probability"), False),
        (("candidate_application", "authorized_runtime_release_sha256"), "a" * 64),
        (("learned_model", "authorized_artifact_release_sha256"), "b" * 64),
        (("learned_model", "outs_transition_support", "HR"), [0, 1]),
    ],
)
def test_every_runtime_relevant_nested_protocol_mutation_fails_exact_digest(
    tmp_path: Path, path: tuple[str, ...], value: object
) -> None:
    protocol = deepcopy(load_protocol(PROTOCOL))
    cursor = protocol
    for key in path[:-1]:
        cursor = cursor[key]
    cursor[path[-1]] = value
    source = tmp_path / "protocol.json"
    source.write_text(json.dumps(protocol), encoding="utf-8")
    with pytest.raises(PitcherJointOpportunityError, match="externally fixed digest"):
        load_protocol(source)
