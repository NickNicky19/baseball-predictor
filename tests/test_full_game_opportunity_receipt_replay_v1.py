from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from src.evaluation import full_game_opportunity_receipt_replay_v1 as replay
from src.evaluation.forward_pitcher_context import ProbablePitcher
from src.evaluation.forward_pitcher_context_v2 import ForwardPitcherContextV2
from src.evaluation.shadow_capture_plan import CaptureTarget, ShadowCapturePlan


ROOT = Path(__file__).resolve().parents[1]
H = "a" * 64


def _target(
    *, official_game_date: str = "2026-07-28",
    official_start_utc: str = "2026-07-28T23:00:00Z",
    target_horizon_utc: str = "2026-07-28T19:00:00Z",
) -> dict:
    captured = CaptureTarget(
        mlb_game_pk=777001,
        official_game_date=official_game_date,
        official_start_time_utc=official_start_utc,
        entry_target_at_utc=target_horizon_utc,
        entry_hours=4,
    )
    return {
        "official_game_date": official_game_date,
        "mlb_game_pk": 777001,
        "official_start_utc": official_start_utc,
        "target_horizon_utc": target_horizon_utc,
        "target_id": captured.target_id,
        "batter_team_id": 117,
        "pitching_team_id": 121,
    }


def _shared_plan(target: dict) -> dict:
    capture_target = CaptureTarget(
        mlb_game_pk=target["mlb_game_pk"],
        official_game_date=target["official_game_date"],
        official_start_time_utc=target["official_start_utc"],
        entry_target_at_utc=target["target_horizon_utc"],
        entry_hours=4,
    )
    return ShadowCapturePlan(
        official_game_date=target["official_game_date"],
        entry_hours=4,
        policy_sha256=H,
        schedule_snapshot_sha256="b" * 64,
        targets=(capture_target,),
    ).to_dict()


def _entry(name: str, *, availability: str = "terminal_missing") -> dict:
    requirement = replay.REQUIREMENTS[name]
    unsigned = {
        "schema_version": replay.ENTRY_SCHEMA,
        "receipt_type": name,
        "source_schema_version": requirement["schema"],
        "source_name": requirement["source"],
        "protocol": requirement["protocol"],
        "availability": availability,
        "relative_path": None,
        "file_sha256": None,
        "raw_payload_sha256": None,
        "observed_at_utc": "2026-07-28T19:00:00Z",
        "terminal_reason": "not_delivered_to_audit",
    }
    return {**unsigned, "entry_sha256": replay.sha256_value(unsigned)}


def _inventory() -> dict:
    target = _target()
    unsigned = {
        "schema_version": replay.INVENTORY_SCHEMA,
        "source_commits": dict(replay.SOURCE_COMMITS),
        "target": target,
        "entries": [_entry(name) for name in replay.REQUIREMENTS],
        "research_only": True,
        "betting_authorized": False,
        "probability_consumption_authorized": False,
        "model_fitting_authorized": False,
    }
    return {**unsigned, "inventory_sha256": replay.sha256_value(unsigned)}


def _rehash_entry(entry: dict) -> None:
    unsigned = dict(entry)
    unsigned.pop("entry_sha256", None)
    entry["entry_sha256"] = replay.sha256_value(unsigned)


def _rehash_inventory(value: dict) -> None:
    unsigned = dict(value)
    unsigned.pop("inventory_sha256", None)
    value["inventory_sha256"] = replay.sha256_value(unsigned)


def _replace_entry(value: dict, name: str, replacement: dict) -> None:
    index = next(i for i, row in enumerate(value["entries"]) if row["receipt_type"] == name)
    value["entries"][index] = replacement
    _rehash_inventory(value)


def _retained_entry(tmp_path: Path, name: str, payload: bytes) -> dict:
    path = tmp_path / "receipts" / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    row = _entry(name, availability="retained")
    row.update({
        "relative_path": path.relative_to(tmp_path).as_posix(),
        "file_sha256": hashlib.sha256(payload).hexdigest(),
        "raw_payload_sha256": hashlib.sha256(payload).hexdigest() if replay.REQUIREMENTS[name]["raw"] else None,
        "terminal_reason": None,
    })
    _rehash_entry(row)
    return row


def _copy_authority(tmp_path: Path) -> None:
    path = tmp_path / replay.AUTHORITY_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((ROOT / replay.AUTHORITY_PATH).read_bytes())


