from __future__ import annotations

import json
import os
import shutil
import stat
from pathlib import Path, PurePosixPath

import pytest

from src.evaluation import shared_pa_experiment_registry as registry


ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "config/shared_pa_experiment_registry_v1.json"
EVALUATOR_CONTRACT = ROOT / "config/shared_pa_market_evaluation_contract_v1.json"
EVALUATOR_MANIFEST = ROOT / "config/shared_pa_market_evaluator_v1_file_manifest.json"
MANIFEST = ROOT / "config/shared_pa_experiment_registry_v1_artifact_manifest.json"


def _write(path: Path, value: bytes) -> dict[str, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)
    return {
        "path": path.relative_to(path.parents[1]).as_posix(),
        "sha256": registry.sha256_file(path),
    }


def _artifacts(tmp_path: Path) -> tuple[Path, dict[str, dict[str, str]]]:
    root = tmp_path / "artifacts"
    evaluator = root / "config/shared_pa_market_evaluation_contract_v1.json"
    evaluator_manifest = root / "config/shared_pa_market_evaluator_v1_file_manifest.json"
    manifest = json.loads(EVALUATOR_MANIFEST.read_text(encoding="utf-8"))
    for row in manifest["files"]:
        destination = root.joinpath(*PurePosixPath(row["path"]).parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / row["path"], destination)
    evaluator_manifest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(EVALUATOR_MANIFEST, evaluator_manifest)
    bindings = {
        "evaluator": {
            "path": "config/shared_pa_market_evaluation_contract_v1.json",
            "sha256": registry.sha256_file(evaluator),
        }
    }
    for name, payload in {
        "feature": b'{"features":["strict_prior_rate"]}\n',
        "grid": b'{"grid":{"strength":[50,200]}}\n',
        "fold": b'{"folds":"expanding_2023"}\n',
        "code": b"def candidate(): return None\n",
        "configuration": b'{"seed":20260728}\n',
        "data": b'{"panel_sha256":"' + b"a" * 64 + b'"}\n',
        "tests": b"def test_contract(): assert True\n",
        "output_schema": b'{"type":"object"}\n',
        "model": b"immutable-model-bytes",
        "evidence": b'{"reason":"measured rejection"}\n',
    }.items():
        path = root / "bound" / f"{name}.bin"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        bindings[name] = {
            "path": path.relative_to(root).as_posix(),
            "sha256": registry.sha256_file(path),
        }
    return root, bindings


def _predeclaration(
    bindings: dict[str, dict[str, str]],
    artifact_root: Path,
    *,
    candidate_id: str = "candidate_v1",
    family_id: str | None = None,
    parent_id: str = registry.ROOT_PARENT,
    prior_attempts: list[str] | None = None,
    output_namespace: str | None = None,
) -> dict:
    payload = {
        "candidate_id": candidate_id,
        "parent_candidate_id": parent_id,
        "multiplicity_family_id": "PENDING_DERIVATION",
        "hypothesis": "One regularized shared-PA candidate using strict-prior inputs.",
        "feature_contract": bindings["feature"],
        "grid_contract": bindings["grid"],
        "fold_contract": bindings["fold"],
        "artifact_bindings": {
            "code": [bindings["code"]],
            "configuration": [bindings["configuration"]],
            "data": [bindings["data"]],
            "tests": [bindings["tests"]],
            "output_schema": [bindings["output_schema"]],
        },
        "allowed_evidence_window_ids": ["development_2023_only"],
        "prior_attempt_entry_hashes": prior_attempts or [],
        "output_namespace": output_namespace or f"experiments/{candidate_id}/locked",
        "selection_authority": {
            "authority_id": "shared_pa_2024_selection_authority_v1",
            "required_before_2024_read": True,
            "token_entry_hash": None,
        },
        "untouched_forward_window": {
            "state": "UNASSIGNED",
            "window_id": None,
            "first_official_date": None,
            "last_official_date": None,
            "backfill_allowed": False,
        },
        "protected_boundaries": dict(registry.PROTECTED_BOUNDARIES),
        "terminal_disposition": "PENDING",
        "evaluator_binding": bindings["evaluator"],
        "equivalence_fingerprint": "",
    }
    payload["equivalence_fingerprint"] = registry.experiment_equivalence_fingerprint(payload, artifact_root)
    payload["multiplicity_family_id"] = family_id or registry.derived_multiplicity_family_id(payload, artifact_root)
    return payload


def _initialize(tmp_path: Path):
    artifact_root, bindings = _artifacts(tmp_path)
    root = tmp_path / "registry"
    state = registry.initialize_registry(
        root=root,
        policy_path=POLICY,
        artifact_root=artifact_root,
    )
    return root, artifact_root, bindings, state


def _canonical_write(path: Path, value: dict) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(registry.canonical_bytes(value) + b"\n")
    return registry.sha256_file(path)


