"""Offline proof that qualified PA authority reaches the active candidate adapter.

The fixture is intentionally synthetic and is never a prediction or evidence
record.  The qualified 2023 PA artifact and its runtime authority are real.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYBASEBALL_CACHE", str(ROOT / "tmp" / "pybaseball-cache"))

from src.evaluation.shared_pa_forward_evidence import sha256_value
from src.evaluation.shared_pa_source_bound_candidate_v3 import (
    build_source_bound_candidate_record_v3,
    load_candidate_protocol_v3,
)
from src.prediction.integrated_shared_pa_candidate import validate_and_project_record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--authority-root", type=Path, required=True)
    parser.add_argument("--authority-manifest-relative", default="authority/manifest.json")
    parser.add_argument("--runtime-receipt-relative", default="authority/runtime_receipt.json")
    parser.add_argument("--runtime-receipt-sha256", required=True)
    parser.add_argument("--pa-volume-artifact-sha256", required=True)
    parser.add_argument("--decision-time-utc", required=True)
    args = parser.parse_args(argv)
    player = 660271
    unsigned = {
        "schema_version": "shared-pa-projected-opportunity-evidence-envelope-v2",
        "research_only": True,
        "betting_authorized": False,
        "promotion_eligible": False,
        "official_game_date": "2026-09-17",
        "official_start_utc": "2026-09-17T23:10:00Z",
        "target_horizon_utc": "2026-09-17T19:10:00Z",
        "prediction_generated_at_utc": "2026-09-17T19:00:00Z",
        "mlb_game_pk": 999999,
        "team_id": 117,
        "side": "home",
        "player_id": player,
        "stats_cutoff_date": "2026-09-16",
        "stats_raw_sha256s": ["a" * 64],
        "stats_transport_receipt_sha256s": ["b" * 64],
        "stats_counts": {
            "strikeout": 80, "walk": 34, "single": 61, "double": 24,
            "triple": 2, "home_run": 18, "bip_out": 170, "other_non_ab": 11,
        },
    }
    envelope = {**unsigned, "evidence_envelope_sha256": sha256_value(unsigned)}
    record = build_source_bound_candidate_record_v3(
        root=ROOT,
        evidence_envelope=envelope,
        projection_marginals={
            "start_probability": {str(player): 0.8},
            "slot_probability": {
                **{f"{player}:{slot}": 0.0 for slot in range(1, 10)},
                f"{player}:2": 0.8,
            },
        },
        authority_arguments={
            "authority_root": args.authority_root,
            "authority_manifest_relative": args.authority_manifest_relative,
            "external_receipt_relative": args.runtime_receipt_relative,
            "expected_external_receipt_sha256": args.runtime_receipt_sha256,
            "expected_pa_volume_artifact_sha256": args.pa_volume_artifact_sha256,
            "decision_time_utc": args.decision_time_utc,
        },
        candidate_protocol=load_candidate_protocol_v3(
            ROOT / "config/shared_pa_candidate_v1_protocol.json"
        ),
        source_manifest_sha256="d" * 64,
        runtime_release_receipt_sha256="e" * 64,
    )
    projected = validate_and_project_record(record, expected_date="2026-09-17")
    summary = {
        "status": "QUALIFIED_AUTHORITY_TO_ACTIVE_ADAPTER_WIRING_VERIFIED",
        "synthetic_fixture_only": True,
        "prediction_generated": False,
        "markets": sorted(projected["markets"]),
        "source_authority_id": record["pa_volume_source_authority_id"],
        "pa_volume_artifact_sha256": record["pa_volume_artifact_sha256"],
        "candidate_record_sha256": record["candidate_record_sha256"],
        "projected_start_probability": projected["projected_start_probability"],
        "slot_probability_sum_given_start": sum(projected["projected_slot_probability"].values()),
        "slot_probability_sum_unconditional": sum(
            projected["projected_slot_probability_unconditional"].values()
        ),
    }
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
