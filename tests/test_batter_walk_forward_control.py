"""Mutation and replay tests for the prospective batter-walk control."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.evaluation.batter_walk_forward_control import (
    BatterWalkForwardControlError,
    LoadedBatterWalkContract,
    canonical_bytes,
    derive_player_output,
    load_contract,
    replay_contract,
    sha256_value,
    validate_player_output_against_source,
    walk_count_distribution,
)
from src.evaluation.batter_walk_forward_ledger import (
    BatterWalkForwardLedger,
    BatterWalkForwardLedgerError,
)
from src.evaluation.batter_walk_official_settlement import (
    BatterWalkSettlementError,
    DISPOSITIONS,
    RawOfficialSettlementResponse,
    build_opening_gate,
    build_settlement_record,
    build_untrusted_test_transport_receipt,
    coverage_and_gradeability,
    expected_request_url,
    load_settlement_contract,
    retain_opening_gate,
    replay_settlement_record,
)
from src.evaluation.shadow_capture_plan import plan_from_schedule
from src.evaluation.shared_pa_forward_collector import RawPregameResponse, build_projected_player_snapshot
from src.evaluation.shared_pa_forward_evidence import load_forward_contract
from src.evaluation.shared_pa_forward_ledger import SharedPAForwardLedger, side_target_id


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config/batter_walk_forward_control_v1.json"
START = datetime(2026, 7, 30, tzinfo=timezone.utc)
HORIZON = START - timedelta(hours=4)
OPENED = "2026-09-28T00:00:01Z"


@pytest.fixture(autouse=True)
def _windows_fchmod_compat(monkeypatch):
    """The upstream ledger's production chmod is POSIX-only; preserve its tests on Windows."""
    if not hasattr(os, "fchmod"):
        monkeypatch.setattr(os, "fchmod", lambda *_: None, raising=False)


def _contract() -> LoadedBatterWalkContract:
    return load_contract(root=ROOT, contract_path=CONTRACT_PATH)


def _isolated_repository(tmp_path: Path) -> Path:
    """Copy only the explicitly bound contract surface for mutation tests."""
    isolated = tmp_path / "isolated-repository"
    relatives = (
        "config/batter_walk_forward_control_v1.json",
        "config/batter_walk_forward_control_v1.sha256",
        "config/batter_walk_official_settlement_contract_v1.json",
        "config/shared_pa_forward_evidence_contract_v1.json",
        "config/shared_pa_forward_eb_control_v1.json",
        "data/analysis/system_integrity_v2/pa_volume_chronology_v1/pa_distribution_fit_2023.json",
        "src/evaluation/batter_walk_forward_control.py",
        "src/evaluation/batter_walk_forward_ledger.py",
        "src/evaluation/batter_walk_official_settlement.py",
        "tests/test_batter_walk_forward_control.py",
    )
    for relative in relatives:
        source = ROOT / relative
        destination = isolated / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    return isolated


def _plan():
    return plan_from_schedule(
        official_game_date="2026-07-30", entry_hours=4, policy_sha256="a" * 64,
        schedule_snapshot=[{
            "gamePk": 123456, "officialDate": "2026-07-30",
            "gameDate": START.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "teams": {"home": {"team": {"name": "Home"}}, "away": {"team": {"name": "Away"}}},
        }],
    )


def _lineup_raw(*, away: bool = True) -> bytes:
    lineups = {"homePlayers": [{"id": value} for value in range(201, 210)]}
    if away:
        lineups["awayPlayers"] = [{"id": value} for value in range(101, 110)]
    return json.dumps({
        "dates": [{"games": [{
            "gamePk": 123456, "officialDate": "2026-07-30",
            "gameDate": START.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "teams": {"home": {"team": {"id": 111, "name": "Home"}}, "away": {"team": {"id": 112, "name": "Away"}}},
            "lineups": lineups,
        }]}],
    }, sort_keys=True).encode()


