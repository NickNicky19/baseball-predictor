from __future__ import annotations

import copy
import json
from pathlib import Path

import pandas as pd
import pytest

from scripts.develop_shared_pa_contact_eb_offset_2023 import (
    ARM_ORDER,
    COMPARATOR_ORDER,
    feature_contracts,
    join_contact,
    load_protocol,
    verify_execution_lock,
)
from src.evaluation.shared_pa_contact_quality import FEATURE_COLUMNS


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "config" / "shared_pa_contact_eb_offset_development_2023_v1.json"
LOCK = ROOT / "config" / "shared_pa_contact_eb_offset_execution_lock_2023_v1.json"


def _write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def test_locked_protocol_derives_one_exact_nonoverlapping_feature_surface() -> None:
    protocol = load_protocol(PROTOCOL)
    base_protocol, registry, features = feature_contracts(protocol)
    numeric_contact = [name for name in FEATURE_COLUMNS if name != "history_contact_max_source_date"]
    assert len(features) == len(base_protocol["candidate"]["features"]) + 43
    assert features[-43:] == numeric_contact
    assert "history_contact_max_source_date" not in features
    assert len(features) == len(set(features))
    assert registry["qualification"]["predictive_improvement_claimed"] is False
    assert ARM_ORDER[1] == "rejected_true_eb_offset_control"
    assert tuple(ARM_ORDER[1:]) == COMPARATOR_ORDER


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        ("open_2024", "2024 boundary"),
        ("market_rescue", "market rescue"),
        ("lineup", "target lineup"),
        ("betting", "betting authorization"),
    ],
)
def test_protocol_boundary_mutations_fail_closed(
    tmp_path: Path, mutation: str, match: str
) -> None:
    value = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    if mutation == "open_2024":
        value["protected_boundaries"]["never_open_2024_for_this_candidate"] = False
    elif mutation == "market_rescue":
        value["markets"]["market_rescue_forbidden"] = False
    elif mutation == "lineup":
        value["pa_opportunity"]["target_game_lineup_slot_consumed"] = True
    else:
        value["protected_boundaries"]["betting_authorized"] = True
    path = tmp_path / "protocol.json"
    _write(path, value)
    with pytest.raises(ValueError, match=match):
        load_protocol(path)


def test_contact_contract_hash_mutation_fails_before_data_use(tmp_path: Path) -> None:
    value = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    value["feature_sources"]["contact_contract"]["sha256"] = "0" * 64
    path = tmp_path / "protocol.json"
    _write(path, value)
    with pytest.raises(ValueError, match="feature-source identity mismatch"):
        feature_contracts(load_protocol(path))


def test_contact_join_preserves_identity_and_valid_missing_rates() -> None:
    base = pd.DataFrame(
        [
            {"season": 2023, "game_date": "2023-04-01", "game_pk": 1, "player_id": 10, "out_pa": 4},
            {"season": 2023, "game_date": "2023-04-02", "game_pk": 2, "player_id": 10, "out_pa": 4},
        ]
    )
    contact = base[["season", "game_date", "game_pk", "player_id"]].copy()
    for name in FEATURE_COLUMNS:
        contact[name] = None if name.endswith("_rate") or "_rate_" in name else 0
    joined = join_contact(base, contact)
    assert len(joined) == len(base)
    assert list(joined["out_pa"]) == [4, 4]
    assert all(name in joined for name in FEATURE_COLUMNS)


def test_contact_join_missing_identity_fails_closed() -> None:
    base = pd.DataFrame(
        [{"season": 2023, "game_date": "2023-04-01", "game_pk": 1, "player_id": 10}]
    )
    contact = pd.DataFrame(
        [{"season": 2023, "game_date": "2023-04-02", "game_pk": 2, "player_id": 10}]
    )
    with pytest.raises(ValueError, match="lacks contact input"):
        join_contact(base, contact)


def test_execution_lock_matches_exact_code_and_runtime() -> None:
    lock, runtime = verify_execution_lock(LOCK)
    assert lock["candidate_feature_identity"]["count"] == 116
    assert runtime["python_version"] == "3.12.13"


def test_execution_lock_hash_mutation_fails_closed(tmp_path: Path) -> None:
    value = json.loads(LOCK.read_text(encoding="utf-8"))
    first = sorted(value["required_file_hashes"])[0]
    value["required_file_hashes"][first] = "0" * 64
    path = tmp_path / "lock.json"
    _write(path, value)
    with pytest.raises(ValueError, match="locked file hash mismatch"):
        verify_execution_lock(path)
