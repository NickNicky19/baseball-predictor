#!/usr/bin/env python3
"""Diagnose the locked March-April hits fit without changing model or policy.

The audit rebuilds the exact protocol-bound strict universe, verifies every
input hash, and reports calibration/capture by line, side, official PA, quote
age, and candidate probability movement.  These are factual associations, not
causal attributions and not permission to tune on the confirmation period.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.evaluation.hits_policy_fit import (  # noqa: E402
    ARM_COLUMNS,
    arm_policy_rows,
    build_policy_pairs,
)
from src.evaluation.market_residuals import reliability_bins  # noqa: E402


SCHEMA = "hits-policy-fit-residual-diagnostic-v1"


def sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root is not an object: {path}")
    return payload


def checked_input(report: dict, name: str) -> Path:
    try:
        raw = report["inputs"][name]
        path = Path(raw["path"])
        expected = str(raw["sha256"])
    except (KeyError, TypeError) as exc:
        raise ValueError(f"fit report lacks hashed input {name!r}") from exc
    if not path.is_file() or sha256(path) != expected:
        raise ValueError(f"fit input {name!r} is missing or hash-mismatched")
    return path


def metrics_for_group(
    pairs: pd.DataFrame,
    rows: pd.DataFrame,
    mask: np.ndarray,
    arm: str,
    threshold: float,
) -> dict[str, Any]:
    sub_pairs = pairs.loc[mask]
    sub_rows = rows.loc[mask]
    p = sub_pairs[ARM_COLUMNS[arm]].to_numpy(float)
    y = (sub_pairs.actual_value.to_numpy(float) > sub_pairs.line.to_numpy(float)).astype(float)
    selected = sub_rows.expected_profit.to_numpy(float) >= threshold
    chosen = sub_rows.loc[selected]
    denominator = float(chosen.market_edge.sum())
    return {
        "arm": arm,
        "rows": int(len(sub_pairs)),
        "official_dates": int(sub_pairs.official_game_date.nunique()),
        "brier": float(np.mean((p - y) ** 2)) if len(p) else float("nan"),
        "mean_probability_residual": float(np.mean(p - y)) if len(p) else float("nan"),
        "mean_abs_probability_residual": float(np.mean(np.abs(p - y))) if len(p) else float("nan"),
        "selected_rows": int(len(chosen)),
        "selected_dates": int(chosen.official_game_date.nunique()),
        "capture": (
            float(chosen.clv.sum() / denominator)
            if len(chosen) and denominator > 0.0
            else float("nan")
        ),
        "flat_stake_roi": (
            float(chosen.realised_profit.mean()) if len(chosen) else float("nan")
        ),
        "mean_expected_profit": (
            float(chosen.expected_profit.mean()) if len(chosen) else float("nan")
        ),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--fit-report",
        default="data/analysis/market_policy_hits_2026/policy_fit_v1/fit_report.json",
    )
    ap.add_argument(
        "--out-dir",
        default="data/analysis/market_policy_hits_2026/policy_fit_v1/residuals",
    )
    args = ap.parse_args(argv)

    report_path = Path(args.fit_report)
    report = load_json(report_path)
    if report.get("schema_version") != "hits-payout-policy-fit-report-v1":
        raise ValueError("unknown or non-fit report")
    if report.get("betting_authorized") is not False:
        raise ValueError("residual diagnostic refuses an unexpectedly authorized fit report")
    protocol_path = Path(report["protocol"]["path"])
    if sha256(protocol_path) != report["protocol"]["sha256"]:
        raise ValueError("policy-fit protocol hash mismatch")
    protocol = load_json(protocol_path)
    fit_dates = (
        list(protocol["internal_chronology"]["selector_dates"])
        + list(protocol["internal_chronology"]["confirmation_dates"])
    )
    if max(fit_dates) >= protocol["internal_chronology"]["may_holdout_start"]:
        raise ValueError("May leaked into the residual diagnostic")

    source = pd.read_csv(checked_input(report, "fit_source"))
    frozen = pd.read_csv(checked_input(report, "frozen_probabilities"))
    candidate = pd.read_csv(checked_input(report, "candidate_probabilities"))
    official = pd.read_csv(checked_input(report, "official_outcomes"))
    frozen_manifest_path = checked_input(report, "frozen_manifest")
    frozen_manifest = load_json(frozen_manifest_path)
    pa_path = Path(str(frozen_manifest.get("pa_distribution_path", "")))
    if not pa_path.is_file() or sha256(pa_path) != frozen_manifest.get(
        "pa_distribution_sha256"
    ):
        raise ValueError("active PA-distribution artifact is missing or hash-mismatched")
    pa_artifact = load_json(pa_path)
    by_slot = pa_artifact.get("by_lineup_slot")
    if not isinstance(by_slot, dict):
        raise ValueError("active PA artifact lacks by_lineup_slot")
    pa_mean_by_slot: dict[int, float] = {}
    for raw_slot, raw_distribution in by_slot.items():
        if not isinstance(raw_distribution, dict) or not raw_distribution:
            raise ValueError(f"invalid PA distribution for lineup slot {raw_slot}")
        weights = {int(pa): float(probability) for pa, probability in raw_distribution.items()}
        total = sum(weights.values())
        if total <= 0.0:
            raise ValueError(f"zero PA-distribution mass for lineup slot {raw_slot}")
        pa_mean_by_slot[int(raw_slot)] = sum(
            pa * probability for pa, probability in weights.items()
        ) / total
    max_age = float(report["historical_freshness_proxy"]["max_quote_age_minutes"])
    pairs, funnel = build_policy_pairs(
        source,
        frozen,
        candidate,
        official,
        max_quote_age=max_age,
        allowed_dates=fit_dates,
    )
    if len(pairs) != int(report["strict_rows"]):
        raise ValueError("residual universe differs from fitted strict universe")
    threshold = float(report["selected_min_expected_profit_per_unit"])
    arm_rows = {arm: arm_policy_rows(pairs, arm) for arm in ARM_COLUMNS}

    # Diagnostic groups.  Exact line, side, and PA values are factual.  Quote
    # age quartiles are derived from the complete fit universe without outcome
    # values and are never candidate policy cutoffs.
    age_band, age_edges = pd.qcut(
        pairs.entry_age_min,
        q=4,
        labels=["Q1", "Q2", "Q3", "Q4"],
        duplicates="drop",
        retbins=True,
    )
    pairs = pairs.copy()
    pairs["over_outcome"] = (
        pairs.actual_value.to_numpy(float) > pairs.line.to_numpy(float)
    ).astype(int)
    pairs["quote_age_quartile"] = age_band.astype(str)
    delta = pairs.p_candidate.to_numpy(float) - pairs.p_frozen.to_numpy(float)
    pairs["candidate_drift_direction"] = np.select(
        [delta < -1e-12, delta > 1e-12], ["lower", "higher"], default="unchanged"
    )
    slot = pd.to_numeric(pairs.official_lineup_slot, errors="raise").astype(int)
    if not slot.between(1, 9).all() or not set(slot.unique()) <= set(pa_mean_by_slot):
        raise ValueError("strict source has a lineup slot absent from the PA artifact")
    pairs["model_mean_pa"] = slot.map(pa_mean_by_slot).astype(float)
    pairs["pa_mean_error"] = pairs.model_mean_pa - pairs.official_pa.astype(float)
    pairs["pa_projection_direction"] = np.select(
        [pairs.pa_mean_error < 0.0, pairs.pa_mean_error > 0.0],
        ["model_below_actual", "model_above_actual"],
        default="equal",
    )
    for arm, rows in arm_rows.items():
        pairs[f"selection_side_{arm}"] = rows.selection_side.to_numpy()

    dimensions: dict[str, pd.Series] = {
        "line": pairs.line.map(lambda value: f"{float(value):g}"),
        "official_pa": pairs.official_pa.map(lambda value: f"{int(value)}"),
        "official_lineup_slot": slot.map(lambda value: f"{int(value)}"),
        "pa_projection_direction": pairs.pa_projection_direction,
        "quote_age_quartile": pairs.quote_age_quartile,
        "candidate_drift_direction": pairs.candidate_drift_direction,
    }
    records: list[dict[str, Any]] = []
    all_mask = np.ones(len(pairs), dtype=bool)
    for arm, rows in arm_rows.items():
        records.append(
            {
                "dimension": "ALL",
                "value": "ALL",
                **metrics_for_group(pairs, rows, all_mask, arm, threshold),
            }
        )
        arm_dimensions = dict(dimensions)
        arm_dimensions["selection_side"] = pairs[f"selection_side_{arm}"]
        for dimension, values in arm_dimensions.items():
            for value in sorted(values.unique().tolist()):
                mask = values.eq(value).to_numpy()
                records.append(
                    {
                        "dimension": dimension,
                        "value": str(value),
                        **metrics_for_group(pairs, rows, mask, arm, threshold),
                    }
                )
            grouped_rows = sum(
                record["rows"]
                for record in records
                if record["arm"] == arm and record["dimension"] == dimension
            )
            if grouped_rows != len(pairs):
                raise AssertionError(f"{arm}/{dimension} groups do not sum to universe")

    table = pd.DataFrame(records)
    reliability = pd.concat(
        [reliability_bins(pairs, "frozen"), reliability_bins(pairs, "candidate")],
        ignore_index=True,
    )
    overall = table[table.dimension.eq("ALL")].set_index("arm")
    brier_change = float(overall.loc["candidate", "brier"] - overall.loc["frozen", "brier"])
    capture_change = float(
        overall.loc["candidate", "capture"] - overall.loc["frozen", "capture"]
    )
    candidate_residual = (
        pairs.p_candidate.to_numpy(float) - pairs.over_outcome.to_numpy(float)
    )
    pa_error = pairs.pa_mean_error.to_numpy(float)
    pa_residual_correlation = float(np.corrcoef(pa_error, candidate_residual)[0, 1])

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    table_path = out_dir / "residual_groups.csv"
    reliability_path = out_dir / "reliability_by_line.csv"
    pairs_path = out_dir / "scored_pairs.csv"
    summary_path = out_dir / "residual_summary.json"
    table.to_csv(table_path, index=False)
    reliability.to_csv(reliability_path, index=False)
    pairs.to_csv(pairs_path, index=False)
    summary = {
        "schema_version": SCHEMA,
        "built_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fit_report": {"path": str(report_path), "sha256": sha256(report_path)},
        "protocol": {"path": str(protocol_path), "sha256": sha256(protocol_path)},
        "pa_distribution": {"path": str(pa_path), "sha256": sha256(pa_path)},
        "fit_dates": fit_dates,
        "may_opened": False,
        "strict_funnel": funnel,
        "strict_rows": int(len(pairs)),
        "historical_freshness_proxy_minutes": max_age,
        "selected_min_expected_profit_per_unit": threshold,
        "quote_age_quartile_edges_minutes": [float(value) for value in age_edges],
        "pa_diagnostic": {
            "model_mean_pa_source": "active fitted P(PA | official lineup slot)",
            "mean_signed_pa_error_model_minus_official": float(pa_error.mean()),
            "mean_absolute_pa_error": float(np.abs(pa_error).mean()),
            "candidate_probability_residual_correlation_with_pa_error": pa_residual_correlation,
            "interpretation_limit": (
                "Association only. Official PA is postgame information; it cannot "
                "enter a live prediction and does not establish a causal PA defect."
            ),
        },
        "global": {
            "frozen": overall.loc["frozen"].to_dict(),
            "candidate": overall.loc["candidate"].to_dict(),
            "candidate_minus_frozen_brier": brier_change,
            "candidate_minus_frozen_point_capture": capture_change,
        },
        "outputs": {
            "groups": {"path": str(table_path), "sha256": sha256(table_path)},
            "reliability": {
                "path": str(reliability_path),
                "sha256": sha256(reliability_path),
            },
            "pairs": {"path": str(pairs_path), "sha256": sha256(pairs_path)},
        },
        "verdict": (
            "DIAGNOSTIC_ONLY: associations locate where error/capture differs. "
            "They do not establish a causal mechanism, justify a model edit, "
            "open May, or authorize betting."
        ),
        "betting_authorized": False,
    }
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    print("HITS FIT-PERIOD RESIDUAL DIAGNOSTIC")
    print(f"  strict rows: {len(pairs):,}; fit dates: {len(fit_dates)}; May opened: NO")
    print(f"  candidate - frozen Brier: {brier_change:+.6f} (negative is better)")
    print(f"  candidate - frozen point capture: {capture_change:+.6f}")
    print("  causal claim: NONE; model/policy changes: NONE; betting authorized: NO")
    print(
        f"  wrote: {summary_path}\n         {table_path}\n         "
        f"{reliability_path}\n         {pairs_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
