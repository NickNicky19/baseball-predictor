#!/usr/bin/env python3
"""Build and apply the locked official DraftKings HR-over grading bridge."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.hr_over_evaluation import MODEL_KEY  # noqa: E402
from src.evaluation.hr_over_settlement import (  # noqa: E402
    attach_official_grades,
    build_settlement_bridge,
    official_positive_ev_metrics,
    official_roi_interval,
)


def sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_json(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root is not an object: {path}")
    return payload


def verify_hash(item: dict[str, Any], label: str) -> Path:
    path = ROOT / str(item["path"])
    if not path.is_file() or sha256(path) != str(item["sha256"]).lower():
        raise ValueError(f"{label} is missing or hash-mismatched")
    return path


def collapse_scored_facts(scored: pd.DataFrame) -> pd.DataFrame:
    required = [*MODEL_KEY, "arm", "role", "official_game_date", "actual_value",
                "settlement_present"]
    missing = [column for column in required if column not in scored.columns]
    if missing:
        raise ValueError(f"evaluation scored rows missing {missing}")
    if set(scored.arm.astype(str).unique()) != {"frozen", "candidate"}:
        raise ValueError("evaluation scored rows do not contain exactly two arms")
    if scored.duplicated([*MODEL_KEY, "arm"]).any():
        raise ValueError("evaluation scored arm identity is duplicated")
    fact_columns = [*MODEL_KEY, "role", "official_game_date", "actual_value",
                    "settlement_present"]
    frozen = scored[scored.arm.eq("frozen")][fact_columns].sort_values(MODEL_KEY).reset_index(drop=True)
    candidate = scored[scored.arm.eq("candidate")][fact_columns].sort_values(MODEL_KEY).reset_index(drop=True)
    if not frozen.equals(candidate):
        raise ValueError("frozen/candidate factual grading rows differ")
    return frozen


def vendor_agreement(frame: pd.DataFrame) -> dict[str, int]:
    present = frame.settlement_present.astype(bool)
    gradeable = frame.official_gradeable.astype(bool)
    return {
        "AGREE_scored": int((present & gradeable).sum()),
        "AGREE_excluded": int((~present & ~gradeable).sum()),
        "FALSE_INCLUSION": int((present & ~gradeable).sum()),
        "FALSE_EXCLUSION": int((~present & gradeable).sum()),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--protocol",
        default="data/analysis/hr_over_contract_v1/settlement_v1/protocol.json",
    )
    parser.add_argument(
        "--out-dir", default="data/analysis/hr_over_contract_v1/settlement_v1"
    )
    args = parser.parse_args(argv)

    protocol_path = ROOT / args.protocol
    protocol = load_json(protocol_path)
    if protocol.get("status") != "LOCKED_BEFORE_HR_OVER_REGRADING":
        raise ValueError("HR settlement protocol was not locked before regrading")
    inputs = protocol["inputs"]
    paths = {name: verify_hash(item, name) for name, item in inputs.items()}
    evaluation_protocol_item = protocol["chronology"]
    evaluation_protocol_path = ROOT / evaluation_protocol_item["source"]
    if sha256(evaluation_protocol_path) != evaluation_protocol_item["source_sha256"]:
        raise ValueError("evaluation chronology protocol is hash-mismatched")
    evaluation_protocol = load_json(evaluation_protocol_path)
    diagnostic_dates = list(evaluation_protocol["chronology"]["diagnostic_dates"])
    confirmation_dates = list(evaluation_protocol["chronology"]["confirmation_dates"])
    allowed_dates = diagnostic_dates + confirmation_dates
    if any(value.startswith("2026-05-") for value in allowed_dates):
        raise ValueError("May leaked into the settlement protocol")

    evaluation_report = load_json(paths["evaluation_report"])
    if evaluation_report.get("betting_authorized") is not False:
        raise ValueError("parent evaluation unexpectedly authorized betting")
    if evaluation_report.get("may_opened") is not False:
        raise ValueError("parent evaluation opened May")
    if evaluation_report.get("verdict") != "PREDECLARED_HR_RESEARCH_ADVANCE_GATE_FAILED":
        raise ValueError("settlement correction cannot overwrite the parent candidate verdict")

    rules = load_json(paths["draftkings_rules_evidence"])
    facts = rules.get("verified_facts", {})
    for required_rule in (
        "pregame_single_game_player_prop", "starting_batter_early_exit",
        "regular_game_length",
    ):
        if required_rule not in facts:
            raise ValueError(f"DraftKings rule evidence missing {required_rule}")

    official_manifest = load_json(paths["official_facts_manifest"])
    if list(official_manifest.get("official_date_universe", [])) != allowed_dates:
        raise ValueError("official-facts manifest does not carry the exact open dates")
    if official_manifest.get("source_type") != "market_source":
        raise ValueError("official facts were not built from the exact HR market source")

    scored = pd.read_csv(paths["evaluation_scored_rows"])
    market_facts = collapse_scored_facts(scored)
    eligibility = pd.read_csv(paths["official_hitter_eligibility"])
    game_completion = pd.read_csv(paths["official_game_completion"])
    bridge, funnel = build_settlement_bridge(
        market_facts, eligibility, game_completion, allowed_dates
    )
    regraded = attach_official_grades(scored, bridge)

    draws = int(protocol["regrading_contract"]["bootstrap_draws"])
    seed = int(protocol["regrading_contract"]["bootstrap_seed"])
    roles: dict[str, Any] = {}
    for role, dates in (("diagnostic", diagnostic_dates), ("confirmation", confirmation_dates)):
        role_rows = regraded[regraded.role.eq(role)]
        roles[role] = {}
        for arm in ("frozen", "candidate"):
            arm_rows = role_rows[role_rows.arm.eq(arm)].copy()
            roles[role][arm] = {
                "point": official_positive_ev_metrics(arm_rows),
                "date_block_interval": official_roi_interval(
                    arm_rows, dates, draws, seed
                ),
            }

    out_dir = ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    bridge_path = out_dir / "official_settlement_bridge.csv"
    regraded_path = out_dir / "regraded_scored_rows.csv"
    report_path = out_dir / "report.json"
    bridge.to_csv(bridge_path, index=False)
    regraded.to_csv(regraded_path, index=False)
    unresolved = int(bridge.official_grade_status.astype(str).str.startswith("UNRESOLVED").sum())
    payload: dict[str, Any] = {
        "schema_version": "draftkings-hr-over-official-settlement-report-v1",
        "built_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "protocol": {"path": str(protocol_path), "sha256": sha256(protocol_path)},
        "parent_evaluation_verdict": evaluation_report["verdict"],
        "parent_candidate_gate_remains_failed": True,
        "may_opened": False,
        "betting_authorized": False,
        "historical_executability_verified": False,
        "official_settlement_unresolved_rows": unresolved,
        "funnel": funnel,
        "vendor_settlement_presence_agreement": vendor_agreement(bridge),
        "roles": roles,
        "verdict": (
            "OFFICIAL_OPEN_DATE_GRADING_COMPLETE_RESEARCH_ONLY"
            if unresolved == 0 else "OFFICIAL_GRADING_HAS_UNRESOLVED_ROWS"
        ),
        "interpretation": (
            "Published DraftKings rules and official MLB postgame facts now define "
            "the historical grading denominator. This does not prove the T-4h quote "
            "was executable and does not reverse the failed HR candidate gate."
        ),
        "outputs": {
            "official_settlement_bridge": {
                "path": str(bridge_path), "sha256": sha256(bridge_path)
            },
            "regraded_scored_rows": {
                "path": str(regraded_path), "sha256": sha256(regraded_path)
            },
        },
    }
    report_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    confirmation = roles["confirmation"]
    print("DRAFTKINGS HR OVER 0.5 - OFFICIAL SETTLEMENT REGRADING")
    print(f"  input/gradeable/unresolved: {funnel['input_rows']:,} / "
          f"{funnel['gradeable_rows']:,} / {unresolved:,}")
    print(f"  status counts: {funnel['status_counts']}")
    print(f"  vendor agreement: {payload['vendor_settlement_presence_agreement']}")
    for arm in ("frozen", "candidate"):
        point = confirmation[arm]["point"]
        interval = confirmation[arm]["date_block_interval"]["official_flat_stake_roi_95"]
        print(
            f"  confirmation {arm}: positive-EV {point['positive_ev_rows']:,}; "
            f"gradeable {point['official_gradeable_rows']:,}; "
            f"ROI {point['official_flat_stake_roi']:+.4f}; "
            f"95% {interval[0]:+.4f} to {interval[1]:+.4f}"
        )
    print(f"  verdict: {payload['verdict']}")
    print("  parent candidate gate: FAILED; betting authorized: NO; May opened: NO")
    print(f"  wrote {report_path}\n        {bridge_path}\n        {regraded_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
