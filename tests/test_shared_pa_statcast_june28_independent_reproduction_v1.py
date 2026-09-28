from __future__ import annotations

import ast
import csv
import hashlib
import importlib.util
import json
import io
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import platform
from datetime import datetime, timezone


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/reproduce_shared_pa_statcast_june28_v1.py"
AUTHORITY_PATH = ROOT / "config/shared_pa_statcast_june28_independent_reproduction_authority_package_v1.json"
AUTHORITY = json.loads(AUTHORITY_PATH.read_text(encoding="utf-8"))
SPEC = importlib.util.spec_from_file_location("clean_room_reproduction", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class IndependentReproductionPreparationTests(unittest.TestCase):
    def test_package_is_inactive_and_non_authorizing(self) -> None:
        for key in ("preparation_is_reproduction_authorization", "reproduction_authorized", "source_qualification_authorized", "promotion_authorized", "network_authorized", "source_request_authorized"):
            self.assertIs(AUTHORITY[key], False)

    def test_exact_artifact_and_19_member_inventory_are_bound(self) -> None:
        self.assertEqual(AUTHORITY["artifact"]["id"], 8868838408)
        self.assertEqual(AUTHORITY["artifact"]["zip_sha256"], "4c77f6d9572d8bc21ab79a06b0d8b77e2a04231375de000ee9e37dc7a2b530b5")
        self.assertEqual(len(AUTHORITY["inventory"]), 19)
        self.assertEqual(len({item["path"] for item in AUTHORITY["inventory"]}), 19)

    def test_raw_response_identity_is_bound(self) -> None:
        raw = next(item for item in AUTHORITY["inventory"] if item["path"] == AUTHORITY["artifact"]["raw_member"])
        self.assertEqual(raw["bytes"], 2928702)
        self.assertEqual(raw["sha256"], "4b8a99e4a4130dcb7fb409f0637a25054f93d536bb57a02051b6d1003716f237")

    def test_implementation_does_not_import_capture_or_embedded_verifier(self) -> None:
        tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import): imports.extend(alias.name for alias in node.names)
            if isinstance(node, ast.ImportFrom): imports.append(node.module or "")
        self.assertFalse(any("capture_shared_pa_statcast_confirmation_v2" in name for name in imports))
        self.assertFalse(any("verify_shared_pa_statcast_confirmation_v2" in name for name in imports))

    def test_socket_guard_blocks_and_restores(self) -> None:
        original = socket.socket
        with MODULE.no_network():
            with self.assertRaisesRegex(RuntimeError, "network guard"):
                socket.socket()
        self.assertIs(socket.socket, original)

    def test_output_is_create_only(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('args.output.open("xb")', source)
        self.assertIn('raise ReproductionError("output report already exists")', source)

    def test_provider_receipt_is_required_because_zip_does_not_self_attest_id(self) -> None:
        self.assertIs(AUTHORITY["artifact"]["provider_metadata_is_self_attested_inside_zip"], False)
        self.assertIs(AUTHORITY["artifact"]["external_immutable_retrieval_receipt_required"], True)

    def test_embedded_verifier_is_partial_only(self) -> None:
        embedded = AUTHORITY["existing_embedded_verifier"]
        self.assertEqual(embedded["status"], "PARTIAL_CORROBORATION_ONLY")
        self.assertIs(embedded["satisfies_complete_independent_reproduction"], False)
        self.assertIs(embedded["use_as_trusted_input"], False)

    def test_successor_ledger_and_official_pa_receipt_are_required(self) -> None:
        inputs = AUTHORITY["required_external_inputs"]
        self.assertIn("append_only_successor_attempt_ledger", inputs)
        self.assertIn("official_pa_denominator_receipt", inputs)

    def test_source_qualification_requires_separate_durable_gate(self) -> None:
        rule = AUTHORITY["decision_rule"]["overall_source_qualification"]
        self.assertIn("durable retention", rule)
        self.assertFalse(AUTHORITY["source_qualification_authorized"])

    def test_future_execution_authorization_must_bind_every_mutable_input(self) -> None:
        required = set(AUTHORITY["future_execution_authorization_contract"]["required_bindings"])
        self.assertIn("authority_package_sha256", required)
        self.assertIn("reproduction_verifier_sha256", required)
        self.assertIn("official_pa_denominator_receipt_sha256", required)
        self.assertIn("exact_create_only_output_path", required)
        self.assertFalse(AUTHORITY["future_execution_authorization_contract"]["authority_or_verifier_self_authorization_allowed"])

    def test_execution_authorization_rejects_unbound_receipt_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            artifact = temp / "artifact.zip"; artifact.write_bytes(b"synthetic")
            receipt_paths = {}
            for name in ("artifact_retrieval_receipt", "terminal_run_receipt", "successor_ledger", "terminal_adjudication", "official_pa_receipt", "official_pa_source", "official_pa_release", "official_pa_verifier"):
                path = temp / f"{name}.json"; path.write_text("{}\n", encoding="utf-8"); receipt_paths[name] = path
            head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()
            execution = {
                "schema_version": "shared-pa-statcast-june28-independent-reproduction-execution-authorization-v1",
                "status": "AUTHORIZED", "execution_authorized": True,
                "repository": "NickNicky19/baseball-predictor", "exact_execution_commit": head,
                "authority_package_sha256": MODULE.sha256_file(AUTHORITY_PATH),
                "reproduction_verifier_sha256": MODULE.sha256_file(SCRIPT),
                "python_runtime": {"implementation": platform.python_implementation(), "version": platform.python_version()},
                "network_disabled": True, "artifact_zip_sha256": MODULE.sha256_file(artifact),
                "artifact_retrieval_receipt_sha256": MODULE.sha256_file(receipt_paths["artifact_retrieval_receipt"]),
                "post_terminal_run_receipt_sha256": MODULE.sha256_file(receipt_paths["terminal_run_receipt"]),
                "successor_attempt_ledger_sha256": MODULE.sha256_file(receipt_paths["successor_ledger"]),
                "terminal_adjudication_sha256": MODULE.sha256_file(receipt_paths["terminal_adjudication"]),
                "official_pa_denominator_receipt_sha256": "0" * 64,
                "official_pa_source_sha256": MODULE.sha256_file(receipt_paths["official_pa_source"]),
                "official_pa_certified_release_sha256": MODULE.sha256_file(receipt_paths["official_pa_release"]),
                "official_pa_independent_verifier_sha256": MODULE.sha256_file(receipt_paths["official_pa_verifier"]),
                "output_path": "reports/future-reproduction-result.json",
                "authorization_id": "synthetic-test-only", "authorized_at_utc": "2026-08-03T00:00:00Z",
                "valid_from_utc": "2026-08-03T01:00:00Z", "expires_at_utc": "2026-08-04T00:00:00Z",
            }
            auth_path = temp / "execution.json"
            auth_path.write_text(json.dumps(execution), encoding="utf-8")
            auth_sha = MODULE.sha256_file(auth_path)
            failures = MODULE.verify_execution_authorization(
                execution, ROOT, AUTHORITY_PATH, artifact, receipt_paths, ROOT / execution["output_path"],
                auth_sha, auth_path, datetime(2026, 8, 3, 2, tzinfo=timezone.utc),
            )
            self.assertIn("execution_input_sha256:official_pa_receipt", failures)

            failures = MODULE.verify_execution_authorization(
                execution, ROOT, AUTHORITY_PATH, artifact, receipt_paths, ROOT / execution["output_path"],
                "f" * 64, auth_path, datetime(2026, 8, 3, 2, tzinfo=timezone.utc),
            )
            self.assertIn("execution_authorization_external_sha256", failures)

    def test_successor_ledger_and_terminal_adjudication_fail_closed(self) -> None:
        ledger = {
            "schema_version": "shared-pa-statcast-confirmation-sample-2023-06-28-attempt-history-v2",
            "predecessor_path": "config/shared_pa_statcast_confirmation_sample_2023-06-28_attempt_history_v1.json",
            "predecessor_sha256": AUTHORITY["immutable_dependencies"]["config/shared_pa_statcast_confirmation_sample_2023-06-28_attempt_history_v1.json"],
            "total_real_statcast_requests": 4,
            "july_2023_attempts": {str(i): {"status": "CONSUMED_QUARANTINED"} for i in (1, 2, 3)},
            "confirmation_attempt_1": {
                "status": "CONSUMED", "workflow_run_id": 30846344146, "run_attempt": 1,
                "artifact_id": 8868838408, "artifact_zip_sha256": AUTHORITY["artifact"]["zip_sha256"],
                "external_request_count": 1, "automatic_http_retry_count": 0,
                "replacement_request_count": 0, "workflow_rerun_count": 0,
                "source_qualified": False, "promotable": False,
                "reservation_timestamp_utc": "2026-08-03T19:34:48Z", "consumed_timestamp_utc": "2026-08-03T19:34:48Z",
            },
            "append_only": True, "attempt_reset_or_reuse_allowed": False,
        }
        ledger["july_2023_attempts"]["4"] = {"status": "UNUSED_UNAUTHORIZED"}
        predecessor = json.loads((ROOT / ledger["predecessor_path"]).read_text(encoding="utf-8"))
        ledger["predecessor_document"] = predecessor
        ledger["old_july_attempt_history_document"] = json.loads((ROOT / predecessor["old_july_25_attempt_history"]["path"]).read_text(encoding="utf-8"))
        ledger.pop("july_2023_attempts")
        self.assertEqual(MODULE.verify_successor_ledger(ledger, AUTHORITY, ROOT), [])
        ledger["attempt_4_authorized"] = True
        self.assertIn("successor_ledger_shape", MODULE.verify_successor_ledger(ledger, AUTHORITY, ROOT))
        ledger.pop("attempt_4_authorized")
        ledger["confirmation_attempt_1"]["external_request_count"] = 2
        self.assertIn("successor_confirmation:external_request_count", MODULE.verify_successor_ledger(ledger, AUTHORITY, ROOT))
        adjudication = {
            "schema_version": "shared-pa-statcast-june28-confirmation-terminal-adjudication-v1",
            "workflow_run_id": 30846344146, "run_attempt": 1, "dispatch_commit": AUTHORITY["artifact"]["dispatch_commit"],
            "artifact_id": 8868838408, "artifact_zip_sha256": AUTHORITY["artifact"]["zip_sha256"],
            "raw_response_sha256": AUTHORITY["artifact"]["raw_sha256"], "technical_validation": "PASS",
            "source_qualified": False, "promotable": False, "confirmation_attempt_1": "CONSUMED",
            "total_real_statcast_requests": 4,
            "terminal_status": "CONFIRMATION_PENDING_SEPARATE_DURABLE_RETENTION_AND_INDEPENDENT_REPRODUCTION",
        }
        adjudication.update({
            "raw_response_byte_count": 2928702, "raw_content_type": "application/download; charset=utf-8",
            "normalized_mime": "application/download",
            "reservation_sha256": "ca327f43c4026a66620ce2b2a4379b2e3496138d72c61ab0b6c42cc8e1d1d553",
            "request_receipt_sha256": "7be672c8c77ca4156abca3ea5887e3ddf6d37cbd2108f8159e44cadd62f03424",
            "result_record_sha256": "59d74a1315ae9f6a917c86974d59f9cf264659b1dfd2d605c939c564e3ca3a65",
            "validation_record_sha256": "02f6be82227f0645b5d074ab7aae66070edf2e6081483efd0120f53a72087868",
            "capture_manifest_sha256": "b784cdd552d2c16a7f69469d9a3649e66e259014311128cf1e75421f90b150f7",
            "successor_attempt_ledger_sha256": "9" * 64,
            "request_started_at_utc": "2026-08-03T19:34:48.605410Z",
            "response_observed_at_utc": "2026-08-03T19:34:55.347057Z",
            "external_request_count": 1, "automatic_http_retry_count": 0,
            "replacement_request_count": 0, "workflow_rerun_count": 0,
        })
        self.assertEqual(MODULE.verify_terminal_adjudication(adjudication, AUTHORITY, "9" * 64), [])
        adjudication["qualified"] = True
        self.assertIn("terminal_adjudication_shape", MODULE.verify_terminal_adjudication(adjudication, AUTHORITY, "9" * 64))
        adjudication.pop("qualified")
        adjudication["promotable"] = True
        self.assertIn("terminal_adjudication:promotable", MODULE.verify_terminal_adjudication(adjudication, AUTHORITY, "9" * 64))

    def test_official_pa_receipt_recomputes_components_and_rejects_statcast_derivation(self) -> None:
        plan = json.loads((ROOT / "config/shared_pa_statcast_confirmation_sample_2023-06-28_request_plan_v1.json").read_text(encoding="utf-8"))
        games = plan["certified_games"]
        source = {
            "schema_version": "shared-pa-statcast-june28-certified-official-pa-source-v1",
            "official_date": "2023-06-28", "certified_source_release_sha256": "1" * 64,
            "per_game": [
                {"game_pk": game["game_pk"], "home_team_id": game["home_team_id"], "away_team_id": game["away_team_id"], "home_plate_appearances": 35, "away_plate_appearances": 35}
                for game in games
            ],
        }
        source_sha = hashlib.sha256(MODULE.canonical_json(source)).hexdigest()
        receipt = {
            "schema_version": "shared-pa-statcast-june28-certified-official-pa-denominator-receipt-v1",
            "official_date": "2023-06-28", "derivation_uses_statcast_distinct_at_bat_number": False,
            "certified_source_release_sha256": "1" * 64, "independent_verifier_sha256": "2" * 64,
            "official_pa_source_sha256": source_sha,
            "per_game": [{"game_pk": game["game_pk"], "official_pa_count": 70} for game in games],
            "official_pa_count": 1050,
        }
        total, failures = MODULE.verify_official_pa_receipt(receipt, source, games, source_sha, "1" * 64, "2" * 64)
        self.assertEqual((total, failures), (1050, []))
        receipt["official_pa_count"] = 1049
        receipt["derivation_uses_statcast_distinct_at_bat_number"] = True
        _total, failures = MODULE.verify_official_pa_receipt(receipt, source, games, source_sha, "1" * 64, "2" * 64)
        self.assertIn("official_pa_sum", failures)
        self.assertIn("official_pa_source_method", failures)
        receipt["certified_source_release_sha256"] = "invented"
        source["per_game"][0]["home_plate_appearances"] = True
        _total, failures = MODULE.verify_official_pa_receipt(receipt, source, games, source_sha, "1" * 64, "2" * 64)
        self.assertIn("official_pa_provenance", failures)
        self.assertIn("official_pa_source_components", failures)

    def test_invalid_zone_and_raw_rehash_are_mandatory(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('if plate["invalid_zone"] != 0: failures.append("invalid_zone")', source)
        self.assertIn('if sha256_bytes(raw) != authority["artifact"]["raw_sha256"]:', source)

    def test_mutated_dependency_hash_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            relative = next(iter(AUTHORITY["immutable_dependencies"]))
            target = repo / relative
            target.parent.mkdir(parents=True)
            target.write_bytes(b"wrong")
            failures = MODULE.verify_dependencies(repo, {"immutable_dependencies": {relative: "0" * 64}})
            self.assertEqual(failures, [f"immutable_dependency:{relative}"])

    def test_all_declared_dependency_paths_exist_and_hash_match(self) -> None:
        self.assertEqual(MODULE.verify_dependencies(ROOT, AUTHORITY), [])

    def test_source_bundle_is_a_bound_composite_not_a_fabricated_file(self) -> None:
        self.assertEqual(
            AUTHORITY["bound_composite_identities"]["source_bundle_sha256"],
            "81861595f8eb4b83772220bdad2fb556fc06ec3640a1a4f2084255e8f5183033",
        )
        self.assertNotIn("config/shared_pa_statcast_confirmation_source_bundle_v1.json", AUTHORITY["immutable_dependencies"])

    def test_preparation_manifest_exactly_binds_changed_scope_and_non_self_hashes(self) -> None:
        manifest = json.loads((ROOT / "reports/shared_pa_statcast_june28_independent_reproduction_preparation_manifest_v1.json").read_text(encoding="utf-8"))
        expected_paths = [
            "config/shared_pa_statcast_june28_independent_reproduction_authority_package_v1.json",
            "reports/shared_pa_statcast_june28_independent_reproduction_execution_plan_v1.json",
            "reports/shared_pa_statcast_june28_independent_reproduction_preparation_manifest_v1.json",
            "scripts/reproduce_shared_pa_statcast_june28_v1.py",
            "tests/test_shared_pa_statcast_june28_independent_reproduction_v1.py",
        ]
        self.assertEqual(manifest["changed_paths"], expected_paths)
        self.assertEqual(manifest["canonical_changed_path_list_sha256"], "1c49191608233e4012eac41e8e2c0c0630150ffdc81c612dd6f387b4b897d118")
        self.assertEqual(set(manifest["non_self_file_sha256"]), set(expected_paths) - {expected_paths[2]})
        for relative, expected in manifest["non_self_file_sha256"].items():
            self.assertEqual(MODULE.sha256_file(ROOT / relative), expected)

    def test_execution_window_is_enforced_by_current_time(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("execution_authorization_not_yet_valid", source)
        self.assertIn("execution_authorization_expired", source)
        self.assertIn("datetime.now(timezone.utc)", source)

    def test_clean_runner_requires_isolated_no_bytecode_and_clean_worktree(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('sys.flags.isolated', source)
        self.assertIn('sys.flags.dont_write_bytecode', source)
        self.assertIn('git", "status", "--porcelain"', source)
        self.assertNotIn('--untracked-files=no', source)

    def test_successor_requires_exact_predecessor_documents(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("successor_predecessor_document", source)
        self.assertIn("successor_july_predecessor_document", source)
        self.assertIn('sha256_file(repository / july_path)', source)

    def test_result_binds_execution_audit_and_deterministic_failure_codes(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('"execution_audit": execution_audit', source)
        self.assertNotIn('deterministic_input_failure:{type(exc).__name__}:{exc}', source)

    def test_missing_required_input_is_incomplete_without_network(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "result.json"
            argv = [
                "reproduce", "--artifact-zip", str(root / "missing.zip"),
                "--repository", str(ROOT), "--authority", str(AUTHORITY_PATH),
                "--execution-authorization", str(root / "missing-authorization.json"),
                "--artifact-retrieval-receipt", str(root / "missing-retrieval.json"),
                "--terminal-run-receipt", str(root / "missing-terminal.json"),
                "--successor-ledger", str(root / "missing-ledger.json"),
                "--terminal-adjudication", str(root / "missing-adjudication.json"),
                "--official-pa-receipt", str(root / "missing-pa.json"),
                "--official-pa-source", str(root / "missing-pa-source.json"),
                "--official-pa-certified-release", str(root / "missing-pa-release.json"),
                "--official-pa-independent-verifier", str(root / "missing-pa-verifier.py"),
                "--expected-execution-authorization-sha256", "0" * 64,
                "--output", str(output),
            ]
            with mock.patch("sys.argv", argv):
                self.assertEqual(MODULE.main(), 1)
            result = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "INDEPENDENT_REPRODUCTION_INCOMPLETE")
            self.assertFalse(result["network_used"])
            self.assertFalse(result["source_qualified"])

    def test_canonical_output_is_stable(self) -> None:
        value = {"b": 2, "a": 1}
        payload = MODULE.canonical_json(value)
        self.assertEqual(payload, b'{"a":1,"b":2}\n')
        self.assertEqual(hashlib.sha256(payload).hexdigest(), "e8d38819d39f705646bfb643368eca78f7db476c16471dbc33b941b27326410d")

    def test_html_json_and_truncated_csv_signatures_fail_closed(self) -> None:
        plan = json.loads((ROOT / "config/shared_pa_statcast_confirmation_sample_2023-06-28_request_plan_v1.json").read_text(encoding="utf-8"))
        v1 = json.loads((ROOT / "config/shared_pa_statcast_source_contract_v1.json").read_text(encoding="utf-8"))
        v2 = json.loads((ROOT / "config/shared_pa_statcast_source_contract_v2_proposal.json").read_text(encoding="utf-8"))
        for raw, expected in (
            (b"<!doctype html><html><body>error</body></html>", "html_signature"),
            (b'{"error":"rate limited"}', "json_signature"),
            (b'game_pk,game_date\n717575,"unterminated', "strict_csv"),
        ):
            receipt = {
                "byte_count": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "http_status": 200,
                "final_url": plan["request"]["full_url"],
                "response_headers": {"content-type": "application/download"},
                "transport_complete": True,
            }
            _result, failures = MODULE.validate_body(raw, receipt, plan, v1, v2)
            self.assertIn(expected, failures)

    def test_synthetic_complete_body_passes_all_body_gates_without_network(self) -> None:
        plan = json.loads((ROOT / "config/shared_pa_statcast_confirmation_sample_2023-06-28_request_plan_v1.json").read_text(encoding="utf-8"))
        v1 = json.loads((ROOT / "config/shared_pa_statcast_source_contract_v1.json").read_text(encoding="utf-8"))
        v2 = json.loads((ROOT / "config/shared_pa_statcast_source_contract_v2_proposal.json").read_text(encoding="utf-8"))
        header = v2["expected_csv_header"]["ordered_columns"]
        rows = []
        pitch = 0
        description = v1["plate_discipline"]["categories"]["TAKE_CALLED_BALL"][0]
        for game in plan["certified_games"]:
            for side in ("Top", "Bot"):
                pitch += 1
                row = {name: "" for name in header}
                row.update({
                    "game_pk": str(game["game_pk"]), "game_date": "2023-06-28", "game_type": "R",
                    "batter": str(100000 + pitch), "pitcher": str(200000 + pitch),
                    "at_bat_number": str(pitch), "pitch_number": "1", "inning": "1",
                    "inning_topbot": side, "home_team": f"H{game['game_pk']}", "away_team": f"A{game['game_pk']}",
                    "stand": "R", "p_throws": "R", "description": description, "type": "B",
                    "zone": "1", "balls": "0", "strikes": "0",
                })
                rows.append([row[name] for name in header])
        stream = io.StringIO(newline="")
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(header); writer.writerows(rows)
        raw = b"\xef\xbb\xbf" + stream.getvalue().rstrip("\n").encode("utf-8")
        receipt = {
            "byte_count": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
            "http_status": 200, "final_url": plan["request"]["full_url"],
            "response_headers": {"content-type": "application/download", "content-length": str(len(raw))},
            "transport_complete": True,
        }
        result, failures = MODULE.validate_body(raw, receipt, plan, v1, v2)
        self.assertEqual(failures, [])
        self.assertEqual(result["row_count"], 30)
        self.assertEqual(result["game_pks"], AUTHORITY["primary_claims_for_post_calculation_comparison"]["games"])
        self.assertFalse(result["terminal_newline_present"])

    def test_no_source_transport_library_or_endpoint_is_used(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            if isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
        self.assertFalse(any(name == "urllib" or name.startswith("urllib.") for name in imports))
        self.assertFalse(any(name == "requests" or name.startswith("requests.") for name in imports))
        self.assertNotIn("baseballsavant.mlb.com", source)


if __name__ == "__main__":
    unittest.main()
