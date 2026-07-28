from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import src.evaluation.shared_pa_batter_evidence_inventory as inventory_module
import scripts.run_shared_pa_batter_evidence_inventory as inventory_runner
from src.evaluation.projected_lineup_contract import canonical_bytes, sha256_value
from src.evaluation.prospective_batter_opportunity_history import (
    ProspectiveOpportunityHistoryLedger,
)
from src.evaluation.shared_pa_batter_evidence_inventory import (
    BatterEvidenceInventoryError,
    assemble_side_inventory,
)
from scripts.run_shared_pa_batter_evidence_inventory import main as inventory_main
from tests.test_prospective_batter_opportunity_history import (
    GAME_DATE,
    _plan,
    _source_roster_ledger,
)
from src.evaluation.shadow_capture_plan import CaptureTarget, ShadowCapturePlan


def _tree_hash(root):
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }


def _sources(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "fchmod", lambda *_: None, raising=False)
    created = _source_roster_ledger(tmp_path / "builder")
    plan = created.plan
    plan_dir = tmp_path / "plans"
    plan_dir.mkdir()
    (plan_dir / f"{GAME_DATE}.plan.json").write_bytes(
        canonical_bytes(plan.to_dict()) + b"\n"
    )
    roster_base = tmp_path / "rosters"
    roster_root = roster_base / GAME_DATE / plan.plan_sha256
    roster_root.parent.mkdir(parents=True)
    shutil.move(str(created.root), roster_root)
    created.root = roster_root.resolve()
    history_base = tmp_path / "history"
    history_root = history_base / GAME_DATE / plan.plan_sha256
    history = ProspectiveOpportunityHistoryLedger(
        history_root,
        collection_epoch_date="2026-07-01",
        contract_sha256="5" * 64,
        collector_code_sha256="6" * 64,
        evidence_scope_sha256="7" * 64,
    )
    history.append_plan(
        _plan(created),
        published_at_utc="2026-07-27T14:00:00Z",
        source_roster_ledger=created,
    )
    return plan, plan_dir, roster_base, history_base


def test_exact_inventory_is_publish_once_and_does_not_modify_sources(tmp_path, monkeypatch):
    plan, plan_dir, roster_root, history_root = _sources(tmp_path, monkeypatch)
    before = _tree_hash(tmp_path)
    kwargs = dict(
        official_game_date=GAME_DATE,
        plan_path=plan_dir / f"{GAME_DATE}.plan.json",
        roster_ledger_root=roster_root,
        history_ledger_root=history_root,
        output_root=tmp_path / "inventory",
        target_id=plan.targets[0].target_id,
        side="away",
        observed_at_utc="2026-07-27T14:00:00Z",
    )
    first = assemble_side_inventory(**kwargs)
    second = assemble_side_inventory(**kwargs)
    assert first == second
    assert first["terminal_state"] == "t4_inventory_published"
    assert first["source_ledgers_modified"] is False
    states = {row["surface_number"]: row["state"] for row in first["surfaces"]}
    assert states[4] == states[8] == states[9] == "captured_exact_bytes"
    assert states[3] == states[10] == states[13] == states[14] == states[15] == "terminal_missing"
    assert all("source_authority_root" not in row for row in first["files"])
    assert all(str(tmp_path) not in json.dumps(row) for row in first["files"])
    after = _tree_hash(tmp_path)
    assert {key: value for key, value in after.items() if not key.startswith("inventory/")} == before


def test_roster_raw_mutation_fails_before_inventory_publication(tmp_path, monkeypatch):
    plan, plan_dir, roster_root, history_root = _sources(tmp_path, monkeypatch)
    raw = next((roster_root / GAME_DATE / plan.plan_sha256 / "raw").glob("*.json"))
    raw.write_bytes(raw.read_bytes() + b" ")
    with pytest.raises(ValueError):
        assemble_side_inventory(
            official_game_date=GAME_DATE,
            plan_path=plan_dir / f"{GAME_DATE}.plan.json",
            roster_ledger_root=roster_root,
            history_ledger_root=history_root,
            output_root=tmp_path / "inventory",
            target_id=plan.targets[0].target_id,
            side="away",
            observed_at_utc="2026-07-27T14:00:00Z",
        )
    assert not (tmp_path / "inventory").exists()


