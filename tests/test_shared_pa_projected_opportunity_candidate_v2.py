"""Integrity and mutation tests for the replayable v2 opportunity candidate."""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import shutil
from datetime import date
from pathlib import Path
from urllib.parse import urlencode

import pytest

from src.evaluation.projected_lineup_contract import load_contract, sha256_value
from src.evaluation.projected_lineup_empirical_joint import build_empirical_joint_projection
from src.evaluation.projected_lineup_history import build_feature_store
from src.evaluation.projected_lineup_official_roster import (
    RawOfficialRosterResponse,
    parse_active_roster_receipt,
)
from src.evaluation.prospective_batter_opportunity import (
    OPPORTUNITY_FIELDS,
    SCHEDULE_FIELDS,
    RawOpportunityResponse,
    build_history_capture_plan,
    build_history_coverage,
    build_pregame_opportunity_snapshot,
    parse_opportunity_history,
    parse_prior_schedule_denominator,
)
from src.evaluation.shadow_capture_plan import CaptureTarget, plan_from_schedule
from src.evaluation.shared_pa_forward_collector import RawPregameResponse
from src.evaluation.shared_pa_forward_evidence import load_forward_contract
from src.evaluation.shared_pa_projected_opportunity_candidate_v2 import (
    ProjectedOpportunityCandidateV2Error,
    ProjectedOpportunityProtocolV2,
    V2_RUNTIME_SOURCE_CLOSURE,
    build_projected_opportunity_candidate_v2,
    classify_evidence_date,
    load_protocol_v2,
    replay_and_validate_candidate_record_v2,
    validate_inherited_evaluation_contract_v2,
)
from src.evaluation.shared_pa_projected_opportunity_evidence_v2 import (
    ProjectedOpportunityEvidenceV2Error,
    RawDateBoundedStatsResponse,
    build_evidence_envelope_v2,
    expected_stats_requests,
)
from src.evaluation.shared_pa_projected_opportunity_release_v2 import (
    PUBLISHED_PROTOCOL_STATUS,
    ProjectedOpportunityReleaseV2Error,
    SOURCE_MANIFEST_RELATIVE,
    load_source_manifest_v2,
    validate_release_claim_v2,
    validate_source_manifest_payload_v2,
)


ROOT = Path(__file__).resolve().parents[1]
GAME_DATE = "2026-09-17"
START = "2026-09-17T20:00:00Z"
HORIZON = "2026-09-17T16:00:00Z"
TEAM_ID = 111
PLAYER_IDS = list(range(201, 212))


def _plan():
    return plan_from_schedule(
        official_game_date=GAME_DATE,
        entry_hours=4,
        policy_sha256="a" * 64,
        schedule_snapshot=[{
            "gamePk": 123456,
            "officialDate": GAME_DATE,
            "gameDate": START,
            "teams": {
                "home": {"team": {"name": "Home"}},
                "away": {"team": {"name": "Away"}},
            },
        }],
    )


def _schedule() -> RawPregameResponse:
    body = {
        "dates": [{"games": [{
            "gamePk": 123456,
            "officialDate": GAME_DATE,
            "gameDate": START,
            "teams": {
                "home": {"team": {"id": TEAM_ID, "name": "Home"}},
                "away": {"team": {"id": 112, "name": "Away"}},
            },
        }]}]
    }
    return RawPregameResponse(json.dumps(body, sort_keys=True).encode(), "2026-09-17T15:45:00Z")


def _roster_raw(team_id: int = TEAM_ID) -> bytes:
    body = {
        "copyright": "synthetic",
        "link": "/synthetic",
        "rosterType": "active",
        "teamId": team_id,
        "roster": [{
            "jerseyNumber": str(index),
            "parentTeamId": team_id,
            "person": {"fullName": f"Player {player}", "id": player, "link": f"/p/{player}"},
            "position": {"abbreviation": "OF", "code": "7", "name": "Outfielder", "type": "Outfielder"},
            "status": {"code": "A", "description": "Active"},
        } for index, player in enumerate(PLAYER_IDS, start=1)],
    }
    return json.dumps(body, sort_keys=True).encode()


def _roster(
    raw: bytes | None = None,
    *,
    received_at_utc: str = "2026-09-17T15:50:00Z",
) -> tuple[bytes, dict]:
    payload = raw or _roster_raw()
    receipt = parse_active_roster_receipt(
        response=RawOfficialRosterResponse(payload, received_at_utc),
        requested_date=GAME_DATE,
        team_id=TEAM_ID,
        target_horizon_utc=HORIZON,
    )
    return payload, receipt