def _stats(player_id: int) -> RawPregameResponse:
    body = json.dumps({"people": [{"id": player_id, "stats": [{
        "group": {"displayName": "hitting"}, "type": {"displayName": "season"},
        "splits": [{"stat": {
            "plateAppearances": 100, "atBats": 88, "hits": 28, "doubles": 5,
            "triples": 1, "homeRuns": 6, "baseOnBalls": 10, "strikeOuts": 20,
        }}],
    }]}]}, sort_keys=True).encode()
    return RawPregameResponse(body=body, received_at_utc=HORIZON.isoformat())


def _upstream(tmp_path: Path):
    plan = _plan()
    loaded = load_forward_contract(root=ROOT, contract_path=ROOT / "config/shared_pa_forward_evidence_contract_v1.json")
    root = tmp_path / "shared-pa"
    ledger = SharedPAForwardLedger(
        root, plan=plan, contract_sha256=loaded["contract_sha256"],
        runtime_manifest_sha256="c" * 64, collector_code_sha256="b" * 64,
    )
    lineup = RawPregameResponse(_lineup_raw(), HORIZON.isoformat())
    players = []
    raws: dict[str, bytes] = {}
    for slot, player_id in enumerate(range(201, 210), start=1):
        stats = _stats(player_id)
        raws[stats.sha256] = stats.body
        players.append(build_projected_player_snapshot(
            plan=plan, target=plan.targets[0], side="home", source_slot=slot,
            player_id=player_id, home_team_id=111, away_team_id=112,
            lineup_response=lineup, stats_response=stats, loaded_contract=loaded,
            collector_instance_id="synthetic", collector_code_sha256="b" * 64,
            runtime_manifest_sha256="c" * 64,
        ))
    ledger.append_complete(
        target=plan.targets[0], side="home", players=players, lineup_raw=lineup.body,
        stats_raw_by_sha256=raws, committed_utc=HORIZON.isoformat(),
    )
    ledger.append_exclusion(
        target=plan.targets[0], side="away", state="lineup_unavailable",
        observed_at_utc=HORIZON.isoformat(), detail="official projected lineup absent",
        raw_payload=_lineup_raw(away=False),
    )
    ledger.verify(require_complete_coverage=True)
    return plan, root, players


def _outputs(players: list[dict]) -> list[dict]:
    return [derive_player_output(
        source_snapshot=value, contract=_contract(), derived_utc=value["target_horizon_utc"],
    ) for value in players]


def _raw_final(output: dict, *, final: bool = True, walks: int = 1, pa: int = 4, starter: bool = True) -> bytes:
    player = {
        "person": {"id": output["player_id"]},
        "battingOrder": "300" if starter else "",
        "stats": {"batting": {"plateAppearances": pa, "baseOnBalls": walks}},
    }
    return json.dumps({
        "gamePk": output["mlb_game_pk"],
        "gameData": {
            "datetime": {"officialDate": output["official_game_date"]},
            "status": {"abstractGameState": "Final" if final else "Live"},
        },
        "liveData": {"boxscore": {"teams": {
            "home": {"team": {"id": output["home_team_id"]}, "players": {f"ID{output['player_id']}": player}},
            "away": {"team": {"id": output["away_team_id"]}, "players": {}},
        }}},
    }, sort_keys=True).encode()


def _settlement(output: dict, plan, *, raw: bytes | None = None) -> tuple[dict, bytes, bytes, bytes]:
    body = raw if raw is not None else _raw_final(output)
    gate = build_opening_gate(contract=_contract(), plan=plan, opened_at_utc=OPENED)
    gate_raw = retain_opening_gate(gate)
    transport = build_untrusted_test_transport_receipt(
        body=body, received_at_utc=OPENED,
        request_url=expected_request_url(output["mlb_game_pk"]),
        player_output=output, observation_sequence=2,
    )
    response = RawOfficialSettlementResponse(
        body=body, transport_receipt_raw=transport,
    )
    return build_settlement_record(
        response=response, player_output=output, contract=_contract(), opening_gate_raw=gate_raw,
    ), body, transport, gate_raw