@pytest.mark.parametrize("sealed", ["2026-05-01", "2026-05-31"])
def test_may_is_rejected_before_any_source_path_read(tmp_path, sealed):
    with pytest.raises(BatterEvidenceInventoryError, match="before source paths"):
        assemble_side_inventory(
            official_game_date=sealed,
            plan_path=tmp_path / "must-not-be-read",
            roster_ledger_root=tmp_path / "must-not-be-read-roster",
            history_ledger_root=tmp_path / "must-not-be-read-history",
            output_root=tmp_path / "inventory",
            target_id="not-read",
            side="away",
            observed_at_utc=f"{sealed}T00:00:00Z",
        )
    assert list(tmp_path.iterdir()) == []


def test_late_tick_records_terminal_missing_without_reading_ledgers(tmp_path, monkeypatch):
    plan, plan_dir, _, _ = _sources(tmp_path, monkeypatch)
    output = tmp_path / "late-inventory"
    value = assemble_side_inventory(
        official_game_date=GAME_DATE,
        plan_path=plan_dir / f"{GAME_DATE}.plan.json",
        roster_ledger_root=tmp_path / "absent-roster",
        history_ledger_root=tmp_path / "absent-history",
        output_root=output,
        target_id=plan.targets[0].target_id,
        side="away",
        observed_at_utc="2026-07-27T14:00:00.000001Z",
    )
    assert value["terminal_state"] == "missed_before_t4_inventory"
    assert value["missing_surface_numbers"] == list(range(3, 18))
    assert value["historical_backfill_authorized"] is False


def test_existing_inventory_and_object_mutations_fail_closed(tmp_path, monkeypatch):
    plan, plan_dir, roster_root, history_root = _sources(tmp_path, monkeypatch)
    kwargs = dict(
        official_game_date=GAME_DATE,
        plan_path=plan_dir / f"{GAME_DATE}.plan.json",
        roster_ledger_root=roster_root,
        history_ledger_root=history_root,
        output_root=tmp_path / "inventory",
        target_id=plan.targets[0].target_id,
        side="away",
        observed_at_utc="2026-07-27T14:00:00Z",
    )
    value = assemble_side_inventory(**kwargs)
    inventory_path = next((tmp_path / "inventory").rglob("terminal.json"))
    forged = json.loads(inventory_path.read_text(encoding="utf-8"))
    forged["side"] = "home"
    inventory_path.write_bytes(canonical_bytes(forged) + b"\n")
    with pytest.raises(BatterEvidenceInventoryError, match="hash differs"):
        assemble_side_inventory(**kwargs)
    forged = dict(value)
    forged.pop("inventory_sha256")
    forged["betting_authorized"] = True
    forged["inventory_sha256"] = sha256_value(forged)
    inventory_path.write_bytes(canonical_bytes(forged) + b"\n")
    with pytest.raises(BatterEvidenceInventoryError, match="schema or safety fields"):
        assemble_side_inventory(**kwargs)
    forged = json.loads(json.dumps(value))
    forged.pop("inventory_sha256")
    forged["roster_protocol"]["contract_sha256"] = "9" * 64
    forged["inventory_sha256"] = sha256_value(forged)
    inventory_path.write_bytes(canonical_bytes(forged) + b"\n")
    with pytest.raises(BatterEvidenceInventoryError, match="protocol-to-manifest binding"):
        assemble_side_inventory(**kwargs)
    forged = json.loads(json.dumps(value))
    forged.pop("inventory_sha256")
    surface_eight = next(row for row in forged["surfaces"] if row["surface_number"] == 8)
    surface_eight["state"] = "terminal_missing"
    forged["inventory_sha256"] = sha256_value(forged)
    inventory_path.write_bytes(canonical_bytes(forged) + b"\n")
    with pytest.raises(BatterEvidenceInventoryError, match="roster surface binding"):
        assemble_side_inventory(**kwargs)
    forged = json.loads(json.dumps(value))
    forged.pop("inventory_sha256")
    plan_row = next(row for row in forged["files"] if row["role"] == "immutable_t4_plan")
    plan_row["source_authority"] = "immutable_projected_lineup_roster_ledger"
    forged["inventory_sha256"] = sha256_value(forged)
    inventory_path.write_bytes(canonical_bytes(forged) + b"\n")
    with pytest.raises(BatterEvidenceInventoryError, match="file semantics differ"):
        assemble_side_inventory(**kwargs)
    forged = json.loads(json.dumps(value))
    forged.pop("inventory_sha256")
    plan_row = next(row for row in forged["files"] if row["role"] == "immutable_t4_plan")
    forged_plan_raw = b"{}\n"
    forged_plan_sha = hashlib.sha256(forged_plan_raw).hexdigest()
    forged_object = inventory_path.parent / "objects" / forged_plan_sha[:2] / forged_plan_sha
    forged_object.parent.mkdir(parents=True, exist_ok=True)
    forged_object.write_bytes(forged_plan_raw)
    plan_row["size"] = len(forged_plan_raw)
    plan_row["sha256"] = forged_plan_sha
    plan_row["object_path"] = forged_object.relative_to(inventory_path.parent).as_posix()
    forged["inventory_sha256"] = sha256_value(forged)
    inventory_path.write_bytes(canonical_bytes(forged) + b"\n")
    with pytest.raises(BatterEvidenceInventoryError, match="copied plan is invalid"):
        assemble_side_inventory(**kwargs)
    inventory_path.write_bytes(canonical_bytes(value) + b"\n")
    object_path = inventory_path.parent / value["files"][0]["object_path"]
    object_path.write_bytes(object_path.read_bytes() + b"mutation")
    with pytest.raises(BatterEvidenceInventoryError, match="object bytes differ"):
        assemble_side_inventory(**kwargs)