def _bound_initialize(tmp_path: Path):
    artifact_root, bindings = _artifacts(tmp_path)
    policy_path = artifact_root / "config/shared_pa_experiment_registry_v1.json"
    manifest_path = artifact_root / "config/shared_pa_experiment_registry_v1_artifact_manifest.json"
    shutil.copyfile(POLICY, policy_path)
    policy_path.write_bytes(registry.canonical_bytes(json.loads(policy_path.read_text(encoding="utf-8"))) + b"\n")
    shutil.copyfile(MANIFEST, manifest_path)
    external = tmp_path / "external-authority"
    checkpoint_path = external / "trusted-current.json"
    expectation = {
        "schema_version": registry.RELEASE_EXPECTATION_SCHEMA,
        "authority_id": "synthetic-independent-test-authority",
        "registry_id": "shared_pa_experiment_registry_v1",
        "source_base_commit": "9f9d839187550f7a7a6da3b4bfdb25b2dff3e794",
        "policy_binding": {"path": "config/shared_pa_experiment_registry_v1.json", "sha256": registry.sha256_file(policy_path)},
        "evaluator_binding": bindings["evaluator"],
        "artifact_manifest_binding": {"path": "config/shared_pa_experiment_registry_v1_artifact_manifest.json", "sha256": registry.sha256_file(manifest_path)},
        "trusted_checkpoint_canonical_path": str(checkpoint_path.absolute()),
        "append_not_before_utc": "2026-07-28T00:00:00Z",
    }
    expectation_path = external / "release-expectation.json"
    expectation_sha = _canonical_write(expectation_path, expectation)
    root = tmp_path / "registry"
    state = registry.initialize_registry(
        root=root,
        policy_path=policy_path,
        artifact_root=artifact_root,
        external_release_expectation_path=expectation_path,
        expected_release_expectation_sha256=expectation_sha,
    )
    registry.write_trusted_checkpoint_once(checkpoint_path, state)
    checkpoint_sha = registry.sha256_file(checkpoint_path)
    bound = registry.verify_registry(
        root=root,
        artifact_root=artifact_root,
        external_release_expectation_path=expectation_path,
        expected_release_expectation_sha256=expectation_sha,
        trusted_checkpoint_path=checkpoint_path,
        expected_trusted_checkpoint_sha256=checkpoint_sha,
    )
    assert bound.authority_state == registry.BOUND_AUTHORITY
    return root, artifact_root, bindings, bound, expectation_path, expectation_sha, checkpoint_path, checkpoint_sha


def _append_receipt(
    path: Path,
    *,
    state,
    entry_type: str,
    payload: dict,
    artifact_root: Path,
    sequence: int | None = None,
    recorded_at: str = "2026-07-28T12:00:00Z",
) -> str:
    semantic_hash = (
        registry.experiment_equivalence_fingerprint(payload, artifact_root)
        if entry_type == "EXPERIMENT_PREDECLARED"
        else registry.sha256_bytes(registry.canonical_bytes(payload))
    )
    receipt = {
        "schema_version": registry.APPEND_RECEIPT_SCHEMA,
        "authority_id": "synthetic-independent-test-authority",
        "registry_id": state.registry_id,
        "append_sequence": sequence if sequence is not None else state.entry_count + 1,
        "expected_previous_checkpoint_hash": state.checkpoint_hash,
        "entry_type": entry_type,
        "payload_semantic_sha256": semantic_hash,
        "payload_canonical_sha256": registry.sha256_bytes(registry.canonical_bytes(payload)),
        "trusted_recorded_at_utc": recorded_at,
    }
    return _canonical_write(path, receipt)


def _append_predeclaration(tmp_path: Path):
    root, artifact_root, bindings, initial = _initialize(tmp_path)
    payload = _predeclaration(bindings, artifact_root)
    state = registry.append_event(
        root=root,
        artifact_root=artifact_root,
        entry_type="EXPERIMENT_PREDECLARED",
        payload=payload,
        recorded_at_utc="2026-07-28T12:00:00Z",
        trusted_checkpoint=initial.trusted_checkpoint(),
        allow_unbound_test_only=True,
    )
    return root, artifact_root, bindings, state, payload


def _make_writable(path: Path) -> None:
    os.chmod(path, stat.S_IREAD | stat.S_IWRITE)


def test_policy_keeps_spent_2024_token_unissued_and_unconsumed() -> None:
    policy = registry.load_policy(POLICY)
    authority = policy["selection_authority"]
    assert authority["window_state"] == "INELIGIBLE_ALREADY_SPENT"
    assert authority["issuance_enabled"] is False
    assert authority["initial_issuance_state"] == "UNISSUED"
    assert authority["initial_consumption_state"] == "UNCONSUMED"


def test_policy_binds_only_the_exact_synthetic_unbound_evaluator() -> None:
    binding = registry.load_policy(POLICY)["evaluator_binding"]
    assert binding == {
        "path": "config/shared_pa_market_evaluation_contract_v1.json",
        "sha256": "53673c56b90e48d593c4355e0fb2c6152e69dbf165c15125db065d9059b5b34d",
        "file_manifest_path": "config/shared_pa_market_evaluator_v1_file_manifest.json",
        "file_manifest_sha256": "83cf0ca50a67f7f3c905d2f2cf189045ba9983ad5fc4a5718682a0b79cd7eec1",
        "state": registry.EVALUATOR_STATE,
        "evidence_class": registry.EVALUATOR_EVIDENCE_CLASS,
        "external_authority_required_before_real_evaluation": True,
        "real_evaluation_authorized": False,
        "model_fitting_authorized": False,
        "promotion_authorized": False,
    }