def test_contract_is_independently_pinned_and_replayed() -> None:
    contract = _contract()
    assert replay_contract(contract).sha256 == contract.sha256
    assert load_settlement_contract(root=ROOT, contract=contract)["status"] == "SEALED_SCHEMA_BOUNDARY_NOT_OPENED"
    forged = LoadedBatterWalkContract(
        copy.deepcopy(contract.payload), contract.sha256, contract.settlement_sha256,
        contract.source_path, contract.repository_root, object(),
    )
    with pytest.raises(BatterWalkForwardControlError, match="immutable loader"):
        replay_contract(forged)
    contract.payload["market"]["binary_lines"] = [0.5]
    with pytest.raises(BatterWalkForwardControlError, match="immutable replay"):
        replay_contract(contract)


def test_contract_path_and_independent_digest_reject_mutations(tmp_path: Path) -> None:
    copied = tmp_path / CONTRACT_PATH.name
    copied.write_bytes(CONTRACT_PATH.read_bytes())
    with pytest.raises(BatterWalkForwardControlError, match="immutable repository path"):
        load_contract(root=ROOT, contract_path=copied)
    isolated = _isolated_repository(tmp_path)
    isolated_contract = isolated / "config/batter_walk_forward_control_v1.json"
    payload = json.loads(isolated_contract.read_bytes())
    payload["protected_boundaries"]["betting_authorized"] = True
    isolated_contract.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(BatterWalkForwardControlError, match="independent digest"):
        load_contract(root=isolated, contract_path=isolated_contract)


def test_exact_evaluation_boundary_and_release_authority_are_executable_contracts(tmp_path: Path) -> None:
    isolated = _isolated_repository(tmp_path)
    isolated_contract = isolated / "config/batter_walk_forward_control_v1.json"
    digest_path = isolated / "config/batter_walk_forward_control_v1.sha256"
    original = isolated_contract.read_bytes()
    for path, value in (
        (("evaluation_boundary", "walk_lines_cannot_rescue_each_other"), False),
        (("release_authority", "activation_eligible"), True),
    ):
        payload = json.loads(original)
        payload[path[0]][path[1]] = value
        mutated = json.dumps(payload, indent=2).encode() + b"\n"
        isolated_contract.write_bytes(mutated)
        digest_path.write_text(hashlib.sha256(mutated).hexdigest() + "\n", encoding="ascii")
        with pytest.raises(BatterWalkForwardControlError, match="evaluation boundary|release authority"):
            load_contract(root=isolated, contract_path=isolated_contract)


def test_exact_walk_mixture_and_source_replay() -> None:
    assert walk_count_distribution(walk_probability=0.2, pa_support=[1, 2], pa_mass=[0.25, 0.75]) == pytest.approx([0.68, 0.29, 0.03])


def test_walk_ledger_derives_sides_and_replays_upstream_raw(tmp_path: Path) -> None:
    plan, upstream, players = _upstream(tmp_path)
    outputs = _outputs(players)
    ledger = BatterWalkForwardLedger(
        tmp_path / "walk", contract=_contract(), plan=plan, source_ledger_root=upstream,
    )
    assert ledger.expected_side_keys == ["123456:away:batter_walks", "123456:home:batter_walks"]
    assert ledger.append_complete(target=plan.targets[0], side="home", player_outputs=outputs)
    assert ledger.append_exclusion(target=plan.targets[0], side="away")
    assert ledger.verify(require_complete_coverage=True) == {
        "expected_sides": 2, "terminal_sides": 2, "missing_sides": 0,
        "complete_sides": 1, "excluded_sides": 1, "prediction_rows": 9,
        "replayed_source_terminals": 2,
    }


