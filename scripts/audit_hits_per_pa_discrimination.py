#!/usr/bin/env python3
"""Test whether player-specific per-PA hit probabilities beat simple controls."""
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
sys.path.insert(0, str(ROOT))

from src.evaluation.hits_pa_counterfactual import (  # noqa: E402
    reject_holdout_dates,
    require_exact_key_set,
)
from src.evaluation.hits_per_pa_diagnostic import (  # noqa: E402
    add_selector_baseline_probabilities,
    fit_selector_baselines,
    paired_date_block_score_interval,
    probability_score_totals,
    validate_trials,
)


SCHEMA = "hits-per-pa-discrimination-report-v1"
PLAYER_KEY = ["mlb_game_pk", "player_id"]


def sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_json(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    payload = json.loads(source.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root is not an object: {source}")
    return payload


def verified_path(record: dict[str, Any], label: str) -> Path:
    path = Path(str(record.get("path", "")))
    expected = str(record.get("sha256", ""))
    if not path.is_file() or not expected or sha256(path) != expected:
        raise ValueError(f"{label} is missing or hash-mismatched")
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--protocol",
        default="data/analysis/market_policy_hits_2026/per_pa_discrimination_protocol_v1.json",
    )
    ap.add_argument(
        "--out-dir",
        default="data/analysis/market_policy_hits_2026/per_pa_discrimination_time_safe_v1",
    )
    args = ap.parse_args(argv)

    protocol_path = Path(args.protocol)
    protocol = load_json(protocol_path)
    if protocol.get("schema_version") != "hits-per-pa-discrimination-protocol-v1":
        raise ValueError("unknown per-PA discrimination protocol")
    if protocol.get("status") != "PREDECLARED_DIAGNOSTIC_ONLY":
        raise ValueError("per-PA discrimination protocol was not predeclared")
    if protocol.get("betting_authorized") is not False:
        raise ValueError("per-PA discrimination refuses an authorized protocol")

    skill_report_path = verified_path(protocol["per_pa_skill_report"], "per-PA skill report")
    skill_report = load_json(skill_report_path)
    if skill_report.get("betting_authorized") is not False:
        raise ValueError("per-PA skill report unexpectedly authorizes betting")
    if skill_report["chronology"].get("may_opened") is not False:
        raise ValueError("per-PA skill report says May was opened")
    trials_record = skill_report["outputs"]["trials"]
    trials_path = verified_path(trials_record, "per-PA player-game trials")
    selector_dates = list(skill_report["chronology"]["selector_dates"])
    confirmation_dates = list(skill_report["chronology"]["confirmation_dates"])
    holdout_start = str(skill_report["chronology"]["holdout_start"])
    reject_holdout_dates([*selector_dates, *confirmation_dates], holdout_start)

    trials = validate_trials(pd.read_csv(trials_path))
    if len(trials) != 3386 or len(trials) != int(skill_report["player_games"]):
        raise ValueError("discrimination diagnostic does not have exactly 3,386 trials")
    require_exact_key_set(
        set(map(tuple, trials[PLAYER_KEY].to_numpy())),
        set(map(tuple, pd.read_csv(trials_path, usecols=PLAYER_KEY).to_numpy())),
        "per-PA discrimination trial",
    )
    reject_holdout_dates(sorted(trials.official_game_date.unique()), holdout_start)

    global_rate, slot_rates = fit_selector_baselines(
        trials,
        selector_dates=selector_dates,
        confirmation_dates=confirmation_dates,
    )
    scored = add_selector_baseline_probabilities(trials, global_rate, slot_rates)
    confirmation = scored[scored.official_game_date.isin(confirmation_dates)].copy()
    if confirmation.empty or set(confirmation.official_game_date.unique()) - set(confirmation_dates):
        raise ValueError("confirmation discrimination frame violates chronology")

    bootstrap = 20000
    seed = 17
    records: list[dict[str, Any]] = []
    baseline_columns = {
        "selector_global": "selector_global_probability",
        "selector_slot": "selector_slot_probability",
    }
    candidate_column = "inferred_per_pa_hit_probability"
    candidate_scores = probability_score_totals(confirmation, candidate_column)
    for baseline, baseline_column in baseline_columns.items():
        baseline_scores = probability_score_totals(confirmation, baseline_column)
        for metric, score_name in (
            ("brier", "pa_weighted_brier"),
            ("log_loss", "pa_weighted_log_loss"),
        ):
            interval = paired_date_block_score_interval(
                confirmation,
                candidate_column=candidate_column,
                baseline_column=baseline_column,
                metric=metric,
                declared_dates=confirmation_dates,
                bootstrap=bootstrap,
                seed=seed,
            )
            point = float(candidate_scores[score_name] - baseline_scores[score_name])
            records.append(
                {
                    "baseline": baseline,
                    "metric": metric,
                    "confirmation_player_games": int(len(confirmation)),
                    "confirmation_official_pa": int(candidate_scores["official_pa"]),
                    "candidate_score": float(candidate_scores[score_name]),
                    "baseline_score": float(baseline_scores[score_name]),
                    "candidate_minus_baseline": point,
                    "lower_95": interval.lower,
                    "upper_95": interval.upper,
                    "valid_bootstrap_draws": interval.valid_draws,
                    "candidate_strictly_better": bool(interval.upper < 0.0),
                }
            )
    comparison = pd.DataFrame(records)
    success = bool(comparison.candidate_strictly_better.all())

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    comparison_path = out_dir / "confirmation_score_comparison.csv"
    scored_path = out_dir / "trials_with_selector_baselines.csv"
    baselines_path = out_dir / "selector_baselines.json"
    report_path = out_dir / "report.json"
    comparison.to_csv(comparison_path, index=False)
    scored.to_csv(scored_path, index=False)
    baseline_payload = {
        "fit_dates": selector_dates,
        "confirmation_dates_not_used": True,
        "selector_global_probability": global_rate,
        "selector_slot_probabilities": {str(slot): value for slot, value in slot_rates.items()},
    }
    baselines_path.write_text(json.dumps(baseline_payload, indent=2) + "\n", encoding="utf-8")
    report = {
        "schema_version": SCHEMA,
        "built_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "protocol": {"path": str(protocol_path), "sha256": sha256(protocol_path)},
        "per_pa_skill_report": {"path": str(skill_report_path), "sha256": sha256(skill_report_path)},
        "chronology": {
            "selector_dates": selector_dates,
            "confirmation_dates": confirmation_dates,
            "holdout_start": holdout_start,
            "may_opened": False,
        },
        "player_games": int(len(scored)),
        "confirmation_player_games": int(len(confirmation)),
        "confirmation_official_pa": int(candidate_scores["official_pa"]),
        "all_four_paired_score_gates_passed": success,
        "gate_rule": protocol["success_condition"],
        "interpretation": (
            "Player-specific q adds confirmation-period discrimination beyond both "
            "simple selector controls only when all four paired upper bounds are below zero."
        ),
        "outputs": {
            "comparison": {"path": str(comparison_path), "sha256": sha256(comparison_path)},
            "scored_trials": {"path": str(scored_path), "sha256": sha256(scored_path)},
            "selector_baselines": {"path": str(baselines_path), "sha256": sha256(baselines_path)},
        },
        "verdict": "DIAGNOSTIC_ONLY",
        "betting_authorized": False,
    }
    report_path.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")

    print("HITS PER-PA DISCRIMINATION CONTROL — CONFIRMATION ONLY")
    print(f"  confirmation player-games: {len(confirmation):,}; official PA: {int(candidate_scores['official_pa']):,}")
    for row in comparison.itertuples(index=False):
        print(
            f"  vs {row.baseline} {row.metric}: {row.candidate_minus_baseline:+.6f} "
            f"[{row.lower_95:+.6f}, {row.upper_95:+.6f}] "
            f"strictly better={row.candidate_strictly_better}"
        )
    print(f"  all four discrimination gates passed: {success}")
    print("  production changes: NONE; May opened: NO; betting authorized: NO")
    print(f"  wrote: {report_path}\n         {comparison_path}\n         {baselines_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