def test_linux_gate_preserves_complete_history_for_spend_ledger_verification() -> None:
    workflow = (
        ROOT / ".github/workflows/shared-pa-registry-evaluator-linux.yml"
    ).read_text(encoding="utf-8")
    assert "fetch-depth: 0" in workflow
    assert "persist-credentials: false" in workflow


@pytest.mark.parametrize(
    ("field", "mutated"),
    [
        ("state", "PRODUCTION_READY"),
        ("evidence_class", "REAL_EVIDENCE"),
        ("external_authority_required_before_real_evaluation", False),
        ("real_evaluation_authorized", True),
        ("model_fitting_authorized", True),
        ("promotion_authorized", True),
        ("sha256", "0" * 64),
        ("file_manifest_sha256", "0" * 64),
    ],
)
def test_policy_evaluator_authority_mutations_fail_closed(
    field: str, mutated: object
) -> None:
    policy = registry.load_policy(POLICY)
    policy["evaluator_binding"][field] = mutated
    with pytest.raises(registry.SharedPAExperimentRegistryError, match="evaluator binding"):
        registry.validate_policy(policy)


@pytest.mark.parametrize(
    "relative",
    [
        "config/shared_pa_market_evaluation_contract_v1.json",
        "config/shared_pa_market_evaluator_v1_file_manifest.json",
        "requirements-prospective-batter-opportunity-ci.lock",
        "src/evaluation/shared_pa_market_evaluator.py",
    ],
)
def test_registry_replay_rehashes_every_evaluator_manifest_authority(
    tmp_path: Path, relative: str
) -> None:
    root, artifact_root, _, _ = _initialize(tmp_path)
    (artifact_root / relative).write_bytes(b"mutated\n")
    with pytest.raises(registry.SharedPAExperimentRegistryError, match="bytes changed"):
        registry.verify_registry(root=root, artifact_root=artifact_root)


def test_initialize_replay_and_external_checkpoint_are_exact(tmp_path: Path) -> None:
    root, artifact_root, _, state = _initialize(tmp_path)
    assert state.entry_count == 0
    checkpoint = tmp_path / "trusted.json"
    registry.write_trusted_checkpoint_once(checkpoint, state)
    trusted = registry.load_trusted_checkpoint(checkpoint)
    assert registry.verify_registry(
        root=root, artifact_root=artifact_root, trusted_checkpoint=trusted,
    ) == state
    with pytest.raises(registry.SharedPAExperimentRegistryError, match="overwrite"):
        registry.write_trusted_checkpoint_once(checkpoint, state)


def test_append_predeclaration_binds_bytes_and_replays(tmp_path: Path) -> None:
    root, artifact_root, _, state, payload = _append_predeclaration(tmp_path)
    assert state.entry_count == 1
    assert state.entries[0]["payload"]["candidate_id"] == payload["candidate_id"]
    assert registry.verify_registry(root=root, artifact_root=artifact_root) == state


def test_changed_artifact_bytes_fail_replay(tmp_path: Path) -> None:
    root, artifact_root, bindings, _, _ = _append_predeclaration(tmp_path)
    (artifact_root / bindings["code"]["path"]).write_bytes(b"changed\n")
    with pytest.raises(registry.SharedPAExperimentRegistryError, match="bytes changed"):
        registry.verify_registry(root=root, artifact_root=artifact_root)


@pytest.mark.parametrize("rename_id", [False, True])
def test_duplicate_or_equivalent_renamed_candidate_fails(tmp_path: Path, rename_id: bool) -> None:
    root, artifact_root, bindings, state, _ = _append_predeclaration(tmp_path)
    candidate_id = "renamed_candidate" if rename_id else "candidate_v1"
    payload = _predeclaration(
        bindings, artifact_root,
        candidate_id=candidate_id,
        output_namespace=f"experiments/{candidate_id}/different-output-path",
        prior_attempts=[state.entries[0]["entry_hash"]],
    )
    match = "equivalent candidate" if rename_id else "duplicate candidate"
    with pytest.raises(registry.SharedPAExperimentRegistryError, match=match):
        registry.append_event(
            root=root,
            artifact_root=artifact_root,
            entry_type="EXPERIMENT_PREDECLARED",
            payload=payload,
            recorded_at_utc="2026-07-28T12:01:00Z",
            trusted_checkpoint=state.trusted_checkpoint(),
            allow_unbound_test_only=True,
        )


