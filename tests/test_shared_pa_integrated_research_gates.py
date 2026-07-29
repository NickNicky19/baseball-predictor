from __future__ import annotations

import copy
from pathlib import Path

import pytest

from scripts.check_shared_pa_integrated_research_gates import (
    ROOT,
    GateError,
    claim_file_ownership,
    load_config,
    validate_complete_delta,
    validate_static,
    verify_git_binding,
)


def test_exact_integration_binding_is_valid() -> None:
    payload = load_config()
    validate_static(payload)
    verify_git_binding(ROOT, payload)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("authority_state",), "RESEARCH_READY"),
        (("required_truth_state", "future_predictions_permitted"), True),
        (("required_truth_state", "promotion_permitted"), True),
        (("integration_support_files",), []),
        (("protected_boundaries", "may_2026"), "EXCLUDE_FROM_SCORING"),
        (("protected_boundaries", "missing_receipts"), "BACKFILL_ALLOWED"),
        (("components", 0, "changed_file_count"), 0),
        (("components", 0, "commit"), "0" * 40),
    ],
)
def test_authority_mutations_fail_closed(path: tuple[object, ...], value: object) -> None:
    payload = copy.deepcopy(load_config())
    target = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(GateError):
        if path[-1] in {"commit", "changed_file_count"} and value not in {0}:
            validate_static(payload)
            verify_git_binding(ROOT, payload)
        else:
            validate_static(payload)


def test_duplicate_json_keys_fail_closed(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    with pytest.raises(GateError, match="duplicate JSON key"):
        load_config(duplicate)


def test_component_overlap_fails_closed() -> None:
    ownership = {"shared.py": "component-a"}
    with pytest.raises(GateError, match="overlap"):
        claim_file_ownership(ownership, "shared.py", "component-b")


def test_unknown_readiness_flag_fails_closed() -> None:
    payload = copy.deepcopy(load_config())
    payload["required_truth_state"]["unknown"] = False
    with pytest.raises(GateError):
        validate_static(payload)


def test_unlisted_integration_support_file_fails_closed() -> None:
    payload = copy.deepcopy(load_config())
    payload["integration_support_files"].append("unreviewed.py")
    with pytest.raises(GateError):
        validate_static(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tree", "f" * 40),
        ("changed_file_count", 36),
    ],
)
def test_wrong_but_well_formed_component_identity_fails_closed(field: str, value: object) -> None:
    payload = copy.deepcopy(load_config())
    payload["components"][0][field] = value
    validate_static(payload)
    with pytest.raises(GateError):
        verify_git_binding(ROOT, payload)


def test_wrong_but_well_formed_merge_base_fails_closed() -> None:
    payload = copy.deepcopy(load_config())
    payload["component_merge_base"] = payload["integration_base"]
    validate_static(payload)
    with pytest.raises(GateError, match="merge base"):
        verify_git_binding(ROOT, payload)


@pytest.mark.parametrize(
    ("actual", "expected"),
    [
        ({"expected.py", "extra.py"}, {"expected.py"}),
        (set(), {"missing.py"}),
    ],
)
def test_complete_delta_mutations_fail_closed(actual: set[str], expected: set[str]) -> None:
    with pytest.raises(GateError, match="delta mismatch"):
        validate_complete_delta(actual, expected)
