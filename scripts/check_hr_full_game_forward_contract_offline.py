#!/usr/bin/env python3
"""Offline mutation tests for the preregistered forward-only HR input contract."""

from __future__ import annotations

import copy
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.hr_full_game_forward_contract import (  # noqa: E402
    HRFullGameForwardContractError,
    analytic_hr_over_probability,
    validate_completed_t4_record,
    validate_contract,
)

CONTRACT = ROOT / "config" / "hr_full_game_forward_input_contract_v1.json"


def must_fail(callable_, label: str) -> None:
    try:
        callable_()
    except HRFullGameForwardContractError:
        print(f"[OK] MUTATION {label} fails")
        return
    raise AssertionError(f"mutation unexpectedly passed: {label}")


def sample_record() -> dict:
    start = datetime(2026, 7, 30, 0, 0, tzinfo=timezone.utc)
    target = start - timedelta(hours=4)
    support = [2, 3, 4, 5]
    weights = [0.05, 0.20, 0.45, 0.30]
    per_pa = 0.042
    return {
        "collector_instance_id": "synthetic-only",
        "receipt_utc": target.isoformat(),
        "monotonic_receipt_sequence": 1,
        "raw_source_payload_sha256": "a" * 64,
        "raw_source_schema_version": "synthetic-v1",
        "collector_code_sha256": "b" * 64,
        "runtime_manifest_sha256": "c" * 64,
        "source_observation_utc": target.isoformat(),
        "official_start_utc": start.isoformat(),
        "mlb_game_pk": 123456,
        "player_id": 654321,
        "official_game_date": "2026-07-29",
        "home_team_id": 111,
        "away_team_id": 112,
        "game_identity_artifact_sha256": "d" * 64,
        "player_identity_artifact_sha256": "e" * 64,
        "hard_model_key": "123456:654321:home_runs:0.5:over",
        "lineup_state": "projected",
        "lineup_slot_or_null": 3,
        "lineup_source_receipt_utc": target.isoformat(),
        "lineup_raw_payload_sha256": "f" * 64,
        "pa_distribution_support": support,
        "pa_distribution_probability_by_support": weights,
        "pa_distribution_sha256": "0" * 64,
        "expected_pa": sum(n * p for n, p in zip(support, weights)),
        "pa_feature_snapshot_sha256": "1" * 64,
        "pa_fallback_labels": [],
        "per_pa_hr_probability": per_pa,
        "per_pa_hr_feature_snapshot_sha256": "2" * 64,
        "per_pa_hr_component_version_sha256": "3" * 64,
        "rate_fallback_labels": [],
        "full_game_p_over_0_5": analytic_hr_over_probability(
            support=support, probabilities=weights, per_pa_hr_probability=per_pa
        ),
        "simulation_seed_or_analytic_method": "analytic-independent-PA-v1",
        "model_code_sha256": "4" * 64,
        "effective_config_sha256": "5" * 64,
        "feature_manifest_sha256": "6" * 64,
        "policy_sha256": "7" * 64,
        "prediction_artifact_sha256": "8" * 64,
        "terminal_state": "captured_complete",
    }


def main() -> int:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    validate_contract(contract)
    validate_completed_t4_record(sample_record())
    print("[OK] contract and synthetic pregame-only T-4 record validate")

    contract_mutations = [
        ("May re-enabled", lambda p: p.update(may_2026_eligible_as_untouched_holdout=True)),
        ("production changed", lambda p: p.update(production_unchanged=False)),
        ("v13 merged", lambda p: p.update(separate_from_running_v13_operational_smoke=False)),
        ("historical backfill", lambda p: p["scope"].update(historical_backfill_forbidden=False)),
        ("outcome leakage guard", lambda p: p["official_settlement"].update(not_allowed_as_pregame_input=[])),
        ("atomic ledger", lambda p: p["publication"].update(atomic_publish_required=False)),
        ("betting authorization", lambda p: p.update(betting_authorized=True)),
        ("missing mutation coverage", lambda p: p.update(required_mutations=[])),
    ]
    for label, mutate in contract_mutations:
        candidate = copy.deepcopy(contract)
        mutate(candidate)
        must_fail(lambda c=candidate: validate_contract(c), label)

    record_mutations = [
        ("post-horizon lineup", lambda r: r.update(lineup_source_receipt_utc=r["official_start_utc"])),
        ("realized PA", lambda r: r.update(official_pa=4)),
        ("invalid PA mass", lambda r: r.update(pa_distribution_probability_by_support=[0.1, 0.2, 0.3, 0.1])),
        ("identity ambiguity", lambda r: r.update(player_id="unknown")),
        ("tail mismatch", lambda r: r.update(full_game_p_over_0_5=0.99)),
        ("unknown lineup", lambda r: r.update(lineup_state="unknown")),
        ("missing provenance", lambda r: r.pop("feature_manifest_sha256")),
    ]
    for label, mutate in record_mutations:
        candidate = sample_record()
        mutate(candidate)
        must_fail(lambda c=candidate: validate_completed_t4_record(c), label)
    print("15/15 mutations rejected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
