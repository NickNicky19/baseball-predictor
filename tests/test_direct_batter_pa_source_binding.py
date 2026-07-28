from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from scripts import validate_direct_batter_pa_source_binding as binding


ROOT = Path(__file__).resolve().parents[1]


def contract() -> dict:
    return json.loads((ROOT / "config/direct_batter_pa_source_binding_v1.json").read_text(encoding="utf-8"))


def test_contract_is_exactly_hash_bound_and_inert() -> None:
    value = binding.load_contract()
    assert value["expected_decision"] == "BLOCKED_INCOMPLETE_SOURCE_AND_DEPENDENCY_AUTHORITY"
    assert value["model_fitting_permitted"] is False
    assert all(flag is False for flag in value["protected_data"].values())


@pytest.mark.parametrize(
    "section,key,value",
    [
        ("observed_authority", "raw_transport_request_and_response_receipts", True),
        ("observed_authority", "independent_official_source_receipts", True),
        ("observed_authority", "exact_reproducible_dependency_lock", True),
        ("observed_authority", "external_expected_release_digest", True),
        (None, "model_fitting_permitted", True),
        ("protected_data", "may_2026_opened", True),
    ],
)
def test_joint_contract_and_digest_mutations_cannot_promote_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, section: str | None, key: str, value: bool,
) -> None:
    mutated = deepcopy(contract())
    target = mutated if section is None else mutated[section]
    target[key] = value
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(mutated, sort_keys=True), encoding="utf-8")
    monkeypatch.setattr(binding, "CONTRACT_SHA256", hashlib.sha256(path.read_bytes()).hexdigest())
    with pytest.raises(ValueError):
        binding.load_contract(path)


def test_unsafe_source_path_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="unsafe"):
        binding.safe_existing_file(root, "../outside", context="mutation")


def test_non_2023_official_row_is_rejected() -> None:
    import pandas as pd

    row = {name: 1 for name in binding.OFFICIAL_COLUMNS}
    row.update({"season": 2024, "game_date": "2024-01-01"})
    with pytest.raises(ValueError, match="non-2023"):
        binding._canonical_official(pd.DataFrame([row]), context="mutation")


def test_duplicate_official_identity_is_rejected() -> None:
    import pandas as pd

    row = {name: 1 for name in binding.OFFICIAL_COLUMNS}
    row.update({"season": 2023, "game_date": "2023-04-01"})
    with pytest.raises(ValueError, match="duplicated"):
        binding._canonical_official(pd.DataFrame([row, row]), context="mutation")


def test_incomplete_authority_cannot_be_consumed_for_fitting() -> None:
    with pytest.raises(ValueError, match="fitting is forbidden"):
        binding.require_complete_authority(
            {"decision": "BLOCKED_INCOMPLETE_SOURCE_AND_DEPENDENCY_AUTHORITY"}
        )
