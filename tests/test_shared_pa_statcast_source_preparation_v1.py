from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess

import pytest

from scripts.capture_direct_batter_pa_source_transport_v2 import RuntimeAuthorization
from scripts.capture_shared_pa_statcast_source_v1 import CaptureError, capture, source_bundle_sha256
from src.data.shared_pa_statcast_source_v1 import (
    StatcastSourceError, ev_launch_angle_counts, load_contract, parse_csv_bytes, sha256_file,
)
from src.evaluation.shared_pa_statcast_stage_a_v1 import run_stage_a, synthetic_csv
from src.evaluation.shared_pa_statcast_historical_source_access_v1 import VerifiedStatcastHistoricalSourceAccess


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config/shared_pa_statcast_source_contract_v1.json"
PARSER_PATH = ROOT / "src/data/shared_pa_statcast_source_v1.py"


def test_stage_a_wires_source_feature_pmf_archive_and_evaluator() -> None:
    contract = load_contract(CONTRACT_PATH)
    result = run_stage_a(contract, sha256_file(CONTRACT_PATH), sha256_file(PARSER_PATH))
    assert result["external_request_count"] == 0
    assert result["source_authority"] == "SYNTHETIC_PREFLIGHT_ONLY"
    assert result["predictions"] != result["mutation_predictions"]
    assert result["evaluator_ingestion"]["status"] == "PASS_SYNTHETIC_ARCHIVE_REPLAY_ONLY"


def test_parser_fails_closed_on_duplicate_unknown_and_date_drift() -> None:
    contract = load_contract(CONTRACT_PATH)
    raw = synthetic_csv(contract)
    games = {700001: {"official_date": "2023-04-01", "home_team_id": 1, "away_team_id": 2,
                      "home_team_code": "HOM", "away_team_code": "AWY"}}
    rows = parse_csv_bytes(raw, contract=contract, expected_date="2023-04-01", certified_games=games)
    assert len(rows) == 6
    lines = raw.decode().splitlines()
    with pytest.raises(StatcastSourceError, match="duplicate"):
        parse_csv_bytes(("\n".join(lines + [lines[1]]) + "\n").encode(), contract=contract,
                        expected_date="2023-04-01", certified_games=games)
    header = lines[0].split(","); values = lines[1].split(",")
    values[header.index("description")] = "new_unknown_description"
    bad = (lines[0] + "\n" + ",".join(values) + "\n" + "\n".join(lines[2:]) + "\n").encode()
    parsed = parse_csv_bytes(bad, contract=contract, expected_date="2023-04-01", certified_games=games)
    from src.data.shared_pa_statcast_source_v1 import plate_discipline_counts
    with pytest.raises(StatcastSourceError, match="unknown"):
        plate_discipline_counts(parsed, contract)
    with pytest.raises(StatcastSourceError, match="date"):
        parse_csv_bytes(raw, contract=contract, expected_date="2023-04-02", certified_games=games)


def test_prepared_plans_are_non_authorizing_and_scope_bound() -> None:
    prep = ROOT / "config/shared_pa_statcast_source_preparation_v1"
    sample = json.loads((prep / "sample_request_plan.json").read_text())
    full = json.loads((prep / "full_request_plan.json").read_text())
    assert sample["external_requests_permitted"] is False and sample["request_count"] == 1
    assert sample["requests"][0]["expected"]["official_date"] == "2023-07-25"
    assert len(sample["requests"][0]["expected"]["certified_games"]) == 15
    assert full["request_count"] == 181 and full["certified_game_count"] == 2415
    assert all("fields" not in item["query"] for item in full["requests"])
    assert all(item["query"]["hfGT"] == "R|" for item in full["requests"])


def test_contact_denominators_do_not_call_unclassified_contact_non_barrel() -> None:
    rows = [
        {"type":"X", "description":"hit_into_play", "events":"home_run", "launch_speed":100.0, "launch_angle":20.0, "launch_speed_angle":6.0},
        {"type":"X", "description":"hit_into_play", "events":"single", "launch_speed":101.0, "launch_angle":15.0, "launch_speed_angle":None},
        {"type":"X", "description":"hit_into_play", "events":"field_out", "launch_speed":102.0, "launch_angle":None, "launch_speed_angle":None},
    ]
    value = ev_launch_angle_counts(rows)
    assert value["hard_hit_ev_count"] == 3
    assert value["classified_hard_hit_count"] == 1
    assert value["barrel_count"] == 1
    assert value["classified_hard_hit_non_barrel_count"] == 0
    assert value["unclassified_joint_count"] == 1
    with pytest.raises(StatcastSourceError, match="contradicts"):
        ev_launch_angle_counts([{**rows[0], "events":"catcher_interf"}])


def test_capture_requires_exact_authorization_and_new_output(tmp_path: Path) -> None:
    prep = ROOT / "config/shared_pa_statcast_source_preparation_v1"
    prepared_plan = prep / "sample_request_plan.json"
    plan_value = json.loads(prepared_plan.read_text())
    plan = tmp_path / "sample-plan.json"; plan.write_text(json.dumps(plan_value,sort_keys=True,separators=(",",":"))+"\n")
    base_lines = synthetic_csv(load_contract(CONTRACT_PATH)).decode().splitlines()
    body_lines = [base_lines[0]]
    for game in plan_value["requests"][0]["expected"]["certified_games"]:
        for line in base_lines[1:]:
            body_lines.append(
                line.replace("2023-04-01", "2023-07-25")
                .replace("700001", str(game["game_pk"]))
                .replace("HOM", game["home_team_code"])
                .replace("AWY", game["away_team_code"])
            )
    body = ("\n".join(body_lines) + "\n").encode()
    carrier = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()
    runtime = RuntimeAuthorization(attestation_sha256="a"*64, attestation={}, source_sha256="b"*64, policy_sha256="c"*64)
    access = VerifiedStatcastHistoricalSourceAccess(
        authorization_id="test", authorization_file_sha256="d"*64,
        carrier_commit=carrier, runtime_policy_sha256="c"*64,
        source_bundle_sha256=source_bundle_sha256(),
        source_contract_sha256=sha256_file(CONTRACT_PATH),
        request_plan_sha256=sha256_file(plan),
    )
    calls: list[str] = []
    def fake(url: str, timeout: float, maximum: int):
        calls.append(url); return 200, {"content-length":str(len(body)),"content-type":"text/csv"}, body
    output = tmp_path / "capture"
    result = capture(plan, CONTRACT_PATH, output, runtime_authorization=runtime, source_access=access, carrier_commit=carrier, transport=fake, sleep=lambda _: None, wall_time=lambda: 0.0)
    assert result["success_count"] == 1 and len(calls) == 1
    assert (output / "statcast-2023-07-25/response.csv").read_bytes() == body
    with pytest.raises(CaptureError, match="already exists"):
        capture(plan, CONTRACT_PATH, output, runtime_authorization=runtime, source_access=access, carrier_commit=carrier, transport=fake)
    wrong = copy.copy(access)
    object.__setattr__(wrong, "source_bundle_sha256", "e"*64)
    with pytest.raises(CaptureError, match="source bundle"):
        capture(plan, CONTRACT_PATH, tmp_path / "unused", runtime_authorization=runtime, source_access=wrong, carrier_commit=carrier, transport=fake)
