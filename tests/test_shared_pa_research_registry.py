from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from src.evaluation.shared_pa_research_registry import (
    SharedPAResearchRegistryError,
    ledger_sha256,
    load_candidate_release_manifest,
    load_future_evaluation_principles,
    load_spend_ledger,
    spent_window_ids,
    verify_ledger_evidence,
)


ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "config" / "shared_pa_evidence_spend_ledger_v1.json"
PRINCIPLES = ROOT / "config" / "shared_pa_future_evaluation_principles_v2.json"


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _candidate(ledger_hash: str) -> dict:
    artifact = {"path": "immutable/artifact", "sha256": "a" * 64}
    return {
        "schema_version": "shared-pa-candidate-release-manifest-v1",
        "status": "FROZEN_RESEARCH_CHALLENGER_NOT_PROMOTED",
        "candidate_id": "shared_pa_true_offset_residual_2023_v1",
        "parent_lineage": "shared_batter_pa_outcome_foundation",
        "research_only": True,
        "betting_authorized": False,
        "fit_periods": ["2023"],
        "development_evaluation_periods": ["2023"],
        "selection_window_ids": [],
        "confirmation_window_ids": [],
        "prospective_window": {
            "locked_at_utc": "2026-07-27T18:00:00Z",
            "first_official_date": "2026-07-28",
            "last_official_date": "2026-09-30",
            "backfill_allowed": False,
        },
        "markets": {
            "outputs": ["hits", "hr_over_0_5", "total_bases"],
            "adjudicate_separately": True,
            "one_market_can_rescue_another": False,
        },
        "pitcher_feature_policy": {
            "enabled": False,
            "enable_only_with_receipt_proven_probable_starter": True,
            "guessed_or_postgame_actual_starter_allowed": False,
        },
        "artifacts": {
            key: copy.deepcopy(artifact)
            for key in (
                "code",
                "configuration",
                "data_manifest",
                "feature_manifest",
                "model",
                "tests",
                "output_schema",
            )
        },
        "ledger_binding": {
            "path": "config/shared_pa_evidence_spend_ledger_v1.json",
            "sha256": ledger_hash,
        },
    }


def test_locked_ledger_and_future_principles_validate() -> None:
    ledger = load_spend_ledger(LEDGER)
    assert "direct_batter_pa_2024_selection" in spent_window_ids(ledger)
    verify_ledger_evidence(repository=ROOT, ledger=ledger)
    principles = load_future_evaluation_principles(PRINCIPLES)
    assert principles["discrimination"]["both_candidate_and_comparator_undefined_is_a_pass"] is False


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        ("repair_resets_spend", "rules changed"),
        ("unknown_becomes_selection", "invalid spend state"),
        ("duplicate_window", "unique"),
        ("backfill", "rules changed"),
    ],
)
def test_ledger_mutations_fail_closed(tmp_path: Path, mutation: str, match: str) -> None:
    value = json.loads(LEDGER.read_text(encoding="utf-8"))
    if mutation == "repair_resets_spend":
        value["rules"]["repair_does_not_restore_untouched_status"] = False
    elif mutation == "unknown_becomes_selection":
        value["windows"][-1]["state"] = "SELECTION_AVAILABLE"
    elif mutation == "duplicate_window":
        value["windows"].append(copy.deepcopy(value["windows"][0]))
    elif mutation == "backfill":
        value["rules"]["historical_or_missed_prospective_backfill_allowed"] = True
    path = tmp_path / "ledger.json"
    _write_json(path, value)
    with pytest.raises(SharedPAResearchRegistryError, match=match):
        load_spend_ledger(path)


def test_candidate_cannot_reuse_2024_or_spent_hr_confirmation(tmp_path: Path) -> None:
    ledger = load_spend_ledger(LEDGER)
    value = _candidate(ledger_sha256(LEDGER))
    value["selection_window_ids"] = ["direct_batter_pa_2024_selection"]
    path = tmp_path / "candidate.json"
    _write_json(path, value)
    with pytest.raises(SharedPAResearchRegistryError, match="unavailable"):
        load_candidate_release_manifest(path, ledger=ledger, ledger_path=LEDGER)
    value = _candidate(ledger_sha256(LEDGER))
    value["confirmation_window_ids"] = ["hr_eb_2025_confirmation"]
    _write_json(path, value)
    with pytest.raises(SharedPAResearchRegistryError, match="unavailable"):
        load_candidate_release_manifest(path, ledger=ledger, ledger_path=LEDGER)