def test_missing_parent_and_family_spoof_fail(tmp_path: Path) -> None:
    root, artifact_root, bindings, state = _initialize(tmp_path)
    missing = _predeclaration(bindings, artifact_root, parent_id="missing_parent")
    with pytest.raises(registry.SharedPAExperimentRegistryError, match="parent is missing"):
        registry.append_event(
            root=root, artifact_root=artifact_root, entry_type="EXPERIMENT_PREDECLARED",
            payload=missing, recorded_at_utc="2026-07-28T12:00:00Z",
            trusted_checkpoint=state.trusted_checkpoint(),
            allow_unbound_test_only=True,
        )
    first = registry.append_event(
        root=root, artifact_root=artifact_root, entry_type="EXPERIMENT_PREDECLARED",
        payload=_predeclaration(bindings, artifact_root), recorded_at_utc="2026-07-28T12:00:00Z",
        trusted_checkpoint=state.trusted_checkpoint(),
        allow_unbound_test_only=True,
    )
    # A materially different design cannot claim the first design's family label.
    changed_grid = artifact_root / "bound" / "grid-v2.bin"
    changed_grid.write_bytes(b'{"grid":{"strength":[100]}}\n')
    bindings["grid"] = {"path": "bound/grid-v2.bin", "sha256": registry.sha256_file(changed_grid)}
    second = _predeclaration(
        bindings,
        artifact_root,
        candidate_id="candidate_v2",
        family_id=first.entries[0]["payload"]["multiplicity_family_id"],
    )
    with pytest.raises(registry.SharedPAExperimentRegistryError, match="family must derive"):
        registry.append_event(
            root=root, artifact_root=artifact_root, entry_type="EXPERIMENT_PREDECLARED",
            payload=second, recorded_at_utc="2026-07-28T12:01:00Z",
            trusted_checkpoint=first.trusted_checkpoint(),
            allow_unbound_test_only=True,
        )


@pytest.mark.parametrize(
    "window",
    ["selection_2024", "hr_confirmation_2025", "2026-05-17", "may_2026", "development-2023-only", "DEVELOPMENT_2023_ONLY"],
)
def test_forbidden_or_spent_evidence_window_fails(tmp_path: Path, window: str) -> None:
    root, artifact_root, bindings, state = _initialize(tmp_path)
    payload = _predeclaration(bindings, artifact_root)
    payload["allowed_evidence_window_ids"] = [window]
    payload["equivalence_fingerprint"] = registry.experiment_equivalence_fingerprint(payload, artifact_root)
    with pytest.raises(registry.SharedPAExperimentRegistryError, match="canonical allowlisted"):
        registry.append_event(
            root=root, artifact_root=artifact_root, entry_type="EXPERIMENT_PREDECLARED",
            payload=payload, recorded_at_utc="2026-07-28T12:00:00Z",
            trusted_checkpoint=state.trusted_checkpoint(),
            allow_unbound_test_only=True,
        )


def test_protected_boundary_mutation_fails(tmp_path: Path) -> None:
    root, artifact_root, bindings, state = _initialize(tmp_path)
    payload = _predeclaration(bindings, artifact_root)
    payload["protected_boundaries"]["may_2026_sealed"] = False
    with pytest.raises(registry.SharedPAExperimentRegistryError, match="protected boundaries"):
        registry.append_event(
            root=root, artifact_root=artifact_root, entry_type="EXPERIMENT_PREDECLARED",
            payload=payload, recorded_at_utc="2026-07-28T12:00:00Z",
            trusted_checkpoint=state.trusted_checkpoint(),
            allow_unbound_test_only=True,
        )


def test_model_freeze_binds_predeclaration_and_model_bytes(tmp_path: Path) -> None:
    root, artifact_root, bindings, state, payload = _append_predeclaration(tmp_path)
    models = [bindings["model"]]
    release_hash = registry.sha256_bytes(registry.canonical_bytes({
        "predeclaration_entry_hash": state.entries[0]["entry_hash"],
        "model_artifacts": models,
    }))
    frozen = registry.append_event(
        root=root,
        artifact_root=artifact_root,
        entry_type="CANDIDATE_FROZEN",
        payload={
            "candidate_id": payload["candidate_id"],
            "predeclaration_entry_hash": state.entries[0]["entry_hash"],
            "model_artifacts": models,
            "model_release_hash": release_hash,
        },
        recorded_at_utc="2026-07-28T12:01:00Z",
        trusted_checkpoint=state.trusted_checkpoint(),
        allow_unbound_test_only=True,
    )
    assert frozen.entry_count == 2
    with pytest.raises(registry.SharedPAExperimentRegistryError, match="already frozen"):
        registry.append_event(
            root=root, artifact_root=artifact_root, entry_type="CANDIDATE_FROZEN",
            payload=frozen.entries[-1]["payload"], recorded_at_utc="2026-07-28T12:02:00Z",
            trusted_checkpoint=frozen.trusted_checkpoint(),
            allow_unbound_test_only=True,
        )


def test_real_2024_authority_refuses_issue(tmp_path: Path) -> None:
    root, artifact_root, bindings, state, payload = _append_predeclaration(tmp_path)
    models = [bindings["model"]]
    release_hash = registry.sha256_bytes(registry.canonical_bytes({
        "predeclaration_entry_hash": state.entries[0]["entry_hash"],
        "model_artifacts": models,
    }))
    frozen = registry.append_event(
        root=root, artifact_root=artifact_root, entry_type="CANDIDATE_FROZEN",
        payload={"candidate_id": payload["candidate_id"], "predeclaration_entry_hash": state.entries[0]["entry_hash"], "model_artifacts": models, "model_release_hash": release_hash},
        recorded_at_utc="2026-07-28T12:01:00Z", trusted_checkpoint=state.trusted_checkpoint(),
        allow_unbound_test_only=True,
    )
    with pytest.raises(registry.SharedPAExperimentRegistryError, match="disabled or ineligible"):
        registry.append_event(
            root=root, artifact_root=artifact_root, entry_type="SELECTION_TOKEN_ISSUED",
            payload={"authority_id": "shared_pa_2024_selection_authority_v1", "candidate_id": payload["candidate_id"], "candidate_frozen_entry_hash": frozen.entries[-1]["entry_hash"], "window_id": "2024_regular_season", "human_approval_sha256": "a" * 64},
            recorded_at_utc="2026-07-28T12:02:00Z", trusted_checkpoint=frozen.trusted_checkpoint(),
            allow_unbound_test_only=True,
        )