def test_rehashed_fake_upstream_exclusion_and_fake_output_fail(tmp_path: Path) -> None:
    plan, upstream, players = _upstream(tmp_path)
    identifier = side_target_id(plan=plan, target=plan.targets[0], side="away")
    path = upstream / "terminal" / f"{identifier}.json"
    terminal = json.loads(path.read_text())
    terminal["players"] = [players[0]]
    unsigned = dict(terminal)
    unsigned.pop("entry_sha256")
    terminal["entry_sha256"] = sha256_value(unsigned)
    path.write_bytes(canonical_bytes(terminal) + b"\n")
    with pytest.raises(BatterWalkForwardLedgerError, match="does not replay"):
        BatterWalkForwardLedger(tmp_path / "walk", contract=_contract(), plan=plan, source_ledger_root=upstream)

    plan, upstream, players = _upstream(tmp_path / "second")
    outputs = _outputs(players)
    outputs[0]["walk_per_pa_probability"] += 0.01
    outputs[0]["output_sha256"] = sha256_value({key: value for key, value in outputs[0].items() if key != "output_sha256"})
    ledger = BatterWalkForwardLedger(tmp_path / "walk2", contract=_contract(), plan=plan, source_ledger_root=upstream)
    with pytest.raises(BatterWalkForwardLedgerError, match="does not reproduce"):
        ledger.append_complete(target=plan.targets[0], side="home", player_outputs=outputs)


def test_settlement_replays_raw_source_parser_and_opening_gate(tmp_path: Path) -> None:
    plan, _, players = _upstream(tmp_path)
    output = _outputs(players)[0]
    record, raw, transport, gate_raw = _settlement(output, plan)
    assert replay_settlement_record(
        record, raw_payload=raw, transport_receipt_raw=transport,
        opening_gate_raw=gate_raw, player_output=output, contract=_contract(),
    ) == "untrusted_evidence_not_gradeable"
    with pytest.raises(BatterWalkSettlementError):
        replay_settlement_record(
            record, raw_payload=raw + b" ", transport_receipt_raw=transport,
            opening_gate_raw=gate_raw, player_output=output, contract=_contract(),
        )
    receipt = json.loads(transport)
    receipt["request_url"] = receipt["request_url"].replace("statsapi.mlb.com", "example.com")
    bad_transport = canonical_bytes(receipt) + b"\n"
    bad = copy.deepcopy(record)
    bad["transport_receipt_sha256"] = hashlib.sha256(bad_transport).hexdigest()
    bad["record_sha256"] = sha256_value({key: value for key, value in bad.items() if key != "record_sha256"})
    with pytest.raises(BatterWalkSettlementError, match="source"):
        replay_settlement_record(
            bad, raw_payload=raw, transport_receipt_raw=bad_transport,
            opening_gate_raw=gate_raw, player_output=output, contract=_contract(),
        )
    forged_gradeable = copy.deepcopy(record)
    forged_gradeable["disposition"] = "gradeable"
    forged_gradeable["record_sha256"] = sha256_value({
        key: value for key, value in forged_gradeable.items() if key != "record_sha256"
    })
    with pytest.raises(BatterWalkSettlementError, match="semantic replay"):
        replay_settlement_record(
            forged_gradeable, raw_payload=raw, transport_receipt_raw=transport,
            opening_gate_raw=gate_raw, player_output=output, contract=_contract(),
        )
    same_sequence_receipt = json.loads(transport)
    same_sequence_receipt["observation_sequence"] = 1
    same_sequence_raw = canonical_bytes(same_sequence_receipt) + b"\n"
    with pytest.raises(BatterWalkSettlementError, match="observation sequence"):
        build_settlement_record(
            response=RawOfficialSettlementResponse(raw, same_sequence_raw),
            player_output=output, contract=_contract(), opening_gate_raw=gate_raw,
        )
    gate = build_opening_gate(contract=_contract(), plan=plan, opened_at_utc=OPENED)
    gate["opened_at_utc"] = "2026-09-27T23:59:59Z"
    gate["opening_gate_sha256"] = sha256_value({key: value for key, value in gate.items() if key != "opening_gate_sha256"})
    with pytest.raises(BatterWalkSettlementError, match="predates"):
        invalid_gate_raw = retain_opening_gate(gate)
        valid_transport = build_untrusted_test_transport_receipt(
            body=raw, received_at_utc=OPENED,
            request_url=expected_request_url(output["mlb_game_pk"]), player_output=output,
            observation_sequence=2,
        )
        build_settlement_record(
            response=RawOfficialSettlementResponse(raw, valid_transport),
            player_output=output, contract=_contract(), opening_gate_raw=invalid_gate_raw,
        )
    valid_gate = build_opening_gate(contract=_contract(), plan=plan, opened_at_utc=OPENED)
    with pytest.raises(BatterWalkSettlementError, match="response predates"):
        early_transport = build_untrusted_test_transport_receipt(
            body=raw, received_at_utc="2026-09-27T23:59:59Z",
            request_url=expected_request_url(output["mlb_game_pk"]), player_output=output,
            observation_sequence=2,
        )
        build_settlement_record(
            response=RawOfficialSettlementResponse(raw, early_transport),
            player_output=output, contract=_contract(), opening_gate_raw=retain_opening_gate(valid_gate),
        )


