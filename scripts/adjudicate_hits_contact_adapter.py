#!/usr/bin/env python3
"""Adjudicate the point-in-time Hits contact adapter on open 2026 evidence.

The evaluator is deliberately bound to the candidate protocol's existing
research-only freshness proxy and previously selected payout threshold.  It
cannot fit a new policy, inspect May, authorize betting, or pool another
market into Hits.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.evaluation.hits_policy_fit import (  # noqa: E402
    arm_policy_rows,
    build_policy_pairs,
    date_block_interval,
    paired_capture_change_interval,
    policy_metrics,
)


MODEL_KEY = ["mlb_game_pk", "player_id", "category", "line"]
EXPECTED_PROTOCOL_SHA = "27ab9c23eb78dd8cd9045b3e243ad768b5bbbab076a12a8c49d34de17c53336b"
EPSILON = 1e-12


def sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_json(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def reject_may(frame: pd.DataFrame, column: str, label: str) -> None:
    if column not in frame.columns:
        raise ValueError(f"{label} missing {column}")
    dates = pd.to_datetime(frame[column], errors="coerce").dt.strftime("%Y-%m-%d")
    if dates.isna().any():
        raise ValueError(f"{label} has invalid dates")
    leaked = sorted({value for value in dates if value.startswith("2026-05")})
    if leaked:
        raise ValueError(f"{label} contains sealed May date(s): {leaked}")


def require_contract(protocol: dict[str, Any]) -> tuple[float, float, int, int]:
    if protocol.get("market") != "hits":
        raise ValueError("candidate protocol is not Hits-only")
    evaluation = protocol.get("open_evaluation", {})
    amendment = protocol.get("economic_evaluation_amendment", {})
    if amendment.get("status") != "locked_after_integrity_validation_before_any_candidate_metric":
        raise ValueError("economic evaluation amendment is not locked")
    if amendment.get("candidate_metrics_read_before_amendment") is not False:
        raise ValueError("economic inputs were not locked before candidate scoring")
    if amendment.get("prior_policy_fit_report", {}).get("verdict") != "NO_POLICY_QUALIFIES":
        raise ValueError("prior policy-fit limitation was not preserved")
    age = float(amendment["max_quote_age_minutes"])
    threshold = float(amendment["min_expected_profit_per_unit"])
    bootstrap = int(evaluation["bootstrap_draws"])
    seed = int(evaluation["bootstrap_seed"])
    if age != 90.0 or threshold != 0.006182901036052662:
        raise ValueError("economic inputs drifted from the locked research contract")
    if bootstrap != 10000 or seed != 41721:
        raise ValueError("bootstrap contract drifted")
    if evaluation.get("may_2026_allowed") is not False:
        raise ValueError("protocol does not preserve sealed May")
    return age, threshold, bootstrap, seed


def verify_protocol_inputs(protocol: dict[str, Any], root: Path) -> None:
    amendment = protocol["economic_evaluation_amendment"]
    for label in (
        "prior_policy_fit_report",
        "june_economic_enrichment_audit",
        "june_identity_correction_certificate",
    ):
        item = amendment[label]
        path = root / item["path"]
        if sha256(path) != item["sha256"]:
            raise ValueError(f"{label} hash mismatch")
    fit = load_json(root / amendment["prior_policy_fit_report"]["path"])
    if fit.get("verdict") != "NO_POLICY_QUALIFIES":
        raise ValueError("bound policy-fit report no longer says NO_POLICY_QUALIFIES")
    if fit.get("betting_authorized") is not False:
        raise ValueError("bound policy-fit report unexpectedly authorizes betting")


def binary_scores(pairs: pd.DataFrame, arm: str) -> pd.DataFrame:
    column = {"baseline": "p_frozen", "candidate": "p_candidate"}[arm]
    p = pd.to_numeric(pairs[column], errors="raise").to_numpy(float)
    y = (
        pd.to_numeric(pairs.actual_value, errors="raise").to_numpy(float)
        > pd.to_numeric(pairs.line, errors="raise").to_numpy(float)
    ).astype(float)
    safe = np.clip(p, EPSILON, 1.0 - EPSILON)
    return pd.DataFrame(
        {
            "official_game_date": pairs.official_game_date.astype(str).to_numpy(),
            "brier": (p - y) ** 2,
            "log_loss": -(y * np.log(safe) + (1.0 - y) * np.log1p(-safe)),
        },
        index=pairs.index,
    )


def score_summary(rows: pd.DataFrame) -> dict[str, float]:
    return {
        "brier": float(rows.brier.mean()),
        "log_loss": float(rows.log_loss.mean()),
    }


def paired_score_interval(
    baseline: pd.DataFrame,
    candidate: pd.DataFrame,
    metric: str,
    *,
    dates: list[str],
    bootstrap: int,
    seed: int,
) -> dict[str, Any]:
    if metric not in {"brier", "log_loss"}:
        raise ValueError("paired score metric must be brier or log_loss")
    if not baseline.index.equals(candidate.index):
        raise ValueError("paired score rows lost exact alignment")
    delta = candidate[metric].to_numpy(float) - baseline[metric].to_numpy(float)
    frame = pd.DataFrame(
        {"official_game_date": baseline.official_game_date.astype(str), "delta": delta}
    )
    unexpected = set(frame.official_game_date) - set(dates)
    if unexpected:
        raise ValueError(f"score rows contain undeclared dates {sorted(unexpected)}")
    totals = frame.groupby("official_game_date").delta.agg(["sum", "count"])
    totals = totals.reindex(dates, fill_value=0.0)
    rng = np.random.default_rng(seed)
    weights = rng.multinomial(len(dates), np.full(len(dates), 1.0 / len(dates)), size=bootstrap)
    denominator = weights @ totals["count"].to_numpy(float)
    valid = denominator > 0
    values = (weights[valid] @ totals["sum"].to_numpy(float)) / denominator[valid]
    return {
        "point": float(delta.mean()),
        "lower": float(np.percentile(values, 2.5)),
        "upper": float(np.percentile(values, 97.5)),
        "valid_draws": int(len(values)),
    }


def paired_roi_interval(
    baseline_rows: pd.DataFrame,
    candidate_rows: pd.DataFrame,
    threshold: float,
    *,
    dates: list[str],
    bootstrap: int,
    seed: int,
) -> dict[str, Any]:
    def totals(rows: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        selected = rows[rows.expected_profit >= threshold]
        grouped = selected.groupby("official_game_date").agg(
            profit=("realised_profit", "sum"), count=("realised_profit", "size")
        ).reindex(dates, fill_value=0.0)
        return grouped.profit.to_numpy(float), grouped["count"].to_numpy(float)

    b_profit, b_count = totals(baseline_rows)
    c_profit, c_count = totals(candidate_rows)
    rng = np.random.default_rng(seed)
    weights = rng.multinomial(len(dates), np.full(len(dates), 1.0 / len(dates)), size=bootstrap)
    b_denominator = weights @ b_count
    c_denominator = weights @ c_count
    valid = (b_denominator > 0) & (c_denominator > 0)
    values = (
        (weights[valid] @ c_profit) / c_denominator[valid]
        - (weights[valid] @ b_profit) / b_denominator[valid]
    )
    b_point = policy_metrics(baseline_rows, threshold).flat_stake_roi
    c_point = policy_metrics(candidate_rows, threshold).flat_stake_roi
    return {
        "point": float(c_point - b_point),
        "lower": float(np.percentile(values, 2.5)),
        "upper": float(np.percentile(values, 97.5)),
        "valid_draws": int(len(values)),
    }


def interval_dict(value: Any) -> dict[str, Any]:
    return {
        "lower": float(value.lower),
        "upper": float(value.upper),
        "valid_draws": int(value.valid_draws),
    }


def evaluate_block(
    name: str,
    source: pd.DataFrame,
    baseline: pd.DataFrame,
    candidate: pd.DataFrame,
    outcomes: pd.DataFrame,
    *,
    max_quote_age: float,
    threshold: float,
    dates: list[str],
    bootstrap: int,
    seed: int,
) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    for label, frame, column in (
        (f"{name} source", source, "official_game_date"),
        (f"{name} baseline", baseline, "game_date"),
        (f"{name} candidate", candidate, "game_date"),
        (f"{name} outcomes", outcomes, "game_date"),
    ):
        reject_may(frame, column, label)
    pairs, funnel = build_policy_pairs(
        source,
        baseline,
        candidate,
        outcomes,
        max_quote_age=max_quote_age,
        allowed_dates=dates,
    )
    baseline_scores = binary_scores(pairs, "baseline")
    candidate_scores = binary_scores(pairs, "candidate")
    baseline_policy = arm_policy_rows(pairs, "frozen")
    candidate_policy = arm_policy_rows(pairs, "candidate")
    baseline_metrics = policy_metrics(baseline_policy, threshold)
    candidate_metrics = policy_metrics(candidate_policy, threshold)
    capture_change = paired_capture_change_interval(
        baseline_policy,
        candidate_policy,
        threshold,
        block_dates=dates,
        bootstrap=bootstrap,
        seed=seed,
    )
    report = {
        "name": name,
        "dates": dates,
        "strict_rows": int(len(pairs)),
        "funnel": funnel,
        "probability_scores": {
            "baseline": score_summary(baseline_scores),
            "candidate": score_summary(candidate_scores),
            "candidate_minus_baseline": {
                metric: paired_score_interval(
                    baseline_scores,
                    candidate_scores,
                    metric,
                    dates=dates,
                    bootstrap=bootstrap,
                    seed=seed,
                )
                for metric in ("brier", "log_loss")
            },
        },
        "economics": {
            "baseline": {
                **baseline_metrics.__dict__,
                "capture_interval": interval_dict(
                    date_block_interval(
                        baseline_policy,
                        threshold,
                        block_dates=dates,
                        bootstrap=bootstrap,
                        seed=seed,
                        metric="capture",
                    )
                ),
                "flat_stake_roi_interval": interval_dict(
                    date_block_interval(
                        baseline_policy,
                        threshold,
                        block_dates=dates,
                        bootstrap=bootstrap,
                        seed=seed,
                        metric="flat_stake_roi",
                    )
                ),
            },
            "candidate": {
                **candidate_metrics.__dict__,
                "capture_interval": interval_dict(
                    date_block_interval(
                        candidate_policy,
                        threshold,
                        block_dates=dates,
                        bootstrap=bootstrap,
                        seed=seed,
                        metric="capture",
                    )
                ),
                "flat_stake_roi_interval": interval_dict(
                    date_block_interval(
                        candidate_policy,
                        threshold,
                        block_dates=dates,
                        bootstrap=bootstrap,
                        seed=seed,
                        metric="flat_stake_roi",
                    )
                ),
            },
            "candidate_minus_baseline_capture": {
                "point": float(candidate_metrics.capture - baseline_metrics.capture),
                **interval_dict(capture_change),
            },
            "candidate_minus_baseline_flat_stake_roi": paired_roi_interval(
                baseline_policy,
                candidate_policy,
                threshold,
                dates=dates,
                bootstrap=bootstrap,
                seed=seed,
            ),
        },
    }
    return report, pairs, baseline_policy, candidate_policy


def expect_failure(name: str, action: Callable[[], Any]) -> dict[str, Any]:
    try:
        action()
    except (AssertionError, KeyError, TypeError, ValueError) as exc:
        return {"name": name, "caught": True, "message": str(exc)}
    raise AssertionError(f"mutation did not fail: {name}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--protocol", required=True)
    ap.add_argument("--march-source", required=True)
    ap.add_argument("--march-baseline", required=True)
    ap.add_argument("--march-candidate", required=True)
    ap.add_argument("--march-outcomes", required=True)
    ap.add_argument("--june-source", required=True)
    ap.add_argument("--june-baseline", required=True)
    ap.add_argument("--june-candidate", required=True)
    ap.add_argument("--june-outcomes", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    protocol_path = Path(args.protocol)
    if sha256(protocol_path) != EXPECTED_PROTOCOL_SHA:
        raise ValueError("candidate protocol hash mismatch")
    protocol = load_json(protocol_path)
    max_quote_age, threshold, bootstrap, seed = require_contract(protocol)
    verify_protocol_inputs(protocol, ROOT)

    raw: dict[str, pd.DataFrame] = {}
    for label in (
        "march_source",
        "march_baseline",
        "march_candidate",
        "march_outcomes",
        "june_source",
        "june_baseline",
        "june_candidate",
        "june_outcomes",
    ):
        path = Path(getattr(args, label))
        if "2026-05" in str(path).replace("\\", "/"):
            raise ValueError(f"sealed May path supplied for {label}")
        raw[label] = pd.read_csv(path)

    block_contract = {item["name"]: item for item in protocol["open_evaluation"]["blocks"]}
    march_dates = sorted(
        pd.to_datetime(raw["march_source"].official_game_date).dt.strftime("%Y-%m-%d").unique()
    )
    june_dates = sorted(
        pd.to_datetime(raw["june_source"].official_game_date).dt.strftime("%Y-%m-%d").unique()
    )
    if len(march_dates) != int(block_contract["march_april_fit"]["dates"]):
        raise ValueError("March-April source date count drifted")
    if len(june_dates) != int(block_contract["june_replication"]["dates"]):
        raise ValueError("June source date count drifted")

    march, march_pairs, march_base_policy, march_candidate_policy = evaluate_block(
        "march_april_fit",
        raw["march_source"],
        raw["march_baseline"],
        raw["march_candidate"],
        raw["march_outcomes"],
        max_quote_age=max_quote_age,
        threshold=threshold,
        dates=march_dates,
        bootstrap=bootstrap,
        seed=seed,
    )
    june, june_pairs, june_base_policy, june_candidate_policy = evaluate_block(
        "june_replication",
        raw["june_source"],
        raw["june_baseline"],
        raw["june_candidate"],
        raw["june_outcomes"],
        max_quote_age=max_quote_age,
        threshold=threshold,
        dates=june_dates,
        bootstrap=bootstrap,
        seed=seed,
    )

    all_dates = [*march_dates, *june_dates]
    combined_pairs = pd.concat([march_pairs, june_pairs], ignore_index=True)
    combined_base_scores = binary_scores(combined_pairs, "baseline")
    combined_candidate_scores = binary_scores(combined_pairs, "candidate")
    combined_base_policy = pd.concat(
        [march_base_policy, june_base_policy], ignore_index=True
    )
    combined_candidate_policy = pd.concat(
        [march_candidate_policy, june_candidate_policy], ignore_index=True
    )
    combined_base_metrics = policy_metrics(combined_base_policy, threshold)
    combined_candidate_metrics = policy_metrics(combined_candidate_policy, threshold)
    combined_capture_change = paired_capture_change_interval(
        combined_base_policy,
        combined_candidate_policy,
        threshold,
        block_dates=all_dates,
        bootstrap=bootstrap,
        seed=seed,
    )
    combined_candidate_capture = date_block_interval(
        combined_candidate_policy,
        threshold,
        block_dates=all_dates,
        bootstrap=bootstrap,
        seed=seed,
        metric="capture",
    )
    combined = {
        "dates": all_dates,
        "strict_rows": int(len(combined_pairs)),
        "probability_scores": {
            "baseline": score_summary(combined_base_scores),
            "candidate": score_summary(combined_candidate_scores),
            "candidate_minus_baseline": {
                metric: paired_score_interval(
                    combined_base_scores,
                    combined_candidate_scores,
                    metric,
                    dates=all_dates,
                    bootstrap=bootstrap,
                    seed=seed,
                )
                for metric in ("brier", "log_loss")
            },
        },
        "economics": {
            "baseline": combined_base_metrics.__dict__,
            "candidate": {
                **combined_candidate_metrics.__dict__,
                "capture_interval": interval_dict(combined_candidate_capture),
            },
            "candidate_minus_baseline_capture": {
                "point": float(
                    combined_candidate_metrics.capture - combined_base_metrics.capture
                ),
                **interval_dict(combined_capture_change),
            },
            "candidate_minus_baseline_flat_stake_roi": paired_roi_interval(
                combined_base_policy,
                combined_candidate_policy,
                threshold,
                dates=all_dates,
                bootstrap=bootstrap,
                seed=seed,
            ),
        },
    }

    def block_pass(report: dict[str, Any]) -> dict[str, bool]:
        delta = report["probability_scores"]["candidate_minus_baseline"]
        economics = report["economics"]
        return {
            "brier_direction": delta["brier"]["point"] < 0.0,
            "log_loss_direction": delta["log_loss"]["point"] < 0.0,
            "capture_direction": economics["candidate_minus_baseline_capture"]["point"] > 0.0,
            "flat_stake_roi_non_regression": (
                economics["candidate_minus_baseline_flat_stake_roi"]["point"] >= 0.0
            ),
        }

    block_checks = {
        "march_april_fit": block_pass(march),
        "june_replication": block_pass(june),
    }
    combined_checks = {
        "brier_upper_below_zero": (
            combined["probability_scores"]["candidate_minus_baseline"]["brier"]["upper"] < 0.0
        ),
        "log_loss_upper_below_zero": (
            combined["probability_scores"]["candidate_minus_baseline"]["log_loss"]["upper"] < 0.0
        ),
        "capture_lower_above_zero": (
            combined["economics"]["candidate_minus_baseline_capture"]["lower"] > 0.0
        ),
    }
    open_gate_passed = all(
        value for checks in block_checks.values() for value in checks.values()
    ) and all(combined_checks.values())

    identical_candidate = raw["march_baseline"].copy()
    removed_candidate = raw["march_candidate"].iloc[1:].copy()
    may_source = raw["march_source"].copy()
    may_source.loc[may_source.index[0], "official_game_date"] = "2026-05-01"
    drifted_contract = json.loads(json.dumps(protocol))
    drifted_contract["economic_evaluation_amendment"]["max_quote_age_minutes"] = 91.0
    mutations = [
        expect_failure(
            "candidate_equals_baseline",
            lambda: build_policy_pairs(
                raw["march_source"],
                raw["march_baseline"],
                identical_candidate,
                raw["march_outcomes"],
                max_quote_age=max_quote_age,
                allowed_dates=march_dates,
            ),
        ),
        expect_failure(
            "candidate_model_key_removed",
            lambda: build_policy_pairs(
                raw["march_source"],
                raw["march_baseline"],
                removed_candidate,
                raw["march_outcomes"],
                max_quote_age=max_quote_age,
                allowed_dates=march_dates,
            ),
        ),
        expect_failure(
            "sealed_may_date_introduced",
            lambda: reject_may(may_source, "official_game_date", "mutated source"),
        ),
        expect_failure(
            "freshness_proxy_drifted",
            lambda: require_contract(drifted_contract),
        ),
    ]

    inputs = {
        label: {"path": str(getattr(args, label)), "sha256": sha256(getattr(args, label))}
        for label in raw
    }
    report = {
        "schema_version": "hits-contact-adapter-open-adjudication-v1",
        "built_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "protocol": {"path": str(protocol_path), "sha256": sha256(protocol_path)},
        "market": "hits",
        "status": (
            "OPEN_PERIOD_SUCCESSOR_RESEARCH_ONLY"
            if open_gate_passed
            else "CANDIDATE_REJECTED_OPEN_GATE_FAILED"
        ),
        "betting_authorized": False,
        "may_2026_read": False,
        "economic_contract": {
            "max_quote_age_minutes": max_quote_age,
            "max_quote_age_status": "UNRESOLVED_TIMESTAMP_PROXY_RESEARCH_ONLY",
            "min_expected_profit_per_unit": threshold,
            "threshold_status": "PREVIOUSLY_SELECTED_NOT_AUTHORIZED",
            "bootstrap_draws": bootstrap,
            "bootstrap_seed": seed,
            "authorization_capture_lower_bound": 0.10,
        },
        "blocks": {
            "march_april_fit": march,
            "june_replication": june,
        },
        "combined_open": combined,
        "gate": {
            "block_direction": block_checks,
            "combined_strength": combined_checks,
            "all_pass": open_gate_passed,
            "absolute_authorization_bar_cleared": (
                combined_candidate_capture.lower > 0.10
            ),
        },
        "mutations": mutations,
        "inputs": inputs,
        "interpretation": (
            "This is open-period research adjudication only. Historical quote age "
            "is not proven executability, the bound policy previously failed its "
            "authorization gate, May remains sealed, and no result here can authorize betting."
        ),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("HITS CONTACT ADAPTER ADJUDICATED")
    print(f"  March-Apr strict rows: {len(march_pairs):,}")
    print(f"  June strict rows: {len(june_pairs):,}")
    print(f"  mutations caught: {sum(item['caught'] for item in mutations)}/{len(mutations)}")
    print(f"  open gate: {'PASS' if open_gate_passed else 'FAIL'}")
    print(f"  betting authorized: FALSE")
    print(f"  wrote: {out}")
    print(f"  sha256: {sha256(out)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