def test_source_symlink_is_rejected(tmp_path, monkeypatch):
    plan, plan_dir, roster_root, history_root = _sources(tmp_path, monkeypatch)
    history = history_root / GAME_DATE / plan.plan_sha256
    target = history / "linked.json"
    try:
        target.symlink_to(history / "ledger_manifest.json")
    except OSError:
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(BatterEvidenceInventoryError, match="symlink"):
        assemble_side_inventory(
            official_game_date=GAME_DATE,
            plan_path=plan_dir / f"{GAME_DATE}.plan.json",
            roster_ledger_root=roster_root,
            history_ledger_root=history_root,
            output_root=tmp_path / "inventory",
            target_id=plan.targets[0].target_id,
            side="away",
            observed_at_utc="2026-07-27T14:00:00Z",
        )


def test_linked_output_ancestor_is_rejected(tmp_path, monkeypatch):
    plan, plan_dir, roster_root, history_root = _sources(tmp_path, monkeypatch)
    real_output = tmp_path / "real-output"
    real_output.mkdir()
    linked_output = tmp_path / "linked-output"
    try:
        linked_output.symlink_to(real_output, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation is unavailable")
    with pytest.raises(BatterEvidenceInventoryError, match="linked ancestor"):
        assemble_side_inventory(
            official_game_date=GAME_DATE,
            plan_path=plan_dir / f"{GAME_DATE}.plan.json",
            roster_ledger_root=roster_root,
            history_ledger_root=history_root,
            output_root=linked_output,
            target_id=plan.targets[0].target_id,
            side="away",
            observed_at_utc="2026-07-27T14:00:00Z",
        )
    assert list(real_output.iterdir()) == []


def test_duplicate_plan_key_fails_closed_at_cli_ingress(tmp_path, capsys):
    plan_dir = tmp_path / "plans"
    plan_dir.mkdir()
    (plan_dir / f"{GAME_DATE}.plan.json").write_bytes(
        b'{"official_game_date":"2026-07-27","official_game_date":"2026-07-27"}'
    )
    result = inventory_main(
        [
            "--official-date",
            GAME_DATE,
            "--plan-dir",
            str(plan_dir),
            "--roster-ledger-root",
            str(tmp_path / "rosters"),
            "--history-ledger-root",
            str(tmp_path / "history"),
            "--inventory-root",
            str(tmp_path / "inventory"),
        ]
    )
    assert result == 2
    assert "duplicate JSON key" in capsys.readouterr().err
    assert not (tmp_path / "inventory").exists()


def test_collection_hash_manifest_rejects_duplicate_keys(tmp_path):
    checker_path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "check_shared_pa_batter_evidence_collection_manifest.py"
    )
    spec = importlib.util.spec_from_file_location("collection_manifest_checker", checker_path)
    assert spec is not None and spec.loader is not None
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"schema_version":"first","schema_version":"second"}', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate JSON key"):
        checker.load_manifest(manifest)