@pytest.mark.parametrize(
    ("raw_factory", "expected"),
    [
        (lambda output: _raw_final(output), "untrusted_evidence_not_gradeable"),
        (lambda output: _raw_final(output, starter=False), "untrusted_evidence_not_gradeable"),
        (lambda output: _raw_final(output, final=False), "untrusted_evidence_not_gradeable"),
        (lambda output: _raw_final(output, walks=len(output["walks_pmf"]), pa=len(output["walks_pmf"])), "untrusted_evidence_not_gradeable"),
        (lambda output: b"{malformed", "untrusted_evidence_not_gradeable"),
    ],
)
def test_all_exact_dispositions_derive_from_retained_raw(tmp_path: Path, raw_factory, expected: str) -> None:
    plan, _, players = _upstream(tmp_path)
    output = _outputs(players)[0]
    record, raw, transport, gate_raw = _settlement(output, plan, raw=raw_factory(output))
    assert record["disposition"] == expected
    assert replay_settlement_record(
        record, raw_payload=raw, transport_receipt_raw=transport,
        opening_gate_raw=gate_raw, player_output=output, contract=_contract(),
    ) == expected


def test_coverage_rejects_bare_dispositions_and_derives_replayed_records(tmp_path: Path) -> None:
    plan, upstream, players = _upstream(tmp_path)
    outputs = _outputs(players)
    ledger = BatterWalkForwardLedger(tmp_path / "walk", contract=_contract(), plan=plan, source_ledger_root=upstream)
    ledger.append_complete(target=plan.targets[0], side="home", player_outputs=outputs)
    ledger.append_exclusion(target=plan.targets[0], side="away")
    terminals = [json.loads(path.read_text()) for path in (tmp_path / "walk" / "terminal").glob("*.json")]
    before = coverage_and_gradeability(
        plan=plan, walk_ledger=ledger, contract=_contract(),
    )
    assert before["gradeable_fraction"] is None
    records = []
    raws: dict[str, bytes] = {}
    transports: dict[str, bytes] = {}
    gates: dict[str, bytes] = {}
    for output in outputs:
        record, raw, transport, gate_raw = _settlement(output, plan)
        records.append(record)
        raws[hashlib.sha256(raw).hexdigest()] = raw
        transports[hashlib.sha256(transport).hexdigest()] = transport
        gates[hashlib.sha256(gate_raw).hexdigest()] = gate_raw
    after = coverage_and_gradeability(
        plan=plan, walk_ledger=ledger, contract=_contract(),
        settlement_records=records, settlement_raw_by_sha256=raws,
        settlement_transport_by_sha256=transports, opening_gate_by_sha256=gates,
    )
    assert after["gradeable_rows"] == 0
    assert set(after["disposition_counts"]) == DISPOSITIONS
    assert after["release_authority_bound"] is False
    assert after["future_gradeability_gate_met"] is False
    missing_gate = dict(gates)
    missing_gate.clear()
    with pytest.raises(BatterWalkSettlementError, match="transport or opening-gate"):
        coverage_and_gradeability(
            plan=plan, walk_ledger=ledger, contract=_contract(),
            settlement_records=records, settlement_raw_by_sha256=raws,
            settlement_transport_by_sha256=transports,
            opening_gate_by_sha256=missing_gate,
        )
    with pytest.raises(TypeError):
        coverage_and_gradeability(  # type: ignore[call-arg]
            plan=plan, walk_ledger=ledger,
            contract=_contract(), settlement_dispositions={"fake": "gradeable"},
        )