def test_checked_in_authority_is_exact_and_terminal() -> None:
    authority = replay.load_authority(ROOT)
    assert authority["required_receipt_types"] == list(replay.REQUIREMENTS)
    result = replay.terminal_blocker(ROOT)
    assert result["terminal_state"] == "BLOCKED_LEGITIMATE_RETAINED_RECEIPTS_NOT_DELIVERED"
    assert result["missing_receipt_types"] == list(replay.REQUIREMENTS)
    assert result["probability_consumption_authorized"] is False
    assert not any("pmf" in key.lower() for key in result)


def test_authority_unknown_field_is_rejected() -> None:
    value = json.loads((ROOT / replay.AUTHORITY_PATH).read_text(encoding="utf-8"))
    value["surprise"] = True
    with pytest.raises(replay.ReceiptReplayError, match="schema changed"):
        replay.validate_authority_payload(value)


def test_authority_source_commit_mutation_is_rejected() -> None:
    value = json.loads((ROOT / replay.AUTHORITY_PATH).read_text(encoding="utf-8"))
    value["source_commits"]["projected_lineup_pr32"] = "0" * 40
    with pytest.raises(replay.ReceiptReplayError, match="source authority"):
        replay.validate_authority_payload(value)


def test_authority_byte_identity_is_fixed(tmp_path: Path) -> None:
    _copy_authority(tmp_path)
    path = tmp_path / replay.AUTHORITY_PATH
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(replay.ReceiptReplayError, match="certified source"):
        replay.load_authority(tmp_path)


def test_terminal_inventory_structure_is_valid_but_not_authorized(tmp_path: Path) -> None:
    assert replay.validate_inventory_structure(tmp_path, _inventory())["research_only"] is True


def test_missing_receipt_type_is_rejected(tmp_path: Path) -> None:
    value = _inventory()
    value["entries"].pop()
    _rehash_inventory(value)
    with pytest.raises(replay.ReceiptReplayError, match="coverage is incomplete"):
        replay.validate_inventory_structure(tmp_path, value)


def test_duplicate_singleton_receipt_is_rejected(tmp_path: Path) -> None:
    value = _inventory()
    value["entries"].append(copy.deepcopy(value["entries"][0]))
    _rehash_inventory(value)
    with pytest.raises(replay.ReceiptReplayError, match="coverage is incomplete or duplicated"):
        replay.validate_inventory_structure(tmp_path, value)


def test_entry_unknown_field_is_rejected(tmp_path: Path) -> None:
    value = _inventory()
    value["entries"][0]["surprise"] = True
    _rehash_inventory(value)
    with pytest.raises(replay.ReceiptReplayError, match="entry schema changed"):
        replay.validate_inventory_structure(tmp_path, value)


def test_entry_hash_mutation_is_rejected(tmp_path: Path) -> None:
    value = _inventory()
    value["entries"][0]["source_name"] = "mutated"
    _rehash_inventory(value)
    with pytest.raises(replay.ReceiptReplayError, match="self-hash differs"):
        replay.validate_inventory_structure(tmp_path, value)


@pytest.mark.parametrize("field", ["source_name", "protocol", "source_schema_version"])
def test_source_protocol_and_schema_mutations_are_rejected(tmp_path: Path, field: str) -> None:
    value = _inventory()
    value["entries"][0][field] = "mutated"
    _rehash_entry(value["entries"][0])
    _rehash_inventory(value)
    with pytest.raises(replay.ReceiptReplayError, match="schema, source, or protocol"):
        replay.validate_inventory_structure(tmp_path, value)


def test_terminal_missingness_cannot_claim_bytes(tmp_path: Path) -> None:
    value = _inventory()
    value["entries"][0]["relative_path"] = "fake.json"
    value["entries"][0]["file_sha256"] = "0" * 64
    _rehash_entry(value["entries"][0])
    _rehash_inventory(value)
    with pytest.raises(replay.ReceiptReplayError, match="cannot claim retained bytes"):
        replay.validate_inventory_structure(tmp_path, value)


def test_missed_terminal_state_cannot_precede_horizon(tmp_path: Path) -> None:
    value = _inventory()
    row = value["entries"][0]
    row["terminal_reason"] = "missed_after_horizon"
    row["observed_at_utc"] = "2026-07-28T18:59:59Z"
    _rehash_entry(row); _rehash_inventory(value)
    with pytest.raises(replay.ReceiptReplayError, match="declared before"):
        replay.validate_inventory_structure(tmp_path, value)


def test_source_error_terminal_state_cannot_follow_horizon(tmp_path: Path) -> None:
    value = _inventory()
    row = value["entries"][0]
    row["terminal_reason"] = "source_error_before_horizon"
    row["observed_at_utc"] = "2026-07-28T19:00:01Z"
    _rehash_entry(row); _rehash_inventory(value)
    with pytest.raises(replay.ReceiptReplayError, match="declared after"):
        replay.validate_inventory_structure(tmp_path, value)


