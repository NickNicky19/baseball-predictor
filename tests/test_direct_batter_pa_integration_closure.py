from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.check_direct_batter_pa_integration_closure import (
    validate_integration_closure,
)


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "config" / "direct_batter_pa_foundation_v4_1_integration_closure.json"


def test_repository_integration_closure_is_hash_bound() -> None:
    result = validate_integration_closure(ROOT, MANIFEST)
    assert result["status"] == "VALID_RESEARCH_ONLY_INTEGRATION_CLOSURE"
    assert len(result["verified_files"]) == 6


def test_mutated_dependency_hash_fails_closed(tmp_path: Path) -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    payload["required_files"][0]["sha256"] = "0" * 64
    mutated = tmp_path / "mutated.json"
    mutated.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        validate_integration_closure(ROOT, mutated)


def test_duplicate_dependency_path_fails_closed(tmp_path: Path) -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    payload["required_files"].append(dict(payload["required_files"][0]))
    mutated = tmp_path / "duplicate.json"
    mutated.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate paths"):
        validate_integration_closure(ROOT, mutated)


def test_research_boundary_mutation_fails_closed(tmp_path: Path) -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    payload["boundaries"]["development_years"] = [2023, 2024]
    mutated = tmp_path / "boundary.json"
    mutated.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="boundary declaration"):
        validate_integration_closure(ROOT, mutated)
