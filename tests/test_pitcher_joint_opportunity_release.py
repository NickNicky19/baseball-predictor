from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from src.evaluation.pitcher_joint_opportunity_release import (
    PitcherJointOpportunityReleaseError,
    load_manifest,
    verify_manifest_payload,
    verify_release,
)


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "reports/pitcher_joint_opportunity_v1_manifest.json"


def test_release_manifest_and_exact_delta_verify() -> None:
    payload = verify_release(ROOT)
    assert payload["status"] == "NO_FIT_NO_SELECTION_NO_PREDICTION"
    assert payload["promotion"]["betting_authorized"] is False


def test_stale_protocol_source_test_and_report_hashes_fail() -> None:
    original = load_manifest(MANIFEST)
    mutations = []
    for group in ("config", "source", "tests"):
        changed = deepcopy(original)
        first = next(iter(changed["changed_files"][group]))
        changed["changed_files"][group][first] = "0" * 64
        mutations.append(changed)
    changed = deepcopy(original)
    changed["report"]["sha256"] = "0" * 64
    mutations.append(changed)
    for payload in mutations:
        with pytest.raises(PitcherJointOpportunityReleaseError, match="hash mismatch"):
            verify_manifest_payload(ROOT, payload)


def test_frozen_pitcher_k_binding_mutation_fails() -> None:
    payload = load_manifest(MANIFEST)
    payload["frozen_production_bindings"]["src/prediction/prop_engine.py"] = "0" * 64
    with pytest.raises(PitcherJointOpportunityReleaseError, match="binding declaration"):
        verify_manifest_payload(ROOT, payload)


def test_runtime_dependency_and_external_authorization_mutations_fail() -> None:
    original = load_manifest(MANIFEST)
    payload = deepcopy(original)
    payload["runtime_dependency_bindings"][
        "src/evaluation/forward_pitcher_context_v2.py"
    ] = "0" * 64
    with pytest.raises(PitcherJointOpportunityReleaseError, match="dependency binding"):
        verify_manifest_payload(ROOT, payload)
    for field, value in (
        ("authorized_evidence_authority_receipt_sha256", "c" * 64),
        ("authorized_runtime_release_sha256", "a" * 64),
        ("authorized_artifact_release_sha256", "b" * 64),
        ("synthetic_self_promotion_allowed", True),
    ):
        payload = deepcopy(original)
        payload["external_authorization"][field] = value
        with pytest.raises(PitcherJointOpportunityReleaseError, match="external authorization"):
            verify_manifest_payload(ROOT, payload)


def test_evidence_authority_manifest_cannot_claim_a_bound_archive() -> None:
    original = load_manifest(MANIFEST)
    for field, value in (
        ("status", "EXTERNALLY_BOUND_APPROVED_ARCHIVE_ERA"),
        ("authorized_receipt_sha256", "a" * 64),
        ("sha256", "b" * 64),
    ):
        payload = deepcopy(original)
        payload["evidence_authority"][field] = value
        with pytest.raises(PitcherJointOpportunityReleaseError, match="evidence authority"):
            verify_manifest_payload(ROOT, payload)


def test_duplicate_manifest_key_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.json"
    path.write_text('{"schema_version":"a","schema_version":"b"}', encoding="utf-8")
    with pytest.raises(PitcherJointOpportunityReleaseError, match="duplicate"):
        load_manifest(path)


def test_manifest_cannot_claim_fit_shadow_or_betting_authority() -> None:
    original = load_manifest(MANIFEST)
    for field in ("fitting_authorized", "shadow_use_authorized", "betting_authorized"):
        payload = deepcopy(original)
        payload["promotion"][field] = True
        with pytest.raises(PitcherJointOpportunityReleaseError, match="promotion"):
            verify_manifest_payload(ROOT, payload)


def test_manifest_cannot_claim_outcomes_or_2024_were_read() -> None:
    original = load_manifest(MANIFEST)
    for field in ("any_outcomes_read", "2024_outcomes_read", "may_2026_read"):
        payload = deepcopy(original)
        payload["read_boundaries"][field] = True
        with pytest.raises(PitcherJointOpportunityReleaseError, match="read boundary"):
            verify_manifest_payload(ROOT, payload)