def _opportunity_url(game_pk: int) -> str:
    return f"https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live?" + urlencode(
        {"fields": OPPORTUNITY_FIELDS}
    )


def _history_plan(game_pk: int, game_date: str) -> dict:
    return build_history_capture_plan(
        created_at_utc=f"{game_date}T12:00:00Z",
        mlb_game_pk=game_pk,
        official_game_date=game_date,
        official_start_time_utc=f"{game_date}T18:00:00Z",
        capture_deadline_utc=f"{game_date}T23:59:59Z",
        side="home",
        team_id=TEAM_ID,
        source_t4_plan_sha256="a" * 64,
        roster_side_target_id="b" * 64,
        active_roster_receipt_sha256="c" * 64,
    )


def _opportunity_raw(game_pk: int, game_date: str, first_player: int) -> bytes:
    home = {
        f"ID{player_id}": {
            "person": {"id": player_id},
            "battingOrder": f"{slot}00",
            "stats": {"batting": {"plateAppearances": 3 + slot % 3}},
        }
        for slot, player_id in enumerate(range(first_player, first_player + 9), start=1)
    }
    away = {
        f"ID{300 + slot}": {
            "person": {"id": 300 + slot},
            "battingOrder": f"{slot}00",
            "stats": {"batting": {"plateAppearances": 4}},
        }
        for slot in range(1, 10)
    }
    return json.dumps({
        "gamePk": game_pk,
        "gameData": {
            "datetime": {"officialDate": game_date},
            "status": {"abstractGameState": "Final", "codedGameState": "F"},
        },
        "liveData": {"boxscore": {"teams": {
            "away": {"team": {"id": 112}, "players": away},
            "home": {"team": {"id": TEAM_ID}, "players": home},
        }}},
    }, sort_keys=True).encode()


def _history_record(game_pk: int, game_date: str, first_player: int) -> tuple[dict, bytes]:
    raw = _opportunity_raw(game_pk, game_date, first_player)
    return (
        parse_opportunity_history(
            response=RawOpportunityResponse(
                raw, f"{game_date}T23:59:00Z", _opportunity_url(game_pk)
            ),
            expected_game_pk=game_pk,
            expected_official_date=game_date,
            side="home",
            expected_team_id=TEAM_ID,
            capture_plan=_history_plan(game_pk, game_date),
        ),
        raw,
    )


def _history_schedule_url() -> str:
    return "https://statsapi.mlb.com/api/v1/schedule?" + urlencode({
        "sportId": "1",
        "teamId": str(TEAM_ID),
        "startDate": "2026-09-14",
        "endDate": "2026-09-16",
        "fields": SCHEDULE_FIELDS,
    })


def _history_schedule_raw() -> bytes:
    dates = []
    for game_pk, game_date in (
        (1001, "2026-09-14"),
        (1002, "2026-09-15"),
        (1003, "2026-09-16"),
    ):
        dates.append({
            "date": game_date,
            "games": [{
                "gamePk": game_pk,
                "officialDate": game_date,
                "status": {"abstractGameState": "Final", "codedGameState": "F"},
                "teams": {
                    "away": {"team": {"id": 112}},
                    "home": {"team": {"id": TEAM_ID}},
                },
            }],
        })
    return json.dumps({"dates": dates}, sort_keys=True).encode()