def test_manifest_exact_schema_orphans_and_symlinks_fail(tmp_path: Path) -> None:
    plan, upstream, players = _upstream(tmp_path)
    ledger_root = tmp_path / "walk"
    ledger = BatterWalkForwardLedger(ledger_root, contract=_contract(), plan=plan, source_ledger_root=upstream)
    manifest_path = ledger_root / "ledger_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["unexpected"] = True
    manifest_path.write_bytes(canonical_bytes(manifest) + b"\n")
    with pytest.raises(BatterWalkForwardLedgerError, match="field set"):
        ledger.verify()

    if hasattr(os, "symlink"):
        outside = tmp_path / "outside"
        outside.mkdir()
        linked = tmp_path / "linked-ledger"
        try:
            os.symlink(outside, linked, target_is_directory=True)
        except OSError:
            pytest.skip("symlink creation is not permitted")
        with pytest.raises(BatterWalkForwardLedgerError, match="symlink"):
            BatterWalkForwardLedger(linked, contract=_contract(), plan=plan, source_ledger_root=upstream)


def test_windows_junction_root_is_rejected_without_privilege_skip(tmp_path: Path) -> None:
    if os.name != "nt":
        pytest.skip("Windows junction mutation")
    plan, upstream, _ = _upstream(tmp_path)
    outside = tmp_path / "junction-target"
    outside.mkdir()
    linked = tmp_path / "junction-ledger"
    created = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(linked), str(outside)],
        check=False, capture_output=True, text=True,
    )
    assert created.returncode == 0, created.stderr or created.stdout
    with pytest.raises(BatterWalkForwardLedgerError, match="junction|reparse"):
        BatterWalkForwardLedger(
            linked, contract=_contract(), plan=plan, source_ledger_root=upstream,
        )
    isolated = _isolated_repository(outside)
    junction_repository = linked / isolated.name
    with pytest.raises(BatterWalkForwardControlError, match="junction|reparse"):
        load_contract(
            root=junction_repository,
            contract_path=junction_repository / "config/batter_walk_forward_control_v1.json",
        )


@pytest.mark.parametrize("relative", ["orphan.txt", ".stale.tmp", "unknown/orphan.json"])
def test_exact_inventory_rejects_unknown_hidden_temp_and_orphan_artifacts(
    tmp_path: Path, relative: str,
) -> None:
    plan, upstream, players = _upstream(tmp_path)
    root = tmp_path / "walk"
    ledger = BatterWalkForwardLedger(root, contract=_contract(), plan=plan, source_ledger_root=upstream)
    ledger.append_complete(target=plan.targets[0], side="home", player_outputs=_outputs(players))
    ledger.append_exclusion(target=plan.targets[0], side="away")
    artifact = root / relative
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(b"stale")
    with pytest.raises(BatterWalkForwardLedgerError, match="inventory"):
        ledger.verify(require_complete_coverage=True)