@pytest.mark.parametrize("window_id", [
    "legacy_gbm_2025_sampled_dates",
    "shared_pa_forward_2026_07_23_to_2026_09_16",
])
def test_candidate_cannot_claim_development_used_or_already_reserved_window(
    tmp_path: Path, window_id: str,
) -> None:
    ledger = load_spend_ledger(LEDGER)
    value = _candidate(ledger_sha256(LEDGER))
    value["selection_window_ids"] = [window_id]
    path = tmp_path / "candidate.json"
    _write_json(path, value)
    with pytest.raises(SharedPAResearchRegistryError, match="unavailable"):
        load_candidate_release_manifest(path, ledger=ledger, ledger_path=LEDGER)


def test_candidate_requires_future_lock_and_separate_markets(tmp_path: Path) -> None:
    ledger = load_spend_ledger(LEDGER)
    value = _candidate(ledger_sha256(LEDGER))
    path = tmp_path / "candidate.json"
    _write_json(path, value)
    assert load_candidate_release_manifest(
        path, ledger=ledger, ledger_path=LEDGER,
    )["research_only"] is True
    value["prospective_window"]["first_official_date"] = "2026-07-27"
    _write_json(path, value)
    with pytest.raises(SharedPAResearchRegistryError, match="not locked before"):
        load_candidate_release_manifest(path, ledger=ledger, ledger_path=LEDGER)
    value = _candidate(ledger_sha256(LEDGER))
    value["markets"]["one_market_can_rescue_another"] = True
    _write_json(path, value)
    with pytest.raises(SharedPAResearchRegistryError, match="market separation"):
        load_candidate_release_manifest(path, ledger=ledger, ledger_path=LEDGER)


def test_sealed_month_and_backfill_mutations_fail_closed(tmp_path: Path) -> None:
    ledger = load_spend_ledger(LEDGER)
    value = _candidate(ledger_sha256(LEDGER))
    value["prospective_window"].update(
        {
            "locked_at_utc": "2026-04-30T00:00:00Z",
            "first_official_date": "2026-05-01",
            "last_official_date": "2026-05-31",
        }
    )
    path = tmp_path / "candidate.json"
    _write_json(path, value)
    with pytest.raises(SharedPAResearchRegistryError, match="sealed month"):
        load_candidate_release_manifest(path, ledger=ledger, ledger_path=LEDGER)
    value = _candidate(ledger_sha256(LEDGER))
    value["prospective_window"]["backfill_allowed"] = True
    _write_json(path, value)
    with pytest.raises(SharedPAResearchRegistryError, match="backfill"):
        load_candidate_release_manifest(path, ledger=ledger, ledger_path=LEDGER)


def test_candidate_ledger_binding_must_match_exact_locked_bytes(tmp_path: Path) -> None:
    ledger = load_spend_ledger(LEDGER)
    value = _candidate("b" * 64)
    path = tmp_path / "candidate.json"
    _write_json(path, value)
    with pytest.raises(SharedPAResearchRegistryError, match="hash mismatch"):
        load_candidate_release_manifest(path, ledger=ledger, ledger_path=LEDGER)
    value = _candidate(ledger_sha256(LEDGER))
    value["ledger_binding"]["path"] = "config/renamed-ledger.json"
    _write_json(path, value)
    with pytest.raises(SharedPAResearchRegistryError, match="path changed"):
        load_candidate_release_manifest(path, ledger=ledger, ledger_path=LEDGER)


def test_future_evaluation_cannot_treat_undefined_auc_as_pass(tmp_path: Path) -> None:
    value = json.loads(PRINCIPLES.read_text(encoding="utf-8"))
    value["discrimination"]["both_candidate_and_comparator_undefined_is_a_pass"] = True
    path = tmp_path / "principles.json"
    _write_json(path, value)
    with pytest.raises(SharedPAResearchRegistryError, match="undefined AUC"):
        load_future_evaluation_principles(path)


def test_future_evaluation_requires_clustered_hr_tail_uncertainty(tmp_path: Path) -> None:
    value = json.loads(PRINCIPLES.read_text(encoding="utf-8"))
    value["hr_high_probability_tail"]["uncertainty_method"] = "point_estimate_only"
    path = tmp_path / "principles.json"
    _write_json(path, value)
    with pytest.raises(SharedPAResearchRegistryError, match="tail uncertainty"):
        load_future_evaluation_principles(path)


def test_ledger_hash_is_byte_identity() -> None:
    assert ledger_sha256(LEDGER) == hashlib.sha256(LEDGER.read_bytes()).hexdigest()
