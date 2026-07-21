#!/usr/bin/env python3
"""Adjudicate the opt-in opposing-pitcher HR/9 direction correction.

Uses only the already-open 37-date March/April diagnostic block and 19-date
June confirmation block. May is rejected before feature loading. The script
never changes production configuration and never authorizes betting.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from scripts.audit_hr_over_frozen_baseline import _feature_rows
from src.evaluation.hr_over_baseline_diagnostic import (
    MODEL_KEY,
    paired_date_block_interval,
    point_metrics,
    require_exact_keys,
    score_probability,
    validate_open_dates,
    validate_pa_distribution,
)
from src.models.dataclasses import LeagueBaselines
from src.simulation.pa_simulator import HybridPASimulator, PASimulatorConfig


ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verified(record: dict[str, Any], label: str) -> Path:
    path = (ROOT / str(record["path"])).resolve()
    if not path.is_file():
        raise ValueError(f"{label} is missing: {path}")
    actual = sha256(path)
    if actual != str(record["sha256"]).lower():
        raise ValueError(f"{label} hash mismatch: expected {record['sha256']}, got {actual}")
    return path


def binary_auc(y: pd.Series, p: pd.Series) -> float:
    outcome = y.astype(bool).to_numpy()
    values = pd.to_numeric(p, errors="raise")
    positive = int(outcome.sum())
    negative = int((~outcome).sum())
    if positive == 0 or negative == 0:
        raise ValueError("AUC requires both outcomes")
    ranks = values.rank(method="average").to_numpy(float)
    return float((ranks[outcome].sum() - positive * (positive + 1) / 2) / (positive * negative))


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    except Exception:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass
        raise


def period_summary(rows: pd.DataFrame, dates: list[str], seed: int) -> dict[str, Any]:
    period = rows[rows.official_game_date.astype(str).isin(dates)].copy()
    baseline = score_probability(period, "frozen_monte_carlo")
    legacy = score_probability(period, "legacy_exact")
    candidate = score_probability(period, "candidate_exact")
    return {
        "frozen_monte_carlo": {"point": point_metrics(baseline), "auc": binary_auc(period.official_won, period.frozen_monte_carlo)},
        "legacy_exact": {
            "point": point_metrics(legacy),
            "auc": binary_auc(period.official_won, period.legacy_exact),
            "paired_vs_frozen": paired_date_block_interval(baseline, legacy, dates, seed=seed),
        },
        "candidate_exact": {
            "point": point_metrics(candidate),
            "auc": binary_auc(period.official_won, period.candidate_exact),
            "paired_vs_frozen": paired_date_block_interval(baseline, candidate, dates, seed=seed),
            "paired_vs_legacy_exact": paired_date_block_interval(legacy, candidate, dates, seed=seed),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", default="data/analysis/hr_over_contract_v1/pitcher_hr9_direction_candidate_v1/protocol.json")
    parser.add_argument("--output", default="data/analysis/hr_over_contract_v1/pitcher_hr9_direction_candidate_v1/report.json")
    args = parser.parse_args()
    protocol_path = (ROOT / args.protocol).resolve()
    output_path = (ROOT / args.output).resolve()
    if output_path.exists():
        raise FileExistsError(f"refusing to overwrite adjudication: {output_path}")
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    if protocol.get("status") != "LOCKED_BEFORE_IMPLEMENTATION_RESEARCH_ONLY":
        raise ValueError("candidate protocol was not locked before implementation")
    if protocol.get("betting_authorized") is not False or protocol.get("may_opened") is not False:
        raise ValueError("candidate protocol violates authorization or sealed-May invariants")

    inputs = protocol["inputs"]
    base_protocol_path = verified(inputs["baseline_protocol"], "baseline protocol")
    verified(inputs["baseline_report"], "baseline report")
    rows_path = verified(inputs["baseline_diagnostic_rows"], "baseline rows")
    # The protocol intentionally binds the pre-change source hash. It must not
    # equal current source after implementation, but is retained in the report.
    current_source = ROOT / inputs["pa_simulator_before"]["path"]
    current_source_hash = sha256(current_source)
    if current_source_hash == inputs["pa_simulator_before"]["sha256"]:
        raise ValueError("candidate implementation is inert: source still equals pre-change hash")

    base_protocol = json.loads(base_protocol_path.read_text(encoding="utf-8"))
    base_inputs = base_protocol["inputs"]
    manifest_path = verified(base_inputs["frozen_reconstruction_manifest"], "frozen manifest")
    pa_path = verified(base_inputs["fitted_pa_distribution"], "PA distribution")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    dates = validate_open_dates(manifest["dates"])
    diagnostic_dates = [d for d in dates if d < "2026-05-01"]
    confirmation_dates = [d for d in dates if d >= "2026-06-01"]
    if len(diagnostic_dates) != 37 or len(confirmation_dates) != 19:
        raise ValueError("candidate chronology differs from the locked 37+19 split")
    pa_weights = validate_pa_distribution(json.loads(pa_path.read_text(encoding="utf-8")))

    rows = pd.read_csv(rows_path, low_memory=False)
    if len(rows) != 8_854 or rows.duplicated(MODEL_KEY).any():
        raise ValueError("baseline diagnostic universe is not the certified 8,854 unique keys")
    if any(str(d).startswith("2026-05-") for d in rows.official_game_date):
        raise ValueError("May entered candidate rows")

    frozen_manifest = copy.deepcopy(manifest)
    frozen_manifest["effective_config"].setdefault("pa_simulator", {}).pop(
        "correct_pitcher_hr9_direction", None
    )
    legacy_features = _feature_rows(frozen_manifest, rows, pa_weights)
    require_exact_keys(legacy_features, rows, "legacy exact features")
    legacy = legacy_features[MODEL_KEY + ["active_exact"]].rename(columns={"active_exact": "legacy_recomputed"})
    rows = rows.merge(legacy, on=MODEL_KEY, how="inner", validate="one_to_one")
    if not np.allclose(rows.legacy_recomputed, rows.active_exact, rtol=0.0, atol=1e-15):
        raise ValueError("disabled correction did not reproduce the locked legacy exact probability")
    rows["legacy_exact"] = rows.legacy_recomputed

    candidate_manifest = copy.deepcopy(manifest)
    candidate_manifest["effective_config"].setdefault("pa_simulator", {})[
        "correct_pitcher_hr9_direction"
    ] = True
    candidate_features = _feature_rows(candidate_manifest, rows, pa_weights)
    require_exact_keys(candidate_features, rows, "candidate exact features")
    candidate = candidate_features[MODEL_KEY + ["active_exact"]].rename(columns={"active_exact": "candidate_exact"})
    rows = rows.merge(candidate, on=MODEL_KEY, how="inner", validate="one_to_one")
    if np.allclose(rows.candidate_exact, rows.legacy_exact, rtol=0.0, atol=1e-15):
        raise ValueError("candidate is inert on the certified open universe")

    # Boundary mutation: neutral is identical; higher HR/9 reverses only under
    # the opt-in correction.
    league = LeagueBaselines()
    legacy_sim = HybridPASimulator(config=PASimulatorConfig.from_league(league), league_baselines=league)
    candidate_sim = HybridPASimulator(
        config=PASimulatorConfig.from_league(league, correct_pitcher_hr9_direction=True),
        league_baselines=league,
    )
    neutral_old = legacy_sim.expected_outcome_probabilities(pitcher_hr_per_9=league.hr_per_9)
    neutral_new = candidate_sim.expected_outcome_probabilities(pitcher_hr_per_9=league.hr_per_9)
    if neutral_old != neutral_new:
        raise ValueError("candidate changes a neutral pitcher")
    low, high = league.hr_per_9 * 0.5, league.hr_per_9 * 1.5
    if not (
        legacy_sim.expected_outcome_probabilities(pitcher_hr_per_9=high)["home_run"]
        < legacy_sim.expected_outcome_probabilities(pitcher_hr_per_9=low)["home_run"]
        and candidate_sim.expected_outcome_probabilities(pitcher_hr_per_9=high)["home_run"]
        > candidate_sim.expected_outcome_probabilities(pitcher_hr_per_9=low)["home_run"]
    ):
        raise ValueError("pitcher HR/9 direction mutation was not distinguished")

    gradeable = rows[rows.official_gradeable.astype(bool)].copy()
    seed = int(protocol["uncertainty"]["seed"])
    summaries = {
        "diagnostic": period_summary(gradeable, diagnostic_dates, seed),
        "confirmation": period_summary(gradeable, confirmation_dates, seed),
    }
    direction = all(
        summaries[role]["candidate_exact"]["point"][metric]
        < summaries[role]["frozen_monte_carlo"]["point"][metric]
        for role in ("diagnostic", "confirmation")
        for metric in ("brier", "log_loss")
    )
    interval = summaries["confirmation"]["candidate_exact"]["paired_vs_frozen"]
    strength = (
        interval["variant_minus_baseline_brier_95"][1] < 0.0
        and interval["variant_minus_baseline_log_loss_95"][1] < 0.0
    )
    auc_nonregression = summaries["confirmation"]["candidate_exact"]["auc"] >= summaries["confirmation"]["frozen_monte_carlo"]["auc"]
    movement = all(
        summaries[role]["candidate_exact"]["point"]["positive_ev_raw_movement"] is not None
        and summaries[role]["candidate_exact"]["point"]["positive_ev_raw_movement"] >= 0.0
        for role in ("diagnostic", "confirmation")
    )
    passed = bool(direction and strength and auc_nonregression and movement)
    report = {
        "schema_version": "hr-pitcher-hr9-direction-candidate-report-v1",
        "built_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "OPEN_PERIOD_CANDIDATE_SUPPORTED_NOT_PROMOTED" if passed else "CANDIDATE_REJECTED_OPEN_GATE_FAILED",
        "betting_authorized": False,
        "may_opened": False,
        "historical_executability_verified": False,
        "protocol": {"path": str(protocol_path), "sha256": sha256(protocol_path)},
        "source": {"pre_change_sha256": inputs["pa_simulator_before"]["sha256"], "candidate_sha256": current_source_hash},
        "funnel": {"certified_keys": int(len(rows)), "official_gradeable_keys": int(len(gradeable)), "changed_probabilities": int((rows.candidate_exact != rows.legacy_exact).sum())},
        "periods": summaries,
        "gate": {"proper_score_direction_both_blocks": bool(direction), "confirmation_uncertainty": bool(strength), "confirmation_auc_nonregression": bool(auc_nonregression), "raw_market_movement_nonnegative": bool(movement), "all_pass": passed},
        "mutations": {"disabled_reproduces_legacy": True, "neutral_pitcher_inert": True, "direction_reversed_only_when_enabled": True, "exact_keys_preserved": True, "May_excluded": True},
        "decision": "FREEZE_FOR_FUTURE_UNTOUCHED_JUDGE" if passed else "REJECT_AND_KEEP_FROZEN",
        "interpretation": "Open/spent historical research only. Even a pass cannot authorize betting or establish executable ROI.",
    }
    if report["betting_authorized"] is not False or report["may_opened"] is not False:
        raise ValueError("report violated fail-closed invariants")
    atomic_json(output_path, report)
    print(json.dumps({"status": report["status"], "gate": report["gate"], "output": str(output_path)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
