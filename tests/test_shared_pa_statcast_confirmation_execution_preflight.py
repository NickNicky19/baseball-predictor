"""Exact no-network tests for the frozen June 28 confirmation execution path."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import capture_shared_pa_statcast_confirmation_v2 as capture  # noqa: E402
from scripts import verify_shared_pa_statcast_confirmation_v2 as independent  # noqa: E402
V1_SHA = "7078857cc1815e5c8dcecd667a9b6d69079ef31c1fc68057473d1fb1dc9ceebd"
PLAN_SHA = "0bcc3c9482ea67189e414502b9b58e6445cd66efeb27c309c5bcff4864108402"
LEDGER_SHA = "4f7aea30afdbff76fedd695ef3bd83bb23e5395f4db9496405552433695d9c56"
JULY_WORKFLOW_SHA = "a79e486acbc1c9a3666f26d6ec3635c5547a226a8138f1182c66e34616a0371c"
GAMES = list(range(717575, 717590))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: str) -> dict:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def body(*, terminal_newline: bool = True, mime_body: bool = False) -> bytes:
    header = load("config/shared_pa_statcast_source_contract_v2_proposal.json")["expected_csv_header"]["ordered_columns"]
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n", quoting=csv.QUOTE_ALL)
    writer.writerow(header)
    if not mime_body:
        for game_index, game_pk in enumerate(GAMES, 1):
            for side_index, side in enumerate(("Top", "Bot"), 1):
                row = {name: "" for name in header}
                row.update({
                    "game_date": "2023-06-28", "game_type": "R", "game_pk": str(game_pk),
                    "at_bat_number": str(game_index * 10 + side_index), "pitch_number": "1",
                    "batter": str(100000 + game_index * 2 + side_index), "pitcher": str(200000 + game_index),
                    "inning_topbot": side, "inning": "1", "home_team": f"H{game_index}", "away_team": f"A{game_index}",
                    "description": "called_strike", "type": "S", "zone": "5", "balls": "0", "strikes": "0",
                    "stand": "R", "p_throws": "R"
                })
                writer.writerow([row[name] for name in header])
    text = output.getvalue()
    if not terminal_newline:
        text = text[:-1]
    return b"\xef\xbb\xbf" + text.encode("utf-8")


def response(raw: bytes, content_type: str = "application/download; charset=utf-8", *, transport_complete: bool = True) -> capture.CapturedResponse:
    return capture.CapturedResponse(200, raw, {"content-type": content_type, "content-length": str(len(raw))}, "https://baseballsavant.mlb.com/statcast_search/csv?all=true&batter_stands=&game_date_gt=2023-06-28&game_date_lt=2023-06-28&group_by=name&hfAB=&hfBBL=&hfBBT=&hfFlag=&hfGT=R%7C&hfInn=&hfNewZones=&hfOuts=&hfPR=&hfPT=&hfRO=&hfSA=&hfSea=&hfSit=&hfZ=&home_road=&metric_1=&min_abs=0&min_pitches=0&min_results=0&opponent=&pitcher_throws=&player_event_sort=h_launch_speed&player_type=pitcher&position=&sort_col=pitches&sort_order=desc&stadium=&team=&type=details", "2026-08-02T12:00:00Z", "2026-08-02T12:00:01Z", transport_complete)


def validate(raw: bytes, resp: capture.CapturedResponse | None = None) -> dict:
    return capture.validate_response(raw, resp or response(raw), load("config/shared_pa_statcast_confirmation_sample_2023-06-28_request_plan_v1.json"), load("config/shared_pa_statcast_source_contract_v2_proposal.json"), load("config/shared_pa_statcast_source_contract_v1.json"))


class ConfirmationPreflightTests(unittest.TestCase):
    def test_01_response_remains_unseen(self):
        tracked = subprocess.run(["git", "-C", str(ROOT), "ls-files"], check=True, capture_output=True, text=True).stdout.splitlines()
        self.assertFalse(any("shared_pa_statcast_v2_confirmation_2023-06-28_v1" in item or "response-attempt-01" in item for item in tracked))

    def test_02_date_and_game_set_are_frozen(self):
        plan = load("config/shared_pa_statcast_confirmation_sample_2023-06-28_request_plan_v1.json")
        self.assertEqual(sha(ROOT / "config/shared_pa_statcast_confirmation_sample_2023-06-28_request_plan_v1.json"), PLAN_SHA)
        self.assertEqual(plan["selection"]["selected_date"], "2023-06-28")
        self.assertEqual([x["game_pk"] for x in plan["certified_games"]], GAMES)

    def test_03_source_contract_v1_is_immutable(self):
        self.assertEqual(sha(ROOT / "config/shared_pa_statcast_source_contract_v1.json"), V1_SHA)

    def test_04_old_july_workflow_is_byte_identical_and_v1_only(self):
        path = ROOT / ".github/workflows/shared-pa-statcast-sample-capture-execution-v1.yml"
        self.assertEqual(sha(path), JULY_WORKFLOW_SHA)
        text = path.read_text(encoding="utf-8")
        self.assertNotIn("shared_pa_statcast_source_contract_v2_proposal", text)
        self.assertIn("ATTEMPT_3_SPENT_ATTEMPT_4_UNAUTHORIZED", text)

    def test_05_old_july_attempt_4_is_unreachable(self):
        contract = load("config/shared_pa_statcast_confirmation_execution_contract_v1.json")
        capture.verify_frozen_dependencies(ROOT, contract)
        old = load("config/shared_pa_statcast_sample_attempt_history_20260802_v3.json")
        self.assertEqual(old["remaining_attempts"], [{"attempt_number": 4, "status": "UNUSED_UNAUTHORIZED"}])

    def test_06_confirmation_attempt_one_stays_unused(self):
        self.assertEqual(sha(ROOT / "config/shared_pa_statcast_confirmation_sample_2023-06-28_attempt_history_v1.json"), LEDGER_SHA)
        ledger = load("config/shared_pa_statcast_confirmation_sample_2023-06-28_attempt_history_v1.json")
        self.assertEqual(ledger["attempts"], [])
        self.assertFalse(ledger["next_attempt"]["consumed"])

    def test_07_application_download_is_not_sufficient(self):
        raw = b"not,csv\n"
        self.assertEqual(validate(raw)["status"], "FAIL")

    def test_08_complete_synthetic_csv_passes_without_release(self):
        raw = body()
        result = validate(raw)
        self.assertEqual(result["status"], "PASS", result)
        self.assertFalse(result["source_release_produced"])
        self.assertFalse(result["scored_predictions_produced"])

    def test_09_html_json_and_savant_errors_fail(self):
        for raw in (b"<html>bad</html>", b'\xef\xbb\xbf {"error":"bad"}', b"Baseball Savant error"):
            with self.subTest(raw=raw[:20]): self.assertEqual(validate(raw)["status"], "FAIL")

    def test_10_header_drift_fails(self):
        raw = body().replace(b'"pitch_type"', b'"pitch_kind"', 1)
        self.assertIn("header", validate(raw)["failures"])

    def test_11_truncated_or_unclosed_final_row_fails(self):
        raw = body()[:-17] + b'"unterminated'
        self.assertEqual(validate(raw)["status"], "FAIL")

    def test_12_missing_terminal_newline_passes_only_with_stronger_gates(self):
        raw = body(terminal_newline=False)
        self.assertEqual(validate(raw)["status"], "PASS")
        no_completion = response(raw, transport_complete=False)
        no_completion.headers.pop("content-length")
        self.assertIn("transport_completion", validate(raw, no_completion)["failures"])

    def test_13_mixed_newlines_fail(self):
        raw = body().replace(b"\n", b"\r\n", 1)
        self.assertIn("mixed_or_bare_cr_newlines", validate(raw)["failures"])

    def test_14_canonical_binding_uses_game_side_and_team_id(self):
        result = validate(body())
        self.assertEqual(result["canonical_team_binding"], "game_pk+official_date+side+official_team_id")
        self.assertFalse(result["raw_abbreviations_used_as_identity"])

    def test_15_abbreviation_only_or_team_contradiction_fails(self):
        raw = body().replace(b'"H1","A1"', b'"",""', 1)
        self.assertIn("raw_team_pair_contradiction", validate(raw)["failures"])

    def test_16_unknown_game_or_missing_side_fails(self):
        raw = body().replace(b'"717575"', b'"999999"', 1)
        self.assertEqual(validate(raw)["status"], "FAIL")
        raw2 = body().replace(b'"Bot"', b'"Top"', 1)
        self.assertIn("batting_side_coverage", validate(raw2)["failures"])

    def test_17_raw_bytes_are_not_mutated(self):
        raw = body(terminal_newline=False)
        before = hashlib.sha256(raw).hexdigest()
        validate(raw)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), before)

    def test_18_socket_guard_blocks_lower_level_networking(self):
        with capture.socket_denial_guard():
            with self.assertRaises(RuntimeError): socket.socket()

    def test_19_no_redirect_handler_refuses_redirect_request(self):
        self.assertIsNone(capture._NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://example.invalid"))

    def test_20_capture_calls_transport_once_and_preserves_invalid_evidence(self):
        authority = {"authority_package_sha256": "1" * 64, "observed_human_authorization_sha256": "2" * 64}
        calls = []
        def fake(url, maximum):
            calls.append((url, maximum)); return response(b"<html>invalid</html>")
        with tempfile.TemporaryDirectory() as td:
            output = Path(td) / "out"
            result = capture.capture(ROOT, ROOT / "config/shared_pa_statcast_confirmation_execution_contract_v1.json", authority, output, fake)
            self.assertEqual(len(calls), 1)
            self.assertEqual(result["scientific_disposition"], "INVALID_QUARANTINED_NON_PROMOTABLE")
            self.assertTrue((output / "statcast-v2-confirmation-2023-06-28/response-attempt-01.csv").is_file())
            self.assertFalse(result["source_qualified"]); self.assertFalse(result["promotable"])

    def test_21_synthetic_valid_capture_and_independent_verifier(self):
        authority = {"authority_package_sha256": "1" * 64, "observed_human_authorization_sha256": "2" * 64}
        raw = body(terminal_newline=False)
        with tempfile.TemporaryDirectory() as td:
            output = Path(td) / "out"
            result = capture.capture(ROOT, ROOT / "config/shared_pa_statcast_confirmation_execution_contract_v1.json", authority, output, lambda *_: response(raw))
            self.assertEqual(result["technical_validation_status"], "PASS")
            independent_report = independent.verify(output, ROOT)
            self.assertEqual(independent_report["status"], "PASS", independent_report)
            self.assertFalse(independent_report["source_qualified"])

    def test_22_attempt3_retention_gate_is_mandatory_and_exact(self):
        contract = load("config/shared_pa_statcast_confirmation_execution_contract_v1.json")
        gate = contract["temporary_attempt_3_retention_gate"]
        receipt = {"schema_version": "shared-pa-statcast-attempt-3-temporary-retention-gate-v1", "status": "PASS", "checked_at_utc": "2026-08-02T12:00:00Z", "zip_verified": True, "raw_verified": True, "tracked_identities_verified": True}
        for key in ("artifact_id", "artifact_name", "artifact_zip_bytes", "artifact_zip_sha256", "raw_member", "raw_response_bytes", "raw_response_sha256", "expires_at_utc"): receipt[key] = gate[key]
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "gate.json"; path.write_text(json.dumps(receipt), encoding="utf-8")
            capture.verify_attempt3_retention_receipt(path, contract)
            receipt["artifact_id"] = 1; path.write_text(json.dumps(receipt), encoding="utf-8")
            with self.assertRaises(capture.ConfirmationError): capture.verify_attempt3_retention_receipt(path, contract)

    def test_23_authority_package_cannot_authorize_itself(self):
        authority_path = ROOT / "config/shared_pa_statcast_confirmation_attempt_01_authority_package_20260802_v1.json"
        runtime_path = ROOT / "config/shared_pa_statcast_confirmation_runtime_authority_v1.json"
        authority = load("config/shared_pa_statcast_confirmation_attempt_01_authority_package_20260802_v1.json")
        self.assertFalse(authority["preparation_is_execution_authorization"])
        self.assertFalse(authority["execution_authorized"])
        self.assertTrue(authority["explicit_human_execution_authorization_required"])
        authority_sha = sha(authority_path)
        prepared = subprocess.run(
            ["git", "-C", str(ROOT), "show", "2e84656c2aab69c20c9cbe0cb0e466ad9d68a5b6:.github/workflows/shared-pa-statcast-v2-confirmation-execution-v1.yml"],
            check=True, capture_output=True,
        ).stdout
        self.assertEqual(hashlib.sha256(prepared).hexdigest(), authority["bindings"]["workflow_sha256"])
        with tempfile.TemporaryDirectory() as td:
            workflow_path = Path(td) / "prepared-workflow.yml"
            workflow_path.write_bytes(prepared)
            with self.assertRaises(capture.ConfirmationError):
                capture.verify_authority(ROOT, authority_path, authority_sha, workflow_path, sha(workflow_path), runtime_path, sha(runtime_path), authority["carrier_commit"], authority_sha)
            capture.verify_authority(ROOT, authority_path, authority_sha, workflow_path, sha(workflow_path), runtime_path, sha(runtime_path), authority["carrier_commit"], "f" * 64)

    def test_24_workflow_has_two_guards_and_failure_publication(self):
        text = (ROOT / ".github/workflows/shared-pa-statcast-v2-confirmation-execution-v1.yml").read_text(encoding="utf-8")
        self.assertIn("blocked_application_transport", text)
        self.assertIn("socket_denial_guard", text)
        self.assertGreater(text.index("Upload confirmation evidence"), text.index("Finalize confirmation evidence"))
        self.assertGreater(text.index("Report preserved terminal status"), text.index("Upload confirmation evidence"))
        self.assertIn("always()", text)
        self.assertNotIn("aws s3", text.lower())

    def test_25_request_accounting_and_blindness_are_preserved(self):
        ledger = load("config/shared_pa_statcast_confirmation_sample_2023-06-28_attempt_history_v1.json")
        authority = load("config/shared_pa_statcast_confirmation_attempt_01_authority_package_20260802_v1.json")
        self.assertEqual(ledger["global_real_external_statcast_request_count"], 3)
        self.assertFalse(authority["june_28_response_seen"])
        self.assertFalse(authority["attempt_1_reserved_or_consumed_by_preparation"])


if __name__ == "__main__":
    unittest.main()
