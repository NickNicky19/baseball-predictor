#!/usr/bin/env python3
"""Evaluate the locked one-sided DraftKings HR-over open-date reconstruction."""
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
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.hr_over_evaluation import (  # noqa: E402
    MODEL_KEY,
    arm_rows,
    build_pairs,
    date_block_intervals,
    point_metrics,
    validate_date_contract,
    validate_research_report,
)


SCHEMA = "draftkings-hr-over-open-evaluation-report-v1"


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


def verify_provenance(
    payload: dict[str, Any], declared_dates: list[str], source_hash: str, manifest_hash: str, label: str
) -> None:
    if list(payload.get("dates", [])) != declared_dates:
        raise ValueError(f"{label} provenance does not carry the exact 56 dates")
    date_source = payload.get("date_source", {})
    if date_source.get("sha256") != manifest_hash:
        raise ValueError(f"{label} provenance market-manifest hash differs")
    if date_source.get("strict_market_artifact_sha256") != source_hash:
        raise ValueError(f"{label} provenance market-source hash differs")


def age_edges(diagnostic: pd.DataFrame) -> np.ndarray:
    values = pd.to_numeric(diagnostic.entry_age_min, errors="raise").to_numpy(float)
    edges = np.quantile(values, [0.0, 0.25, 0.5, 0.75, 1.0])
    if len(np.unique(edges)) != len(edges):
        raise ValueError("diagnostic quote-age quartiles are not distinct")
    edges[0], edges[-1] = -np.inf, np.inf
    return edges


def grouped_diagnostics(
    role_rows: pd.DataFrame, role: str, edges: np.ndarray
) -> pd.DataFrame:
    labels = ["Q1", "Q2", "Q3", "Q4"]
    records: list[dict[str, Any]] = []
    for arm in ("frozen", "candidate"):
        rows = arm_rows(role_rows, arm)
        rows["quote_age_quartile"] = pd.cut(
            rows.entry_age_min, bins=edges, labels=labels, include_lowest=True
        ).astype(str)
        dimensions = {
            "quote_age_quartile": rows.quote_age_quartile,
            "settlement_present": rows.settlement_present.astype(str),
            "same_price_after_horizon": rows.same_price_after_horizon.fillna("unknown").astype(str),
        }
        for dimension, values in dimensions.items():
            before = len(records)
            for value in sorted(values.unique()):
                group = rows.loc[values.eq(value)]
                selected = group[group.positive_ev]
                records.append({
                    "role": role,
                    "arm": arm,
                    "dimension": dimension,
                    "value": value,
                    "all_rows": int(len(group)),
                    "positive_ev_rows": int(len(selected)),
                    "brier": float(group.brier.mean()),
                    "log_loss": float(group.log_loss.mean()),
                    "positive_ev_mean_raw_probability_movement": (
                        float(selected.raw_probability_movement.mean()) if len(selected) else None
                    ),
                    "positive_ev_theoretical_flat_stake_roi": (
                        float(selected.theoretical_realised_profit.mean()) if len(selected) else None
                    ),
                })
            if sum(record["all_rows"] for record in records[before:]) != len(rows):
                raise AssertionError(f"{role}/{arm}/{dimension} groups do not sum")
    return pd.DataFrame(records)


def _fixture() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    dates = ["2026-03-25", "2026-03-26", "2026-06-01", "2026-06-02"]
    rows = []
    for index, date in enumerate(dates, start=1):
        start = pd.Timestamp(date, tz="UTC") + pd.Timedelta(hours=23)
        horizon = start - pd.Timedelta(hours=4)
        rows.append({
            "sportsbook": "draftkings", "vendor_market": "player home runs",
            "selection_side": "over", "market_date": date,
            "vendor_game_id": f"g{index}", "start_time": start,
            "player": f"p{index}", "line": 0.5, "horizon": horizon,
            "settlement_present": True, "over_observations": 2,
            "entry_quote_time": horizon - pd.Timedelta(minutes=10),
            "entry_decimal_odds": 4.0 + index / 10,
            "close_quote_time": start - pd.Timedelta(minutes=5),
            "close_decimal_odds": 3.9 + index / 10,
            "entry_age_min": 10.0, "non_over_rows": 0,
            "same_price_after_horizon": False,
            "official_game_date": date, "mlb_game_pk": 100 + index,
            "player_id": 200 + index, "category": "home_runs",
        })
    source = pd.DataFrame(rows)
    frozen = source[["mlb_game_pk", "player_id", "category", "line"]].copy()
    frozen["game_date"] = dates
    frozen["sim_p_over"] = [0.18, 0.22, 0.26, 0.30]
    candidate = frozen.copy()
    candidate["sim_p_over"] += [0.01, -0.01, 0.02, -0.02]
    official = source[["mlb_game_pk", "player_id", "category"]].copy()
    official["game_date"] = dates
    official["actual_value"] = [0, 1, 0, 1]
    return source, frozen, candidate, official


