from __future__ import annotations

import csv
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
V1 = ROOT / "config/shared_pa_statcast_source_contract_v1.json"
V2 = ROOT / "config/shared_pa_statcast_source_contract_v2_proposal.json"
TEAM = ROOT / "config/shared_pa_statcast_canonical_team_identity_policy_v1.json"
EOF = ROOT / "config/shared_pa_statcast_csv_eof_completeness_policy_v1.json"
PLAN = ROOT / "config/shared_pa_statcast_confirmation_sample_2023-06-28_request_plan_v1.json"
LEDGER = ROOT / "config/shared_pa_statcast_confirmation_sample_2023-06-28_attempt_history_v1.json"
OLD_LEDGER = ROOT / "config/shared_pa_statcast_sample_attempt_history_20260802_v3.json"
REPLAY = ROOT / "reports/shared_pa_statcast_attempt3_v2_development_replay_20260802_v1.json"
SCRIPT = ROOT / "scripts/validate_shared_pa_statcast_source_contract_v2_proposal.py"


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_validator():
    spec = importlib.util.spec_from_file_location("v2_validator", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class SourceContractV2ProposalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.v = load_validator()
        cls.v2 = load_json(V2)
        cls.header = cls.v2["expected_csv_header"]["ordered_columns"]
        stream = io.StringIO(newline="")
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(cls.header)
        writer.writerow(["x"] * len(cls.header))
        cls.valid_no_newline = b"\xef\xbb\xbf" + stream.getvalue().rstrip("\n").encode()

    def body(self, raw: bytes, *, content_length: str | None = None):
        return self.v.validate_csv_body(
            raw,
            expected_header=self.header,
            receipt_bytes=len(raw),
            receipt_sha256=hashlib.sha256(raw).hexdigest(),
            content_length=str(len(raw)) if content_length is None else content_length,
        )

    def test_01_v1_is_byte_identical_and_attempt3_remains_invalid_under_v1(self):
        self.assertEqual(hashlib.sha256(V1.read_bytes()).hexdigest(), "7078857cc1815e5c8dcecd667a9b6d69079ef31c1fc68057473d1fb1dc9ceebd")
        replay = load_json(REPLAY)
        self.assertEqual(replay["attempt_3_v1_disposition"], "CONSUMED_INVALID_UNDER_V1_QUARANTINED_NON_PROMOTABLE")
        self.assertFalse(replay["source_qualified"])

    def test_02_v2_is_inactive_and_absent_from_active_workflow(self):
        self.assertEqual(self.v2["status"], "INACTIVE_PROPOSAL_ONLY")
        self.assertFalse(self.v2["activation_barriers"]["active_capture_workflow_may_select_this_proposal"])
        workflow = (ROOT / ".github/workflows/shared-pa-statcast-sample-capture-execution-v1.yml").read_text(encoding="utf-8")
        self.assertNotIn("shared_pa_statcast_source_contract_v2_proposal.json", workflow)
        self.assertIn("shared_pa_statcast_source_contract_v1.json", workflow)

    def test_03_generic_mime_alone_is_insufficient_and_octet_stream_is_rejected(self):
        policy = self.v2["conditional_content_type_policy"]
        self.assertFalse(policy["application_download_is_trusted_by_mime_alone"])
        self.assertFalse(policy["application_octet_stream_allowed"])
        self.assertFalse(policy["wildcard_mime_allowed"])

    def test_04_html_json_and_savant_error_signatures_fail(self):
        for raw, failure in (
            (b"<!doctype html><html><body>error</body></html>", "html_signature"),
            (b'{"error":"failure"}', "json_signature"),
            (b"Baseball Savant error,request failed", "savant_error_signature"),
        ):
            self.assertIn(failure, self.body(raw)["failures"])

    def test_05_exact_header_missing_unexpected_duplicate_and_reordering_fail(self):
        for header in (self.header[:-1], self.header + ["unexpected"], self.header + [self.header[-1]], list(reversed(self.header))):
            raw = (",".join(header) + "\n" + ",".join(["x"] * len(header))).encode()
            self.assertIn("ordered_header_mismatch", self.body(raw)["failures"])

    def test_06_truncated_final_rows_and_stale_content_length_fail(self):
        truncated = self.valid_no_newline[:-2]
        self.assertIn("row_field_count_mismatch", self.body(truncated)["failures"])
        self.assertIn("content_length_mismatch", self.body(self.valid_no_newline, content_length=str(len(self.valid_no_newline) + 5))["failures"])

    def test_07_no_terminal_newline_passes_only_all_stronger_gates(self):
        result = self.body(self.valid_no_newline)
        self.assertTrue(result["passed"])
        self.assertFalse(result["terminal_newline_present"])
        without_receipt = self.v.validate_csv_body(self.valid_no_newline, expected_header=self.header, receipt_bytes=0, receipt_sha256="0" * 64, content_length=None)
        self.assertFalse(without_receipt["passed"])

    def test_08_raw_bytes_are_never_modified(self):
        before = bytes(self.valid_no_newline)
        result = self.body(before)
        self.assertEqual(before, self.valid_no_newline)
        self.assertEqual(result["raw_sha256_before"], result["raw_sha256_after"])

    def test_09_team_abbreviations_are_observations_not_identities(self):
        policy = load_json(TEAM)
        self.assertFalse(policy["canonical_identity"]["raw_abbreviation_is_identity"])
        self.assertFalse(policy["versioned_source_label_mapping"]["included_in_this_policy"])
        self.assertIn("game_pk", policy["canonical_identity"]["tuple"])
        self.assertIn("certified_official_team_id", policy["canonical_identity"]["tuple"])

    def test_10_certified_side_binding_succeeds_and_contradictions_fail_closed(self):
        policy = load_json(TEAM)
        self.assertIn("home_or_away_side", policy["canonical_identity"]["tuple"])
        self.assertIn("unresolved batting-side or fielding-side contradiction", policy["fail_closed_conditions"])
        replay = load_json(REPLAY)
        self.assertTrue(replay["canonical_team_identity"]["all_games_have_top_and_bottom_batting_side"])
        self.assertEqual(replay["canonical_team_identity"]["unresolved_identity_contradictions"], 0)

    def test_11_replay_is_development_only_and_cannot_release_source(self):
        replay = load_json(REPLAY)
        self.assertEqual(replay["label"], "DEVELOPMENT_REPLAY_ONLY")
        self.assertEqual(replay["decision"], "ATTEMPT_3_V2_DEVELOPMENT_REPLAY_PASSED")
        self.assertFalse(replay["source_release_produced"])
        self.assertFalse(replay["independent_confirmation"])

    def test_12_confirmation_date_is_distinct_deterministic_and_response_blind(self):
        plan = load_json(PLAN)
        selection = plan["selection"]
        self.assertEqual(selection["selected_date"], "2023-06-28")
        self.assertNotEqual(selection["selected_date"], "2023-07-25")
        self.assertEqual(selection["candidate_count"], 80)
        self.assertEqual(selection["selected_zero_based_index"], selection["candidate_count"] // 2)
        self.assertTrue(selection["rule_frozen_before_source_response_inspection"])
        self.assertFalse(selection["selection_used_statcast_source_bytes"])
        self.assertFalse(selection["selection_used_player_outcomes"])

    def test_13_confirmation_scope_has_15_games_and_30_unique_teams(self):
        games = load_json(PLAN)["certified_games"]
        self.assertEqual(len(games), 15)
        self.assertEqual([row["game_pk"] for row in games], sorted(row["game_pk"] for row in games))
        teams = [team for row in games for team in (row["away_team_id"], row["home_team_id"])]
        self.assertEqual(len(set(teams)), 30)
        self.assertTrue(all(row["official_date"] == "2023-06-28" and row["game_type"] == "R" for row in games))

    def test_14_new_ledger_is_unreserved_and_old_attempt4_remains_unauthorized(self):
        ledger = load_json(LEDGER)
        old = load_json(OLD_LEDGER)
        self.assertEqual(ledger["attempts"], [])
        self.assertEqual(ledger["next_attempt"]["status"], "UNUSED_UNAUTHORIZED")
        self.assertFalse(ledger["next_attempt"]["reserved"])
        self.assertEqual(old["remaining_attempts"], [{"attempt_number": 4, "status": "UNUSED_UNAUTHORIZED"}])
        self.assertFalse(old["attempt_4_authorized"])

    def test_15_no_real_request_or_capture_is_authorized(self):
        plan = load_json(PLAN)
        ledger = load_json(LEDGER)
        self.assertFalse(plan["capture_authorized"])
        self.assertFalse(plan["external_request_authorized"])
        self.assertFalse(ledger["workflow_dispatch_capture_authorized"])
        self.assertEqual(ledger["global_real_external_statcast_request_count"], 3)

    def test_16_eof_policy_requires_receipt_and_all_structural_gates(self):
        policy = load_json(EOF)
        self.assertFalse(policy["terminal_newline_policy"]["terminal_newline_required_as_sole_completeness_proof"])
        self.assertTrue(policy["raw_byte_policy"]["preserve_exact_bytes"])
        self.assertIn("receipt byte count or SHA-256 mismatch", policy["fail_closed_conditions"])


if __name__ == "__main__":
    unittest.main()