def test_cli_rejects_plan_replacement_that_adds_target_mid_tick(tmp_path, monkeypatch, capsys):
    plan, plan_dir, roster_root, history_root = _sources(tmp_path, monkeypatch)
    plan_path = plan_dir / f"{GAME_DATE}.plan.json"
    second_target = CaptureTarget(
        mlb_game_pk=999999,
        official_game_date=GAME_DATE,
        official_start_time_utc="2026-07-27T20:00:00Z",
        entry_target_at_utc="2026-07-27T16:00:00Z",
        entry_hours=4,
    )
    replacement = ShadowCapturePlan(
        official_game_date=plan.official_game_date,
        entry_hours=plan.entry_hours,
        policy_sha256=plan.policy_sha256,
        schedule_snapshot_sha256=plan.schedule_snapshot_sha256,
        targets=plan.targets + (second_target,),
    )
    real_assemble = inventory_runner.assemble_side_inventory
    calls = 0

    def replace_after_first(**kwargs):
        nonlocal calls
        result = real_assemble(**kwargs)
        calls += 1
        if calls == 1:
            plan_path.write_bytes(canonical_bytes(replacement.to_dict()) + b"\n")
        return result

    monkeypatch.setattr(inventory_runner, "assemble_side_inventory", replace_after_first)
    result = inventory_runner.main(
        [
            "--official-date", GAME_DATE,
            "--plan-dir", str(plan_dir),
            "--roster-ledger-root", str(roster_root),
            "--history-ledger-root", str(history_root),
            "--inventory-root", str(tmp_path / "inventory"),
            "--observed-at-utc", "2026-07-27T14:00:00Z",
        ]
    )
    assert result == 2
    assert "plan" in capsys.readouterr().err.lower()


def test_duplicate_key_in_source_ledger_fails_before_replay_or_copy(tmp_path, monkeypatch):
    plan, plan_dir, roster_root, history_root = _sources(tmp_path, monkeypatch)
    manifest = history_root / GAME_DATE / plan.plan_sha256 / "ledger_manifest.json"
    raw = manifest.read_bytes().rstrip()
    manifest.write_bytes(raw[:-1] + b',"contract_sha256":"' + b"5" * 64 + b'"}\n')
    with pytest.raises(BatterEvidenceInventoryError, match="duplicate JSON key"):
        assemble_side_inventory(
            official_game_date=GAME_DATE,
            plan_path=plan_dir / f"{GAME_DATE}.plan.json",
            roster_ledger_root=roster_root,
            history_ledger_root=history_root,
            output_root=tmp_path / "inventory",
            target_id=plan.targets[0].target_id,
            side="away",
            observed_at_utc="2026-07-27T14:00:00Z",
        )
    assert not (tmp_path / "inventory").exists()


