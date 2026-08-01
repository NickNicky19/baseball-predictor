#!/usr/bin/env python3
"""Create the deterministic manifest for the prepared Statcast source package."""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.shared_pa_statcast_source_v1 import canonical_json_bytes, sha256_bytes, sha256_file


FILES = (
    "config/shared_pa_feature_source_admissibility_v1.json",
    "config/shared_pa_statcast_source_contract_v1.json",
    "config/shared_pa_statcast_runtime_authority_v1.json",
    "config/shared_pa_statcast_source_preparation_v1/full_request_plan.json",
    "config/shared_pa_statcast_source_preparation_v1/sample_request_plan.json",
    "config/shared_pa_statcast_source_preparation_v1/source_preparation_summary.json",
    "config/shared_pa_statcast_source_preparation_v1/package_manifest.json",
    "config/shared_pa_statcast_stage_a_preflight_v1/stage_a_preflight.json",
    "config/shared_pa_statcast_stage_a_preflight_v1/manifest.json",
    "scripts/build_shared_pa_statcast_request_plans_v1.py",
    "scripts/capture_shared_pa_statcast_source_v1.py",
    "scripts/finalize_shared_pa_statcast_source_package_v1.py",
    "scripts/run_shared_pa_statcast_stage_a_preflight_v1.py",
    "src/data/shared_pa_statcast_source_v1.py",
    "src/evaluation/shared_pa_statcast_historical_source_access_v1.py",
    "src/evaluation/shared_pa_statcast_stage_a_v1.py",
    "tests/test_shared_pa_statcast_source_preparation_v1.py",
)


def main() -> int:
    output = ROOT / "config/shared_pa_statcast_source_package_v1.json"
    rows = []
    for relative in FILES:
        path = ROOT / relative
        rows.append({"path": relative, "size": path.stat().st_size, "sha256": sha256_file(path)})
    value = {
        "schema_version": "shared-pa-statcast-source-package-v1",
        "status": "STAGE_A_COMPLETE_STAGE_B_SAMPLE_NOT_AUTHORIZED",
        "frozen_inputs": {
            "outcome_label_release_sha256": "5a242eb96dacea5bb1459c7c8592325abbc2572c08cff9a941d67892a8e9c2bc",
            "c0_parent_sha256": "d6bf5bd3517b34c2b54dde7ff89ab4148f1095a5f7ad98447a1004debcd85736",
            "learned_recency_release_sha256": "76ef634e686379429640f597bd51432057fc700da043370f0a2b2ce4bd28cca8",
            "feature_source_admissibility_sha256": "49b5025e787734ea06ca5f44796dac564da755bc99c03ce2361ea3ce24f9f2d8",
        },
        "historical_scope": {
            "source_start": "2023-03-30", "source_end": "2023-09-30",
            "scored_target_start": "2023-05-01", "last_target_date": "2023-10-01",
            "request_partitions": 181, "certified_games": 2415,
            "bounded_sample_date": "2023-07-25", "bounded_sample_requests": 1,
            "bounded_sample_games": 15,
        },
        "stage_a_result": "PASS_SYNTHETIC_ENGINEERING_ONLY",
        "stage_b_result": "NOT_EXECUTED_EXPLICIT_AUTHORIZATION_REQUIRED",
        "expected_contact_direct_fields": "INELIGIBLE",
        "external_request_count": 0,
        "model_fitting_performed": False,
        "market_scoring_performed": False,
        "may_2026_accessed": False,
        "files": rows,
    }
    value["package_sha256"] = sha256_bytes(canonical_json_bytes(value))
    output.write_bytes(canonical_json_bytes(value))
    print(json.dumps({"path": str(output), "sha256": sha256_file(output), "package_sha256": value["package_sha256"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
