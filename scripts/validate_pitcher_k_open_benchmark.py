#!/usr/bin/env python3
"""Independently certify the published research-only pitcher-K benchmark."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.pitcher_k_open_benchmark import (  # noqa: E402
    MARKET_KEY,
    PITCHER_GAME_KEY,
    _scores,
    add_comparators,
    build_model_market_universe,
    build_official_pitcher_outcomes,
    calibration_rows,
    crosscheck_reconstruction_outcomes,
    fit_empirical_baseline,
    load_protocol,
    sha256,
)


REPORT_SCHEMA = "pitcher-k-open-benchmark-report-v1"
REPORT_STATUS = "OPEN_2026_PRODUCTION_BENCHMARK_COMPLETE"
COMPARATORS = ["sim_p_over", "empirical_p_over", "entry_reference_p_over"]


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _bound(root: Path, relative: object) -> Path:
    candidate = (root / str(relative)).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("published artifact path escapes evidence root") from exc
    return candidate


def _artifact(root: Path, record: dict[str, Any]) -> Path:
    path = _bound(root, record.get("path"))
    if not path.is_file() or sha256(path) != record.get("sha256"):
        raise ValueError(f"published artifact hash mismatch: {record.get('path')}")
    return path


def _state_counts(frame: pd.DataFrame) -> dict[str, int]:
    return {
        str(key): int(value)
        for key, value in frame["terminal_state"].value_counts().sort_index().items()
    }


def _expected_metrics(rows: pd.DataFrame, clip: float) -> pd.DataFrame:
    started = rows[rows.terminal_state.eq("started")].copy()
    result: list[dict[str, Any]] = []
    scopes = [("all", started)]
    scopes.extend((f"line:{line}", group) for line, group in started.groupby("line"))
    scopes.extend(
        (f"month:{month}", group)
        for month, group in started.groupby(started.game_date.str[:7])
    )
    for scope, group in scopes:
        for comparator in COMPARATORS:
            result.append({"scope": scope, "comparator": comparator, **_scores(group, comparator, clip)})
    return pd.DataFrame(result).sort_values(["scope", "comparator"]).reset_index(drop=True)


def _expected_calibration(rows: pd.DataFrame) -> pd.DataFrame:
    started = rows[rows.terminal_state.eq("started")].copy()
    values: list[dict[str, Any]] = []
    for comparator in COMPARATORS:
        values.extend(calibration_rows(started, comparator))
    return pd.DataFrame(values).sort_values(["comparator", "bin"]).reset_index(drop=True)


def _assert_frames_equal(expected: pd.DataFrame, actual: pd.DataFrame, *, name: str) -> None:
    expected = expected.sort_values(list(expected.columns)).reset_index(drop=True)
    actual = actual.loc[:, expected.columns].sort_values(list(expected.columns)).reset_index(drop=True)
    if list(actual.columns) != list(expected.columns) or len(actual) != len(expected):
        raise ValueError(f"published {name} schema or row count differs from recomputation")
    for column in expected.columns:
        if pd.api.types.is_numeric_dtype(expected[column]):
            if not np.allclose(
                pd.to_numeric(expected[column], errors="raise"),
                pd.to_numeric(actual[column], errors="raise"),
                rtol=0.0,
                atol=1e-12,
                equal_nan=True,
            ):
                raise ValueError(f"published {name} differs in {column}")
        elif not expected[column].astype(str).equals(actual[column].astype(str)):
            raise ValueError(f"published {name} differs in {column}")


def validate(report_path: Path, *, protocol_path: Path, evidence_root: Path) -> dict[str, Any]:
    report = _json(report_path)
    if report.get("schema_version") != REPORT_SCHEMA or report.get("status") != REPORT_STATUS:
        raise ValueError("published pitcher-K benchmark report schema/status changed")
    if any(report.get(key) is not expected for key, expected in {
        "betting_authorized": False,
        "production_unchanged": True,
        "may_2026_opened": False,
        "economic_evidence_eligible": False,
    }.items()):
        raise ValueError("published pitcher-K benchmark weakened protected scope")

    protocol_record = report.get("protocol") or {}
    if protocol_record != {
        "path": protocol_path.resolve().relative_to(ROOT.resolve()).as_posix(),
        "sha256": sha256(protocol_path),
        "status": "LOCKED_AFTER_OUTCOME_BLIND_IDENTITY_BEFORE_FULL_OPEN_BENCHMARK",
    }:
        raise ValueError("published benchmark protocol binding changed")
    protocol = load_protocol(protocol_path, code_root=ROOT, evidence_root=evidence_root)
    for key in ("source_bindings", "locked_denominators", "official_outcome_contract", "comparators", "metrics_contract", "protected_invariants"):
        protocol_key = "metrics" if key == "metrics_contract" else "inputs" if key == "source_bindings" else key
        if report.get(key) != protocol.get(protocol_key):
            raise ValueError(f"published benchmark {key} changed from the locked protocol")

    implementation = report.get("implementation") or {}
    bindings = {
        "module_path": ROOT / "src/evaluation/pitcher_k_open_benchmark.py",
        "builder_path": ROOT / "scripts/build_pitcher_k_open_benchmark.py",
    }
    for path_key, local_path in bindings.items():
        hash_key = path_key.replace("_path", "_sha256")
        if implementation.get(path_key) != local_path.resolve().relative_to(ROOT.resolve()).as_posix() or sha256(local_path) != implementation.get(hash_key):
            raise ValueError(f"published benchmark implementation binding changed: {path_key}")

    artifacts = report.get("artifacts") or {}
    outcome_path = _artifact(evidence_root, artifacts.get("official_outcomes") or {})
    rows_path = _artifact(evidence_root, artifacts.get("benchmark_rows") or {})
    metric_path = _artifact(evidence_root, artifacts.get("metrics") or {})
    calibration_path = _artifact(evidence_root, artifacts.get("calibration") or {})
    outcomes = pd.read_csv(outcome_path)
    rows = pd.read_csv(rows_path)
    metrics = pd.read_csv(metric_path)
    calibration = pd.read_csv(calibration_path)
    for frame, record, name in (
        (outcomes, artifacts["official_outcomes"], "official outcomes"),
        (rows, artifacts["benchmark_rows"], "benchmark rows"),
        (metrics, artifacts["metrics"], "metrics"),
        (calibration, artifacts["calibration"], "calibration"),
    ):
        if len(frame) != int(record.get("rows", -1)):
            raise ValueError(f"published {name} row count changed")
    if outcomes[PITCHER_GAME_KEY].isna().any().any() or outcomes.duplicated(PITCHER_GAME_KEY).any():
        raise ValueError("published official outcome identity is incomplete")
    if rows[MARKET_KEY].isna().any().any() or rows.duplicated(MARKET_KEY).any():
        raise ValueError("published benchmark market identity is incomplete")
    if set(outcomes.terminal_state) - {"started", "not_started", "player_missing_official", "pitching_stat_missing", "malformed"}:
        raise ValueError("published official outcomes contain an unknown terminal state")
    if set(rows.terminal_state) - set(outcomes.terminal_state):
        raise ValueError("published benchmark rows contain an untracked terminal state")
    if rows.game_date.astype(str).str.startswith("2026-05").any() or outcomes.game_date.astype(str).str.startswith("2026-05").any():
        raise ValueError("published benchmark opened May")
    if {"result", "won", "payout", "roi"}.intersection(rows.columns):
        raise ValueError("forbidden vendor/economic field entered published benchmark")

    recomputed_market = build_model_market_universe(protocol, evidence_root=evidence_root)
    recomputed_outcomes = build_official_pitcher_outcomes(protocol, recomputed_market, evidence_root=evidence_root)
    crosscheck_reconstruction_outcomes(protocol, recomputed_outcomes, evidence_root=evidence_root)
    recomputed_rows = add_comparators(recomputed_market, recomputed_outcomes, fit_empirical_baseline(protocol, evidence_root=evidence_root))
    _assert_frames_equal(recomputed_outcomes, outcomes, name="official outcomes")
    _assert_frames_equal(recomputed_rows, rows, name="benchmark rows")
    _assert_frames_equal(_expected_metrics(recomputed_rows, float(protocol["metrics"]["log_loss_probability_clip"])), metrics, name="metrics")
    _assert_frames_equal(_expected_calibration(recomputed_rows), calibration, name="calibration")

    primary = rows[rows.terminal_state.eq("started")]
    expected_summary = {
        "official_pitcher_games": int(len(outcomes)),
        "official_outcome_terminal_states": _state_counts(outcomes),
        "market_rows": int(len(rows)),
        "primary_scoring_rows": int(len(primary)),
        "primary_scoring_pitcher_games": int(primary[PITCHER_GAME_KEY].drop_duplicates().shape[0]),
        "primary_scoring_rows_by_line": {str(float(line)): int(count) for line, count in primary.groupby("line").size().items()},
        "mean_entry_to_close_reference_probability_movement": float(rows.reference_probability_movement.mean()),
        "median_entry_to_close_reference_probability_movement": float(rows.reference_probability_movement.median()),
    }
    published_summary = report.get("summary") or {}
    for key, value in expected_summary.items():
        published = published_summary.get(key)
        if isinstance(value, float):
            # CSV text serialization can change the final IEEE-754 digit while
            # preserving the same computed quantity.  Accept only that lossless
            # representation-level difference, not a material discrepancy.
            if not isinstance(published, (float, int)) or not np.isclose(
                float(published), value, rtol=0.0, atol=1e-15
            ):
                raise ValueError(f"published benchmark summary differs in {key}")
        elif published != value:
            raise ValueError(f"published benchmark summary differs in {key}")
    return {"report_sha256": sha256(report_path), **expected_summary}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, default=ROOT / "config/pitcher_k_open_benchmark_protocol.json")
    parser.add_argument("--evidence-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(validate(args.report, protocol_path=args.protocol, evidence_root=args.evidence_root), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