def test_concurrent_history_tree_change_fails_before_manifest(tmp_path, monkeypatch):
    plan, plan_dir, roster_root, history_root = _sources(tmp_path, monkeypatch)
    real_snapshot = inventory_module._source_snapshot
    history_authority = history_root / GAME_DATE / plan.plan_sha256
    history_calls = 0

    def racing_snapshot(root):
        nonlocal history_calls
        value = real_snapshot(root)
        if Path(root) == history_authority:
            history_calls += 1
            if history_calls == 1:
                (history_authority / "concurrent-marker").write_bytes(b"changed")
        return value

    monkeypatch.setattr(inventory_module, "_source_snapshot", racing_snapshot)
    with pytest.raises(BatterEvidenceInventoryError, match="changed during verification"):
        assemble_side_inventory(
            official_game_date=GAME_DATE,
            plan_path=plan_dir / f"{GAME_DATE}.plan.json",
            roster_ledger_root=roster_root,
            history_ledger_root=history_root,
            output_root=tmp_path / "inventory",
            target_id=plan.targets[0].target_id,
            side="away",
            observed_at_utc="2026-07-27T14:00:00Z",
        )
    assert not list((tmp_path / "inventory").rglob("terminal.json"))


def test_source_published_after_observation_is_rejected(tmp_path, monkeypatch):
    plan, plan_dir, roster_root, history_root = _sources(tmp_path, monkeypatch)
    with pytest.raises(BatterEvidenceInventoryError, match="later than inventory observation"):
        assemble_side_inventory(
            official_game_date=GAME_DATE,
            plan_path=plan_dir / f"{GAME_DATE}.plan.json",
            roster_ledger_root=roster_root,
            history_ledger_root=history_root,
            output_root=tmp_path / "inventory",
            target_id=plan.targets[0].target_id,
            side="away",
            observed_at_utc="2026-07-27T13:59:59Z",
        )
    assert not list((tmp_path / "inventory").rglob("terminal.json"))


def test_inventory_and_late_missing_compete_for_one_atomic_terminal(tmp_path, monkeypatch):
    plan, plan_dir, roster_root, history_root = _sources(tmp_path, monkeypatch)
    real_publish = inventory_module._publish_once
    barrier = threading.Barrier(2)

    def synchronized_publish(path, payload):
        if Path(path).name == "terminal.json":
            barrier.wait(timeout=5)
        return real_publish(path, payload)

    monkeypatch.setattr(inventory_module, "_publish_once", synchronized_publish)
    common = dict(
        official_game_date=GAME_DATE,
        plan_path=plan_dir / f"{GAME_DATE}.plan.json",
        roster_ledger_root=roster_root,
        history_ledger_root=history_root,
        output_root=tmp_path / "inventory",
        target_id=plan.targets[0].target_id,
        side="away",
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                assemble_side_inventory,
                **common,
                observed_at_utc="2026-07-27T14:00:00Z",
            ),
            executor.submit(
                assemble_side_inventory,
                **common,
                observed_at_utc="2026-07-27T14:00:00.000001Z",
            ),
        ]
        outcomes = []
        for future in futures:
            try:
                outcomes.append(future.result())
            except BatterEvidenceInventoryError:
                outcomes.append("conflict")
    assert outcomes.count("conflict") == 1
    terminals = list((tmp_path / "inventory").rglob("terminal.json"))
    assert len(terminals) == 1
    terminal = json.loads(terminals[0].read_text(encoding="utf-8"))
    assert terminal["schema_version"] in {
        inventory_module.SCHEMA_VERSION,
        inventory_module.TERMINAL_SCHEMA_VERSION,
    }


def test_cli_rejects_may_before_constructing_or_reading_source_paths(tmp_path, capsys):
    result = inventory_main(
        [
            "--official-date",
            "2026-05-01",
            "--plan-dir",
            str(tmp_path / "plans-must-not-be-read"),
            "--roster-ledger-root",
            str(tmp_path / "rosters-must-not-be-read"),
            "--history-ledger-root",
            str(tmp_path / "history-must-not-be-read"),
            "--inventory-root",
            str(tmp_path / "must-not-be-written"),
        ]
    )
    assert result == 2
    assert "sealed May 2026" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