def test_retained_receipt_after_horizon_is_rejected(tmp_path: Path) -> None:
    value = _inventory()
    payload = json.dumps(_shared_plan(value["target"]), sort_keys=True, separators=(",", ":")).encode()
    row = _retained_entry(tmp_path, "shared_plan", payload)
    row["observed_at_utc"] = "2026-07-28T19:00:01Z"
    _rehash_entry(row); _replace_entry(value, "shared_plan", row)
    with pytest.raises(replay.ReceiptReplayError, match="arrived after T-4"):
        replay.validate_inventory_structure(tmp_path, value)


def test_retained_receipt_hash_mutation_is_rejected(tmp_path: Path) -> None:
    value = _inventory()
    payload = json.dumps(_shared_plan(value["target"]), sort_keys=True, separators=(",", ":")).encode()
    row = _retained_entry(tmp_path, "shared_plan", payload)
    row["file_sha256"] = "0" * 64
    _rehash_entry(row); _replace_entry(value, "shared_plan", row)
    with pytest.raises(replay.ReceiptReplayError, match="bytes differ"):
        replay.validate_inventory_structure(tmp_path, value)


def test_retained_payload_schema_mutation_is_rejected(tmp_path: Path) -> None:
    value = _inventory()
    row = _retained_entry(tmp_path, "shared_plan", b'{"schema_version":"wrong"}')
    _replace_entry(value, "shared_plan", row)
    with pytest.raises(replay.ReceiptReplayError, match="payload schema differs"):
        replay.validate_inventory_structure(tmp_path, value)


def test_retained_payload_target_identity_mutation_is_rejected(tmp_path: Path) -> None:
    value = _inventory()
    wrong = dict(value["target"])
    wrong["mlb_game_pk"] = 999999
    wrong_target = CaptureTarget(
        mlb_game_pk=999999,
        official_game_date=wrong["official_game_date"],
        official_start_time_utc=wrong["official_start_utc"],
        entry_target_at_utc=wrong["target_horizon_utc"],
        entry_hours=4,
    )
    wrong["target_id"] = wrong_target.target_id
    payload = json.dumps(_shared_plan(wrong), sort_keys=True, separators=(",", ":")).encode()
    row = _retained_entry(tmp_path, "shared_plan", payload)
    _replace_entry(value, "shared_plan", row)
    with pytest.raises(replay.ReceiptReplayError, match="target identity differs"):
        replay.validate_inventory_structure(tmp_path, value)


def test_may_path_is_rejected_before_file_access(tmp_path: Path) -> None:
    value = _inventory(); row = _entry("shared_plan", availability="retained")
    row.update({"relative_path": "receipts/2026-05-01.json", "file_sha256": "0" * 64, "terminal_reason": None})
    _rehash_entry(row); _replace_entry(value, "shared_plan", row)
    with pytest.raises(replay.ReceiptReplayError, match="sealed May 2026"):
        replay.validate_inventory_structure(tmp_path, value)


def test_may_payload_is_rejected(tmp_path: Path) -> None:
    payload = json.dumps({
        "schema_version": replay.REQUIREMENTS["shared_plan"]["schema"],
        "note": "2026-05-12",
    }).encode()
    value = _inventory(); row = _retained_entry(tmp_path, "shared_plan", payload)
    _replace_entry(value, "shared_plan", row)
    with pytest.raises(replay.ReceiptReplayError, match="sealed May 2026"):
        replay.validate_inventory_structure(tmp_path, value)


@pytest.mark.parametrize(
    "sealed_representation",
    [
        "05-12-2026",
        "05/12/2026",
        "20260512",
        "2026-W20-2",
        "2026W202",
        "2026-132",
        "2026132",
        "May 12, 2026",
    ],
)
def test_alternate_may_representations_are_rejected(
    tmp_path: Path, sealed_representation: str
) -> None:
    payload = replay.canonical_bytes({
        "schema_version": replay.REQUIREMENTS["shared_plan"]["schema"],
        "note": sealed_representation,
    })
    value = _inventory()
    _replace_entry(value, "shared_plan", _retained_entry(tmp_path, "shared_plan", payload))
    with pytest.raises(replay.ReceiptReplayError, match="sealed May 2026"):
        replay.validate_inventory_structure(tmp_path, value)