def self_test() -> int:
    print("SELF-TEST - ONE-SIDED HR-OVER EVALUATION")
    diagnostic = ["2026-03-25", "2026-03-26"]
    confirmation = ["2026-06-01", "2026-06-02"]
    source, frozen, candidate, official = _fixture()
    pairs, _ = build_pairs(source, frozen, candidate, official, diagnostic, confirmation)
    print("  [OK] exact one-sided market/model/official universe builds")

    try:
        validate_date_contract(
            list(source.official_game_date) + ["2026-05-01"],
            diagnostic,
            confirmation + ["2026-05-01"],
        )
        raise AssertionError("May mutation passed")
    except ValueError:
        print("  [OK] MUTATION: injecting May hard-fails")

    try:
        build_pairs(source, frozen, candidate.iloc[:-1], official, diagnostic, confirmation)
        raise AssertionError("missing candidate key passed")
    except ValueError:
        print("  [OK] MUTATION: removing one arm key hard-fails")

    inert = frozen.copy()
    try:
        build_pairs(source, frozen, inert, official, diagnostic, confirmation)
        raise AssertionError("inert candidate passed")
    except ValueError:
        print("  [OK] MUTATION: candidate == frozen hard-fails")

    try:
        build_pairs(pd.concat([source, source.iloc[[0]]]), frozen, candidate, official, diagnostic, confirmation)
        raise AssertionError("duplicate market key passed")
    except ValueError:
        print("  [OK] MUTATION: duplicated final market key hard-fails")

    contaminated = source.copy()
    contaminated["result"] = 1.0
    try:
        build_pairs(contaminated, frozen, candidate, official, diagnostic, confirmation)
        raise AssertionError("vendor result passed")
    except ValueError:
        print("  [OK] MUTATION: numeric vendor result hard-fails")

    base = arm_rows(pairs, "candidate")
    changed_official = official.copy()
    changed_official.loc[0, "actual_value"] = 1
    changed_pairs, _ = build_pairs(source, frozen, candidate, changed_official, diagnostic, confirmation)
    truth_mutation = arm_rows(changed_pairs, "candidate")
    if np.array_equal(base.brier.to_numpy(), truth_mutation.brier.to_numpy()):
        raise AssertionError("official mutation did not move predictive score")
    if np.array_equal(
        base.theoretical_realised_profit.to_numpy(),
        truth_mutation.theoretical_realised_profit.to_numpy(),
    ):
        raise AssertionError("official mutation did not move theoretical payout")
    print("  [OK] MUTATION: official HR actual moves score and theoretical payout")

    entry_mutation = pairs.copy()
    entry_mutation["entry_decimal_odds"] *= 1.1
    entry_rows = arm_rows(entry_mutation, "candidate")
    if not np.array_equal(base.brier.to_numpy(), entry_rows.brier.to_numpy()):
        raise AssertionError("entry odds changed predictive score")
    if np.array_equal(base.expected_profit.to_numpy(), entry_rows.expected_profit.to_numpy()):
        raise AssertionError("entry odds did not move expected value")
    if np.array_equal(
        base.theoretical_realised_profit.to_numpy(), entry_rows.theoretical_realised_profit.to_numpy()
    ):
        raise AssertionError("entry odds did not move theoretical payout")
    print("  [OK] MUTATION: entry odds move EV/payout but not predictive score")

    close_mutation = pairs.copy()
    close_mutation["close_decimal_odds"] *= 1.1
    close_rows = arm_rows(close_mutation, "candidate")
    if np.array_equal(
        base.raw_probability_movement.to_numpy(), close_rows.raw_probability_movement.to_numpy()
    ):
        raise AssertionError("close odds did not move raw movement")
    if not np.array_equal(base.brier.to_numpy(), close_rows.brier.to_numpy()):
        raise AssertionError("close odds changed predictive score")
    if not np.array_equal(
        base.theoretical_realised_profit.to_numpy(), close_rows.theoretical_realised_profit.to_numpy()
    ):
        raise AssertionError("close odds changed theoretical payout")
    print("  [OK] MUTATION: close odds move raw movement only")

    good = {
        "betting_authorized": False, "may_opened": False,
        "probability_label": "RAW_BREAK_EVEN_INCLUDES_UNKNOWN_MARGIN",
        "historical_executability_verified": False,
        "official_draftkings_settlement_rule_verified": False,
    }
    validate_research_report(good)
    print("  [OK] valid research-only report passes")
    for mutation in ("probability_label", "betting_authorized"):
        bad = dict(good)
        bad[mutation] = "FAIR_DEVIGGED" if mutation == "probability_label" else True
        try:
            validate_research_report(bad)
            raise AssertionError(f"{mutation} mutation passed")
        except ValueError:
            print(f"  [OK] MUTATION: {mutation} false claim hard-fails")
    print("  11/11")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--protocol",
        default="data/analysis/hr_over_contract_v1/evaluation_v1/protocol.json",
    )
    parser.add_argument(
        "--out-dir", default="data/analysis/hr_over_contract_v1/evaluation_v1"
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()

    protocol_path = ROOT / args.protocol
    protocol = load_json(protocol_path)
    if protocol.get("status") != "LOCKED_BEFORE_HR_OVER_PERFORMANCE_RESULTS":
        raise ValueError("HR evaluation protocol was not locked before results")
    diagnostic_dates = list(protocol["chronology"]["diagnostic_dates"])
    confirmation_dates = list(protocol["chronology"]["confirmation_dates"])
    declared_dates = diagnostic_dates + confirmation_dates
    validate_date_contract(declared_dates, diagnostic_dates, confirmation_dates)

    inputs = protocol["inputs"]
    paths = {name: verify_hash(item, name) for name, item in inputs.items()}
    source_hash = inputs["market_source"]["sha256"]
    market_manifest_hash = inputs["market_manifest"]["sha256"]
    market_manifest = load_json(paths["market_manifest"])
    if market_manifest.get("hashes", {}).get("artifact") != source_hash:
        raise ValueError("market manifest does not certify the source artifact")
    if list(market_manifest.get("official_date_universe", [])) != declared_dates:
        raise ValueError("market manifest date universe differs from the protocol")
    for label in ("frozen", "candidate"):
        verify_provenance(
            load_json(paths[f"{label}_provenance"]),
            declared_dates,
            source_hash,
            market_manifest_hash,
            label,
        )

    source = pd.read_csv(paths["market_source"])
    frozen = pd.read_csv(paths["frozen_probabilities"])
    candidate = pd.read_csv(paths["candidate_probabilities"])
    official = pd.read_csv(paths["official_outcomes"])
    pairs, funnel = build_pairs(
        source, frozen, candidate, official, diagnostic_dates, confirmation_dates
    )
    diagnostic_pairs = pairs[pairs.official_game_date.isin(diagnostic_dates)].copy()
    confirmation_pairs = pairs[pairs.official_game_date.isin(confirmation_dates)].copy()
    edges = age_edges(diagnostic_pairs)

    draws = int(protocol["inference"]["bootstrap_draws"])
    seed = int(protocol["inference"]["bootstrap_seed"])
    role_reports: dict[str, Any] = {}
    scored_parts: list[pd.DataFrame] = []
    grouped_parts: list[pd.DataFrame] = []
    for role, role_pairs, dates in (
        ("diagnostic", diagnostic_pairs, diagnostic_dates),
        ("confirmation", confirmation_pairs, confirmation_dates),
    ):
        frozen_rows = arm_rows(role_pairs, "frozen")
        candidate_rows = arm_rows(role_pairs, "candidate")
        role_reports[role] = {
            "frozen": point_metrics(frozen_rows),
            "candidate": point_metrics(candidate_rows),
            "date_block_intervals": date_block_intervals(
                frozen_rows, candidate_rows, dates, draws, seed
            ),
            "changed_probabilities": int(
                (~np.isclose(
                    frozen_rows.model_probability.to_numpy(float),
                    candidate_rows.model_probability.to_numpy(float),
                    rtol=0.0,
                    atol=1e-12,
                )).sum()
            ),
        }
        for frame in (frozen_rows, candidate_rows):
            canonical = frame[[
                *MODEL_KEY, "official_game_date", "entry_age_min",
                "settlement_present", "same_price_after_horizon",
                "entry_decimal_odds", "close_decimal_odds", "actual_value", "won",
                "arm", "model_probability", "brier", "log_loss",
                "entry_raw_break_even", "close_raw_break_even", "model_edge_raw",
                "expected_profit", "positive_ev", "raw_probability_movement",
                "theoretical_realised_profit",
            ]].copy()
            canonical.insert(0, "role", role)
            scored_parts.append(canonical)
        grouped_parts.append(grouped_diagnostics(role_pairs, role, edges))

    intervals = role_reports["confirmation"]["date_block_intervals"]
    gate_passed = (
        intervals["valid_draws"] == draws
        and intervals["candidate_minus_frozen_brier_95"][1] < 0.0
        and intervals["candidate_minus_frozen_log_loss_95"][1] < 0.0
        and intervals["candidate_positive_ev_raw_movement_95"][0] > 0.0
        and intervals["candidate_positive_ev_theoretical_roi_95"][0] > 0.0
    )

    out_dir = ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    scored_path = out_dir / "scored_rows.csv"
    grouped_path = out_dir / "grouped_diagnostics.csv"
    report_path = out_dir / "report.json"
    pd.concat(scored_parts, ignore_index=True).to_csv(scored_path, index=False)
    pd.concat(grouped_parts, ignore_index=True).to_csv(grouped_path, index=False)
    payload: dict[str, Any] = {
        "schema_version": SCHEMA,
        "built_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "protocol": {"path": str(protocol_path), "sha256": sha256(protocol_path)},
        "may_opened": False,
        "betting_authorized": False,
        "historical_executability_verified": False,
        "official_draftkings_settlement_rule_verified": False,
        "probability_label": "RAW_BREAK_EVEN_INCLUDES_UNKNOWN_MARGIN",
        "hits_capture_bar_reused": False,
        "funnel": funnel,
        "quote_age_quartile_edges_from_diagnostic_only": [
            None if not np.isfinite(value) else float(value) for value in edges
        ],
        "roles": role_reports,
        "research_advance_gate_passed": bool(gate_passed),
        "verdict": (
            "ADVANCE_TO_SEPARATE_HR_POLICY_RESEARCH_ONLY"
            if gate_passed else "PREDECLARED_HR_RESEARCH_ADVANCE_GATE_FAILED"
        ),
        "interpretation": (
            "This evaluates a one-sided, raw-margin-inclusive historical price proxy. "
            "It cannot prove fills, grading, profitability, or betting authorization."
        ),
        "outputs": {
            "scored_rows": {"path": str(scored_path), "sha256": sha256(scored_path)},
            "grouped_diagnostics": {"path": str(grouped_path), "sha256": sha256(grouped_path)},
        },
    }
    validate_research_report(payload)
    report_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    candidate_metrics = role_reports["confirmation"]["candidate"]
    print("DRAFTKINGS HR OVER 0.5 - LOCKED OPEN-DATE EVALUATION")
    print(
        f"  strict/model/scored: {funnel['strict_market_rows']:,} / "
        f"{funnel['model_available_rows']:,} / {funnel['officially_scored_rows']:,}"
    )
    print(
        f"  confirmation candidate Brier/log loss: "
        f"{candidate_metrics['brier']:.6f} / {candidate_metrics['log_loss']:.6f}"
    )
    print(
        "  candidate-minus-frozen Brier 95%: "
        f"{intervals['candidate_minus_frozen_brier_95'][0]:+.6f} to "
        f"{intervals['candidate_minus_frozen_brier_95'][1]:+.6f}"
    )
    print(
        "  candidate positive-EV raw movement 95%: "
        f"{intervals['candidate_positive_ev_raw_movement_95'][0]:+.6f} to "
        f"{intervals['candidate_positive_ev_raw_movement_95'][1]:+.6f}"
    )
    print(
        "  candidate positive-EV theoretical ROI 95%: "
        f"{intervals['candidate_positive_ev_theoretical_roi_95'][0]:+.6f} to "
        f"{intervals['candidate_positive_ev_theoretical_roi_95'][1]:+.6f}"
    )
    print(f"  verdict: {payload['verdict']}")
    print("  betting authorized: NO; May opened: NO")
    print(f"  wrote {report_path}\n        {scored_path}\n        {grouped_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
