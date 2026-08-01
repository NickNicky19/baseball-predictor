"""Synthetic-only Stage-A wiring proof for the prepared Statcast source."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any, Mapping

from src.data.shared_pa_statcast_source_v1 import (
    build_point_in_time_feature,
    canonical_json_bytes,
    parse_csv_bytes,
    sha256_bytes,
    validate_raw_receipt,
)
from src.evaluation.shared_pa_c0_tournament_v1 import FEATURE_SCHEMA, OUTCOMES, _losses, _market_record


SYNTHETIC_SOURCE_SHA256 = "SYNTHETIC_PREFLIGHT_ONLY"


def synthetic_csv(contract: Mapping[str, Any], *, hard_hit: bool = True) -> bytes:
    """Return deterministic rows spanning takes, whiffs, contact and missing EV."""
    header = list(contract["fixture_observed_complete_header"])
    events = [
        (1, "called_strike", "S", "", "", "", "", "8"),
        (2, "ball", "B", "", "", "", "", "12"),
        (3, "swinging_strike", "S", "", "", "", "", "5"),
        (4, "foul", "S", "", "", "", "", "3"),
        (5, "hit_into_play", "X", "single", "101.2" if hard_hit else "94.2", "14", "6" if hard_hit else "5", "6"),
        (6, "hit_into_play", "X", "field_out", "", "", "", "4"),
    ]
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=header, lineterminator="\n")
    writer.writeheader()
    for pitch, description, pitch_type_result, event, ev, la, contact_class, zone in events:
        row = {name: "" for name in header}
        row.update({
            "pitch_type": "FF", "game_date": "2023-04-01", "game_type": "R",
            "batter": "1001", "pitcher": "2001", "game_pk": "700001",
            "at_bat_number": str(pitch), "pitch_number": "1", "home_team": "HOM",
            "away_team": "AWY", "inning_topbot": "Top" if pitch <= 3 else "Bot", "stand": "R",
            "p_throws": "R", "description": description, "type": pitch_type_result,
            "events": event, "zone": zone, "launch_speed": ev, "launch_angle": la,
            "launch_speed_angle": contact_class, "bb_type": "line_drive" if event else "",
        })
        writer.writerow(row)
    return output.getvalue().encode("utf-8")


def _receipt(raw: bytes, request: Mapping[str, Any], contract_sha: str, parser_sha: str) -> dict[str, Any]:
    return {
        "schema_version": "shared-pa-statcast-raw-receipt-v1", "request": dict(request),
        "request_plan_sha256": "synthetic-plan", "source_contract_sha256": contract_sha,
        "parser_sha256": parser_sha, "attempt_number": 1,
        "request_started_at_utc": "2023-04-02T00:00:00Z",
        "observed_at_utc": "2023-04-02T00:00:01Z", "http_status": 200,
        "response_headers": {"content-length": str(len(raw)), "content-type": "text/csv"},
        "byte_count": len(raw), "sha256": sha256_bytes(raw), "terminal_state": "SUCCESS",
    }


def _candidate_per_pa(feature: Mapping[str, Any]) -> dict[str, float]:
    """Fixed synthetic adapter, not a fitted candidate or scientific model."""
    d = feature["plate_discipline"]
    ev = feature["ev_launch_angle"]
    swing = d["swing_count"] / d["physical_pitch_rows"]
    hard = ev["hard_hit_ev_count"] / max(1, ev["measured_ev_count"])
    base = {
        "strikeout": 0.20 + 0.02 * swing,
        "non_intentional_walk": 0.08, "hit_by_pitch": 0.01, "home_run": 0.03 + 0.01 * hard,
        "single": 0.14, "double": 0.05, "triple": 0.005,
        "bip_out": 0.43, "sac_fly": 0.01, "sac_bunt": 0.005,
        "intentional_walk": 0.002, "catcher_interference": 0.001, "other_official_pa": 0.0,
    }
    total = sum(base.values())
    base["bip_out"] += 1.0 - total
    if min(base.values()) < 0 or abs(sum(base.values()) - 1.0) > 1e-12:
        raise ValueError("synthetic PA simplex is invalid")
    return base


def run_stage_a(contract: Mapping[str, Any], contract_sha: str, parser_sha: str) -> dict[str, Any]:
    request = {"request_id": "synthetic", "full_url": "synthetic://no-network"}
    raw = synthetic_csv(contract)
    receipt = _receipt(raw, request, contract_sha, parser_sha)
    validate_raw_receipt(raw=raw, receipt=receipt, expected_request=request,
                         contract_sha256=contract_sha, parser_sha256=parser_sha,
                         request_plan_sha256="synthetic-plan")
    games = {700001: {"official_date": "2023-04-01", "home_team_id": 1, "away_team_id": 2,
                      "home_team_code": "HOM", "away_team_code": "AWY"}}
    rows = parse_csv_bytes(raw, contract=contract, expected_date="2023-04-01", certified_games=games)
    synthetic_label = {"game_pk":700001,"team_side":"away","team_id":2,"player_id":1001,"plate_appearances":2,"source":"SYNTHETIC_PREFLIGHT_ONLY"}
    synthetic_label_sha = sha256_bytes(canonical_json_bytes(synthetic_label))
    feature = build_point_in_time_feature(rows, target_date="2023-05-01", player_id=1001,
                                          official_pa_count=synthetic_label["plate_appearances"], contract=contract,
                                          source_release_sha256=SYNTHETIC_SOURCE_SHA256,
                                          official_pa_source_sha256=synthetic_label_sha)
    pa_pmf = {0: 0.05, 3: 0.15, 4: 0.45, 5: 0.30, 6: 0.05}
    per_pa = _candidate_per_pa(feature)
    if set(per_pa) != set(OUTCOMES):
        raise ValueError("synthetic adapter differs from C0 outcome space")
    synthetic_target_game = {"game_pk":700002,"official_date":"2023-05-01","team_side":"away","team_id":2}
    candidate_input = {
        "schema_version":"shared-pa-c0-pit-feature-row-v1","candidate_id":"shared_pa_statcast_stage_a_synthetic_adapter_v1",
        "official_date":synthetic_target_game["official_date"],"game_pk":synthetic_target_game["game_pk"],"team_side":synthetic_target_game["team_side"],"team_id":synthetic_target_game["team_id"],"player_id":1001,
        "decision_cutoff":"2023-05-01T00:00:00Z","player_prior_pa":2,"team_player_prior_opportunities":1,
        "start_probability":0.8,"slot_probabilities":{"1":1.0},"opportunity_pmf":pa_pmf,
        "per_pa_probability":per_pa,"feature_schema_sha256":sha256_bytes(canonical_json_bytes(FEATURE_SCHEMA)),
        "candidate_code_sha256":sha256_bytes(Path(__file__).read_bytes()),"source_feature_sha256":feature["feature_sha256"],
        "source_receipt_sha256":sha256_bytes(canonical_json_bytes(receipt)),"source_contract_sha256":contract_sha,
        "source_parser_sha256":parser_sha,"feature_sha256":"",
    }
    candidate_input["feature_sha256"] = sha256_bytes(canonical_json_bytes({k:v for k,v in candidate_input.items() if k != "feature_sha256"}))
    parent = _market_record(candidate_input["per_pa_probability"], candidate_input["opportunity_pmf"])
    mutated_raw = synthetic_csv(contract, hard_hit=False)
    mutated_receipt = _receipt(mutated_raw, request, contract_sha, parser_sha)
    validate_raw_receipt(raw=mutated_raw, receipt=mutated_receipt, expected_request=request,
                         contract_sha256=contract_sha, parser_sha256=parser_sha,
                         request_plan_sha256="synthetic-plan")
    mutated_rows = parse_csv_bytes(mutated_raw, contract=contract, expected_date="2023-04-01", certified_games=games)
    mutated_feature = build_point_in_time_feature(
        mutated_rows, target_date="2023-05-01", player_id=1001,
        official_pa_count=synthetic_label["plate_appearances"], contract=contract,
        source_release_sha256=sha256_bytes(mutated_raw),
        official_pa_source_sha256=synthetic_label_sha,
    )
    mutated_input = dict(candidate_input)
    mutated_input["per_pa_probability"] = _candidate_per_pa(mutated_feature)
    mutated_input["source_feature_sha256"] = mutated_feature["feature_sha256"]
    mutated_input["source_receipt_sha256"] = sha256_bytes(canonical_json_bytes(mutated_receipt))
    mutated_input["feature_sha256"] = sha256_bytes(canonical_json_bytes({k:v for k,v in mutated_input.items() if k != "feature_sha256"}))
    mutated = _market_record(mutated_input["per_pa_probability"], pa_pmf)
    if parent == mutated:
        raise ValueError("feature mutation did not move authoritative PMFs")
    for record in parent.values():
        pmf = record["pmf"]
        if abs(record["projected_value"] - sum(i * p for i, p in enumerate(pmf))) > 1e-12:
            raise ValueError("archived mean does not reconcile to PMF")
        if abs(record["threshold_probabilities"]["over_0_5"] - (1 - pmf[0])) > 1e-12:
            raise ValueError("archived threshold does not reconcile to PMF")
    prediction_archive = {"schema_version":"shared-pa-statcast-synthetic-prediction-archive-v1","candidate_input":candidate_input,"predictions":parent,"observed":{market:0 for market in parent},"archive_sha256":""}
    prediction_archive["archive_sha256"] = sha256_bytes(canonical_json_bytes({k:v for k,v in prediction_archive.items() if k != "archive_sha256"}))
    serialized = canonical_json_bytes(prediction_archive)
    replay = json.loads(serialized)
    if replay["archive_sha256"] != sha256_bytes(canonical_json_bytes({k:v for k,v in replay.items() if k != "archive_sha256"})):
        raise ValueError("synthetic prediction archive hash differs on replay")
    evaluator = {market:list(_losses(record["pmf"],replay["observed"][market])[:4]) for market,record in replay["predictions"].items()}
    archive = {
        "schema_version": "shared-pa-statcast-stage-a-preflight-v1",
        "status": "SYNTHETIC_ENGINEERING_PREFLIGHT_ONLY", "external_request_count": 0,
        "source_authority": SYNTHETIC_SOURCE_SHA256, "synthetic_official_pa_label":synthetic_label,"synthetic_target_game":synthetic_target_game,
        "feature": feature, "candidate_input":candidate_input,
        "predictions": parent, "mutation_feature": mutated_feature, "mutation_candidate_input":mutated_input,"mutation_predictions": mutated,
        "prediction_archive":replay,
        "evaluator_ingestion": {"status":"PASS_SYNTHETIC_PREDICTION_ARCHIVE_REPLAY_ONLY","market_losses":evaluator}, "model_fitting_performed": False,
        "market_scoring_performed": False, "may_2026_accessed": False,
    }
    archive["archive_sha256"] = sha256_bytes(canonical_json_bytes(archive))
    return archive