def test_missing_plan_attempt_is_durable_without_fabricated_game_identity(tmp_path, capsys):
    arguments = [
        "--official-date",
        GAME_DATE,
        "--plan-dir",
        str(tmp_path / "plans"),
        "--roster-ledger-root",
        str(tmp_path / "rosters"),
        "--history-ledger-root",
        str(tmp_path / "history"),
        "--inventory-root",
        str(tmp_path / "inventory"),
        "--observed-at-utc",
        "2026-07-27T18:00:00Z",
    ]
    assert inventory_main(arguments) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["state"] == "no_immutable_plan"
    attempt = first["durable_attempt"]["attempt"]
    assert attempt["game_identity_available"] is False
    assert attempt["side_identity_available"] is False
    assert first["durable_attempt"]["terminal"] is None
    attempt_path = tmp_path / "inventory" / first["durable_attempt"]["attempt_path"]
    original = attempt_path.read_bytes()
    assert inventory_main(arguments) == 0
    capsys.readouterr()
    assert attempt_path.read_bytes() == original
    later_same_bucket = list(arguments)
    later_same_bucket[-1] = "2026-07-27T18:59:59Z"
    assert inventory_main(later_same_bucket) == 0
    capsys.readouterr()
    assert len(list(attempt_path.parent.glob("*.json"))) == 1


def test_missing_plan_attempt_observation_must_lie_in_signed_bucket(tmp_path, capsys):
    arguments = [
        "--official-date", GAME_DATE,
        "--plan-dir", str(tmp_path / "plans"),
        "--roster-ledger-root", str(tmp_path / "rosters"),
        "--history-ledger-root", str(tmp_path / "history"),
        "--inventory-root", str(tmp_path / "inventory"),
        "--observed-at-utc", "2026-07-27T18:10:00Z",
    ]
    assert inventory_main(arguments) == 0
    value = json.loads(capsys.readouterr().out)["durable_attempt"]
    path = tmp_path / "inventory" / value["attempt_path"]
    forged = json.loads(path.read_text(encoding="utf-8"))
    forged.pop("attempt_sha256")
    forged["observed_at_utc"] = "2026-07-27T17:59:59.000000Z"
    forged["attempt_sha256"] = sha256_value(forged)
    path.write_bytes(canonical_bytes(forged) + b"\n")
    assert inventory_main(arguments) == 2
    assert "existing missing-plan attempt differs" in capsys.readouterr().err


def test_missing_plan_becomes_date_terminal_only_at_conservative_utc_boundary(tmp_path, capsys):
    result = inventory_main(
        [
            "--official-date",
            GAME_DATE,
            "--plan-dir",
            str(tmp_path / "plans"),
            "--roster-ledger-root",
            str(tmp_path / "rosters"),
            "--history-ledger-root",
            str(tmp_path / "history"),
            "--inventory-root",
            str(tmp_path / "inventory"),
            "--observed-at-utc",
            "2026-07-29T00:00:00Z",
        ]
    )
    assert result == 0
    value = json.loads(capsys.readouterr().out)["durable_attempt"]
    assert value["terminal"]["terminal_state"] == "date_ended_without_immutable_plan"
    assert value["terminal"]["coverage_denominator_available"] is False
    assert value["terminal"]["game_identity_available"] is False
    terminal_path = tmp_path / "inventory" / GAME_DATE / "plan_missing" / "terminal_plan_absent.json"
    assert terminal_path.is_file()
    original = terminal_path.read_bytes()
    assert inventory_main(
        [
            "--official-date", GAME_DATE,
            "--plan-dir", str(tmp_path / "plans"),
            "--roster-ledger-root", str(tmp_path / "rosters"),
            "--history-ledger-root", str(tmp_path / "history"),
            "--inventory-root", str(tmp_path / "inventory"),
            "--observed-at-utc", "2026-07-30T00:00:00Z",
        ]
    ) == 0
    capsys.readouterr()
    assert terminal_path.read_bytes() == original


