#!/usr/bin/env python3
"""Fail-closed binding check for the shared-PA integrated integrity gates."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "shared_pa_integrated_research_gates_v1.json"
HEX40 = re.compile(r"^[0-9a-f]{40}$")
EXPECTED_COMPONENT_IDS = {
    "projected_opportunity_receipt_inventory",
    "full_game_opportunity_receipt_replay",
    "direct_batter_pa_source_authority",
    "experiment_registry_and_synthetic_evaluator",
}
REQUIRED_FALSE_FLAGS = {
    "candidate_family_locked",
    "evaluation_protocol_locked",
    "genuine_receipt_producers_complete",
    "historical_results_permitted",
    "future_predictions_permitted",
    "promotion_permitted",
    "betting_permitted",
}
EXPECTED_INTEGRATION_SUPPORT_FILES = {
    ".gitattributes",
    ".github/workflows/shared-pa-integrated-research-gates-linux.yml",
    "config/shared_pa_integrated_research_gates_v1.json",
    "reports/shared_pa_integrated_research_gates_v1.md",
    "scripts/check_shared_pa_integrated_research_gates.py",
    "tests/test_shared_pa_integrated_research_gates.py",
}


class GateError(RuntimeError):
    """The integrated authority boundary is invalid or incomplete."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise GateError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_config(path: Path = CONFIG) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (OSError, json.JSONDecodeError) as exc:
        raise GateError(f"cannot load integration authority: {exc}") from exc
    if not isinstance(payload, dict):
        raise GateError("integration authority must be a JSON object")
    return payload


def validate_static(payload: dict[str, Any]) -> None:
    if payload.get("schema_version") != 1:
        raise GateError("unsupported schema_version")
    if payload.get("authority_state") != "INTEGRATED_INTEGRITY_GATE_ONLY":
        raise GateError("authority_state may not imply research, prediction, or promotion readiness")
    for field in ("integration_base", "component_merge_base"):
        if not isinstance(payload.get(field), str) or not HEX40.fullmatch(payload[field]):
            raise GateError(f"{field} must be an exact lowercase commit identity")
    support_files = payload.get("integration_support_files")
    if (
        not isinstance(support_files, list)
        or len(support_files) != len(set(support_files))
        or set(support_files) != EXPECTED_INTEGRATION_SUPPORT_FILES
    ):
        raise GateError("integration_support_files must match the exact closed support-file set")

    components = payload.get("components")
    if not isinstance(components, list) or len(components) != len(EXPECTED_COMPONENT_IDS):
        raise GateError("exactly four integrated components are required")
    ids: list[str] = []
    commits: list[str] = []
    for component in components:
        if not isinstance(component, dict):
            raise GateError("component entry must be an object")
        component_id = component.get("component_id")
        ids.append(component_id)
        commit = component.get("commit")
        tree = component.get("tree")
        commits.append(commit)
        if not isinstance(commit, str) or not HEX40.fullmatch(commit):
            raise GateError(f"invalid component commit for {component_id}")
        if not isinstance(tree, str) or not HEX40.fullmatch(tree):
            raise GateError(f"invalid component tree for {component_id}")
        count = component.get("changed_file_count")
        if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
            raise GateError(f"invalid changed_file_count for {component_id}")
    if set(ids) != EXPECTED_COMPONENT_IDS or len(ids) != len(set(ids)):
        raise GateError("component IDs are missing, duplicated, or unexpected")
    if len(commits) != len(set(commits)):
        raise GateError("component commits must be distinct")

    truth = payload.get("required_truth_state")
    if not isinstance(truth, dict) or set(truth) != REQUIRED_FALSE_FLAGS:
        raise GateError("required_truth_state must enumerate every fail-closed readiness flag")
    if any(truth[flag] is not False for flag in REQUIRED_FALSE_FLAGS):
        raise GateError("no research, prediction, promotion, or betting readiness flag may be true")

    boundaries = payload.get("protected_boundaries")
    required_boundaries = {
        "may_2026": "SKIP_ENTIRELY",
        "spent_2024_selection": "DO_NOT_REUSE",
        "spent_2025_hr_confirmation": "DO_NOT_REUSE_AS_FRESH_PROOF",
        "operational_smoke": "PERMANENTLY_NON_ECONOMIC",
        "process_20872": "DO_NOT_TOUCH",
        "missing_receipts": "TERMINAL_MISSING_NO_BACKFILL",
    }
    if boundaries != required_boundaries:
        raise GateError("protected boundaries differ from the exact fail-closed contract")


def _git(repo: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    if check and result.returncode != 0:
        raise GateError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def claim_file_ownership(ownership: dict[str, str], relative: str, component_id: str) -> None:
    previous = ownership.setdefault(relative, component_id)
    if previous != component_id:
        raise GateError(f"component file overlap is not permitted: {relative}")


def validate_complete_delta(actual: set[str], expected: set[str]) -> None:
    if actual != expected:
        unexpected = sorted(actual - expected)
        missing = sorted(expected - actual)
        raise GateError(f"complete integration delta mismatch; unexpected={unexpected}, missing={missing}")


def verify_git_binding(repo: Path, payload: dict[str, Any]) -> None:
    head = _git(repo, "rev-parse", "HEAD")
    integration_base = payload["integration_base"]
    if subprocess.run(["git", "merge-base", "--is-ancestor", integration_base, head], cwd=repo).returncode:
        raise GateError("integration base is not an ancestor of the candidate")

    ownership: dict[str, str] = {}
    for component in payload["components"]:
        component_id = component["component_id"]
        commit = component["commit"]
        expected_tree = component["tree"]
        if _git(repo, "rev-parse", f"{commit}^{{tree}}") != expected_tree:
            raise GateError(f"component tree mismatch: {component_id}")
        if subprocess.run(["git", "merge-base", "--is-ancestor", commit, head], cwd=repo).returncode:
            raise GateError(f"component is not an ancestor: {component_id}")
        merge_base = _git(repo, "merge-base", integration_base, commit)
        if merge_base != payload["component_merge_base"]:
            raise GateError(f"unexpected merge base: {component_id}")
        changed = [line for line in _git(repo, "diff", "--name-only", merge_base, commit).splitlines() if line]
        if len(changed) != component["changed_file_count"]:
            raise GateError(f"changed-file count mismatch: {component_id}")
        for relative in changed:
            claim_file_ownership(ownership, relative, component_id)
            component_blob = _git(repo, "rev-parse", f"{commit}:{relative}")
            integrated_blob = _git(repo, "rev-parse", f"HEAD:{relative}")
            if component_blob != integrated_blob:
                raise GateError(f"integrated bytes differ from component authority: {relative}")

    complete_delta = {
        line
        for line in _git(repo, "diff", "--name-only", integration_base, "HEAD").splitlines()
        if line
    }
    expected_delta = set(ownership) | set(payload["integration_support_files"])
    validate_complete_delta(complete_delta, expected_delta)

    if _git(repo, "status", "--porcelain=v1", "--untracked-files=all"):
        raise GateError("integration checkout is not clean")


def main() -> int:
    try:
        payload = load_config()
        validate_static(payload)
        verify_git_binding(ROOT, payload)
    except GateError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1
    print("[OK] four exact non-overlapping integrity components are bound; all readiness flags remain false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