def test_generic_token_transition_is_single_use_and_path_bound(tmp_path: Path) -> None:
    artifact_root, bindings = _artifacts(tmp_path)
    pre_payload = _predeclaration(bindings, artifact_root)
    pre = {"entry_type": "EXPERIMENT_PREDECLARED", "entry_hash": "1" * 64, "payload": pre_payload}
    freeze_payload = {"candidate_id": "candidate_v1", "predeclaration_entry_hash": "1" * 64, "model_artifacts": [bindings["model"]], "model_release_hash": "2" * 64}
    frozen = {"entry_type": "CANDIDATE_FROZEN", "entry_hash": "3" * 64, "payload": freeze_payload}
    policy = registry.load_policy(POLICY)
    policy["selection_authority"] = {**policy["selection_authority"], "window_state": "ELIGIBLE_UNTOUCHED", "issuance_enabled": True}
    issue_payload = {"authority_id": "shared_pa_2024_selection_authority_v1", "candidate_id": "candidate_v1", "candidate_frozen_entry_hash": "3" * 64, "window_id": "2024_regular_season", "human_approval_sha256": "a" * 64}
    registry._validate_event_payload("SELECTION_TOKEN_ISSUED", issue_payload, entries=[pre, frozen], policy=policy, artifact_root=artifact_root)
    issue = {"entry_type": "SELECTION_TOKEN_ISSUED", "entry_hash": "4" * 64, "payload": issue_payload}
    with pytest.raises(registry.SharedPAExperimentRegistryError, match="already issued"):
        registry._validate_event_payload("SELECTION_TOKEN_ISSUED", issue_payload, entries=[pre, frozen, issue], policy=policy, artifact_root=artifact_root)
    consume_payload = {"authority_id": "shared_pa_2024_selection_authority_v1", "candidate_id": "candidate_v1", "issue_entry_hash": "4" * 64, "window_id": "2024_regular_season", "selection_output_namespace": "experiments/candidate_v1/locked/selection_2024"}
    registry._validate_event_payload("SELECTION_TOKEN_CONSUMED", consume_payload, entries=[pre, frozen, issue], policy=policy, artifact_root=artifact_root)
    bypass = {**consume_payload, "selection_output_namespace": "other/path"}
    with pytest.raises(registry.SharedPAExperimentRegistryError, match="output path"):
        registry._validate_event_payload("SELECTION_TOKEN_CONSUMED", bypass, entries=[pre, frozen, issue], policy=policy, artifact_root=artifact_root)
    consumed = {"entry_type": "SELECTION_TOKEN_CONSUMED", "entry_hash": "5" * 64, "payload": consume_payload}
    with pytest.raises(registry.SharedPAExperimentRegistryError, match="already consumed"):
        registry._validate_event_payload("SELECTION_TOKEN_CONSUMED", consume_payload, entries=[pre, frozen, issue, consumed], policy=policy, artifact_root=artifact_root)


def test_terminal_rejection_is_immutable_and_cannot_be_replaced(tmp_path: Path) -> None:
    root, artifact_root, bindings, state, payload = _append_predeclaration(tmp_path)
    terminal_payload = {"candidate_id": payload["candidate_id"], "disposition": "REJECTED_DEVELOPMENT", "reason_code": "FAILED_PREDECLARED_GATE", "evidence_bindings": [bindings["evidence"]]}
    terminal = registry.append_event(
        root=root, artifact_root=artifact_root, entry_type="TERMINAL_DISPOSITION",
        payload=terminal_payload, recorded_at_utc="2026-07-28T12:01:00Z",
        trusted_checkpoint=state.trusted_checkpoint(),
        allow_unbound_test_only=True,
    )
    with pytest.raises(registry.SharedPAExperimentRegistryError, match="already terminal"):
        registry.append_event(
            root=root, artifact_root=artifact_root, entry_type="TERMINAL_DISPOSITION",
            payload={**terminal_payload, "disposition": "RETAINED_RESEARCH_ONLY"},
            recorded_at_utc="2026-07-28T12:02:00Z",
            trusted_checkpoint=terminal.trusted_checkpoint(),
            allow_unbound_test_only=True,
        )
    entry_path = sorted((root / "entries").iterdir())[-1]
    _make_writable(entry_path)
    value = json.loads(entry_path.read_text(encoding="ascii"))
    value["payload"]["disposition"] = "RETAINED_RESEARCH_ONLY"
    entry_path.write_bytes(registry.canonical_bytes(value) + b"\n")
    with pytest.raises(registry.SharedPAExperimentRegistryError, match="payload hash"):
        registry.verify_registry(root=root, artifact_root=artifact_root)