def test_forged_rehashed_plan_absence_terminal_fails_closed(tmp_path, capsys):
    arguments = [
        "--official-date", GAME_DATE,
        "--plan-dir", str(tmp_path / "plans"),
        "--roster-ledger-root", str(tmp_path / "rosters"),
        "--history-ledger-root", str(tmp_path / "history"),
        "--inventory-root", str(tmp_path / "inventory"),
        "--observed-at-utc", "2026-07-29T00:00:00Z",
    ]
    assert inventory_main(arguments) == 0
    capsys.readouterr()
    terminal_path = tmp_path / "inventory" / GAME_DATE / "plan_missing" / "terminal_plan_absent.json"
    forged = json.loads(terminal_path.read_text(encoding="utf-8"))
    forged.pop("terminal_sha256")
    forged["betting_authorized"] = True
    forged["terminal_sha256"] = sha256_value(forged)
    terminal_path.write_bytes(canonical_bytes(forged) + b"\n")
    assert inventory_main(arguments) == 2
    assert "terminal differs" in capsys.readouterr().err


def test_unattended_adjacent_scan_reaches_terminal_without_timezone_database(tmp_path, capsys):
    assert inventory_main(
        [
            "--plan-dir", str(tmp_path / "plans"),
            "--roster-ledger-root", str(tmp_path / "rosters"),
            "--history-ledger-root", str(tmp_path / "history"),
            "--inventory-root", str(tmp_path / "inventory"),
            "--observed-at-utc", "2026-07-29T00:00:00Z",
        ]
    ) == 0
    capsys.readouterr()
    assert (
        tmp_path / "inventory" / GAME_DATE / "plan_missing" / "terminal_plan_absent.json"
    ).is_file()


def test_current_game_history_can_never_claim_prior_game_surfaces():
    rows = inventory_module._surface_rows(
        roster_terminal={"terminal_state": "captured_active_roster", "roster_raw": {}}
    )
    states = {row["surface_number"]: row for row in rows}
    assert states[13]["state"] == "terminal_missing"
    assert states[14]["state"] == "terminal_missing"
    assert "surfaces 10-12" in states[13]["detail"]


def test_code_only_units_preserve_network_and_runtime_write_boundaries():
    root = Path(__file__).resolve().parents[1]
    deploy = root / "deploy" / "pr32_batter_evidence"
    inventory = (deploy / "baseball-pr32-batter-evidence-inventory.service").read_text(
        encoding="utf-8"
    )
    timer = (deploy / "baseball-pr32-batter-evidence-inventory.timer").read_text(
        encoding="utf-8"
    )
    denylist = {
        "PYTHONPATH", "PYTHONHOME", "PYTHONINSPECT", "PYTHONSTARTUP",
        "PYTHONWARNINGS", "PYTHONHASHSEED", "PYTHONBREAKPOINT", "PYTHONCASEOK",
        "PYTHONDONTWRITEBYTECODE", "PYTHONUSERBASE", "SSL_CERT_FILE", "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "HTTPS_PROXY", "HTTP_PROXY",
        "ALL_PROXY", "NO_PROXY", "LD_PRELOAD", "LD_LIBRARY_PATH",
    }
    unset = next(line for line in inventory.splitlines() if line.startswith("UnsetEnvironment="))
    assert set(unset.removeprefix("UnsetEnvironment=").split()) == denylist
    assert "WorkingDirectory=/opt/baseball-predictor-pr32-batter-evidence/current" in inventory
    assert "current-venv/bin/python -I -B" in inventory
    assert "/usr/bin/python3" not in inventory
    assert "User=baseball-shadow" in inventory
    assert "PrivateNetwork=true" in inventory
    assert "ReadOnlyPaths=/srv/baseball-shadow/pitcher-receipts/plans" in inventory
    assert "ReadWritePaths=/srv/baseball-shadow/pr32-batter-evidence-inventory" in inventory
    assert "run_forward_pitcher_context_collector.py" not in inventory
    assert "systemctl" not in inventory
    assert "--watch" not in inventory
    assert "EnvironmentFile" not in inventory
    assert "ConditionPath" not in inventory
    assert "%E" not in inventory and "${" not in inventory
    assert "AccuracySec=1s" in timer
    assert "RandomizedDelaySec=0" in timer
    assert not (deploy / "baseball-prospective-batter-opportunity.service").exists()
    assert not (deploy / "baseball-prospective-batter-opportunity.timer").exists()