def test_coverage_replays_retained_source_not_self_rehashed_probability(tmp_path: Path) -> None:
    plan, upstream, players = _upstream(tmp_path)
    root = tmp_path / "walk"
    ledger = BatterWalkForwardLedger(root, contract=_contract(), plan=plan, source_ledger_root=upstream)
    outputs = _outputs(players)
    ledger.append_complete(target=plan.targets[0], side="home", player_outputs=outputs)
    ledger.append_exclusion(target=plan.targets[0], side="away")
    retained = root / "outputs" / f"{outputs[0]['output_sha256']}.json"
    forged = json.loads(retained.read_text())
    forged["walk_per_pa_probability"] += 0.01
    forged["walks_pmf"] = walk_count_distribution(
        walk_probability=forged["walk_per_pa_probability"],
        pa_support=forged["pa_support"], pa_mass=forged["pa_mass"],
    )
    forged["tails"] = {
        "walks_over_0.5": 1.0 - forged["walks_pmf"][0],
        "walks_over_1.5": 1.0 - sum(forged["walks_pmf"][:2]),
    }
    forged["output_sha256"] = sha256_value({
        key: value for key, value in forged.items() if key != "output_sha256"
    })
    retained.write_bytes(canonical_bytes(forged) + b"\n")
    with pytest.raises(BatterWalkSettlementError, match="exact replayed ledger"):
        coverage_and_gradeability(plan=plan, walk_ledger=ledger, contract=_contract())


def test_unproven_source_error_is_rejected_until_typed_attempt_receipt_exists(tmp_path: Path) -> None:
    plan = _plan()
    loaded = load_forward_contract(root=ROOT, contract_path=ROOT / "config/shared_pa_forward_evidence_contract_v1.json")
    upstream = tmp_path / "shared-pa"
    source = SharedPAForwardLedger(
        upstream, plan=plan, contract_sha256=loaded["contract_sha256"],
        runtime_manifest_sha256="c" * 64, collector_code_sha256="b" * 64,
    )
    for side in ("home", "away"):
        source.append_exclusion(
            target=plan.targets[0], side=side, state="source_error",
            observed_at_utc=HORIZON.isoformat(), detail="timeout",
        )
    source.verify(require_complete_coverage=True)
    ledger = BatterWalkForwardLedger(
        tmp_path / "walk", contract=_contract(), plan=plan, source_ledger_root=upstream,
    )
    with pytest.raises(BatterWalkForwardLedgerError, match="typed retained failed-attempt"):
        ledger.append_exclusion(target=plan.targets[0], side="home")


def test_terminal_last_crash_retry_and_concurrent_identical_publish(tmp_path: Path, monkeypatch) -> None:
    plan, upstream, players = _upstream(tmp_path)
    outputs = _outputs(players)
    root = tmp_path / "walk"
    ledger = BatterWalkForwardLedger(root, contract=_contract(), plan=plan, source_ledger_root=upstream)
    import src.evaluation.batter_walk_forward_ledger as module
    real_publish = module._publish_once
    failed = False

    def fail_terminal(base, relative, payload):
        nonlocal failed
        if str(relative).replace("\\", "/").startswith("terminal/") and not failed:
            failed = True
            raise OSError("synthetic crash before terminal")
        return real_publish(base, relative, payload)

    monkeypatch.setattr(module, "_publish_once", fail_terminal)
    with pytest.raises(OSError, match="synthetic crash"):
        ledger.append_complete(target=plan.targets[0], side="home", player_outputs=outputs)
    assert not (root / "terminal").exists()
    monkeypatch.setattr(module, "_publish_once", real_publish)
    assert ledger.append_complete(target=plan.targets[0], side="home", player_outputs=outputs)

    results: list[bool] = []
    errors: list[BaseException] = []
    def publish():
        try:
            results.append(ledger.append_exclusion(target=plan.targets[0], side="away"))
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)
    threads = [threading.Thread(target=publish) for _ in range(2)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    assert not errors
    assert sorted(results) == [False, True]
    ledger.verify(require_complete_coverage=True)


def test_control_collection_modules_have_no_outcome_transport_or_prices() -> None:
    control_source = (ROOT / "src/evaluation/batter_walk_forward_control.py").read_text()
    ledger_source = (ROOT / "src/evaluation/batter_walk_forward_ledger.py").read_text()
    for forbidden in ("import requests", "urllib.request", "import httpx", "american_odds"):
        assert forbidden not in control_source
        assert forbidden not in ledger_source