def test_entry_or_checkpoint_deletion_and_orphan_crash_fail_closed(tmp_path: Path) -> None:
    root, artifact_root, _, _, _ = _append_predeclaration(tmp_path)
    entry = next((root / "entries").iterdir())
    _make_writable(entry)
    entry.unlink()
    with pytest.raises(registry.SharedPAExperimentRegistryError, match="checkpoint deletion or truncation|HEAD differs"):
        registry.verify_registry(root=root, artifact_root=artifact_root)

    root2, artifact_root2, _, _, _ = _append_predeclaration(tmp_path / "second")
    orphan = root2 / "entries" / ("00000002-" + "a" * 64 + ".json")
    orphan.write_text("{}\n", encoding="ascii")
    with pytest.raises(registry.SharedPAExperimentRegistryError):
        registry.verify_registry(root=root2, artifact_root=artifact_root2)


def test_external_checkpoint_detects_complete_suffix_rollback(tmp_path: Path) -> None:
    root, artifact_root, bindings, state1, payload = _append_predeclaration(tmp_path)
    head1 = (root / "HEAD.json").read_bytes()
    terminal = registry.append_event(
        root=root, artifact_root=artifact_root, entry_type="TERMINAL_DISPOSITION",
        payload={"candidate_id": payload["candidate_id"], "disposition": "REJECTED_DEVELOPMENT", "reason_code": "FAILED", "evidence_bindings": [bindings["evidence"]]},
        recorded_at_utc="2026-07-28T12:01:00Z", trusted_checkpoint=state1.trusted_checkpoint(),
        allow_unbound_test_only=True,
    )
    last_entry = sorted((root / "entries").iterdir())[-1]
    last_checkpoint = sorted((root / "checkpoints").iterdir())[-1]
    for path in (last_entry, last_checkpoint):
        _make_writable(path)
        path.unlink()
    (root / "HEAD.json").write_bytes(head1)
    # Local chain alone cannot detect a complete suffix rollback.
    assert registry.verify_registry(root=root, artifact_root=artifact_root).entry_count == 1
    with pytest.raises(registry.SharedPAExperimentRegistryError, match="trusted checkpoint"):
        registry.verify_registry(root=root, artifact_root=artifact_root, trusted_checkpoint=terminal.trusted_checkpoint())


def test_existing_or_stale_lock_blocks_concurrent_append(tmp_path: Path) -> None:
    root, artifact_root, bindings, state = _initialize(tmp_path)
    with registry._exclusive_lock(root):
        with pytest.raises(registry.SharedPAExperimentRegistryError, match="append lock"):
            registry.append_event(
                root=root, artifact_root=artifact_root, entry_type="EXPERIMENT_PREDECLARED",
                payload=_predeclaration(bindings, artifact_root), recorded_at_utc="2026-07-28T12:00:00Z",
                trusted_checkpoint=state.trusted_checkpoint(),
                allow_unbound_test_only=True,
            )


def test_symlink_artifact_is_rejected_when_supported(tmp_path: Path) -> None:
    root, artifact_root, bindings, state = _initialize(tmp_path)
    original = artifact_root / bindings["code"]["path"]
    replacement = original.with_name("replacement.py")
    replacement.write_bytes(original.read_bytes())
    original.unlink()
    try:
        original.symlink_to(replacement)
    except OSError:
        pytest.skip("symlink creation is unavailable on this host")
    with pytest.raises(registry.SharedPAExperimentRegistryError, match="symlink or reparse"):
        registry.append_event(
            root=root, artifact_root=artifact_root, entry_type="EXPERIMENT_PREDECLARED",
            payload=_predeclaration(bindings, artifact_root), recorded_at_utc="2026-07-28T12:00:00Z",
            trusted_checkpoint=state.trusted_checkpoint(),
            allow_unbound_test_only=True,
        )


def test_unbound_registry_refuses_authoritative_append(tmp_path: Path) -> None:
    root, artifact_root, bindings, state = _initialize(tmp_path)
    assert state.authority_state == registry.UNBOUND_AUTHORITY
    with pytest.raises(registry.SharedPAExperimentRegistryError, match="UNBOUND"):
        registry.append_event(
            root=root,
            artifact_root=artifact_root,
            entry_type="EXPERIMENT_PREDECLARED",
            payload=_predeclaration(bindings, artifact_root),
            trusted_checkpoint=state.trusted_checkpoint(),
        )


def test_equivalent_json_and_python_reserialization_is_not_a_new_candidate(tmp_path: Path) -> None:
    root, artifact_root, bindings, first, _ = _append_predeclaration(tmp_path)
    feature = artifact_root / "bound/feature-reserialized.bin"
    feature.write_text('{\n  "features" : [ "strict_prior_rate" ]\n}\n', encoding="utf-8")
    code = artifact_root / "bound/code-reserialized.bin"
    code.write_text("def candidate( ):\n    return None\n", encoding="utf-8")
    changed = dict(bindings)
    changed["feature"] = {"path": "bound/feature-reserialized.bin", "sha256": registry.sha256_file(feature)}
    changed["code"] = {"path": "bound/code-reserialized.bin", "sha256": registry.sha256_file(code)}
    payload = _predeclaration(
        changed,
        artifact_root,
        candidate_id="renamed_equivalent",
        prior_attempts=[first.entries[0]["entry_hash"]],
    )
    assert payload["equivalence_fingerprint"] == first.entries[0]["payload"]["equivalence_fingerprint"]
    with pytest.raises(registry.SharedPAExperimentRegistryError, match="equivalent candidate"):
        registry.append_event(
            root=root,
            artifact_root=artifact_root,
            entry_type="EXPERIMENT_PREDECLARED",
            payload=payload,
            recorded_at_utc="2026-07-28T12:01:00Z",
            trusted_checkpoint=first.trusted_checkpoint(),
            allow_unbound_test_only=True,
        )