def _history_materials(
    roster_raw: bytes,
    receipt: dict,
    *,
    schedule_received_at_utc: str = "2026-09-17T15:59:00Z",
) -> dict:
    pairs = [
        _history_record(1001, "2026-09-14", 201),
        _history_record(1002, "2026-09-15", 201),
        _history_record(1003, "2026-09-16", 202),
    ]
    records = [record for record, _ in pairs]
    raw_by_sha = {record["source_payload_sha256"]: raw for record, raw in pairs}
    schedule_raw = _history_schedule_raw()
    schedule_receipt = parse_prior_schedule_denominator(
        response=RawOpportunityResponse(
            schedule_raw, schedule_received_at_utc, _history_schedule_url()
        ),
        requested_start_date="2026-09-14",
        requested_end_date="2026-09-16",
        target_official_game_date=GAME_DATE,
        target_horizon_utc=HORIZON,
        team_id=TEAM_ID,
    )
    coverage = build_history_coverage(
        collection_epoch_date="2026-09-14",
        target_official_game_date=GAME_DATE,
        team_id=TEAM_ID,
        schedule_receipts=[schedule_receipt],
        captured_prior_game_pks=[1001, 1002, 1003],
        terminal_missing_prior_game_pks=[],
    )
    schedule_raws = {schedule_receipt["source_payload_sha256"]: schedule_raw}
    snapshot = build_pregame_opportunity_snapshot(
        official_game_date=GAME_DATE,
        mlb_game_pk=123456,
        side="home",
        team_id=TEAM_ID,
        target_horizon_utc=HORIZON,
        assembled_at_utc="2026-09-17T15:59:00Z",
        active_roster_receipt=receipt,
        active_roster_raw=roster_raw,
        history_records=records,
        history_raw_by_sha256=raw_by_sha,
        history_coverage=coverage,
        schedule_raw_by_sha256=schedule_raws,
    )
    completed = [
        {
            "official_game_date": record["official_game_date"],
            "mlb_game_pk": record["mlb_game_pk"],
            "team_id": TEAM_ID,
            "player_id": player["player_id"],
            "slot": player["lineup_slot"],
        }
        for record in records
        for player in record["players"]
        if player["is_starter"]
    ]
    return {
        "history_records": records,
        "history_raw_by_sha256": raw_by_sha,
        "history_coverage": coverage,
        "history_schedule_raw_by_sha256": schedule_raws,
        "opportunity_snapshot": snapshot,
        "completed_lineups": completed,
    }


def _projection(
    receipt: dict,
    history: list[dict],
    *,
    projection_receipt_utc: str = "2026-09-17T15:55:00Z",
) -> tuple[dict, dict]:
    store = build_feature_store(
        official_game_date=GAME_DATE,
        team_id=TEAM_ID,
        active_roster_receipt=receipt,
        completed_lineups=history,
    )
    record = build_empirical_joint_projection(
        official_game_date=GAME_DATE,
        mlb_game_pk=123456,
        team_id=TEAM_ID,
        target_horizon_utc=HORIZON,
        projection_receipt_utc=projection_receipt_utc,
        active_roster_receipt=receipt,
        historical_feature_store=store,
        completed_lineups=history,
        contract=load_contract(ROOT / "config/projected_lineup_contract_v1.json"),
    )
    return store, record


def _stats(
    player_id: int = 201, *, request_urls: list[str] | None = None,
    split_date: str = "2026-09-16", received_at_utc: str = "2026-09-17T15:58:00Z",
) -> list[RawDateBoundedStatsResponse]:
    stat = {
        "plateAppearances": 100,
        "atBats": 88,
        "hits": 28,
        "doubles": 5,
        "triples": 1,
        "homeRuns": 6,
        "baseOnBalls": 10,
        "strikeOuts": 20,
    }
    dated_body = {"people": [{
        "id": player_id,
        "stats": [{
            "group": {"displayName": "hitting"},
            "type": {"displayName": "gameLog"},
            "splits": [{"date": split_date, "game": {"id": 1003}, "stat": stat}],
        }],
    }]}
    empty_body = {"people": [{"id": player_id, "stats": []}]}
    expected, _ = expected_stats_requests(
        player_id=player_id, target_date=date.fromisoformat(GAME_DATE)
    )
    urls = request_urls or expected
    bodies = [empty_body, dated_body]
    return [
        RawDateBoundedStatsResponse.capture(
            body=json.dumps(body, sort_keys=True).encode(),
            request_sent_at_utc="2026-09-17T15:57:00Z",
            received_at_utc=received_at_utc,
            request_url=url,
        )
        for body, url in zip(bodies, urls)
    ]


def _inputs() -> dict:
    plan = _plan()
    raw, receipt = _roster()
    history = _history_materials(raw, receipt)
    store, projection = _projection(receipt, history["completed_lineups"])
    return {
        "root": ROOT,
        "plan": plan,
        "target": plan.targets[0],
        "side": "home",
        "team_id": TEAM_ID,
        "schedule_response": _schedule(),
        "active_roster_receipt": receipt,
        "active_roster_raw": raw,
        "history_records": history["history_records"],
        "history_raw_by_sha256": history["history_raw_by_sha256"],
        "history_coverage": history["history_coverage"],
        "history_schedule_raw_by_sha256": history["history_schedule_raw_by_sha256"],
        "opportunity_snapshot": history["opportunity_snapshot"],
        "projected_lineup_record": projection,
        "player_id": 201,
        "stats_responses": _stats(),
        "prediction_generated_at_utc": "2026-09-17T15:59:00Z",
    }


