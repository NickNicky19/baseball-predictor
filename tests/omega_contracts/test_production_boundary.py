import json
from pathlib import Path

import pytest

from src.omega_contracts.errors import ContractError
from src.omega_contracts.production import verify_candidate_and_production_boundaries

ROOT = Path(__file__).resolve().parents[2]


def test_candidate_and_all_frozen_production_files_verify():
    assert verify_candidate_and_production_boundaries(ROOT)["verified_files"] >= 1


def test_active_state_mutation_fails_on_temporary_copy(tmp_path: Path):
    (tmp_path / "config").mkdir()
    candidate = json.loads(
        (ROOT / "config/omega_candidate_v0.json").read_text(encoding="utf-8")
    )
    candidate["active"] = True
    (tmp_path / "config/omega_candidate_v0.json").write_text(
        json.dumps(candidate), encoding="utf-8"
    )
    boundary = {
        "schema_version": "omega-production-boundary-v1",
        "canonical_base_commit": "a" * 64,
        "files": {"config/omega_candidate_v0.json": "b" * 64},
    }
    (tmp_path / "config/omega_production_boundary_v1.json").write_text(
        json.dumps(boundary), encoding="utf-8"
    )
    with pytest.raises(ContractError, match="state|active"):
        verify_candidate_and_production_boundaries(tmp_path)
