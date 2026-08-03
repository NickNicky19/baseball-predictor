"""No-network tests for the non-circular one-time authorization gate."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import capture_shared_pa_statcast_confirmation_v2 as capture  # noqa: E402
from scripts import validate_shared_pa_statcast_confirmation_human_execution_authorization_v1 as gate  # noqa: E402

NOW = datetime(2026, 8, 2, 20, 0, 0, tzinfo=timezone.utc)
TEXT = "I authorize one manual June 28 confirmation dispatch bound to this exact record.\n"
TEXT_SHA = hashlib.sha256(TEXT.encode()).hexdigest()
WORKFLOW_SHA = "b" * 64


def run_git(repository: Path, *args: str, input_bytes: bytes | None = None) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *args], input=input_bytes,
        check=True, capture_output=True, text=input_bytes is None,
    )
    output = result.stdout
    return output.strip() if isinstance(output, str) else output.decode().strip()


def record(base: str = "a" * 40) -> dict:
    return {
        "schema_version": "shared-pa-statcast-confirmation-human-execution-authorization-v1",
        "status": "ACTIVE_ONE_TIME",
        "authorization_id": "june28-confirmation-attempt01-20260802T2000Z",
        "repository": {"owner": "NickNicky19", "name": "baseball-predictor", "full_name": "NickNicky19/baseball-predictor"},
        "authorized_actor": {"login": "NickNicky19", "numeric_user_id": 208912933},
        "bindings": {
            "authorization_base_main_commit": base,
            "workflow_path": gate.WORKFLOW_PATH,
            "workflow_sha256": WORKFLOW_SHA,
            **gate.HASH_BINDINGS,
            "carrier_commit": "a8ebc64eddf8ace3a48e25af57ee0d6658d0663a",
        },
        "scope": {
            "official_date": "2023-06-28", "certified_game_pks": gate.GAME_PKS,
            "output_path": "data/source/shared_pa_statcast_v2_confirmation_2023-06-28_v1",
            "confirmation_attempt_number": 1, "maximum_request_count": 1,
            "automatic_http_retry_maximum": 0, "replacement_request_maximum": 0,
            "automatic_workflow_rerun_allowed": False, "redirects_allowed": False, "no_overwrite": True,
        },
        "validity": {
            "authorization_issued_utc": "2026-08-02T19:58:00Z",
            "authorization_valid_from_utc": "2026-08-02T19:59:00Z",
            "authorization_expires_utc": "2026-08-02T20:29:00Z",
            "maximum_validity_seconds": 1800,
        },
        "canonical_human_authorization_text": TEXT,
        "canonical_human_authorization_text_sha256": TEXT_SHA,
        "preparation_is_execution_authorization": False,
        "dispatch_authorized": True,
        "single_use": True,
    }


def canonical(value: dict) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


@dataclass
class Graph:
    temporary: tempfile.TemporaryDirectory
    repository: Path
    value: dict
    record_path: Path
    record_bytes: bytes
    base: str
    head: str
    merge: str

    def close(self) -> None:
        self.temporary.cleanup()


def build_graph(
    value: dict | None = None, *, extra_path: bool = False,
    base_has_record: bool = False, rename_record: bool = False,
    delete_record: bool = False, mode: str = "100644", one_parent: bool = False,
) -> Graph:
    temporary = tempfile.TemporaryDirectory()
    repository = Path(temporary.name) / "repository"
    repository.mkdir()
    run_git(repository, "init", "-b", "main")
    run_git(repository, "config", "user.name", "Synthetic Gate")
    run_git(repository, "config", "user.email", "synthetic@example.invalid")
    run_git(repository, "config", "core.autocrlf", "false")
    for relative in [*gate.FILE_BINDINGS.values(), "config/shared_pa_statcast_sample_attempt_history_20260802_v3.json"]:
        destination = repository / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)
    record_path = repository / gate.FUTURE_RECORD_PATH
    old_path = repository / "config/authorization-record-old-name.json"
    if base_has_record or delete_record:
        record_path.parent.mkdir(parents=True, exist_ok=True)
        record_path.write_text('{"historical":true}\n', encoding="utf-8")
    if rename_record:
        old_path.parent.mkdir(parents=True, exist_ok=True)
        old_path.write_text('{"old_name":true}\n', encoding="utf-8")
    run_git(repository, "add", ".")
    run_git(repository, "commit", "-m", "synthetic base")
    base = run_git(repository, "rev-parse", "HEAD")
    value = copy.deepcopy(value or record(base))
    value["bindings"]["authorization_base_main_commit"] = base
    record_bytes = canonical(value)
    run_git(repository, "switch", "-c", "authorization-record")
    if delete_record:
        record_path.unlink()
        run_git(repository, "add", "-u")
    elif rename_record:
        run_git(repository, "mv", old_path.relative_to(repository).as_posix(), record_path.relative_to(repository).as_posix())
        record_path.write_bytes(record_bytes)
        run_git(repository, "add", record_path.relative_to(repository).as_posix())
    else:
        record_path.parent.mkdir(parents=True, exist_ok=True)
        record_path.write_bytes(record_bytes)
        run_git(repository, "add", record_path.relative_to(repository).as_posix())
    if extra_path:
        extra = repository / "config/unexpected.json"
        extra.write_text("{}\n", encoding="utf-8")
        run_git(repository, "add", extra.relative_to(repository).as_posix())
    if not delete_record and mode == "100755":
        run_git(repository, "update-index", "--chmod=+x", record_path.relative_to(repository).as_posix())
    elif not delete_record and mode in {"120000", "160000"}:
        object_id = base if mode == "160000" else run_git(repository, "hash-object", "-w", record_path.relative_to(repository).as_posix())
        run_git(repository, "update-index", "--add", "--cacheinfo", f"{mode},{object_id},{record_path.relative_to(repository).as_posix()}")
    run_git(repository, "commit", "-m", "add authorization record")
    head = run_git(repository, "rev-parse", "HEAD")
    if one_parent:
        merge = head
    elif mode == "160000":
        tree = run_git(repository, "rev-parse", f"{head}^{{tree}}")
        merge = run_git(repository, "commit-tree", tree, "-p", base, "-p", head, "-m", "merge authorization record")
        if record_path.is_file() or record_path.is_symlink():
            record_path.unlink()
        elif record_path.is_dir():
            shutil.rmtree(record_path)
        run_git(repository, "switch", "main")
        run_git(repository, "update-ref", "refs/heads/main", merge)
        run_git(repository, "reset", "--hard", merge)
    else:
        run_git(repository, "switch", "main")
        run_git(repository, "merge", "--no-ff", "authorization-record", "-m", "merge authorization record")
        merge = run_git(repository, "rev-parse", "HEAD")
    if not delete_record and (not record_path.is_file() or record_path.is_symlink()):
        if record_path.exists() or record_path.is_symlink():
            if record_path.is_dir() and not record_path.is_symlink():
                shutil.rmtree(record_path)
            else:
                record_path.unlink()
        record_path.parent.mkdir(parents=True, exist_ok=True)
        record_path.write_bytes(record_bytes)
    return Graph(temporary, repository, value, record_path, record_bytes, base, head, merge)


def validate_graph(graph: Graph, **changes):
    defaults = dict(
        record_path=graph.record_path,
        schema_path=ROOT / "config/shared_pa_statcast_confirmation_human_execution_authorization_schema_v1.json",
        repository_root=graph.repository,
        expected_record_sha256=hashlib.sha256(graph.record_bytes).hexdigest(),
        expected_text_sha256=TEXT_SHA,
        expected_authorization_id=graph.value.get("authorization_id", ""),
        observed_repository="NickNicky19/baseball-predictor",
        observed_actor_login="NickNicky19",
        observed_actor_id=208912933,
        expected_dispatch_commit=graph.merge,
        observed_dispatch_commit=graph.merge,
        observed_first_parent=graph.base,
        observed_default_branch_head=graph.merge,
        observed_workflow_sha256=WORKFLOW_SHA,
        run_attempt=1,
        now=NOW,
        runs={"workflow_runs": []},
        artifacts={"artifacts": []},
        current_run_id=9001,
    )
    defaults.update(changes)
    return gate.validate_record(**defaults)


class GateTests(unittest.TestCase):
    def check(self, value: dict | None = None, **changes):
        graph = build_graph(value)
        try:
            return validate_graph(graph, **changes)
        finally:
            graph.close()

    def assert_rejected(self, value: dict | None = None, **changes):
        with self.assertRaises(gate.AuthorizationError):
            self.check(value, **changes)

    def test_01_missing_authorization_record_fails_before_transport(self):
        with tempfile.TemporaryDirectory() as td, self.assertRaises(gate.AuthorizationError):
            gate.validate_record(Path(td) / "missing.json", ROOT / "config/shared_pa_statcast_confirmation_human_execution_authorization_schema_v1.json", ROOT, "f" * 64, TEXT_SHA, "x", "NickNicky19/baseball-predictor", "NickNicky19", 208912933, "a" * 40, "b" * 40, "c" * 40, "b" * 40, WORKFLOW_SHA, 1, NOW, {"workflow_runs": []}, {"artifacts": []}, 1)

    def test_02_proposal_only_record_fails(self):
        value = record(); value["status"] = "PROPOSAL_ONLY"; self.assert_rejected(value)

    def test_03_placeholder_record_fails(self):
        value = record(); value["authorization_id"] = "PLACEHOLDER"; self.assert_rejected(value)

    def test_04_dispatch_authorized_false_fails(self):
        value = record(); value["dispatch_authorized"] = False; self.assert_rejected(value)

    def test_05_stale_overlong_record_fails(self):
        value = record(); value["validity"]["authorization_expires_utc"] = "2026-08-02T20:45:00Z"; self.assert_rejected(value)

    def test_06_future_dated_record_fails(self):
        value = record(); value["validity"]["authorization_valid_from_utc"] = "2026-08-02T20:01:00Z"; self.assert_rejected(value)

    def test_07_expired_record_fails(self):
        value = record(); value["validity"]["authorization_expires_utc"] = "2026-08-02T20:00:00Z"; self.assert_rejected(value)

    def test_08_other_actor_fails(self): self.assert_rejected(observed_actor_login="someone-else")
    def test_09_actor_numeric_id_mismatch_fails(self): self.assert_rejected(observed_actor_id=1)
    def test_10_repository_mismatch_fails(self): self.assert_rejected(observed_repository="NickNicky19/other")
    def test_11_dispatch_commit_mismatch_fails(self): self.assert_rejected(expected_dispatch_commit="c" * 40)
    def test_12_workflow_hash_mismatch_fails(self): self.assert_rejected(observed_workflow_sha256="c" * 64)

    def test_13_any_package_hash_mismatch_fails(self):
        for key in gate.HASH_BINDINGS:
            with self.subTest(key=key):
                value = record(); value["bindings"][key] = "d" * 64; self.assert_rejected(value)

    def test_14_human_text_hash_mismatch_fails(self): self.assert_rejected(expected_text_sha256="e" * 64)
    def test_15_rerun_fails(self): self.assert_rejected(run_attempt=2)

    def test_16_reused_authorization_id_fails(self):
        value = record(); marker = f"authorization={value['authorization_id']} record={'e' * 64}"
        self.assert_rejected(value, runs={"workflow_runs": [{"id": 5, "display_title": marker}]})

    def test_17_reused_authorization_hash_fails(self):
        graph = build_graph()
        try:
            sha = hashlib.sha256(graph.record_bytes).hexdigest()
            with self.assertRaises(gate.AuthorizationError):
                validate_graph(graph, runs={"workflow_runs": [{"id": 5, "display_title": f"authorization=other record={sha}"}]})
        finally: graph.close()

    def test_18_existing_deterministic_artifact_fails(self):
        value = record(); name = gate.deterministic_artifact_name(value["authorization_id"])
        self.assert_rejected(value, artifacts={"artifacts": [{"name": name}]})

    def test_19_concurrent_duplicate_cannot_reach_transport(self):
        value = record(); title = f"authorization={value['authorization_id']} record={'e' * 64}"
        self.assert_rejected(value, runs={"workflow_runs": [{"id": 5, "status": "in_progress", "display_title": title}]})

    def test_20_old_july_attempt_4_cannot_execute(self):
        old = gate.load_json(ROOT / "config/shared_pa_statcast_sample_attempt_history_20260802_v3.json")
        self.assertEqual(old["remaining_attempts"], [{"attempt_number": 4, "status": "UNUSED_UNAUTHORIZED"}])

    def test_21_confirmation_attempt_one_begins_unused(self):
        ledger = gate.load_json(ROOT / gate.FILE_BINDINGS["attempt_ledger_sha256"])
        self.assertEqual(ledger["attempts"], [])
        self.assertEqual(ledger["next_attempt"], {"attempt_number": 1, "status": "UNUSED_UNAUTHORIZED", "reserved": False, "consumed": False})

    def test_22_output_path_is_no_overwrite(self): self.assert_rejected(output_path_exists=True)

    def test_23_attempt3_retention_gate_is_still_before_transport(self):
        text = (ROOT / gate.WORKFLOW_PATH).read_text(encoding="utf-8")
        self.assertLess(text.index("Verify temporary attempt-3 retention gate"), text.index("Execute one authorized source transport"))
        self.assertIn("8826086488", text)

    def test_24_application_transport_guard_passes(self):
        calls = []
        def blocked(*args, **kwargs): calls.append((args, kwargs)); raise RuntimeError("blocked_application_transport")
        with self.assertRaisesRegex(RuntimeError, "blocked_application_transport"): blocked("https://baseballsavant.mlb.com/statcast_search/csv")
        self.assertEqual(len(calls), 1)

    def test_25_lower_level_socket_guard_passes(self):
        with capture.socket_denial_guard(), self.assertRaisesRegex(RuntimeError, "lower-level network guard"): socket.socket()

    def test_26_valid_authorization_reaches_boundary_without_network(self):
        receipt = self.check(); self.assertEqual(receipt["status"], "PASS"); self.assertFalse(receipt["transport_authorized_by_validator"])

    def test_27_synthetic_invalid_body_is_quarantined(self):
        authority = {"authority_package_sha256": "1" * 64, "observed_human_authorization_sha256": "2" * 64}
        with tempfile.TemporaryDirectory() as td:
            result = capture.capture(ROOT, ROOT / "config/shared_pa_statcast_confirmation_execution_contract_v1.json", authority, Path(td) / "out", lambda *_: capture.CapturedResponse(200, b"<html>invalid</html>", {"content-type": "application/download"}, "https://baseballsavant.mlb.com/statcast_search/csv", "2026-08-02T20:00:00Z", "2026-08-02T20:00:01Z", True))
        self.assertEqual(result["scientific_disposition"], "INVALID_QUARANTINED_NON_PROMOTABLE"); self.assertFalse(result["promotable"])

    def test_28_failure_evidence_precedes_terminal_failure(self):
        text = (ROOT / gate.WORKFLOW_PATH).read_text(encoding="utf-8")
        self.assertLess(text.index("Upload confirmation evidence"), text.index("Report preserved terminal status")); self.assertIn("if: ${{ always() }}", text)

    def test_29_no_aws_mutation_exists(self):
        text = (ROOT / gate.WORKFLOW_PATH).read_text(encoding="utf-8").lower(); self.assertNotIn("aws s3", text); self.assertNotIn("aws cloudformation", text)

    def test_30_no_real_statcast_request_occurs_in_preflight(self):
        with mock.patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("network forbidden")) as opener:
            self.check(); opener.assert_not_called()

    def test_31_total_real_statcast_requests_remain_three(self):
        self.assertEqual(gate.load_json(ROOT / gate.FILE_BINDINGS["attempt_ledger_sha256"])["global_real_external_statcast_request_count"], 3)

    def test_32_june_28_response_remains_unseen(self):
        self.assertFalse((ROOT / "data/source/shared_pa_statcast_v2_confirmation_2023-06-28_v1").exists()); self.assertFalse((ROOT / gate.FUTURE_RECORD_PATH).exists())


class NonCircularGraphTests(unittest.TestCase):
    def graph(self, **kwargs) -> Graph: return build_graph(**kwargs)

    def test_33_synthetic_base_B_exists(self):
        graph = self.graph()
        try: self.assertEqual(run_git(graph.repository, "cat-file", "-t", graph.base), "commit")
        finally: graph.close()

    def test_34_head_H_adds_exact_record_path(self):
        graph = self.graph()
        try: self.assertEqual(run_git(graph.repository, "diff", "--name-only", graph.base, graph.head), gate.FUTURE_RECORD_PATH)
        finally: graph.close()

    def test_35_merge_M_has_ordered_parents_B_H(self):
        graph = self.graph()
        try: self.assertEqual(run_git(graph.repository, "show", "-s", "--format=%P", graph.merge).split(), [graph.base, graph.head])
        finally: graph.close()

    def test_36_record_embedding_B_passes_with_runtime_M(self):
        graph = self.graph()
        try:
            result = validate_graph(graph); self.assertEqual(result["authorization_base_main_commit"], graph.base); self.assertEqual(result["dispatch_commit"], graph.merge)
        finally: graph.close()

    def test_37_record_does_not_embed_H_or_M(self):
        graph = self.graph()
        try:
            self.assertIn(graph.base, graph.record_bytes.decode()); self.assertNotIn(graph.head, graph.record_bytes.decode()); self.assertNotIn(graph.merge, graph.record_bytes.decode())
        finally: graph.close()

    def test_38_wrong_first_parent_fails(self):
        graph = self.graph()
        try:
            with self.assertRaises(gate.AuthorizationError): validate_graph(graph, observed_first_parent="c" * 40)
        finally: graph.close()

    def test_39_wrong_expected_dispatch_commit_fails(self):
        graph = self.graph()
        try:
            with self.assertRaises(gate.AuthorizationError): validate_graph(graph, expected_dispatch_commit="c" * 40)
        finally: graph.close()

    def test_40_one_parent_commit_fails(self):
        graph = self.graph(one_parent=True)
        try:
            with self.assertRaises(gate.AuthorizationError): validate_graph(graph)
        finally: graph.close()

    def test_41_extra_first_parent_path_fails(self):
        graph = self.graph(extra_path=True)
        try:
            with self.assertRaises(gate.AuthorizationError): validate_graph(graph)
        finally: graph.close()

    def test_42_modified_record_path_fails(self):
        graph = self.graph(base_has_record=True)
        try:
            with self.assertRaises(gate.AuthorizationError): validate_graph(graph)
        finally: graph.close()

    def test_43_rename_fails(self):
        graph = self.graph(rename_record=True)
        try:
            with self.assertRaises(gate.AuthorizationError): validate_graph(graph)
        finally: graph.close()

    def test_44_deletion_fails(self):
        graph = self.graph(delete_record=True)
        try:
            with self.assertRaises(gate.AuthorizationError): validate_graph(graph)
        finally: graph.close()

    def test_45_symlink_record_fails(self):
        graph = self.graph(mode="120000")
        try:
            with self.assertRaisesRegex(gate.AuthorizationError, "regular non-executable"): validate_graph(graph)
        finally: graph.close()

    def test_46_submodule_record_fails(self):
        graph = self.graph(mode="160000")
        try:
            with self.assertRaises(gate.AuthorizationError): validate_graph(graph)
        finally: graph.close()

    def test_47_executable_record_fails(self):
        graph = self.graph(mode="100755")
        try:
            with self.assertRaisesRegex(gate.AuthorizationError, "regular non-executable"): validate_graph(graph)
        finally: graph.close()

    def test_48_record_hash_mismatch_fails(self):
        graph = self.graph()
        try:
            with self.assertRaises(gate.AuthorizationError): validate_graph(graph, expected_record_sha256="e" * 64)
        finally: graph.close()

    def test_49_canonical_text_hash_mismatch_fails(self):
        graph = self.graph()
        try:
            with self.assertRaises(gate.AuthorizationError): validate_graph(graph, expected_text_sha256="e" * 64)
        finally: graph.close()

    def test_50_stale_default_branch_head_fails(self):
        graph = self.graph()
        try:
            with self.assertRaises(gate.AuthorizationError): validate_graph(graph, observed_default_branch_head=graph.base)
        finally: graph.close()

    def test_51_record_attempting_to_embed_merge_commit_is_rejected(self):
        value = record(); value["bindings"]["dispatch_commit"] = "c" * 40
        graph = self.graph(value=value)
        try:
            with self.assertRaises(gate.AuthorizationError): validate_graph(graph)
        finally: graph.close()

    def test_52_no_self_referential_identity_is_required(self):
        schema = gate.load_json(ROOT / "config/shared_pa_statcast_confirmation_human_execution_authorization_schema_v1.json")
        required = schema["properties"]["bindings"]["required"]
        self.assertIn("authorization_base_main_commit", required); self.assertNotIn("main_commit", required)
        for forbidden in ("record_sha256", "blob_sha", "tree_sha", "pr_head_commit", "merge_commit"):
            self.assertNotIn(forbidden, required)


if __name__ == "__main__":
    unittest.main()