def _protocol() -> ProjectedOpportunityProtocolV2:
    return load_protocol_v2(
        root=ROOT,
        path=ROOT / "config/shared_pa_projected_opportunity_forward_v2.json",
    )


def test_replayable_envelope_and_candidate_are_coherent() -> None:
    inputs = _inputs()
    envelope = build_evidence_envelope_v2(**inputs)
    assert envelope["official_start_utc"] == START
    assert envelope["target_horizon_utc"] == HORIZON
    assert envelope["stats_cutoff_date"] == "2026-09-16"
    record = build_projected_opportunity_candidate_v2(
        **inputs,
        protocol=_protocol(),
        loaded_forward_contract=load_forward_contract(
            root=ROOT,
            contract_path=ROOT / "config/shared_pa_forward_evidence_contract_v1.json",
        ),
    )
    assert record["candidate_id"].endswith("_v2")
    assert record["confirmation_eligible"] is False
    assert record["evidence_class"] == "nonqualifying_exact_release_pending"
    assert record["per_pa_probability"]["home_run"] > 0


def test_v2_protocol_replays_every_bound_byte() -> None:
    protocol = _protocol()
    assert protocol.value["candidate_id"].endswith("_v2")


def _local_runtime_import_closure(entries: set[str]) -> set[str]:
    closure: set[str] = set()
    pending = list(entries)
    while pending:
        relative = pending.pop()
        if relative in closure:
            continue
        closure.add(relative)
        tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or not node.module:
                continue
            if not node.module.startswith("src."):
                continue
            imported = f"{node.module.replace('.', '/')}.py"
            if (ROOT / imported).is_file() and imported not in closure:
                pending.append(imported)
    return closure


def test_v2_immutable_bindings_equal_transitive_local_runtime_source_closure() -> None:
    entrypoints = {
        "src/evaluation/shared_pa_projected_opportunity_candidate_v2.py",
        "src/evaluation/shared_pa_projected_opportunity_evidence_v2.py",
        "src/evaluation/shared_pa_projected_opportunity_release_v2.py",
        "src/evaluation/shared_pa_projected_opportunity_runner_v2.py",
    }
    observed = _local_runtime_import_closure(entrypoints)
    assert observed == set(V2_RUNTIME_SOURCE_CLOSURE)
    protocol = json.loads(
        (ROOT / "config/shared_pa_projected_opportunity_forward_v2.json").read_text(
            encoding="utf-8"
        )
    )
    bound = {row["path"] for row in protocol["immutable_bindings"]}
    assert observed <= bound


def _copy_protocol_closure(tmp_path: Path) -> Path:
    destination = tmp_path / "release"
    v2 = json.loads(
        (ROOT / "config/shared_pa_projected_opportunity_forward_v2.json").read_text(
            encoding="utf-8"
        )
    )
    v1 = json.loads(
        (ROOT / "config/shared_pa_projected_opportunity_forward_v1.json").read_text(
            encoding="utf-8"
        )
    )
    paths = {
        "config/shared_pa_projected_opportunity_forward_v2.json",
        *(row["path"] for row in v2["immutable_bindings"]),
        *(row["path"] for row in v1["immutable_bindings"]),
    }
    for relative in paths:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    return destination