def test_output_namespace_is_globally_unique_across_design_families(tmp_path: Path) -> None:
    root, artifact_root, bindings, first, first_payload = _append_predeclaration(tmp_path)
    grid = artifact_root / "bound/grid-distinct.bin"
    grid.write_bytes(b'{"grid":{"strength":[999]}}\n')
    changed = dict(bindings)
    changed["grid"] = {"path": "bound/grid-distinct.bin", "sha256": registry.sha256_file(grid)}
    payload = _predeclaration(
        changed,
        artifact_root,
        candidate_id="different_design",
        output_namespace=first_payload["output_namespace"],
    )
    with pytest.raises(registry.SharedPAExperimentRegistryError, match="reserved globally"):
        registry.append_event(
            root=root,
            artifact_root=artifact_root,
            entry_type="EXPERIMENT_PREDECLARED",
            payload=payload,
            recorded_at_utc="2026-07-28T12:01:00Z",
            trusted_checkpoint=first.trusted_checkpoint(),
            allow_unbound_test_only=True,
        )


def test_external_authority_binds_append_and_old_checkpoint_becomes_rollback(tmp_path: Path) -> None:
    root, artifact_root, bindings, state, expectation_path, expectation_sha, checkpoint_path, checkpoint_sha = _bound_initialize(tmp_path)
    payload = _predeclaration(bindings, artifact_root)
    receipt_path = tmp_path / "external-authority/append-00000001.json"
    receipt_sha = _append_receipt(
        receipt_path,
        state=state,
        entry_type="EXPERIMENT_PREDECLARED",
        payload=payload,
        artifact_root=artifact_root,
    )
    appended = registry.append_event(
        root=root,
        artifact_root=artifact_root,
        entry_type="EXPERIMENT_PREDECLARED",
        payload=payload,
        external_release_expectation_path=expectation_path,
        expected_release_expectation_sha256=expectation_sha,
        trusted_checkpoint_path=checkpoint_path,
        expected_trusted_checkpoint_sha256=checkpoint_sha,
        append_receipt_path=receipt_path,
        expected_append_receipt_sha256=receipt_sha,
    )
    assert appended.entry_count == 1
    with pytest.raises(registry.SharedPAExperimentRegistryError, match="stale, replaced, or rolled back"):
        registry.verify_registry(
            root=root,
            artifact_root=artifact_root,
            external_release_expectation_path=expectation_path,
            expected_release_expectation_sha256=expectation_sha,
            trusted_checkpoint_path=checkpoint_path,
            expected_trusted_checkpoint_sha256=checkpoint_sha,
        )


def test_checkpoint_replacement_digest_fails_closed(tmp_path: Path) -> None:
    root, artifact_root, _, _, expectation_path, expectation_sha, checkpoint_path, checkpoint_sha = _bound_initialize(tmp_path)
    _make_writable(checkpoint_path)
    checkpoint_path.write_bytes(b'{}\n')
    with pytest.raises(registry.SharedPAExperimentRegistryError, match="digest changed or was replaced"):
        registry.verify_registry(
            root=root,
            artifact_root=artifact_root,
            external_release_expectation_path=expectation_path,
            expected_release_expectation_sha256=expectation_sha,
            trusted_checkpoint_path=checkpoint_path,
            expected_trusted_checkpoint_sha256=checkpoint_sha,
        )


def test_alternate_policy_bytes_fail_external_release_binding(tmp_path: Path) -> None:
    root, artifact_root, _, _, expectation_path, expectation_sha, checkpoint_path, checkpoint_sha = _bound_initialize(tmp_path)
    policy_path = artifact_root / "config/shared_pa_experiment_registry_v1.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    policy["registry_id"] = "alternate_registry"
    policy_path.write_bytes(registry.canonical_bytes(policy) + b"\n")
    with pytest.raises(registry.SharedPAExperimentRegistryError, match="exact policy"):
        registry.verify_registry(
            root=root,
            artifact_root=artifact_root,
            external_release_expectation_path=expectation_path,
            expected_release_expectation_sha256=expectation_sha,
            trusted_checkpoint_path=checkpoint_path,
            expected_trusted_checkpoint_sha256=checkpoint_sha,
        )