def test_cross_midnight_utc_start_is_valid_for_official_game_date(
    tmp_path: Path,
) -> None:
    value = _inventory()
    value["target"] = _target(
        official_game_date="2026-07-28",
        official_start_utc="2026-07-29T02:10:00Z",
        target_horizon_utc="2026-07-28T22:10:00Z",
    )
    _rehash_inventory(value)
    assert replay.validate_inventory_structure(tmp_path, value)["target"] == value["target"]


def test_official_start_cannot_drift_beyond_utc_rollover(tmp_path: Path) -> None:
    value = _inventory()
    value["target"] = _target(
        official_game_date="2026-07-28",
        official_start_utc="2026-07-30T02:10:00Z",
        target_horizon_utc="2026-07-29T22:10:00Z",
    )
    _rehash_inventory(value)
    with pytest.raises(replay.ReceiptReplayError, match="UTC rollover"):
        replay.validate_inventory_structure(tmp_path, value)


def test_target_id_must_bind_computed_schedule_identity(tmp_path: Path) -> None:
    value = _inventory()
    value["target"]["target_id"] = "0" * 64
    _rehash_inventory(value)
    with pytest.raises(replay.ReceiptReplayError, match="immutable schedule identity"):
        replay.validate_inventory_structure(tmp_path, value)


@pytest.mark.parametrize(
    "noncanonical",
    [
        "2026-07-28 23:00:00Z",
        "2026-07-28T23:00Z",
        "2026-07-28T19:00:00-04:00",
        "2026-07-28T23:00:00.000000Z",
        "2026-07-28T23:00:00.1Z",
    ],
)
def test_noncanonical_target_timestamps_are_rejected(
    tmp_path: Path, noncanonical: str
) -> None:
    value = _inventory()
    value["target"]["official_start_utc"] = noncanonical
    _rehash_inventory(value)
    with pytest.raises(replay.ReceiptReplayError, match="canonical UTC"):
        replay.validate_inventory_structure(tmp_path, value)


def test_schema_label_without_complete_typed_plan_is_rejected(tmp_path: Path) -> None:
    payload = replay.canonical_bytes({
        "schema_version": replay.REQUIREMENTS["shared_plan"]["schema"],
    })
    value = _inventory()
    _replace_entry(value, "shared_plan", _retained_entry(tmp_path, "shared_plan", payload))
    with pytest.raises(replay.ReceiptReplayError, match="typed field set differs"):
        replay.validate_inventory_structure(tmp_path, value)


def test_duplicate_json_keys_are_rejected(tmp_path: Path) -> None:
    payload = (
        b'{"schema_version":"shadow-capture-plan-v1",'
        b'"schema_version":"shadow-capture-plan-v1"}'
    )
    value = _inventory()
    _replace_entry(value, "shared_plan", _retained_entry(tmp_path, "shared_plan", payload))
    with pytest.raises(replay.ReceiptReplayError, match="duplicate JSON key"):
        replay.validate_inventory_structure(tmp_path, value)


def test_inventory_duplicate_json_keys_are_rejected(tmp_path: Path) -> None:
    _copy_authority(tmp_path)
    value = _inventory()
    raw = replay.canonical_bytes(value)
    duplicate = raw[:-1] + b',"research_only":true}'
    path = tmp_path / "inventory_2026-07-28.json"
    path.write_bytes(duplicate)
    with pytest.raises(replay.ReceiptReplayError, match="duplicate JSON key"):
        replay.audit_inventory(
            root=tmp_path,
            inventory_path=path,
            expected_inventory_sha256=hashlib.sha256(duplicate).hexdigest(),
        )


