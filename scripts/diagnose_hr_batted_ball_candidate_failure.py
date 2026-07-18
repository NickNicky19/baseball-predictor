#!/usr/bin/env python3
"""Read-only diagnosis of the rejected HR batted-ball candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
MODEL_KEY = ["mlb_game_pk", "player_id", "category", "line"]


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


def require_unique(frame: pd.DataFrame, key: list[str], label: str) -> None:
    if frame[key].isna().any().any() or frame.duplicated(key).any():
        raise ValueError(f"{label} key is null or duplicated")


def binary_auc(outcome: pd.Series, score: pd.Series) -> float | None:
    values = pd.to_numeric(score, errors="coerce")
    valid = values.notna()
    y = outcome.loc[valid].astype(bool).to_numpy()
    values = values.loc[valid]
    positives = int(y.sum())
    negatives = int((~y).sum())
    if positives == 0 or negatives == 0:
        return None
    ranks = values.rank(method="average").to_numpy(float)
    return float(
        (ranks[y].sum() - positives * (positives + 1) / 2)
        / (positives * negatives)
    )


def rank_correlation(left: pd.Series, right: pd.Series) -> float | None:
    frame = pd.DataFrame(
        {
            "left": pd.to_numeric(left, errors="coerce"),
            "right": pd.to_numeric(right, errors="coerce"),
        }
    ).dropna()
    if len(frame) < 2:
        return None
    x = frame.left.rank(method="average").to_numpy(float)
    y = frame.right.rank(method="average").to_numpy(float)
    if np.std(x) == 0 or np.std(y) == 0:
        return None
    return float(np.corrcoef(x, y)[0, 1])


def summary(rows: pd.DataFrame) -> dict[str, Any]:
    won = rows.won.astype(bool)
    return {
        "rows": int(len(rows)),
        "dates": int(rows.official_game_date.nunique()),
        "observed_hr_rate": float(won.mean()),
        "frozen_mean_probability": float(rows.frozen_probability.mean()),
        "candidate_mean_probability": float(rows.candidate_probability.mean()),
        "mean_probability_delta": float(rows.probability_delta.mean()),
        "median_probability_delta": float(rows.probability_delta.median()),
        "positive_probability_delta_rate": float(rows.probability_delta.gt(0).mean()),
        "frozen_calibration_bias": float(rows.frozen_probability.mean() - won.mean()),
        "candidate_calibration_bias": float(rows.candidate_probability.mean() - won.mean()),
        "mean_brier_delta": float(rows.brier_delta.mean()),
        "mean_log_loss_delta": float(rows.log_loss_delta.mean()),
        "frozen_auc": binary_auc(won, rows.frozen_probability),
        "candidate_auc": binary_auc(won, rows.candidate_probability),
        "probability_delta_auc": binary_auc(won, rows.probability_delta),
        "mean_delta_when_hr": (
            float(rows.loc[won, "probability_delta"].mean()) if won.any() else None
        ),
        "mean_delta_when_no_hr": (
            float(rows.loc[~won, "probability_delta"].mean()) if (~won).any() else None
        ),
        "probability_delta_vs_raw_movement_rank_correlation": rank_correlation(
            rows.probability_delta, rows.raw_probability_movement
        ),
    }


def diagnostic_edges(values: pd.Series, label: str) -> np.ndarray:
    numeric = pd.to_numeric(values, errors="raise").to_numpy(float)
    edges = np.quantile(numeric, [0.0, 0.25, 0.5, 0.75, 1.0])
    if len(np.unique(edges)) != 5:
        raise ValueError(f"diagnostic {label} quartiles are not distinct")
    edges[0], edges[-1] = -np.inf, np.inf
    return edges


def feature_rows(manifest: dict[str, Any]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for date in manifest["dates"]:
        item = manifest["feature_snapshots"][date]
        path = Path(item["bundle_path"])
        if not path.is_file() or sha256(path) != item["bundle_sha256"]:
            raise ValueError(f"candidate feature snapshot hash mismatch for {date}")
        bundles = json.loads(path.read_text(encoding="utf-8"))
        if len(bundles) != item["bundle_count"]:
            raise ValueError(f"candidate feature snapshot count mismatch for {date}")
        for bundle in bundles:
            statcast = bundle.get("statcast") or {}
            distribution = statcast.get("distribution") or {}
            rows.append(
                {
                    "mlb_game_pk": int(bundle["hitter"]["game"]["game_pk"]),
                    "player_id": int(bundle["hitter"]["player"]["mlb_id"]),
                    "feature_game_date": str(bundle["hitter"]["game"]["game_date"]),
                    "statcast_sample_pa": statcast.get("sample_pa"),
                    "barrel_rate": statcast.get("barrel_rate"),
                    "hard_hit_rate": statcast.get("hard_hit_rate"),
                    "distribution_barrel_rate": distribution.get("barrel_rate"),
                    "distribution_hard_hit_rate": distribution.get("hard_hit_rate"),
                }
            )
    result = pd.DataFrame(rows)
    require_unique(result, ["mlb_game_pk", "player_id"], "candidate feature")
    for column in (
        "statcast_sample_pa",
        "barrel_rate",
        "hard_hit_rate",
        "distribution_barrel_rate",
        "distribution_hard_hit_rate",
    ):
        result[column] = pd.to_numeric(result[column], errors="coerce")
    for column in ("statcast_sample_pa", "barrel_rate", "hard_hit_rate"):
        if result[column].isna().any():
            raise ValueError(f"candidate feature {column} is missing")
    return result


def paired_rows(scored: pd.DataFrame) -> pd.DataFrame:
    key = ["role", *MODEL_KEY]
    frozen = scored[scored.arm.eq("frozen")].copy()
    candidate = scored[scored.arm.eq("candidate")].copy()
    require_unique(frozen, key, "frozen scored")
    require_unique(candidate, key, "candidate scored")
    frozen = frozen.sort_values(key).reset_index(drop=True)
    candidate = candidate.sort_values(key).reset_index(drop=True)
    if not frozen[key].equals(candidate[key]):
        raise ValueError("scored arm keys differ")
    invariant = [
        "official_game_date",
        "actual_value",
        "won",
        "entry_decimal_odds",
        "close_decimal_odds",
        "raw_probability_movement",
        "official_grade_status",
        "official_gradeable",
    ]
    if not frozen[invariant].equals(candidate[invariant]):
        raise ValueError("scored arm truth/market fields differ")
    result = frozen[[*key, *invariant]].copy()
    result["frozen_probability"] = frozen.model_probability.to_numpy(float)
    result["candidate_probability"] = candidate.model_probability.to_numpy(float)
    result["probability_delta"] = (
        result.candidate_probability - result.frozen_probability
    )
    result["brier_delta"] = candidate.brier.to_numpy(float) - frozen.brier.to_numpy(float)
    result["log_loss_delta"] = (
        candidate.log_loss.to_numpy(float) - frozen.log_loss.to_numpy(float)
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--protocol",
        default=(
            "data/analysis/hr_over_contract_v1/batted_ball_candidate_v1/"
            "failure_diagnosis_protocol.json"
        ),
    )
    parser.add_argument(
        "--out-dir",
        default=(
            "data/analysis/hr_over_contract_v1/batted_ball_candidate_v1/"
            "failure_diagnosis_v1"
        ),
    )
    args = parser.parse_args()

    protocol_path = ROOT / args.protocol
    protocol = load_json(protocol_path)
    if protocol.get("status") != "LOCKED_BEFORE_SUBGROUP_FAILURE_DIAGNOSIS":
        raise ValueError("failure diagnosis was not locked before subgroup inspection")
    if protocol.get("betting_authorized") or protocol.get("may_opened"):
        raise ValueError("failure diagnosis opened May or authorized betting")
    inputs = {name: verify_hash(item, name) for name, item in protocol["inputs"].items()}
    economic_report = load_json(inputs["economic_gate_report"])
    if economic_report["decision"]["batted_ball_candidate_supported"] is not False:
        raise ValueError("failure diagnosis requires a rejected candidate")
    if economic_report.get("may_opened") is not False:
        raise ValueError("economic report opened May")

    scored = pd.read_csv(inputs["scored_gradeable_rows"])
    paired = paired_rows(scored)
    if len(paired) != 8827 or set(paired.role) != {"diagnostic", "confirmation"}:
        raise ValueError("failure diagnosis scoring universe drift")
    if paired.official_game_date.astype(str).str.startswith("2026-05").any():
        raise ValueError("May leaked into failure diagnosis")
    features = feature_rows(load_json(inputs["candidate_provenance"]))
    paired = paired.merge(
        features,
        on=["mlb_game_pk", "player_id"],
        how="left",
        validate="many_to_one",
    )
    if paired.statcast_sample_pa.isna().any():
        raise ValueError("a scored row lacks its certified candidate feature")
    if not paired.feature_game_date.eq(paired.official_game_date.astype(str)).all():
        raise ValueError("feature/scored date drift")

    diagnostic = paired[paired.role.eq("diagnostic")]
    delta_edges = diagnostic_edges(diagnostic.probability_delta, "probability-delta")
    sample_edges = diagnostic_edges(diagnostic.statcast_sample_pa, "sample-PA")
    labels = ["Q1", "Q2", "Q3", "Q4"]
    paired["probability_delta_quartile"] = pd.cut(
        paired.probability_delta, delta_edges, labels=labels, include_lowest=True
    ).astype(str)
    paired["sample_pa_quartile"] = pd.cut(
        paired.statcast_sample_pa, sample_edges, labels=labels, include_lowest=True
    ).astype(str)

    role_reports: dict[str, Any] = {}
    grouped_records: list[dict[str, Any]] = []
    for role in ("diagnostic", "confirmation"):
        period = paired[paired.role.eq(role)].copy()
        role_reports[role] = summary(period)
        for dimension in ("probability_delta_quartile", "sample_pa_quartile"):
            before = len(grouped_records)
            for value in labels:
                group = period[period[dimension].eq(value)]
                if group.empty:
                    raise ValueError(f"{role}/{dimension}/{value} is empty")
                grouped_records.append(
                    {
                        "role": role,
                        "dimension": dimension,
                        "value": value,
                        **summary(group),
                    }
                )
            if sum(record["rows"] for record in grouped_records[before:]) != len(period):
                raise ValueError(f"{role}/{dimension} groups do not partition the role")

    feature_associations: dict[str, Any] = {}
    for role in ("diagnostic", "confirmation"):
        period = paired[paired.role.eq(role)]
        feature_associations[role] = {
            feature: {
                "available_rows": int(period[feature].notna().sum()),
                "vs_probability_delta_rank_correlation": rank_correlation(
                    period[feature], period.probability_delta
                ),
                "vs_outcome_auc": binary_auc(period.won, period[feature]),
                "vs_brier_delta_rank_correlation": rank_correlation(
                    period[feature], period.brier_delta
                ),
            }
            for feature in (
                "statcast_sample_pa",
                "barrel_rate",
                "hard_hit_rate",
                "distribution_barrel_rate",
                "distribution_hard_hit_rate",
            )
        }

    out_dir = ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    rows_path = out_dir / "diagnostic_rows.csv"
    groups_path = out_dir / "grouped_diagnostics.csv"
    report_path = out_dir / "report.json"
    paired.to_csv(rows_path, index=False)
    pd.DataFrame(grouped_records).to_csv(groups_path, index=False)
    report = {
        "schema_version": "hr-batted-ball-candidate-failure-diagnosis-report-v1",
        "built_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "REJECTED_CANDIDATE_DIAGNOSIS_RESEARCH_ONLY",
        "betting_authorized": False,
        "may_opened": False,
        "candidate_remains_rejected": True,
        "protocol": {"path": str(protocol_path), "sha256": sha256(protocol_path)},
        "diagnostic_learned_edges": {
            "probability_delta_quartiles": [
                None if not np.isfinite(value) else float(value) for value in delta_edges
            ],
            "sample_pa_quartiles": [
                None if not np.isfinite(value) else float(value) for value in sample_edges
            ],
        },
        "roles": role_reports,
        "feature_associations": feature_associations,
        "outputs": {
            "diagnostic_rows": {"path": str(rows_path.resolve()), "sha256": sha256(rows_path)},
            "grouped_diagnostics": {
                "path": str(groups_path.resolve()),
                "sha256": sha256(groups_path),
            },
        },
        "interpretation_limit": (
            "Open-period subgroup evidence may identify one next experiment. It cannot rescue "
            "the rejected candidate, select a betting subgroup, open May, or authorize betting."
        ),
    }
    temporary = report_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(report_path)

    print("HR BATTED-BALL FAILURE DIAGNOSIS COMPLETE - RESEARCH ONLY")
    for role in ("diagnostic", "confirmation"):
        item = role_reports[role]
        print(
            f"  {role}: mean delta={item['mean_probability_delta']:+.6f}; "
            f"Brier delta={item['mean_brier_delta']:+.6f}; "
            f"AUC frozen/candidate={item['frozen_auc']:.6f}/{item['candidate_auc']:.6f}"
        )
    print("  candidate remains rejected: YES")
    print("  betting authorized: NO; May opened: NO")
    print(f"  report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
