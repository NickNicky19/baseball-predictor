#!/usr/bin/env python3
"""Produce a hash-bound, non-promotional forward-shadow readiness audit."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]

CHECKS = {
    "capture_plan": ("scripts/check_shadow_capture_plan_offline.py", "15/15 checks passed", 15),
    "prediction_snapshot": ("scripts/check_shadow_prediction_snapshot_offline.py", "6/6", 6),
    "ledger": ("scripts/check_shadow_ledger_offline.py", "17/17 checks passed", 17),
    "ledger_report": ("scripts/check_shadow_ledger_report_offline.py", "3/3 checks passed", 3),
    "target_capture": ("scripts/check_shadow_target_capture_offline.py", "12/12", 12),
    "live_identity": ("scripts/check_live_market_identity_offline.py", "7/7", 7),
    "provider_adapter_e2e": ("scripts/check_shadow_provider_adapter_e2e.py", "14/14 checks passed", 14),
    "live_provider": ("scripts/check_shadow_live_provider_offline.py", "18/18 checks passed", 18),
    "research_selection_policy": ("scripts/check_shadow_research_selection_policy_offline.py", "7/7 checks passed", 7),
    "primary_collector": ("scripts/check_shadow_primary_collector_offline.py", "14/14 checks passed", 14),
    "collector_tick": ("scripts/check_shadow_collector_tick_offline.py", "24/24 checks passed", 24),
    "prestart_reference": ("scripts/check_shadow_close_collector_offline.py", "6/6 checks passed", 6),
    "official_hits_rules": ("scripts/check_shadow_official_hits_offline.py", "10/10 checks passed", 10),
    "official_settlement_worker": ("scripts/check_shadow_official_settlement_worker.py", "5/5 checks passed", 5),
    "independent_lifecycle_tree": ("scripts/check_shadow_lifecycle_tree_offline.py", "6/6 checks passed", 6),
    "failure_lifecycle": ("scripts/check_shadow_failure_lifecycle_offline.py", "2/2 checks passed", 2),
    "forward_evidence_boundary": ("scripts/check_forward_evidence_boundary_offline.py", "7/7 checks passed", 7),
    "forward_evidence_era": ("scripts/check_forward_evidence_era_offline.py", "28/28 checks passed", 28),
    "evaluation_import_boundary": ("scripts/check_evaluation_import_boundary_offline.py", "3/3 checks passed", 3),
    "execution_product_contracts": ("scripts/check_execution_product_contracts_offline.py", "8/8 checks passed", 8),
    "execution_product_observation": ("scripts/check_execution_product_observation_offline.py", "8/8 checks passed", 8),
    "tracked_secret_scanner": ("scripts/check_tracked_secrets_offline.py", "3/3 checks passed", 3),
}

BOUND_FILES = [
    ".gitattributes",
    ".gitignore",
    ".github/workflows/daily-predictions.yml",
    ".github/workflows/shadow-evidence-verifier.yml",
    "config/forward_shadow_deployment_protocol.json",
    "config/forward_shadow_evidence_boundary.json",
    "config/shadow_collector_runtime.json",
    "reports/v11_event_identity_offset_diagnostic_2026-07-18.json",
    "config/shadow_hits_research_policy.json",
    "config/shadow_draftkings_hits_reference_settlement.json",
    "config/hits_execution_product_contracts.json",
    "data/evidence/execution_products/onyx_public_rules_observation_2026-07-17.json",
    "data/evidence/execution_products/novig_public_rules_observation_2026-07-17.json",
    "data/evidence/execution_products/chalkboard_public_rules_observation_2026-07-17.json",
    "data/evidence/execution_products/prizepicks_public_rules_observation_2026-07-17.json",
    "config/config.kbb.json",
    "requirements.txt",
    "scripts/audit_forward_shadow_readiness.py",
    "scripts/build_shadow_capture_plan.py",
    "scripts/audit_shadow_capture_plan.py",
    "src/evaluation/shadow_capture_plan.py",
    "src/evaluation/shadow_prediction_snapshot.py",
    "src/evaluation/shadow_ledger.py",
    "run_shadow_ledger.py",
    "run_shadow_ledger_report.py",
    "src/evaluation/shadow_target_capture.py",
    "run_shadow_target_capture.py",
    "src/evaluation/live_market_identity.py",
    "src/evaluation/shadow_provider_adapter.py",
    "src/evaluation/shadow_live_provider.py",
    "src/evaluation/shadow_lifecycle.py",
    "src/evaluation/shadow_official_hits.py",
    "src/evaluation/forward_evidence_boundary.py",
    "src/evaluation/forward_evidence_era.py",
    "src/evaluation/__init__.py",
    "src/features/feature_factory.py",
    "src/evaluation/official_game_completion.py",
    "src/evaluation/market_economics.py",
    "src/evaluation/execution_product_contracts.py",
    "src/evaluation/execution_product_observation.py",
    "src/data/mlb_api.py",
    "src/utils/provenance.py",
    "run_slate.py",
    "run_shadow_primary_collector.py",
    "run_shadow_close_collector.py",
    "run_shadow_official_settlement.py",
    "run_shadow_collector_tick.py",
    "scripts/prepare_forward_evidence_scope.py",
    "scripts/certify_forward_operational_smoke.py",
    "scripts/validate_execution_product_observation.py",
    "scripts/verify_shadow_capture_tree.py",
    "scripts/verify_shadow_lifecycle_tree.py",
    "scripts/check_tracked_secrets.py",
    "deploy/shadow_collector/baseball-shadow-collector.service",
    "deploy/shadow_collector/baseball-shadow-collector.timer",
    "deploy/shadow_collector/windows/Run-LocalOperationalSmoke.ps1",
    "data/analysis/forward_shadow_adapter_v1/protocol.json",
]


def sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _run_check(relative: str, marker: str, expected: int) -> dict[str, Any]:
    path = ROOT / relative
    result = subprocess.run(
        [sys.executable, str(path)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    combined = result.stdout + result.stderr
    if result.returncode != 0 or marker not in combined:
        raise RuntimeError(
            f"forward-shadow check failed: {relative} exit={result.returncode}\n{combined}"
        )
    return {
        "path": relative,
        "sha256": sha256(path),
        "checks_passed": expected,
        "expected_marker": marker,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    checks = {
        name: _run_check(relative, marker, expected)
        for name, (relative, marker, expected) in CHECKS.items()
    }
    total = sum(item["checks_passed"] for item in checks.values())
    workflow_path = ROOT / ".github/workflows/daily-predictions.yml"
    workflow = workflow_path.read_text(encoding="utf-8")
    if "- cron: \"0 20 * * *\"" not in workflow:
        raise ValueError("GitHub backup schedule drifted")
    if "NOT the primary T-4h" not in workflow or "future external shadow collector" not in workflow:
        raise ValueError("workflow no longer declares its non-primary capture role")
    verifier_workflow = (ROOT / ".github/workflows/shadow-evidence-verifier.yml").read_text(encoding="utf-8")
    if 'cron: "17 * * * *"' not in verifier_workflow:
        raise ValueError("GitHub evidence verifier schedule drifted")
    if "NEVER fetches" not in verifier_workflow or "NOT the primary T-4h" not in verifier_workflow:
        raise ValueError("GitHub verifier no longer declares its independent non-primary role")
    if (
        "$PRIMARY_ROOT/evidence_scope.json" not in verifier_workflow
        or "--evidence-scope evidence/evidence_scope.json" not in verifier_workflow
    ):
        raise ValueError("GitHub verifier is not bound to the immutable evidence scope")

    windows_runner = (
        ROOT / "deploy/shadow_collector/windows/Run-LocalOperationalSmoke.ps1"
    ).read_text(encoding="utf-8")
    if (
        "[Parameter(Mandatory = $true)]" not in windows_runner
        or "[string]$OfficialDate" not in windows_runner
        or "--date $OfficialDate" not in windows_runner
        or "the runner never rolls into another date" not in windows_runner
        or '$env:PYTHONDONTWRITEBYTECODE = "1"' not in windows_runner
    ):
        raise ValueError(
            "local operational smoke is not fixed to one date or can mutate tracked bytecode"
        )

    secret_scan = subprocess.run(
        [sys.executable, str(ROOT / "scripts/check_tracked_secrets.py")],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if secret_scan.returncode != 0:
        raise RuntimeError("tracked-source credential scan failed without exposing credential values")

    ledger = ROOT / "data/learning/shadow/service/ledger/forward_ledger.jsonl"
    ledger_rows = 0
    if ledger.exists():
        ledger_rows = sum(1 for row in ledger.read_text(encoding="utf-8").splitlines() if row.strip())

    diagnostic_path = ROOT / "reports/v11_event_identity_offset_diagnostic_2026-07-18.json"
    diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))
    if (
        diagnostic.get("operational_smoke_status")
        != "PERMANENT_SOURCE_ERROR_NOT_CERTIFIABLE"
        or diagnostic.get("scope") != "outcome_blind_operational_identity_only"
        or diagnostic.get("provider_prices_inspected") is not False
        or diagnostic.get("model_probabilities_inspected") is not False
        or diagnostic.get("official_outcomes_inspected") is not False
        or diagnostic.get("may_2026_read") is not False
        or diagnostic.get("economic_evidence_eligible") is not False
        or diagnostic.get("betting_authorized") is not False
    ):
        raise ValueError("v11 operational failure diagnostic is unsafe or incomplete")

    payload = {
        "schema_version": "forward-shadow-readiness-audit-v2",
        "built_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "FULL_LOCAL_LIFECYCLE_GUARDS_VALID_PRIMARY_COLLECTOR_NOT_DEPLOYED_NO_FORWARD_EVIDENCE",
        "betting_authorized": False,
        "guard_checks": checks,
        "guard_checks_passed": total,
        "capture_timing": {
            "target": "per-game T-4h derived from official start time",
            "github_backup_cron": "0 20 * * *",
            "github_is_primary_collector": False,
            "durable_external_primary_collector_deployed": False,
            "github_independent_verifier_implemented": True,
            "github_independent_verifier_configured": False,
            "research_entry_ledger_commit_implemented": True,
            "prestart_reference_capture_implemented": True,
            "official_mlb_outcome_resolution_implemented": True,
            "void_and_unscored_funnels_implemented": True,
            "event_identity_v2_mutation_tested": True,
            "v11_permanently_failed_and_excluded": True,
        },
        "operational_identity": {
            "v11_status": diagnostic["operational_smoke_status"],
            "v11_diagnostic_path": str(diagnostic_path.relative_to(ROOT)).replace("\\", "/"),
            "v11_diagnostic_sha256": sha256(diagnostic_path),
            "successor_max_event_start_delta_seconds": diagnostic["new_incompatible_contract"]["max_abs_start_delta_seconds"],
            "time_delta_used_for_selection": False,
            "provider_and_official_start_times_retained_separately": True,
            "successor_smoke_completed": False,
        },
        "forward_evidence": {
            "ledger_path": str(ledger.relative_to(ROOT)).replace("\\", "/"),
            "ledger_exists": ledger.exists(),
            "ledger_rows": ledger_rows,
            "graded_forward_entries": 0,
            "capture_measurable": False,
        },
        "execution_products": {
            "contract_path": "config/hits_execution_product_contracts.json",
            "contract_sha256": sha256(ROOT / "config/hits_execution_product_contracts.json"),
            "onyx_ready": False,
            "novig_ready": False,
            "chalkboard_ready": False,
            "prizepicks_ready": False,
            "draftkings_role": "reference_market_only_not_execution",
        },
        "bound_files": {
            relative: sha256(ROOT / relative) for relative in BOUND_FILES
        },
        "blockers": [
            "v11 permanently failed event identity and cannot be retried, backfilled, or certified",
            "the incompatible successor operational smoke has not yet completed",
            "no durable always-on per-game T-4h primary collector is deployed",
            "no external host or provider plan has been explicitly selected and authorized",
            "GitHub read-only evidence SSH variables/secrets are not configured",
            "no successful complete future T-4h/prestart/official lifecycle has yet been captured operationally",
            "no exact executable execution-product prices, accepted entries, fills, or settlements have been captured",
            "Onyx, Novig, Chalkboard, and PrizePicks each remain blocked on separate primary rules/account/payout/executability evidence",
            "no market-specific forward capture interval can yet be calculated",
        ],
        "interpretation": (
            "The fail-closed T-4h entry, research selection ledger commit, exact prestart reference, official MLB "
            "outcome, modeled DraftKings reference settlement, void/unscored, one-minute service state machine, "
            "independent GitHub lifecycle verifier, event-identity v2, and product-separation guards pass locally. "
            "The first real v11 attempt is permanently failed and excluded; its outcome-blind diagnostic binds the incompatible successor. "
            "The successor is locally ready, not deployed. "
            "GitHub remains a verifier and backup, never the primary collector. No prospective performance or "
            "executability claim exists yet."
        ),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print("FORWARD SHADOW READINESS AUDITED")
    print(f"  guard checks: {total}/{total}")
    print("  durable primary collector deployed: FALSE")
    print("  forward capture measurable: FALSE")
    print(f"  wrote: {out}")
    print(f"  sha256: {sha256(out)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