def test_raw_companion_is_byte_bound_without_semantic_invention(tmp_path: Path) -> None:
    value = _inventory()
    raw_payload = {
        "dates": [{
            "games": [{
                "gamePk": value["target"]["mlb_game_pk"],
                "officialDate": value["target"]["official_game_date"],
                "gameDate": value["target"]["official_start_utc"],
                "gameType": "R",
                "teams": {
                    "home": {"team": {"id": 117, "name": "Batter Team"}, "probablePitcher": {"id": 9001}},
                    "away": {"team": {"id": 121, "name": "Pitching Team"}, "probablePitcher": {"id": 9002}},
                },
            }]
        }]
    }
    raw_bytes = json.dumps(raw_payload, sort_keys=True, separators=(",", ":")).encode()
    raw_row = _retained_entry(tmp_path, "starter_context_raw", raw_bytes)
    raw_hash = hashlib.sha256(raw_bytes).hexdigest()
    context = ForwardPitcherContextV2(
        target_id=value["target"]["target_id"],
        plan_sha256=H,
        captured_at_utc=value["target"]["target_horizon_utc"],
        source_name="mlb_statsapi_schedule",
        source_payload_sha256=raw_hash,
        mlb_game_pk=value["target"]["mlb_game_pk"],
        official_game_date=value["target"]["official_game_date"],
        official_start_time_utc=value["target"]["official_start_utc"],
        game_type="R",
        home_team_id=117,
        home_team_name="Batter Team",
        away_team_id=121,
        away_team_name="Pitching Team",
        home_probable_pitcher=ProbablePitcher(status="resolved", player_id=9001),
        away_probable_pitcher=ProbablePitcher(status="resolved", player_id=9002),
    )
    receipt_bytes = json.dumps(context.to_dict(), sort_keys=True, separators=(",", ":")).encode()
    receipt_row = _retained_entry(tmp_path, "starter_context_receipt", receipt_bytes)
    receipt_row["raw_payload_sha256"] = raw_hash
    _rehash_entry(receipt_row)
    _replace_entry(value, "starter_context_raw", raw_row)
    _replace_entry(value, "starter_context_receipt", receipt_row)
    assert replay.validate_inventory_structure(tmp_path, value)["research_only"] is True


def test_raw_companion_hash_mutation_is_rejected(tmp_path: Path) -> None:
    value = _inventory()
    row = _retained_entry(tmp_path, "starter_context_raw", b'{"opaque":"retained raw"}')
    row["raw_payload_sha256"] = "0" * 64
    _rehash_entry(row); _replace_entry(value, "starter_context_raw", row)
    with pytest.raises(replay.ReceiptReplayError, match="raw companion identity"):
        replay.validate_inventory_structure(tmp_path, value)


def test_optional_failed_stats_attempt_may_be_absent(tmp_path: Path) -> None:
    value = _inventory()
    value["entries"] = [row for row in value["entries"] if row["receipt_type"] != "batter_stats_terminal_attempt"]
    _rehash_inventory(value)
    assert replay.validate_inventory_structure(tmp_path, value)["research_only"] is True


def test_inventory_external_hash_is_required_and_authority_still_blocks(tmp_path: Path) -> None:
    _copy_authority(tmp_path)
    value = _inventory()
    path = tmp_path / "inventory_2026-07-28.json"
    raw = replay.canonical_bytes(value)
    path.write_bytes(raw)
    result = replay.audit_inventory(
        root=tmp_path,
        inventory_path=path,
        expected_inventory_sha256=hashlib.sha256(raw).hexdigest(),
    )
    assert result["terminal_state"] == "BLOCKED_UNBOUND_EXTERNAL_RECEIPT_AUTHORITY"
    assert result["semantic_replay_authorized"] is False
    assert result["probability_consumption_authorized"] is False
    assert result["model_fitting_authorized"] is False


def test_inventory_external_hash_mutation_is_rejected(tmp_path: Path) -> None:
    _copy_authority(tmp_path)
    value = _inventory(); path = tmp_path / "inventory_2026-07-28.json"
    path.write_bytes(replay.canonical_bytes(value))
    with pytest.raises(replay.ReceiptReplayError, match="external expected bytes"):
        replay.audit_inventory(root=tmp_path, inventory_path=path, expected_inventory_sha256="0" * 64)


def test_target_horizon_must_be_exactly_t4(tmp_path: Path) -> None:
    value = _inventory(); value["target"]["target_horizon_utc"] = "2026-07-28T18:59:59Z"
    _rehash_inventory(value)
    with pytest.raises(replay.ReceiptReplayError, match="exactly T-4"):
        replay.validate_inventory_structure(tmp_path, value)


def test_authorization_mutation_is_rejected(tmp_path: Path) -> None:
    value = _inventory(); value["model_fitting_authorized"] = True; _rehash_inventory(value)
    with pytest.raises(replay.ReceiptReplayError, match="authorization was widened"):
        replay.validate_inventory_structure(tmp_path, value)


def test_release_manifest_hashes_match_current_bytes() -> None:
    manifest = json.loads(
        (ROOT / "reports/full_game_opportunity_receipt_replay_v1_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["status"] == (
        "LOCAL_VALIDATOR_REPAIRED_LINUX_GATE_DECLARED_"
        "REAL_RECEIPT_REPLAY_TERMINALLY_BLOCKED"
    )
    assert manifest["base_commit"] == "b95f3589a99d4c0ca6055462d492a4e3f0593c09"
    for row in manifest["files"]:
        raw = (ROOT / row["path"]).read_bytes()
        assert len(raw) == row["size_bytes"]
        assert hashlib.sha256(raw).hexdigest() == row["sha256"]