@pytest.mark.parametrize(
    ("sequence", "recorded_at", "message"),
    [(2, "2026-07-28T12:00:00Z", "nonmonotonic"), (1, "2026-07-27T23:59:59Z", "predates")],
)
def test_external_append_receipt_rejects_nonmonotonic_or_backdated(
    tmp_path: Path, sequence: int, recorded_at: str, message: str,
) -> None:
    root, artifact_root, bindings, state, expectation_path, expectation_sha, checkpoint_path, checkpoint_sha = _bound_initialize(tmp_path)
    payload = _predeclaration(bindings, artifact_root)
    receipt_path = tmp_path / "external-authority/invalid-append.json"
    receipt_sha = _append_receipt(
        receipt_path,
        state=state,
        entry_type="EXPERIMENT_PREDECLARED",
        payload=payload,
        artifact_root=artifact_root,
        sequence=sequence,
        recorded_at=recorded_at,
    )
    with pytest.raises(registry.SharedPAExperimentRegistryError, match=message):
        registry.append_event(
            root=root,
            artifact_root=artifact_root,
            entry_type="EXPERIMENT_PREDECLARED",
            payload=payload,
            external_release_expectation_path=expectation_path,
            expected_release_expectation_sha256=expectation_sha,
            trusted_checkpoint_path=checkpoint_path,
            expected_trusted_checkpoint_sha256=checkpoint_sha,
            append_receipt_path=receipt_path,
            expected_append_receipt_sha256=receipt_sha,
        )


def test_caller_timestamp_cannot_override_external_append_receipt(tmp_path: Path) -> None:
    root, artifact_root, bindings, state, expectation_path, expectation_sha, checkpoint_path, checkpoint_sha = _bound_initialize(tmp_path)
    payload = _predeclaration(bindings, artifact_root)
    receipt_path = tmp_path / "external-authority/append-with-trusted-time.json"
    receipt_sha = _append_receipt(
        receipt_path,
        state=state,
        entry_type="EXPERIMENT_PREDECLARED",
        payload=payload,
        artifact_root=artifact_root,
        recorded_at="2026-07-28T12:00:00Z",
    )
    with pytest.raises(registry.SharedPAExperimentRegistryError, match="caller timestamp"):
        registry.append_event(
            root=root,
            artifact_root=artifact_root,
            entry_type="EXPERIMENT_PREDECLARED",
            payload=payload,
            recorded_at_utc="2026-07-28T11:59:59Z",
            external_release_expectation_path=expectation_path,
            expected_release_expectation_sha256=expectation_sha,
            trusted_checkpoint_path=checkpoint_path,
            expected_trusted_checkpoint_sha256=checkpoint_sha,
            append_receipt_path=receipt_path,
            expected_append_receipt_sha256=receipt_sha,
        )


@pytest.mark.parametrize("field", ["candidate_id", "output_namespace", "hypothesis"])
def test_append_receipt_for_candidate_a_rejects_governance_substitution(
    tmp_path: Path, field: str,
) -> None:
    root, artifact_root, bindings, state, expectation_path, expectation_sha, checkpoint_path, checkpoint_sha = _bound_initialize(tmp_path)
    authorized_payload = _predeclaration(bindings, artifact_root)
    receipt_path = tmp_path / "external-authority/append-candidate-a.json"
    receipt_sha = _append_receipt(
        receipt_path,
        state=state,
        entry_type="EXPERIMENT_PREDECLARED",
        payload=authorized_payload,
        artifact_root=artifact_root,
    )
    substituted = json.loads(json.dumps(authorized_payload))
    replacements = {
        "candidate_id": "candidate_b",
        "output_namespace": "experiments/candidate_b/locked",
        "hypothesis": "A different governance statement over the identical predictive design.",
    }
    substituted[field] = replacements[field]
    assert registry.experiment_equivalence_fingerprint(substituted, artifact_root) == authorized_payload["equivalence_fingerprint"]
    with pytest.raises(registry.SharedPAExperimentRegistryError, match="complete canonical payload"):
        registry.append_event(
            root=root,
            artifact_root=artifact_root,
            entry_type="EXPERIMENT_PREDECLARED",
            payload=substituted,
            external_release_expectation_path=expectation_path,
            expected_release_expectation_sha256=expectation_sha,
            trusted_checkpoint_path=checkpoint_path,
            expected_trusted_checkpoint_sha256=checkpoint_sha,
            append_receipt_path=receipt_path,
            expected_append_receipt_sha256=receipt_sha,
        )


def test_checkpoint_reparse_ancestor_is_rejected_when_supported(tmp_path: Path) -> None:
    real = tmp_path / "real-authority"
    real.mkdir()
    redirected = tmp_path / "redirected-authority"
    try:
        redirected.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink or junction creation is unavailable on this host")
    checkpoint = redirected / "trusted.json"
    with pytest.raises(registry.SharedPAExperimentRegistryError, match="symlink or reparse"):
        registry._absolute_canonical_path(checkpoint, "trusted checkpoint path")


def test_schema_documents_parse_and_forbid_unknown_top_level_fields() -> None:
    for path in sorted((ROOT / "config/schemas").glob("shared_pa_experiment_registry_*_v1.schema.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        assert value["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert value["additionalProperties"] is False


def test_artifact_manifest_binds_every_delivered_artifact() -> None:
    manifest_path = ROOT / "config" / "shared_pa_experiment_registry_v1_artifact_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["source_base_commit"] == "9f9d839187550f7a7a6da3b4bfdb25b2dff3e794"
    paths = [row["path"] for row in manifest["artifacts"]]
    assert paths == sorted(paths)
    assert len(paths) == len(set(paths))
    for row in manifest["artifacts"]:
        artifact = ROOT / row["path"]
        assert artifact.is_file()
        assert artifact.stat().st_size == row["size_bytes"]
        assert registry.sha256_file(artifact) == row["sha256"]
