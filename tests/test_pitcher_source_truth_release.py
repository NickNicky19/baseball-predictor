"""Release binding and mutation tests for pitcher source-truth repair v1."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from src.evaluation.pitcher_source_truth_release import (
    DEFAULT_MANIFEST_PATH,
    PitcherSourceTruthReleaseError,
    load_manifest,
    verify_manifest_payload,
    verify_release,
)


ROOT = Path(__file__).resolve().parents[1]


def test_current_pitcher_source_truth_release_verifies():
    payload = verify_release(ROOT)
    assert payload["classification"] == "REPAIR_ONLY_NO_PREDICTIVE_PROMOTION"
    assert payload["promotion"]["model_fit"] is False
    assert payload["promotion"]["betting_authorized"] is False


def test_mutation_stale_source_hash_fails_closed():
    payload = load_manifest(ROOT / DEFAULT_MANIFEST_PATH)
    mutated = copy.deepcopy(payload)
    mutated["changed_files"]["source"]["src/data/mlb_api.py"] = "0" * 64
    with pytest.raises(PitcherSourceTruthReleaseError, match="hash mismatch"):
        verify_manifest_payload(ROOT, mutated)


def test_mutation_stale_report_hash_fails_closed():
    payload = load_manifest(ROOT / DEFAULT_MANIFEST_PATH)
    mutated = copy.deepcopy(payload)
    mutated["report"]["sha256"] = "f" * 64
    with pytest.raises(PitcherSourceTruthReleaseError, match="report hash mismatch"):
        verify_manifest_payload(ROOT, mutated)

