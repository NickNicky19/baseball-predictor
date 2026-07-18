#!/usr/bin/env python3
"""Apply the locked research-only HR batted-ball candidate economic gate."""

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
    require_unique,
    validate_date_contract,
)


SCHEMA = "hr-batted-ball-candidate-economic-gate-report-v1"


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


def _bool_column(series: pd.Series, label: str) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series.astype(bool)
    mapped = series.astype(str).str.strip().str.lower().map({"true": True, "false": False})
    if mapped.isna().any():
        raise ValueError(f"{label} contains a non-boolean value")
    return mapped.astype(bool)


def apply_official_grading(
    pairs: pd.DataFrame,
    bridge: pd.DataFrame,
    expected_certified: int,
    expected_gradeable: int,
    expected_void: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    required = [
        *MODEL_KEY,
        "official_game_date",
        "actual_value",
        "official_grade_status",
        "official_gradeable",
        "official_won",
    ]
    missing = [column for column in required if column not in bridge.columns]
    if missing:
        raise ValueError(f"official settlement bridge missing {missing}")
    bridge = bridge[required].copy()
    require_unique(bridge, MODEL_KEY, "official settlement bridge")
    require_unique(pairs, MODEL_KEY, "HR candidate pairs")
    if set(map(tuple, pairs[MODEL_KEY].to_numpy())) != set(
        map(tuple, bridge[MODEL_KEY].to_numpy())
    ):
        raise ValueError("settlement bridge keys differ from the certified model universe")

    bridge["official_gradeable"] = _bool_column(
        bridge["official_gradeable"], "official_gradeable"
    )
    bridge["official_won"] = _bool_column(bridge["official_won"], "official_won")
    bridge["actual_value"] = pd.to_numeric(bridge.actual_value, errors="coerce")
    if bridge.actual_value.isna().any() or (bridge.actual_value < 0).any():
        raise ValueError("settlement bridge actual is missing or negative")
    grade_status = bridge.official_grade_status.astype(str)
    gradeable = bridge.official_gradeable
    if grade_status[gradeable].str.startswith("VOID").any():
        raise ValueError("a void status was marked gradeable")
    if (~grade_status[~gradeable].str.startswith("VOID")).any():
        raise ValueError("a non-void status was excluded from grading")
    if not bridge.loc[gradeable, "official_won"].eq(
        bridge.loc[gradeable, "actual_value"].ge(1.0)
    ).all():
        raise ValueError("official grade outcome disagrees with official HR truth")

    merged = pairs.merge(
        bridge.rename(
            columns={
                "official_game_date": "bridge_game_date",
                "actual_value": "bridge_actual_value",
            }
        ),
        on=MODEL_KEY,
        how="inner",
        validate="one_to_one",
    )
    if not merged.bridge_game_date.astype(str).eq(merged.official_game_date.astype(str)).all():
        raise ValueError("settlement bridge date drift")
    if not np.array_equal(
        merged.bridge_actual_value.to_numpy(float), merged.actual_value.to_numpy(float)
    ):
        raise ValueError("settlement bridge official actual drift")
    if len(merged) != expected_certified:
        raise ValueError(f"expected {expected_certified} certified rows, got {len(merged)}")
    graded = merged[merged.official_gradeable].copy()
    void = merged[~merged.official_gradeable].copy()
    if len(graded) != expected_gradeable or len(void) != expected_void:
        raise ValueError(
            "official grading denominator drift: "
            f"gradeable={len(graded)} void={len(void)}"
        )
    if not graded.won.eq(graded.official_won).all():
        raise ValueError("model outcome grading disagrees with official grading")
    return graded.sort_values(MODEL_KEY).reset_index(drop=True), void.sort_values(
        MODEL_KEY
    ).reset_index(drop=True)


def gate_decision(
    role_reports: dict[str, Any],
    draws: int,
    exact_coverage: bool,
    feature_certificate_valid: bool,
) -> tuple[dict[str, bool], bool]:
    if set(role_reports) != {"diagnostic", "confirmation"}:
        raise ValueError("both open blocks are required by the locked gate")
    diagnostic = role_reports["diagnostic"]
    confirmation = role_reports["confirmation"]
    confirmation_interval = confirmation["date_block_intervals"]

    parts = {
        "diagnostic_brier_improved": (
            diagnostic["candidate"]["brier"] < diagnostic["frozen"]["brier"]
        ),
        "diagnostic_log_loss_improved": (
            diagnostic["candidate"]["log_loss"] < diagnostic["frozen"]["log_loss"]
        ),
        "confirmation_brier_improved": (
            confirmation["candidate"]["brier"] < confirmation["frozen"]["brier"]
        ),
        "confirmation_log_loss_improved": (
            confirmation["candidate"]["log_loss"]
            < confirmation["frozen"]["log_loss"]
        ),
        "confirmation_brier_upper_below_zero": (
            confirmation_interval["candidate_minus_frozen_brier_95"][1] < 0.0
        ),
        "confirmation_log_loss_upper_below_zero": (
            confirmation_interval["candidate_minus_frozen_log_loss_95"][1] < 0.0
        ),
        "diagnostic_candidate_movement_nonnegative": (
            diagnostic["candidate"]["positive_ev_mean_raw_probability_movement"]
            is not None
            and diagnostic["candidate"]["positive_ev_mean_raw_probability_movement"]
            >= 0.0
        ),
        "confirmation_candidate_movement_nonnegative": (
            confirmation["candidate"]["positive_ev_mean_raw_probability_movement"]
            is not None
            and confirmation["candidate"]["positive_ev_mean_raw_probability_movement"]
            >= 0.0
        ),
        "diagnostic_all_bootstrap_draws_valid": (
            diagnostic["date_block_intervals"]["valid_draws"] == draws
        ),
        "confirmation_all_bootstrap_draws_valid": (
            confirmation_interval["valid_draws"] == draws
        ),
        "exact_coverage_preserved": exact_coverage,
        "full_feature_certificate_valid": feature_certificate_valid,
    }
    result = {key: bool(value) for key, value in parts.items()}
    return result, all(result.values())


def validate_report(payload: dict[str, Any]) -> None:
    if payload.get("betting_authorized") is not False:
        raise ValueError("HR candidate report attempted to authorize betting")
    if payload.get("may_opened") is not False:
        raise ValueError("HR candidate report opened May")
    if payload.get("historical_executability_verified") is not False:
        raise ValueError("historical HR prices were mislabeled executable")
    if payload.get("probability_label") != "RAW_BREAK_EVEN_INCLUDES_UNKNOWN_MARGIN":
        raise ValueError("raw one-sided break-even was mislabeled fair/de-vigged")


def _fixture() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    dates = ["2026-03-25", "2026-03-26", "2026-06-01", "2026-06-02"]
    market_rows: list[dict[str, Any]] = []
    for index, date in enumerate(dates, start=1):
        start = pd.Timestamp(date, tz="UTC") + pd.Timedelta(hours=23)
        horizon = start - pd.Timedelta(hours=4)
        market_rows.append(
            {
                "sportsbook": "draftkings",
                "vendor_market": "player home runs",
                "selection_side": "over",
                "market_date": date,
                "vendor_game_id": f"g{index}",
                "start_time": start,
                "player": f"p{index}",
                "line": 0.5,
                "horizon": horizon,
                "settlement_present": True,
                "over_observations": 2,
                "entry_quote_time": horizon - pd.Timedelta(minutes=10),
                "entry_decimal_odds": 4.0,
                "close_quote_time": start - pd.Timedelta(minutes=5),
                "close_decimal_odds": 3.8,
                "entry_age_min": 10.0,
                "non_over_rows": 0,
                "same_price_after_horizon": False,
                "official_game_date": date,
                "mlb_game_pk": 100 + index,
                "player_id": 200 + index,
                "category": "home_runs",
            }
        )
    source = pd.DataFrame(market_rows)
    frozen = source[[*MODEL_KEY]].copy()
    frozen["game_date"] = dates
    frozen["sim_p_over"] = [0.18, 0.22, 0.26, 0.30]
    candidate = frozen.copy()
    candidate["sim_p_over"] = [0.17, 0.24, 0.25, 0.32]
    official = source[["mlb_game_pk", "player_id", "category"]].copy()
    official["game_date"] = dates
    official["actual_value"] = [0, 1, 0, 1]
    bridge = source[[*MODEL_KEY, "official_game_date"]].copy()
    bridge["actual_value"] = official.actual_value
    bridge["official_grade_status"] = [
        "VOID_EARLY_EXIT",
        "GRADED_WIN",
        "GRADED_LOSS",
        "GRADED_WIN",
    ]
    bridge["official_gradeable"] = [False, True, True, True]
    bridge["official_won"] = [False, True, False, True]
    return source, frozen, candidate, official, bridge


def self_test() -> int:
    print("SELF-TEST - HR BATTED-BALL ECONOMIC GATE")
    diagnostic_dates = ["2026-03-25", "2026-03-26"]
    confirmation_dates = ["2026-06-01", "2026-06-02"]
    source, frozen, candidate, official, bridge = _fixture()
    pairs, _ = build_pairs(
        source, frozen, candidate, official, diagnostic_dates, confirmation_dates
    )
    graded, void = apply_official_grading(pairs, bridge, 4, 3, 1)
    assert len(graded) == 3 and len(void) == 1
    print("  [OK] exact official grading denominator builds")

    try:
        validate_date_contract(
            diagnostic_dates + confirmation_dates + ["2026-05-01"],
            diagnostic_dates,
            confirmation_dates + ["2026-05-01"],
        )
        raise AssertionError("May mutation passed")
    except ValueError:
        print("  [OK] MUTATION: injecting May hard-fails")

    for label, mutated in (
        ("arm key", candidate.iloc[:-1]),
        ("inert candidate", frozen.copy()),
    ):
        try:
            build_pairs(source, frozen, mutated, official, diagnostic_dates, confirmation_dates)
            raise AssertionError(f"{label} mutation passed")
        except ValueError:
            print(f"  [OK] MUTATION: {label} hard-fails")

    try:
        apply_official_grading(pairs, bridge.iloc[:-1], 4, 3, 1)
        raise AssertionError("missing settlement key passed")
    except ValueError:
        print("  [OK] MUTATION: missing settlement key hard-fails")

    bad_void = bridge.copy()
    bad_void.loc[0, "official_gradeable"] = True
    try:
        apply_official_grading(pairs, bad_void, 4, 4, 0)
        raise AssertionError("void-as-loss mutation passed")
    except ValueError:
        print("  [OK] MUTATION: void marked gradeable hard-fails")

    base = arm_rows(graded, "candidate")
    changed_official = official.copy()
    changed_official.loc[1, "actual_value"] = 0
    changed_bridge = bridge.copy()
    changed_bridge.loc[1, ["actual_value", "official_won", "official_grade_status"]] = [
        0,
        False,
        "GRADED_LOSS",
    ]
    changed_pairs, _ = build_pairs(
        source, frozen, candidate, changed_official, diagnostic_dates, confirmation_dates
    )
    changed_graded, _ = apply_official_grading(changed_pairs, changed_bridge, 4, 3, 1)
    if np.array_equal(base.brier.to_numpy(), arm_rows(changed_graded, "candidate").brier.to_numpy()):
        raise AssertionError("official mutation did not move score")
    print("  [OK] MUTATION: official HR actual moves predictive score")

    entry_mutation = graded.copy()
    entry_mutation["entry_decimal_odds"] *= 1.1
    entry_rows = arm_rows(entry_mutation, "candidate")
    assert np.array_equal(base.brier.to_numpy(), entry_rows.brier.to_numpy())
    assert not np.array_equal(base.expected_profit.to_numpy(), entry_rows.expected_profit.to_numpy())
    print("  [OK] MUTATION: entry odds move EV but not predictive score")

    close_mutation = graded.copy()
    close_mutation["close_decimal_odds"] *= 1.1
    close_rows = arm_rows(close_mutation, "candidate")
    assert np.array_equal(base.brier.to_numpy(), close_rows.brier.to_numpy())
    assert not np.array_equal(
        base.raw_probability_movement.to_numpy(), close_rows.raw_probability_movement.to_numpy()
    )
    print("  [OK] MUTATION: close odds move raw movement only")

    try:
        gate_decision({"diagnostic": {}}, 20, True, True)
        raise AssertionError("one-block gate passed")
    except ValueError:
        print("  [OK] MUTATION: omitting an open block hard-fails")

    good_report = {
        "betting_authorized": False,
        "may_opened": False,
        "historical_executability_verified": False,
        "probability_label": "RAW_BREAK_EVEN_INCLUDES_UNKNOWN_MARGIN",
    }
    validate_report(good_report)
    for field in ("betting_authorized", "may_opened"):
        bad = dict(good_report)
        bad[field] = True
        try:
            validate_report(bad)
            raise AssertionError(f"{field} mutation passed")
        except ValueError:
            print(f"  [OK] MUTATION: {field}=true hard-fails")
    print("  12/12")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--protocol",
        default=(
            "data/analysis/hr_over_contract_v1/batted_ball_candidate_v1/"
            "economic_gate_protocol.json"
        ),
    )
    parser.add_argument(
        "--out-dir",
        default=(
            "data/analysis/hr_over_contract_v1/batted_ball_candidate_v1/"
            "economic_gate_v1"
        ),
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()

    protocol_path = ROOT / args.protocol
    protocol = load_json(protocol_path)
    if protocol.get("status") != "LOCKED_BEFORE_BATTED_BALL_CANDIDATE_PERFORMANCE_RESULTS":
        raise ValueError("HR candidate gate was not locked before performance results")
    if protocol.get("betting_authorized") or protocol.get("may_opened"):
        raise ValueError("HR candidate protocol opened May or authorized betting")

    diagnostic_dates = list(protocol["chronology"]["diagnostic_dates"])
    confirmation_dates = list(protocol["chronology"]["confirmation_dates"])
    declared_dates = diagnostic_dates + confirmation_dates
    validate_date_contract(declared_dates, diagnostic_dates, confirmation_dates)

    paths = {
        name: verify_hash(item, name) for name, item in protocol["inputs"].items()
    }
    feature_certificate = load_json(paths["full_artifact_certificate"])
    feature_certificate_valid = (
        feature_certificate.get("status") == "FULL_ARTIFACTS_VALID_RESEARCH_ONLY"
        and feature_certificate.get("verdict")
        == "PASS_FULL_ARTIFACT_VALIDATION_ECONOMIC_GATE_REQUIRED"
        and feature_certificate.get("may_opened") is False
        and feature_certificate.get("betting_authorized") is False
        and feature_certificate.get("dates") == declared_dates
    )
    if not feature_certificate_valid:
        raise ValueError("full feature certificate is not valid for this gate")

    source = pd.read_csv(paths["market_source"])
    frozen = pd.read_csv(paths["frozen_probabilities"])
    candidate = pd.read_csv(paths["candidate_probabilities"])
    official = pd.read_csv(paths["official_outcomes"])
    bridge = pd.read_csv(paths["official_settlement_bridge"])
    pairs, raw_funnel = build_pairs(
        source,
        frozen,
        candidate,
        official,
        diagnostic_dates,
        confirmation_dates,
    )
    scoring = protocol["scoring"]
    graded, void = apply_official_grading(
        pairs,
        bridge,
        int(scoring["certified_market_keys_required"]),
        int(scoring["official_gradeable_keys_required"]),
        int(scoring["void_or_unresolved_keys_required"]),
    )
    exact_coverage = (
        raw_funnel["model_available_rows"] == scoring["certified_market_keys_required"]
        and len(graded) == scoring["official_gradeable_keys_required"]
        and len(void) == scoring["void_or_unresolved_keys_required"]
    )

    draws = int(protocol["inference"]["bootstrap_draws"])
    seed = int(protocol["inference"]["bootstrap_seed"])
    role_reports: dict[str, Any] = {}
    scored_parts: list[pd.DataFrame] = []
    for role, dates in (
        ("diagnostic", diagnostic_dates),
        ("confirmation", confirmation_dates),
    ):
        role_pairs = graded[graded.official_game_date.isin(dates)].copy()
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
            canonical = frame[
                [
                    *MODEL_KEY,
                    "official_game_date",
                    "official_grade_status",
                    "official_gradeable",
                    "entry_age_min",
                    "settlement_present",
                    "same_price_after_horizon",
                    "entry_decimal_odds",
                    "close_decimal_odds",
                    "actual_value",
                    "won",
                    "arm",
                    "model_probability",
                    "brier",
                    "log_loss",
                    "entry_raw_break_even",
                    "close_raw_break_even",
                    "model_edge_raw",
                    "expected_profit",
                    "positive_ev",
                    "raw_probability_movement",
                    "theoretical_realised_profit",
                ]
            ].copy()
            canonical.insert(0, "role", role)
            scored_parts.append(canonical)

    success_parts, candidate_supported = gate_decision(
        role_reports, draws, exact_coverage, feature_certificate_valid
    )
    out_dir = ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    scored_path = out_dir / "scored_gradeable_rows.csv"
    void_path = out_dir / "void_or_unresolved_rows.csv"
    report_path = out_dir / "report.json"
    pd.concat(scored_parts, ignore_index=True).to_csv(scored_path, index=False)
    void[
        [
            *MODEL_KEY,
            "official_game_date",
            "official_grade_status",
            "official_gradeable",
            "bridge_actual_value",
        ]
    ].to_csv(void_path, index=False)

    payload: dict[str, Any] = {
        "schema_version": SCHEMA,
        "built_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "RESEARCH_ONLY",
        "betting_authorized": False,
        "may_opened": False,
        "historical_executability_verified": False,
        "probability_label": "RAW_BREAK_EVEN_INCLUDES_UNKNOWN_MARGIN",
        "protocol": {"path": str(protocol_path), "sha256": sha256(protocol_path)},
        "evaluator": {
            "path": str(Path(__file__).resolve()),
            "sha256": sha256(Path(__file__).resolve()),
        },
        "funnel": {
            **raw_funnel,
            "official_gradeable_rows": int(len(graded)),
            "void_or_unresolved_rows": int(len(void)),
        },
        "roles": role_reports,
        "decision": {
            "success_parts": success_parts,
            "batted_ball_candidate_supported": bool(candidate_supported),
            "next_action": (
                "CREATE_READY_TO_OPEN_MAY_PACKAGE_AND_STOP_FOR_APPROVAL"
                if candidate_supported
                else "REJECT_CANDIDATE_PRESERVE_FROZEN_AND_DIAGNOSE_ONE_NEXT_EXPERIMENT"
            ),
            "promotion_permitted": False,
            "betting_authorized": False,
        },
        "outputs": {
            "scored_gradeable_rows": {
                "path": str(scored_path.resolve()),
                "sha256": sha256(scored_path),
            },
            "void_or_unresolved_rows": {
                "path": str(void_path.resolve()),
                "sha256": sha256(void_path),
            },
        },
        "interpretation": (
            "This is an open-period candidate adjudication using official gradeable states. "
            "It does not verify historical executability, open May, promote a model, or "
            "authorize betting."
        ),
    }
    validate_report(payload)
    temporary = report_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(report_path)

    confirmation = role_reports["confirmation"]
    intervals = confirmation["date_block_intervals"]
    print("HR BATTED-BALL CANDIDATE ECONOMIC GATE - RESEARCH ONLY")
    print(
        f"  certified/gradeable/void: {len(pairs):,} / {len(graded):,} / {len(void):,}"
    )
    print(
        "  confirmation candidate Brier/log loss: "
        f"{confirmation['candidate']['brier']:.6f} / "
        f"{confirmation['candidate']['log_loss']:.6f}"
    )
    print(
        "  candidate-minus-frozen Brier 95%: "
        f"{intervals['candidate_minus_frozen_brier_95'][0]:+.6f} to "
        f"{intervals['candidate_minus_frozen_brier_95'][1]:+.6f}"
    )
    print(
        "  candidate-minus-frozen log loss 95%: "
        f"{intervals['candidate_minus_frozen_log_loss_95'][0]:+.6f} to "
        f"{intervals['candidate_minus_frozen_log_loss_95'][1]:+.6f}"
    )
    print(f"  candidate supported: {candidate_supported}")
    print("  betting authorized: NO; May opened: NO")
    print(f"  report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
