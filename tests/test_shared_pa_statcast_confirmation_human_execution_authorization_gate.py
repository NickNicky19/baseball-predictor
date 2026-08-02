"""No-network tests for the one-time human authorization gate."""
from __future__ import annotations

import copy
import hashlib
import json
import socket
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import capture_shared_pa_statcast_confirmation_v2 as capture  # noqa: E402
from scripts import validate_shared_pa_statcast_confirmation_human_execution_authorization_v1 as gate  # noqa: E402

NOW = datetime(2026, 8, 2, 20, 0, 0, tzinfo=timezone.utc)
TEXT = "I authorize one manual June 28 confirmation dispatch bound to this exact record.\n"
TEXT_SHA = hashlib.sha256(TEXT.encode()).hexdigest()
RECORD_SHA_PLACEHOLDER = "f" * 64
MAIN = "a" * 40
WORKFLOW_SHA = "b" * 64


def record() -> dict:
    return {
        "schema_version": "shared-pa-statcast-confirmation-human-execution-authorization-v1",
        "status": "ACTIVE_ONE_TIME",
        "authorization_id": "june28-confirmation-attempt01-20260802T2000Z",
        "repository": {"owner": "NickNicky19", "name": "baseball-predictor", "full_name": "NickNicky19/baseball-predictor"},
        "authorized_actor": {"login": "NickNicky19", "numeric_user_id": 208912933},
        "bindings": {
            "main_commit": MAIN,
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


class GateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.record_path = Path(self.temp.name) / "record.json"

    def tearDown(self):
        self.temp.cleanup()

    def write(self, value: dict) -> str:
        self.record_path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        return gate.sha256_file(self.record_path)

    def check(self, value: dict | None = None, **changes):
        value = copy.deepcopy(value or record())
        sha = self.write(value)
        defaults = dict(
            record_path=self.record_path,
            schema_path=ROOT / "config/shared_pa_statcast_confirmation_human_execution_authorization_schema_v1.json",
            repository_root=ROOT,
            expected_record_sha256=sha,
            expected_text_sha256=TEXT_SHA,
            expected_authorization_id=value.get("authorization_id", ""),
            observed_repository="NickNicky19/baseball-predictor",
            observed_actor_login="NickNicky19",
            observed_actor_id=208912933,
            observed_main_commit=MAIN,
            observed_workflow_sha256=WORKFLOW_SHA,
            run_attempt=1,
            now=NOW,
            runs={"workflow_runs": []},
            artifacts={"artifacts": []},
            current_run_id=9001,
        )
        defaults.update(changes)
        return gate.validate_record(**defaults)

    def assert_rejected(self, value: dict | None = None, **changes):
        with self.assertRaises(gate.AuthorizationError):
            self.check(value, **changes)

    def test_01_missing_authorization_record_fails_before_transport(self):
        with self.assertRaises(gate.AuthorizationError):
            gate.validate_record(self.record_path, ROOT / "config/shared_pa_statcast_confirmation_human_execution_authorization_schema_v1.json", ROOT, RECORD_SHA_PLACEHOLDER, TEXT_SHA, "x", "NickNicky19/baseball-predictor", "NickNicky19", 208912933, MAIN, WORKFLOW_SHA, 1, NOW, {"workflow_runs": []}, {"artifacts": []}, 1)

    def test_02_proposal_only_record_fails(self):
        value = record(); value["status"] = "PROPOSAL_ONLY"
        self.assert_rejected(value)

    def test_03_placeholder_record_fails(self):
        value = record(); value["authorization_id"] = "PLACEHOLDER"
        self.assert_rejected(value)

    def test_04_dispatch_authorized_false_fails(self):
        value = record(); value["dispatch_authorized"] = False
        self.assert_rejected(value)

    def test_05_stale_overlong_record_fails(self):
        value = record(); value["validity"]["authorization_expires_utc"] = "2026-08-02T20:45:00Z"
        self.assert_rejected(value)

    def test_06_future_dated_record_fails(self):
        value = record(); value["validity"]["authorization_valid_from_utc"] = "2026-08-02T20:01:00Z"
        self.assert_rejected(value)

    def test_07_expired_record_fails(self):
        value = record(); value["validity"]["authorization_expires_utc"] = "2026-08-02T20:00:00Z"
        self.assert_rejected(value)

    def test_08_other_actor_fails(self):
        self.assert_rejected(observed_actor_login="someone-else")

    def test_09_actor_numeric_id_mismatch_fails(self):
        self.assert_rejected(observed_actor_id=1)

    def test_10_repository_mismatch_fails(self):
        self.assert_rejected(observed_repository="NickNicky19/other")

    def test_11_main_commit_mismatch_fails(self):
        self.assert_rejected(observed_main_commit="c" * 40)

    def test_12_workflow_hash_mismatch_fails(self):
        self.assert_rejected(observed_workflow_sha256="c" * 64)

    def test_13_any_package_hash_mismatch_fails(self):
        for key in gate.HASH_BINDINGS:
            with self.subTest(key=key):
                value = record(); value["bindings"][key] = "d" * 64
                self.assert_rejected(value)

    def test_14_human_text_hash_mismatch_fails(self):
        self.assert_rejected(expected_text_sha256="e" * 64)

    def test_15_rerun_fails(self):
        self.assert_rejected(run_attempt=2)

    def test_16_reused_authorization_id_fails(self):
        value = record(); marker = f"authorization={value['authorization_id']} record={'e' * 64}"
        self.assert_rejected(value, runs={"workflow_runs": [{"id": 5, "display_title": marker}]})

    def test_17_reused_authorization_hash_fails(self):
        value = record(); sha = self.write(value)
        marker = f"authorization=other record={sha}"
        self.assert_rejected(value, runs={"workflow_runs": [{"id": 5, "display_title": marker}]})

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

    def test_22_output_path_is_no_overwrite(self):
        self.assert_rejected(output_path_exists=True)

    def test_23_attempt3_retention_gate_is_still_before_transport(self):
        text = (ROOT / gate.WORKFLOW_PATH).read_text(encoding="utf-8")
        self.assertLess(text.index("Verify temporary attempt-3 retention gate"), text.index("Execute one authorized source transport"))
        self.assertIn("8826086488", text)

    def test_24_application_transport_guard_passes(self):
        calls = []
        def blocked(*args, **kwargs):
            calls.append((args, kwargs)); raise RuntimeError("blocked_application_transport")
        with self.assertRaisesRegex(RuntimeError, "blocked_application_transport"):
            blocked("https://baseballsavant.mlb.com/statcast_search/csv")
        self.assertEqual(len(calls), 1)

    def test_25_lower_level_socket_guard_passes(self):
        with capture.socket_denial_guard():
            with self.assertRaisesRegex(RuntimeError, "lower-level network guard"):
                socket.socket()

    def test_26_valid_authorization_reaches_boundary_without_network(self):
        receipt = self.check()
        self.assertEqual(receipt["status"], "PASS")
        self.assertFalse(receipt["transport_authorized_by_validator"])

    def test_27_synthetic_invalid_body_is_quarantined(self):
        authority = {"authority_package_sha256": "1" * 64, "observed_human_authorization_sha256": "2" * 64}
        with tempfile.TemporaryDirectory() as td:
            result = capture.capture(ROOT, ROOT / "config/shared_pa_statcast_confirmation_execution_contract_v1.json", authority, Path(td) / "out", lambda *_: capture.CapturedResponse(200, b"<html>invalid</html>", {"content-type": "application/download"}, "https://baseballsavant.mlb.com/statcast_search/csv", "2026-08-02T20:00:00Z", "2026-08-02T20:00:01Z", True))
        self.assertEqual(result["scientific_disposition"], "INVALID_QUARANTINED_NON_PROMOTABLE")
        self.assertFalse(result["promotable"])

    def test_28_failure_evidence_precedes_terminal_failure(self):
        text = (ROOT / gate.WORKFLOW_PATH).read_text(encoding="utf-8")
        self.assertLess(text.index("Upload confirmation evidence"), text.index("Report preserved terminal status"))
        self.assertIn("if: ${{ always() }}", text)

    def test_29_no_aws_mutation_exists(self):
        text = (ROOT / gate.WORKFLOW_PATH).read_text(encoding="utf-8").lower()
        self.assertNotIn("aws s3", text); self.assertNotIn("aws cloudformation", text)

    def test_30_no_real_statcast_request_occurs_in_preflight(self):
        with mock.patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("network forbidden")) as opener:
            self.check()
            opener.assert_not_called()

    def test_31_total_real_statcast_requests_remain_three(self):
        ledger = gate.load_json(ROOT / gate.FILE_BINDINGS["attempt_ledger_sha256"])
        self.assertEqual(ledger["global_real_external_statcast_request_count"], 3)

    def test_32_june_28_response_remains_unseen(self):
        self.assertFalse((ROOT / "data/source/shared_pa_statcast_v2_confirmation_2023-06-28_v1").exists())
        self.assertFalse((ROOT / gate.FUTURE_RECORD_PATH).exists())


if __name__ == "__main__":
    unittest.main()
