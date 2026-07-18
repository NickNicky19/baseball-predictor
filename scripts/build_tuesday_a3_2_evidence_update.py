#!/usr/bin/env python3
"""Build a create-once Tuesday evidence update for the completed a3.2 branch."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

INPUTS = {
    "base_report": ("data/analysis/tuesday_market_authorization_2026-07-21/report.json", "6be5eb316d8e90334a074e61e7d62e880fa293b3ff320c2a2a84a0b974ec8b33"),
    "base_binding": ("data/analysis/tuesday_market_authorization_2026-07-21/report_binding.json", "5c37be0b67e4bbc238395d2e9810c15b2fda1032ae2dca6aee97f9c729ced86d"),
    "shadow_readiness": ("data/analysis/tuesday_market_authorization_2026-07-21/forward_shadow_readiness_v4.json", "6eb19172bccfcc54f0bb9a5943fa512bced0c07613c1ffb694d8ed7f11facd6c"),
    "migration_protocol": ("data/analysis/hr_over_contract_v1/pre2026_a3_2_migration_v2/migration_protocol.json", "3476d59538f1027150e44fd8721cafae497cbabc96f3052dec18c8f423df8bc0"),
    "raw_validation": ("data/analysis/hr_over_contract_v1/pre2026_a3_2_migration_v2/raw_validation_report.json", "c534036a59d856a2f021a1d13cb96cd4d20dbf02b6a57b25eec8dff47977eddd"),
    "enriched_validation": ("data/analysis/hr_over_contract_v1/pre2026_a3_2_migration_v2/enriched_validation_report.json", "640a283cafe1e2be09154ab53dcb91c5c9c10ac12a6c014ee31f8472b2de2194"),
    "assembled_validation": ("data/analysis/hr_over_contract_v1/pre2026_a3_2_migration_v2/assembled_validation_report.json", "47236d30ac63684c7fa7b70e0fa1d3d9124c8f6b9c743032aa555923bc289113"),
    "pa_validation": ("data/analysis/hr_over_contract_v1/pre2026_a3_2_migration_v2/pa_refit_validation_report.json", "8d901cdd5b806d160501e77a86e20ac8e0ef2f7631ac8e4b81e564a9e3e98af7"),
    "signal_validation": ("data/analysis/hr_over_contract_v1/pre2026_a3_2_migration_v2/signal_screen_validation_report.json", "8f4f481f28a40dfc42cf67cbc336418da52bc2e1872117cfbed0d4626b0cfb83"),
    "full_reconstruction_validation": ("data/analysis/hr_over_contract_v1/pre2026_a3_2_migration_v2/level_mapping_full_v1/full_validation_report.json", "a6bcafba62db3819ec9a074d47d61147e82f8dea1dcbbf6e6fcbcbff9ab0d96a"),
    "mapping_implementation": ("data/analysis/hr_over_contract_v1/pre2026_a3_2_migration_v2/level_mapping_implementation_v1.json", "9cf3b965d1763e805a46ff87b58d1524fe85aa7bb52e8cd0a642893253f94272"),
    "mapping_rejection": ("data/analysis/hr_over_contract_v1/pre2026_a3_2_migration_v2/level_mapping_adjudication_v1/REJECTED_LEVEL_MAPPING.json", "003396bf92364ba4b26cb47b6f53f5ee3edcdf10bd561310b63a5c21a9c92697"),
    "confirmation_validation": ("data/analysis/hr_over_contract_v1/pre2026_a3_2_migration_v2/level_mapping_adjudication_v1/confirmation_validation_report.json", "4d08a1c8f3909e24087dd3cce8a548e2be4a063d1cde2ddc4edcc8df9c31e4d3"),
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def verify_inputs() -> tuple[dict[str, dict[str, str]], dict[str, dict[str, Any]]]:
    bound = {}
    loaded = {}
    for name, (relative, expected) in INPUTS.items():
        path = ROOT / relative
        actual = sha256(path)
        if actual != expected:
            raise ValueError(f"{name} hash mismatch: expected {expected}, got {actual}")
        bound[name] = {"path": relative, "sha256": actual}
        loaded[name] = load(path)
    return bound, loaded


def require_facts(items: dict[str, dict[str, Any]]) -> None:
    if items["base_report"].get("betting_authorized") is not False or items["base_report"].get("may_2026_read") is not False:
        raise ValueError("base Tuesday report opened May or authorized betting")
    statuses = {
        "raw_validation": "VALID_RAW_A3_2",
        "enriched_validation": "VALID_ENRICHED_A3_2_A4_1",
        "assembled_validation": "VALID_ASSEMBLED_A3_2_A4_1",
        "pa_validation": "VALID_PA_REFIT_ORIGINAL_STARTERS_A3_2",
        "signal_validation": "VALID_PASS_QUALIFIES_MODEL_RECONSTRUCTION",
        "full_reconstruction_validation": "VALID_FULL_A3_2_HR_LEVEL_MAPPING_RESEARCH_ONLY",
        "confirmation_validation": "VALID_REJECTED_LEVEL_MAPPING",
    }
    for name, status in statuses.items():
        record = items[name]
        if record.get("status") != status:
            raise ValueError(f"{name} status differs: {record.get('status')}")
        if record.get("betting_authorized") is not False or record.get("may_2026_opened") is not False:
            raise ValueError(f"{name} opened May or authorized betting")
    rejection = items["mapping_rejection"]
    if (
        rejection.get("status") != "REJECTED_LEVEL_MAPPING"
        or rejection.get("model_install_permitted") is not False
        or rejection.get("2026_open_period_experiment_permitted") is not False
        or len(rejection.get("failed_locked_conditions") or {}) != 3
    ):
        raise ValueError("a3.2 mapping rejection facts differ")
    shadow = items["shadow_readiness"]
    if (
        shadow.get("guard_checks_passed") != 58
        or shadow.get("capture_timing", {}).get("durable_external_primary_collector_deployed") is not False
        or shadow.get("forward_evidence", {}).get("capture_measurable") is not False
        or shadow.get("betting_authorized") is not False
    ):
        raise ValueError("forward-shadow readiness facts differ")


def _markdown(report: dict[str, Any]) -> str:
    hr = report["hr_a3_2_level_mapping"]
    lines = [
        "# Tuesday Evidence Update — Corrected a3.2 HR Branch",
        "",
        f"Status: **{report['status']}**",
        "",
        "Betting is **not authorized**. May 2026 remained sealed.",
        "",
        "## Completed gates",
        "",
        "- Rebuilt 2023–2025 regular-season original-starter source under isolated a3.2.",
        "- Validated raw, Statcast-enriched, and assembled sources without silent row loss.",
        "- Refit PA on corrected 2023–2024 original starters.",
        "- Passed the separate 2023-fit / 2024-select / 2025-confirm signal screen.",
        "- Certified one smoke and the full 24-date HR-over-0.5 reconstruction.",
        "- Locked and independently reproduced calibration before opening confirmation once.",
        "- Independently reproduced the final rejection and verified create-once enforcement.",
        "",
        "## HR mapping result",
        "",
        f"- Confirmation rows: {hr['confirmation_rows']:,} across 12 dates.",
        f"- Raw / control / candidate Brier: {hr['raw_brier']:.6f} / {hr['control_brier']:.6f} / {hr['candidate_brier']:.6f}.",
        f"- Raw / control / candidate log loss: {hr['raw_log_loss']:.6f} / {hr['control_log_loss']:.6f} / {hr['candidate_log_loss']:.6f}.",
        "- Point estimates improved slightly, but all three locked proof requirements did not pass.",
        "- Candidate bias was worse than the calibration-only control, and paired Brier/log-loss upper bounds crossed zero.",
        "- The mapping is rejected; no subgroup or threshold change can rescue it.",
        "",
        "## Remaining authorization blockers",
        "",
    ]
    lines.extend(f"- {item}" for item in report["remaining_authorization_blockers"])
    lines.extend(
        [
            "",
            "## Single highest-value next action",
            "",
            report["single_highest_value_next_action"],
            "",
            "This requires explicit credential and infrastructure authority. It does not alter the model, open May, or authorize betting.",
        ]
    )
    return "\n".join(lines) + "\n"


def build(bound: dict[str, Any], items: dict[str, Any]) -> dict[str, Any]:
    rejection = items["mapping_rejection"]
    metrics = rejection["confirmation_metrics"]
    base = items["base_report"]
    return {
        "schema_version": "tuesday-market-authorization-a3.2-update-v1",
        "built_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "deadline": "2026-07-21T08:00:00-05:00",
        "status": "EVIDENCE_PACKAGE_UPDATED_RESEARCH_ONLY_HR_A3_2_REJECTED",
        "betting_authorized": False,
        "may_2026_read": False,
        "locked_capture_lower_bound": 0.10,
        "preserved_base_package": bound["base_report"],
        "completed_gates": {
            "isolated_a3_2_protocol": "completed",
            "regular_season_original_starter_raw_rebuild": "completed_615_dates_131202_hitters",
            "point_in_time_statcast_enrichment": "completed_615_dates",
            "exact_source_assembly": "completed",
            "corrected_2023_2024_pa_refit": "completed",
            "2023_fit_2024_select_2025_confirm_signal_screen": "passed_reconstruction_only",
            "one_date_smoke": "passed",
            "full_24_date_reconstruction": "passed_artifact_validation_5724_hr_rows",
            "calibration_lock": "fitted_and_independently_reproduced_before_confirmation",
            "confirmation": "opened_once_independently_reproduced_rejected",
        },
        "chronological_boundaries": {
            "source_seasons": [2023, 2024, 2025],
            "source_game_type": "R_only",
            "source_population": "original_starters_only",
            "pa_fit": "2023-03-30_through_2024-09-30",
            "signal_fit": 2023,
            "signal_selection": 2024,
            "signal_confirmation_open_once": 2025,
            "level_mapping_calibration": "2025-03-27_through_2025-06-22_12_dates",
            "level_mapping_confirmation_open_once": "2025-06-29_through_2025-09-28_12_dates",
            "may_2026": "SEALED",
        },
        "hr_a3_2_level_mapping": {
            "status": "REJECTED_LEVEL_MAPPING",
            "confirmation_rows": rejection["chronology"]["confirmation_rows"],
            "raw_brier": metrics["raw"]["brier"],
            "control_brier": metrics["calibration_only_control"]["brier"],
            "candidate_brier": metrics["batted_ball_mapping_candidate"]["brier"],
            "raw_log_loss": metrics["raw"]["log_loss"],
            "control_log_loss": metrics["calibration_only_control"]["log_loss"],
            "candidate_log_loss": metrics["batted_ball_mapping_candidate"]["log_loss"],
            "failed_locked_conditions": rejection["failed_locked_conditions"],
            "point_estimate_improvement_is_insufficient": True,
            "candidate_install_permitted": False,
            "2026_open_period_experiment_permitted": False,
        },
        "candidate_register": {
            "accepted_for_betting": [],
            "ready_to_open_may": [],
            "surviving_research_baselines": base["candidate_register"]["still_research_only"],
            "newly_rejected": [
                {
                    "market": "home_runs_over_0.5",
                    "candidate": "a3.2_corrected_batted_ball_level_mapping_v1",
                    "reason": "bias guard failed and paired date Brier/log-loss upper bounds were not below zero versus raw and control",
                }
            ],
            "previously_rejected": base["candidate_register"]["rejected"],
        },
        "forward_shadow_readiness": {
            "status": items["shadow_readiness"]["status"],
            "guard_checks_passed": 58,
            "durable_primary_collector_deployed": False,
            "capture_measurable": False,
            "prospective_rows": 0,
        },
        "remaining_authorization_blockers": [
            "no candidate absolute capture lower bound clears +0.10",
            "the corrected HR mapping failed its locked confirmation gate",
            "historical timestamps do not prove executable sportsbook availability or fills",
            "no durable per-game T−4h primary collector is deployed",
            "no prospective linked entry, close, official outcome, and void evidence exists",
            "May 2026 remains sealed and no candidate is ready to open it",
            "no immutable market-specific authorization record exists",
        ],
        "single_highest_value_next_action": (
            "After explicit approval to rotate/configure credentials and provision durable infrastructure, "
            "deploy the already mutation-tested hard-keyed DraftKings Hits per-game T−4h forward-shadow "
            "collector, with GitHub only as verification and alert backup. Do not reopen the rejected HR "
            "mapping unless a materially different pre-2026 signal independently qualifies."
        ),
        "inputs": bound,
        "terminal_statement": (
            "The a3.2 branch is complete and rejected without weakening any guard. Further irreplaceable "
            "authorization evidence requires prospective executable-price capture under explicit deployment authority."
        ),
    }


def self_test() -> int:
    _, items = verify_inputs()
    require_facts(items)
    print("[OK] current bound facts pass")
    mutations = []
    bad = copy.deepcopy(items); bad["mapping_rejection"]["status"] = "PASS"
    mutations.append((bad, "mapping pass rewrite"))
    bad = copy.deepcopy(items); bad["confirmation_validation"]["status"] = "VALID_PASS_LEVEL_MAPPING_RESEARCH_ONLY"
    mutations.append((bad, "confirmation validation rewrite"))
    bad = copy.deepcopy(items); bad["raw_validation"]["status"] = "VALID_RAW_A3_1"
    mutations.append((bad, "retired builder schema"))
    bad = copy.deepcopy(items); bad["shadow_readiness"]["guard_checks_passed"] = 57
    mutations.append((bad, "shadow guard loss"))
    bad = copy.deepcopy(items); bad["shadow_readiness"]["forward_evidence"]["capture_measurable"] = True
    mutations.append((bad, "fabricated forward evidence"))
    bad = copy.deepcopy(items); bad["base_report"]["may_2026_read"] = True
    mutations.append((bad, "May access"))
    for payload, label in mutations:
        try:
            require_facts(payload)
        except ValueError:
            print(f"[OK] MUTATION {label} fails")
        else:
            raise AssertionError(f"mutation unexpectedly passed: {label}")
    print("7/7")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--out-dir")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    if not args.out_dir:
        parser.error("production build requires --out-dir")
    out_dir = ROOT / args.out_dir
    report_path = out_dir / "report.json"
    markdown_path = out_dir / "REPORT.md"
    binding_path = out_dir / "report_binding.json"
    if any(path.exists() for path in (report_path, markdown_path, binding_path)):
        raise FileExistsError("a3.2 Tuesday evidence update already exists; refusing overwrite")
    bound, items = verify_inputs()
    require_facts(items)
    report = build(bound, items)
    markdown = _markdown(report)
    out_dir.mkdir(parents=True, exist_ok=True)
    report_tmp = report_path.with_suffix(".json.tmp")
    markdown_tmp = markdown_path.with_suffix(".md.tmp")
    binding_tmp = binding_path.with_suffix(".json.tmp")
    report_tmp.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_tmp.write_text(markdown, encoding="utf-8")
    binding = {
        "schema_version": "tuesday-a3.2-evidence-binding-v1",
        "built_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "report": {"path": str(report_path), "sha256": sha256(report_tmp)},
        "markdown": {"path": str(markdown_path), "sha256": sha256(markdown_tmp)},
        "betting_authorized": False,
        "may_2026_read": False,
    }
    binding_tmp.write_text(json.dumps(binding, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report_tmp.replace(report_path)
    markdown_tmp.replace(markdown_path)
    binding_tmp.replace(binding_path)
    print("TUESDAY A3.2 EVIDENCE UPDATE COMPLETE - RESEARCH ONLY")
    print(f"  report: {report_path} sha {sha256(report_path)}")
    print(f"  markdown: {markdown_path} sha {sha256(markdown_path)}")
    print(f"  binding: {binding_path} sha {sha256(binding_path)}")
    print("  May 2026 opened: NO; betting authorized: NO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