@pytest.mark.parametrize(
    "relative",
    [
        "src/evaluation/shared_pa_projected_opportunity_candidate.py",
        "src/evaluation/projected_lineup_contract.py",
    ],
)
def test_rehashed_inherited_runtime_source_mutation_fails_before_probability(
    tmp_path: Path, relative: str
) -> None:
    release = _copy_protocol_closure(tmp_path)
    source = release / relative
    source.write_bytes(source.read_bytes() + b"\n# inherited runtime mutation\n")
    protocol_path = release / "config/shared_pa_projected_opportunity_forward_v2.json"
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    for binding in protocol["immutable_bindings"]:
        if binding["path"] == relative:
            binding["sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
            break
    else:
        raise AssertionError(f"runtime source is not directly bound: {relative}")
    protocol_path.write_text(json.dumps(protocol, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(ProjectedOpportunityCandidateV2Error, match="inherited v1 runtime binding"):
        load_protocol_v2(root=release, path=protocol_path)


EVALUATION_GATE_MUTATIONS = [
    ("markets",),
    ("baselines", "all_markets"),
    ("baselines", "home_runs_over_0_5_additional"),
    ("baselines", "missing_required_baseline"),
    ("evaluation", "markets_scored_separately"),
    ("evaluation", "one_market_cannot_rescue_another"),
    ("evaluation", "probability_metrics"),
    ("evaluation", "hr_tail_metric"),
    ("evaluation", "distribution_metrics"),
    ("evaluation", "coverage"),
    ("evaluation", "uncertainty", "unit"),
    ("evaluation", "uncertainty", "method"),
    ("evaluation", "uncertainty", "confidence"),
    ("evaluation", "uncertainty", "resamples"),
    ("evaluation", "uncertainty", "seed"),
    ("evaluation", "material_proper_score_fraction"),
    ("evaluation", "point_and_upper_confidence_bound_must_clear"),
    ("evaluation", "post_outcome_threshold_changes_allowed"),
    ("prospective_windows", "reserved_smoke", "first_official_date"),
    ("prospective_windows", "reserved_smoke", "last_official_date"),
    ("prospective_windows", "reserved_smoke", "confirmation_eligible"),
    ("prospective_windows", "reserved_smoke", "outcome_scoring_authorized"),
    ("prospective_windows", "first_fresh_confirmation_date"),
    ("prospective_windows", "first_fresh_confirmation_last_official_date"),
    ("prospective_windows", "first_fresh_window_limitation"),
    ("prospective_windows", "independent_forward_replication_after_first_window"),
    ("prospective_windows", "first_candidate_record_rule"),
    ("outcome_and_settlement", "pregame_predictions_immutable_before_outcomes"),
    ("outcome_and_settlement", "official_final_lineup_pa_hits_hr_total_bases_captured_after_game"),
    ("outcome_and_settlement", "settlement_requires_verified_market_line_and_rules"),
    ("outcome_and_settlement", "valid_price_requires_two_sided_same_book_same_market_same_line_same_observation"),
    ("outcome_and_settlement", "predictions_may_be_displayed_immediately"),
    ("outcome_and_settlement", "display_label"),
]


def _mutate_path(payload: dict, path: tuple[str, ...]) -> None:
    parent = payload
    for key in path[:-1]:
        parent = parent[key]
    key = path[-1]
    value = parent[key]
    if isinstance(value, bool):
        parent[key] = not value
    elif isinstance(value, (int, float)):
        parent[key] = value + 1
    elif isinstance(value, list):
        parent[key] = [*value, "mutation"]
    else:
        parent[key] = f"{value}-mutation"


@pytest.mark.parametrize("path", EVALUATION_GATE_MUTATIONS)
def test_every_inherited_evaluation_gate_mutation_fails(path: tuple[str, ...]) -> None:
    payload = json.loads(
        (ROOT / "config/shared_pa_projected_opportunity_forward_v1.json").read_text(
            encoding="utf-8"
        )
    )
    _mutate_path(payload, path)
    with pytest.raises(ProjectedOpportunityCandidateV2Error, match="evaluation.*changed"):
        validate_inherited_evaluation_contract_v2(payload)


@pytest.mark.parametrize("mutation", ["omit_file", "role", "boundary", "release_status", "release_commit"])
def test_source_manifest_exact_set_and_protected_release_mutations_fail(mutation: str) -> None:
    payload, _ = load_source_manifest_v2(root=ROOT)
    forged = copy.deepcopy(payload)
    if mutation == "omit_file":
        forged["files"].pop()
    elif mutation == "role":
        forged["files"][0]["role"] = "forged-role"
    elif mutation == "boundary":
        forged["protected_boundaries"]["may_2026_accessed"] = True
    elif mutation == "release_status":
        forged["status"] = "EXACT_RELEASE_PUBLISHED"
    else:
        forged["release_commit"] = "a" * 40
    with pytest.raises(ProjectedOpportunityReleaseV2Error):
        validate_source_manifest_payload_v2(root=ROOT, payload=forged)


def _runtime_release_receipt(**updates: object) -> dict:
    unsigned = {
        "schema_version": "shared-pa-projected-opportunity-runtime-release-v2",
        "candidate_id": "shared_pa_projected_opportunity_eb200_v2",
        "source_commit": "b" * 40,
        "source_tree_clean": True,
        "source_manifest_path": SOURCE_MANIFEST_RELATIVE,
        "source_manifest_sha256": "c" * 64,
        "candidate_protocol_path": "config/shared_pa_projected_opportunity_forward_v2.json",
        "candidate_protocol_sha256": "a" * 64,
        "candidate_protocol_status": PUBLISHED_PROTOCOL_STATUS,
        "created_at_utc": "2026-09-17T15:00:00Z",
        "research_only": True,
        "betting_authorized": False,
    }
    unsigned.update(updates)
    return {**unsigned, "release_receipt_sha256": sha256_value(unsigned)}


def _validate_runtime_release(receipt: dict | None, *, clean: bool = True) -> dict:
    return validate_release_claim_v2(
        protocol_status=PUBLISHED_PROTOCOL_STATUS,
        protocol_sha256="a" * 64,
        protocol_path="config/shared_pa_projected_opportunity_forward_v2.json",
        source_manifest_sha256="c" * 64,
        receipt_payload=receipt,
        observed_commit="b" * 40,
        observed_clean=clean,
    )


def test_published_protocol_with_null_release_receipt_fails() -> None:
    with pytest.raises(ProjectedOpportunityReleaseV2Error, match="non-null receipt"):
        _validate_runtime_release(None)


@pytest.mark.parametrize(
    "receipt,clean",
    [
        (_runtime_release_receipt(source_commit="d" * 40), True),
        (_runtime_release_receipt(candidate_protocol_sha256="e" * 64), True),
        (_runtime_release_receipt(source_manifest_sha256="f" * 64), True),
        (_runtime_release_receipt(candidate_protocol_status="FORGED"), True),
        (_runtime_release_receipt(source_tree_clean=False), True),
        (_runtime_release_receipt(), False),
    ],
)
def test_rehashed_runtime_release_identity_mutations_fail(receipt: dict, clean: bool) -> None:
    with pytest.raises(ProjectedOpportunityReleaseV2Error):
        _validate_runtime_release(receipt, clean=clean)


def test_directly_constructed_protocol_cannot_bypass_byte_replay() -> None:
    inputs = _inputs()
    forged = ProjectedOpportunityProtocolV2(
        value=_protocol().value,
        sha256="f" * 64,
        root=ROOT,
        source_path=ROOT / "config/shared_pa_projected_opportunity_forward_v2.json",
    )
    with pytest.raises(ProjectedOpportunityCandidateV2Error, match="retained-byte replay"):
        build_projected_opportunity_candidate_v2(
            **inputs,
            protocol=forged,
            loaded_forward_contract=load_forward_contract(
                root=ROOT,
                contract_path=ROOT / "config/shared_pa_forward_evidence_contract_v1.json",
            ),
        )


def test_rehashed_probability_mutation_fails_candidate_replay() -> None:
    inputs = _inputs()
    build_inputs = {
        **inputs,
        "protocol": _protocol(),
        "loaded_forward_contract": load_forward_contract(
            root=ROOT,
            contract_path=ROOT / "config/shared_pa_forward_evidence_contract_v1.json",
        ),
    }
    record = build_projected_opportunity_candidate_v2(**build_inputs)
    forged = copy.deepcopy(record)
    forged["candidate_market_distributions"]["tails"]["home_runs_over_0.5"] += 0.01
    unsigned = dict(forged)
    unsigned.pop("candidate_record_sha256")
    forged["candidate_record_sha256"] = sha256_value(unsigned)
    with pytest.raises(ProjectedOpportunityCandidateV2Error, match="retained-evidence replay"):
        replay_and_validate_candidate_record_v2(record=forged, **build_inputs)


def test_arbitrary_horizon_fails_in_typed_target() -> None:
    with pytest.raises(ValueError, match="must equal"):
        CaptureTarget(
            mlb_game_pk=123456,
            official_game_date=GAME_DATE,
            official_start_time_utc=START,
            entry_target_at_utc="2026-09-17T19:00:00Z",
            entry_hours=4,
        )


def test_wrong_side_team_fails_schedule_replay() -> None:
    inputs = _inputs()
    inputs["side"] = "away"
    with pytest.raises(ProjectedOpportunityEvidenceV2Error, match="side/team"):
        build_evidence_envelope_v2(**inputs)


@pytest.mark.parametrize("mutation", ["source", "port", "userinfo", "season", "cutoff"])
def test_stats_request_identity_mutations_fail(mutation: str) -> None:
    inputs = _inputs()
    urls, _ = expected_stats_requests(player_id=201, target_date=date.fromisoformat(GAME_DATE))
    urls = list(urls)
    index = 1 if mutation == "cutoff" else 0
    url = urls[index]
    if mutation == "source":
        url = url.replace("statsapi.mlb.com", "example.invalid")
    elif mutation == "port":
        url = url.replace("statsapi.mlb.com", "statsapi.mlb.com:444")
    elif mutation == "userinfo":
        url = url.replace("statsapi.mlb.com", "example.invalid@statsapi.mlb.com")
    elif mutation == "season":
        url = url.replace("startDate=2026-01-01", "startDate=2025-01-01")
    else:
        url = url.replace("endDate=2026-09-16", "endDate=2026-09-17")
    urls[index] = url
    inputs["stats_responses"] = _stats(request_urls=urls)
    with pytest.raises(ProjectedOpportunityEvidenceV2Error, match="source, player, season, or prior-date cutoff"):
        build_evidence_envelope_v2(**inputs)


def test_valid_url_with_out_of_range_dated_body_fails() -> None:
    inputs = _inputs()
    inputs["stats_responses"] = _stats(split_date=GAME_DATE)
    with pytest.raises(ProjectedOpportunityEvidenceV2Error, match="out-of-range"):
        build_evidence_envelope_v2(**inputs)


def test_schedule_receipt_after_prediction_generation_fails() -> None:
    inputs = _inputs()
    inputs["schedule_response"] = RawPregameResponse(
        inputs["schedule_response"].body, "2026-09-17T15:59:30Z"
    )
    with pytest.raises(ProjectedOpportunityEvidenceV2Error, match="predates a retained"):
        build_evidence_envelope_v2(**inputs)


def test_roster_receipt_after_prediction_generation_fails() -> None:
    inputs = _inputs()
    raw, receipt = _roster(
        inputs["active_roster_raw"], received_at_utc="2026-09-17T15:59:30Z"
    )
    history = _history_materials(raw, receipt)
    _, projection = _projection(receipt, history["completed_lineups"])
    inputs.update({
        "active_roster_raw": raw,
        "active_roster_receipt": receipt,
        "history_records": history["history_records"],
        "history_raw_by_sha256": history["history_raw_by_sha256"],
        "history_coverage": history["history_coverage"],
        "history_schedule_raw_by_sha256": history["history_schedule_raw_by_sha256"],
        "opportunity_snapshot": history["opportunity_snapshot"],
        "projected_lineup_record": projection,
    })
    with pytest.raises(ProjectedOpportunityEvidenceV2Error, match="predates a retained"):
        build_evidence_envelope_v2(**inputs)


def test_history_schedule_receipt_after_prediction_generation_fails() -> None:
    inputs = _inputs()
    history = _history_materials(
        inputs["active_roster_raw"],
        inputs["active_roster_receipt"],
        schedule_received_at_utc="2026-09-17T15:59:30Z",
    )
    _, projection = _projection(
        inputs["active_roster_receipt"], history["completed_lineups"]
    )
    inputs.update({
        "history_records": history["history_records"],
        "history_raw_by_sha256": history["history_raw_by_sha256"],
        "history_coverage": history["history_coverage"],
        "history_schedule_raw_by_sha256": history["history_schedule_raw_by_sha256"],
        "opportunity_snapshot": history["opportunity_snapshot"],
        "projected_lineup_record": projection,
    })
    with pytest.raises(ProjectedOpportunityEvidenceV2Error, match="predates a retained"):
        build_evidence_envelope_v2(**inputs)


def test_stats_receipt_after_prediction_generation_fails() -> None:
    inputs = _inputs()
    inputs["stats_responses"] = _stats(received_at_utc="2026-09-17T15:59:30Z")
    with pytest.raises(ProjectedOpportunityEvidenceV2Error, match="predates a retained stats"):
        build_evidence_envelope_v2(**inputs)


def test_stats_request_after_prediction_generation_fails() -> None:
    inputs = _inputs()
    responses = []
    for original in inputs["stats_responses"]:
        responses.append(RawDateBoundedStatsResponse.capture(
            body=original.body,
            request_sent_at_utc="2026-09-17T15:59:30Z",
            received_at_utc="2026-09-17T15:59:40Z",
            request_url=original.request_url,
        ))
    inputs["stats_responses"] = responses
    with pytest.raises(ProjectedOpportunityEvidenceV2Error, match="predates a retained stats"):
        build_evidence_envelope_v2(**inputs)


def test_rehashed_projection_receipt_after_generation_fails() -> None:
    inputs = _inputs()
    completed = [
        {
            "official_game_date": record["official_game_date"],
            "mlb_game_pk": record["mlb_game_pk"],
            "team_id": TEAM_ID,
            "player_id": player["player_id"],
            "slot": player["lineup_slot"],
        }
        for record in inputs["history_records"]
        for player in record["players"]
        if player["is_starter"]
    ]
    _, forged = _projection(
        inputs["active_roster_receipt"],
        completed,
        projection_receipt_utc="2026-09-17T15:59:30Z",
    )
    inputs["projected_lineup_record"] = forged
    with pytest.raises(ProjectedOpportunityEvidenceV2Error, match="predates a retained"):
        build_evidence_envelope_v2(**inputs)


def test_stats_segments_skip_may_2026_entirely() -> None:
    urls, cutoff = expected_stats_requests(
        player_id=201, target_date=date.fromisoformat(GAME_DATE)
    )
    assert cutoff == "2026-09-16"
    assert len(urls) == 2
    assert "endDate=2026-04-30" in urls[0]
    assert "startDate=2026-06-01" in urls[1]


def test_may_2026_target_cannot_construct_stats_requests() -> None:
    with pytest.raises(ProjectedOpportunityEvidenceV2Error, match="May 2026 is sealed"):
        expected_stats_requests(player_id=201, target_date=date(2026, 5, 15))


def test_forged_roster_receipt_fails_raw_replay() -> None:
    inputs = _inputs()
    forged = copy.deepcopy(inputs["active_roster_receipt"])
    forged["source_record_id"] = "forged"
    inputs["active_roster_receipt"] = forged
    with pytest.raises(ProjectedOpportunityEvidenceV2Error, match="retained raw replay"):
        build_evidence_envelope_v2(**inputs)


def test_rehashed_forged_projection_artifact_fails_semantic_replay() -> None:
    inputs = _inputs()
    forged = copy.deepcopy(inputs["projected_lineup_record"])
    forged["fitted_artifact_sha256"] = "0" * 64
    unsigned = dict(forged)
    unsigned.pop("projection_content_sha256")
    forged["projection_content_sha256"] = sha256_value(unsigned)
    inputs["projected_lineup_record"] = forged
    with pytest.raises(ProjectedOpportunityEvidenceV2Error, match="semantic replay"):
        build_evidence_envelope_v2(**inputs)


def test_rehashed_raw_history_mutation_fails_snapshot_and_projection_replay() -> None:
    inputs = _inputs()
    original = inputs["history_records"][0]
    old_raw = inputs["history_raw_by_sha256"][original["source_payload_sha256"]]
    payload = json.loads(old_raw)
    player = payload["liveData"]["boxscore"]["teams"]["home"]["players"].pop("ID201")
    player["person"]["id"] = 211
    payload["liveData"]["boxscore"]["teams"]["home"]["players"]["ID211"] = player
    new_raw = json.dumps(payload, sort_keys=True).encode()
    replayed = parse_opportunity_history(
        response=RawOpportunityResponse(
            new_raw,
            original["source_received_at_utc"],
            original["source_request_url"],
        ),
        expected_game_pk=original["mlb_game_pk"],
        expected_official_date=original["official_game_date"],
        side="home",
        expected_team_id=TEAM_ID,
        capture_plan=original["capture_plan"],
    )
    inputs["history_records"] = [replayed, *inputs["history_records"][1:]]
    raws = dict(inputs["history_raw_by_sha256"])
    raws.pop(original["source_payload_sha256"])
    raws[replayed["source_payload_sha256"]] = new_raw
    inputs["history_raw_by_sha256"] = raws
    with pytest.raises(ProjectedOpportunityEvidenceV2Error, match="snapshot differs"):
        build_evidence_envelope_v2(**inputs)


@pytest.mark.parametrize("value", [date(2026, 9, 28), date(2026, 10, 1), date(2027, 9, 20)])
def test_postseason_and_later_dates_are_never_confirmation_eligible(value: date) -> None:
    state, eligible = classify_evidence_date(value)
    assert state == "nonqualifying_outside_locked_confirmation_window"
    assert eligible is False
