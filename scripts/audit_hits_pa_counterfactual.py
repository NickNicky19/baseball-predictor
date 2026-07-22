#!/usr/bin/env python3
"""Run the predeclared March-April hits PA counterfactual.

The audit is deliberately outcome-time and diagnostic.  It does not edit the
production simulator, refit the wager policy, inspect May, or authorize bets.
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

from src.evaluation.hits_pa_counterfactual import (  # noqa: E402
    exponential_tilt_to_mean,
    infer_per_pa_hit_probability,
    infer_probability_lattice_denominator,
    normalise_pa_distribution,
    pa_mean,
    probability_at_least_hits,
    realised_pa_distribution,
    require_exact_key_set,
    require_locked_float,
    reject_holdout_dates,
)
from src.evaluation.hits_policy_fit import arm_policy_rows, build_policy_pairs  # noqa: E402
from src.evaluation.identity_keys import MODEL_KEY, require_unique  # noqa: E402


SCHEMA = "hits-pa-counterfactual-report-v1"
PLAYER_KEY = ["mlb_game_pk", "player_id"]
ARMS = (
    "candidate_mc",
    "current_analytic",
    "selector_mean_aligned",
    "realised_pa_oracle",
)


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


def checked_fit_input(report: dict[str, Any], name: str) -> Path:
    try:
        record = report["inputs"][name]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"fit report lacks hashed input {name!r}") from exc
    return verified_path(record, f"fit input {name!r}")


def unique_player_facts(pairs: pd.DataFrame) -> pd.DataFrame:
    columns = [
        *PLAYER_KEY,
        "official_game_date",
        "official_lineup_slot",
        "official_pa",
    ]
    facts = pairs[columns].drop_duplicates()
    duplicated = facts.duplicated(PLAYER_KEY, keep=False)
    if duplicated.any():
        raise ValueError(
            "one player-game has contradictory official PA/slot/date facts:\n"
            + facts.loc[duplicated].head(20).to_string(index=False)
        )
    return facts.reset_index(drop=True)


def candidate_hit_probabilities(candidate: pd.DataFrame) -> pd.DataFrame:
    required = [*MODEL_KEY, "game_date", "sim_p_over"]
    missing = [column for column in required if column not in candidate.columns]
    if missing:
        raise ValueError(f"candidate probabilities missing {missing}")
    hits = candidate[candidate.category.eq("hits")].copy()
    require_unique(hits, MODEL_KEY, "candidate hits probabilities")
    if set(hits.line.astype(float).unique()) != {0.5, 1.5}:
        raise ValueError("candidate hits artifact does not contain exactly lines 0.5 and 1.5")
    wide = hits.pivot(index=PLAYER_KEY, columns="line", values="sim_p_over")
    if wide[[0.5, 1.5]].isna().any().any():
        raise ValueError("candidate does not carry both hits lines for every player-game")
    wide = wide[[0.5, 1.5]].rename(
        columns={0.5: "candidate_mc_ge1", 1.5: "candidate_mc_ge2"}
    )
    return wide.reset_index()


def per_player_counterfactuals(
    facts: pd.DataFrame,
    candidate: pd.DataFrame,
    by_slot: dict[int, dict[int, float]],
    selector_dates: set[str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    player = facts.merge(candidate_hit_probabilities(candidate), on=PLAYER_KEY, how="left", validate="one_to_one")
    if player[["candidate_mc_ge1", "candidate_mc_ge2"]].isna().any().any():
        raise ValueError("a strict player-game lacks the candidate's paired hits lines")

    player["official_lineup_slot"] = pd.to_numeric(
        player.official_lineup_slot, errors="raise"
    ).astype(int)
    player["official_pa"] = pd.to_numeric(player.official_pa, errors="raise")
    if not player.official_lineup_slot.between(1, 9).all():
        raise ValueError("official lineup slot lies outside 1..9")
    if (player.official_pa < 0).any() or not np.equal(
        player.official_pa, np.floor(player.official_pa)
    ).all():
        raise ValueError("official PA is not a non-negative integer")

    selector = player[player.official_game_date.isin(selector_dates)]
    if selector.empty:
        raise ValueError("selector dates contain no strict player-games")
    observed_slots = set(player.official_lineup_slot.unique())
    if set(selector.official_lineup_slot.unique()) != observed_slots:
        raise ValueError("selector period does not cover every scored lineup slot")

    slot_records: list[dict[str, Any]] = []
    mean_aligned: dict[int, dict[int, float]] = {}
    for slot in sorted(observed_slots):
        current = normalise_pa_distribution(by_slot[slot])
        target = float(
            selector.loc[selector.official_lineup_slot.eq(slot), "official_pa"].mean()
        )
        aligned = exponential_tilt_to_mean(current, target)
        mean_aligned[slot] = aligned
        slot_records.append(
            {
                "official_lineup_slot": slot,
                "selector_player_games": int(
                    selector.official_lineup_slot.eq(slot).sum()
                ),
                "current_mean_pa": pa_mean(current),
                "selector_observed_mean_pa": target,
                "mean_aligned_pa": pa_mean(aligned),
            }
        )

    q_values: list[float] = []
    current_ge2: list[float] = []
    aligned_ge1: list[float] = []
    aligned_ge2: list[float] = []
    oracle_ge1: list[float] = []
    oracle_ge2: list[float] = []
    current_mean: list[float] = []
    for row in player.itertuples(index=False):
        slot = int(row.official_lineup_slot)
        current = by_slot[slot]
        q = infer_per_pa_hit_probability(float(row.candidate_mc_ge1), current)
        q_values.append(q)
        current_ge2.append(probability_at_least_hits(q, current, 2))
        aligned_ge1.append(probability_at_least_hits(q, mean_aligned[slot], 1))
        aligned_ge2.append(probability_at_least_hits(q, mean_aligned[slot], 2))
        oracle = realised_pa_distribution(row.official_pa)
        oracle_ge1.append(probability_at_least_hits(q, oracle, 1))
        oracle_ge2.append(probability_at_least_hits(q, oracle, 2))
        current_mean.append(pa_mean(current))

    player["inferred_per_pa_hit_probability"] = q_values
    # P(H>=1) is the inversion anchor.  Record it explicitly instead of
    # pretending it is an independent analytic validation.
    player["current_analytic_ge1"] = player.candidate_mc_ge1.to_numpy(float)
    player["current_analytic_ge2"] = current_ge2
    player["selector_mean_aligned_ge1"] = aligned_ge1
    player["selector_mean_aligned_ge2"] = aligned_ge2
    player["realised_pa_oracle_ge1"] = oracle_ge1
    player["realised_pa_oracle_ge2"] = oracle_ge2
    for arm in ("selector_mean_aligned", "realised_pa_oracle"):
        player[f"{arm}_shift_ge1_vs_candidate_mc"] = (
            player[f"{arm}_ge1"] - player.candidate_mc_ge1
        )
        player[f"{arm}_shift_ge2_vs_candidate_mc"] = (
            player[f"{arm}_ge2"] - player.candidate_mc_ge2
        )
    player["current_mean_pa"] = current_mean
    player["pa_error_current_minus_official"] = (
        player.current_mean_pa - player.official_pa
    )
    player["mc_minus_current_analytic_ge2"] = (
        player.candidate_mc_ge2 - player.current_analytic_ge2
    )
    return player, pd.DataFrame(slot_records)


def attach_market_rows(pairs: pd.DataFrame, player: pd.DataFrame) -> pd.DataFrame:
    probability_columns = [
        *PLAYER_KEY,
        "candidate_mc_ge1",
        "candidate_mc_ge2",
        "current_analytic_ge1",
        "current_analytic_ge2",
        "selector_mean_aligned_ge1",
        "selector_mean_aligned_ge2",
        "realised_pa_oracle_ge1",
        "realised_pa_oracle_ge2",
        "inferred_per_pa_hit_probability",
        "current_mean_pa",
        "pa_error_current_minus_official",
    ]
    rows = pairs.merge(
        player[probability_columns], on=PLAYER_KEY, how="left", validate="many_to_one"
    )
    if len(rows) != len(pairs) or rows[probability_columns[2:]].isna().any().any():
        raise ValueError("counterfactual attachment lost a strict market row")
    if not set(rows.line.astype(float).unique()) <= {0.5, 1.5}:
        raise ValueError("counterfactual saw an unsupported hits line")

    use_ge1 = rows.line.astype(float).eq(0.5).to_numpy()
    rows["p_candidate_mc"] = rows.p_candidate.to_numpy(float)
    for arm in ARMS[1:]:
        rows[f"p_{arm}"] = np.where(
            use_ge1,
            rows[f"{arm}_ge1"].to_numpy(float),
            rows[f"{arm}_ge2"].to_numpy(float),
        )
    for arm in ARMS:
        probability = rows[f"p_{arm}"].to_numpy(float)
        if (~np.isfinite(probability)).any() or ((probability < 0.0) | (probability > 1.0)).any():
            raise ValueError(f"{arm} emitted an invalid probability")
    if not np.allclose(rows.p_candidate_mc, rows.p_candidate, atol=0.0, rtol=0.0):
        raise AssertionError("candidate reference changed while building counterfactual")
    require_unique(rows, MODEL_KEY, "PA counterfactual strict market rows")
    return rows


def metric_record(
    rows: pd.DataFrame,
    arm: str,
    period: str,
    line_label: str,
    mask: np.ndarray,
    threshold: float,
) -> dict[str, Any]:
    subset = rows.loc[mask].copy()
    p = subset[f"p_{arm}"].to_numpy(float)
    y = (subset.actual_value.to_numpy(float) > subset.line.to_numpy(float)).astype(float)
    policy_input = subset.copy()
    policy_input["p_candidate"] = p
    policy = arm_policy_rows(policy_input, "candidate")
    selected = policy[policy.expected_profit >= threshold]
    denominator = float(selected.market_edge.sum())
    return {
        "period": period,
        "line": line_label,
        "arm": arm,
        "rows": int(len(subset)),
        "official_dates": int(subset.official_game_date.nunique()),
        "brier": float(np.mean((p - y) ** 2)),
        "mean_probability_residual": float(np.mean(p - y)),
        "selected_rows": int(len(selected)),
        "selected_dates": int(selected.official_game_date.nunique()),
        "capture": (
            float(selected.clv.sum() / denominator)
            if len(selected) and denominator > 0.0
            else float("nan")
        ),
        "flat_stake_roi": (
            float(selected.realised_profit.mean()) if len(selected) else float("nan")
        ),
        "mean_expected_profit": (
            float(selected.expected_profit.mean()) if len(selected) else float("nan")
        ),
        "mean_probability_shift_vs_candidate_mc": float(
            np.mean(p - subset.p_candidate_mc.to_numpy(float))
        ),
        "mean_absolute_probability_shift_vs_candidate_mc": float(
            np.mean(np.abs(p - subset.p_candidate_mc.to_numpy(float)))
        ),
    }


def metric_table(
    rows: pd.DataFrame,
    selector_dates: set[str],
    confirmation_dates: set[str],
    threshold: float,
) -> pd.DataFrame:
    periods = {
        "all_open_fit": np.ones(len(rows), dtype=bool),
        "selector": rows.official_game_date.isin(selector_dates).to_numpy(),
        "confirmation": rows.official_game_date.isin(confirmation_dates).to_numpy(),
    }
    lines = {
        "ALL": np.ones(len(rows), dtype=bool),
        "0.5": rows.line.astype(float).eq(0.5).to_numpy(),
        "1.5": rows.line.astype(float).eq(1.5).to_numpy(),
    }
    records = []
    for period, period_mask in periods.items():
        for line_label, line_mask in lines.items():
            mask = period_mask & line_mask
            if not mask.any():
                continue
            for arm in ARMS:
                records.append(
                    metric_record(rows, arm, period, line_label, mask, threshold)
                )
    return pd.DataFrame(records)


def monte_carlo_compatibility(
    player: pd.DataFrame,
    n_sims: int,
    by_slot: dict[int, dict[int, float]],
) -> dict[str, Any]:
    """Delta-method check for P(H>=2) after anchoring q on sampled P(H>=1)."""

    if n_sims <= 0:
        raise ValueError("candidate simulation count must be positive")
    z_values: list[float] = []
    standard_errors: list[float] = []
    for row in player.itertuples(index=False):
        p1 = float(row.candidate_mc_ge1)
        p2 = float(row.candidate_mc_ge2)
        q = float(row.inferred_per_pa_hit_probability)
        slot = int(row.official_lineup_slot)
        # For the variance ratio, a stable finite-difference on q is sufficient
        # and is checked for finite positive derivatives.
        dist = by_slot[slot]
        step = min(1e-5, max(1e-8, q / 2.0 if q > 0.0 else 1e-5), max(1e-8, (1.0 - q) / 2.0))
        lo, hi = max(0.0, q - step), min(1.0, q + step)
        d1 = (
            probability_at_least_hits(hi, dist, 1)
            - probability_at_least_hits(lo, dist, 1)
        ) / (hi - lo)
        d2 = (
            probability_at_least_hits(hi, dist, 2)
            - probability_at_least_hits(lo, dist, 2)
        ) / (hi - lo)
        if not np.isfinite(d1) or d1 <= 0.0 or not np.isfinite(d2):
            continue
        slope = d2 / d1
        covariance = p2 * (1.0 - p1)
        variance = (
            p2 * (1.0 - p2)
            + slope * slope * p1 * (1.0 - p1)
            - 2.0 * slope * covariance
        ) / n_sims
        se = float(np.sqrt(max(variance, 0.0)))
        if se > 0.0:
            standard_errors.append(se)
            z_values.append(float((p2 - row.current_analytic_ge2) / se))
    delta = np.abs(player.mc_minus_current_analytic_ge2.to_numpy(float))
    z = np.abs(np.asarray(z_values, dtype=float))
    return {
        "n_sims": int(n_sims),
        "player_games": int(len(player)),
        "p_ge1_note": "anchored identity used to infer q; not an independent validation",
        "p_ge2_mean_absolute_difference": float(delta.mean()),
        "p_ge2_median_absolute_difference": float(np.median(delta)),
        "p_ge2_p95_absolute_difference": float(np.percentile(delta, 95)),
        "p_ge2_max_absolute_difference": float(delta.max()),
        "delta_method_rows": int(len(z)),
        "median_delta_method_standard_error": float(np.median(standard_errors)),
        "fraction_abs_z_at_most_1_96": float(np.mean(z <= 1.96)),
        "median_abs_z": float(np.median(z)),
        "p95_abs_z": float(np.percentile(z, 95)),
        "interpretation": (
            "Compatibility diagnostic only. The delta method accounts for the "
            "shared 8,000-draw P(H>=1)/P(H>=2) sample after q is anchored on P(H>=1)."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--protocol",
        default="data/analysis/market_policy_hits_2026/pa_counterfactual_protocol_v1.json",
    )
    ap.add_argument(
        "--out-dir",
        default="data/analysis/market_policy_hits_2026/pa_counterfactual_time_safe_v1",
    )
    args = ap.parse_args(argv)

    protocol_path = Path(args.protocol)
    protocol = load_json(protocol_path)
    if protocol.get("schema_version") != "hits-pa-counterfactual-protocol-v1":
        raise ValueError("unknown PA counterfactual protocol")
    if protocol.get("status") != "PREDECLARED_DIAGNOSTIC_ONLY":
        raise ValueError("PA counterfactual protocol was not predeclared")
    if protocol.get("betting_authorized") is not False:
        raise ValueError("PA counterfactual refuses an authorized protocol")

    fit_report_path = verified_path(protocol["fit_report"], "fit report")
    fit_report = load_json(fit_report_path)
    if fit_report.get("betting_authorized") is not False:
        raise ValueError("fit report unexpectedly authorizes betting")
    chronology_path = verified_path(
        protocol["chronology_protocol"], "chronology protocol"
    )
    chronology = load_json(chronology_path)
    selector_dates = set(chronology["internal_chronology"]["selector_dates"])
    confirmation_dates = set(chronology["internal_chronology"]["confirmation_dates"])
    fit_dates = sorted(selector_dates | confirmation_dates)
    holdout_start = str(chronology["internal_chronology"]["may_holdout_start"])
    reject_holdout_dates(fit_dates, holdout_start)
    if selector_dates & confirmation_dates or max(selector_dates) >= min(confirmation_dates):
        raise ValueError("selector/confirmation chronology is not disjoint and ordered")

    candidate_path = verified_path(
        protocol["candidate_probabilities"], "candidate probabilities"
    )
    pa_path = verified_path(protocol["pa_distribution"], "PA distribution")
    if candidate_path.resolve() != checked_fit_input(
        fit_report, "candidate_probabilities"
    ).resolve():
        raise ValueError("counterfactual candidate differs from the fitted candidate")

    source = pd.read_csv(checked_fit_input(fit_report, "fit_source"))
    frozen = pd.read_csv(checked_fit_input(fit_report, "frozen_probabilities"))
    candidate = pd.read_csv(candidate_path)
    official = pd.read_csv(checked_fit_input(fit_report, "official_outcomes"))
    max_age = float(fit_report["historical_freshness_proxy"]["max_quote_age_minutes"])
    pairs, funnel = build_policy_pairs(
        source,
        frozen,
        candidate,
        official,
        max_quote_age=max_age,
        allowed_dates=fit_dates,
    )
    if len(pairs) != 3502 or len(pairs) != int(fit_report["strict_rows"]):
        raise ValueError("counterfactual strict universe is not the predeclared 3,502 rows")
    reject_holdout_dates(sorted(pairs.official_game_date.unique()), holdout_start)

    locked_threshold = require_locked_float(
        fit_report["selected_min_expected_profit_per_unit"],
        protocol["fixed_policy"]["min_expected_profit_per_unit"],
        "minimum expected-profit threshold",
    )
    if protocol["fixed_policy"]["min_expected_profit_per_unit_source"] != (
        "fit_report.selected_min_expected_profit_per_unit"
    ):
        raise ValueError("counterfactual protocol does not bind the fitted policy")

    pa_payload = load_json(pa_path)
    raw_by_slot = pa_payload.get("by_lineup_slot")
    if not isinstance(raw_by_slot, dict):
        raise ValueError("PA artifact lacks by_lineup_slot")
    by_slot = {
        int(slot): normalise_pa_distribution(distribution)
        for slot, distribution in raw_by_slot.items()
    }
    if set(by_slot) != set(range(1, 10)):
        raise ValueError("PA artifact does not contain exactly slots 1..9")
    facts = unique_player_facts(pairs)
    player, slot_alignment = per_player_counterfactuals(
        facts, candidate, by_slot, selector_dates
    )
    rows = attach_market_rows(pairs, player)
    require_exact_key_set(
        set(map(tuple, rows[MODEL_KEY].to_numpy())),
        set(map(tuple, pairs[MODEL_KEY].to_numpy())),
        "counterfactual strict MODEL_KEY",
    )
    metrics = metric_table(rows, selector_dates, confirmation_dates, locked_threshold)

    candidate_manifest_path = checked_fit_input(fit_report, "candidate_manifest")
    candidate_manifest = load_json(candidate_manifest_path)
    if candidate_manifest.get("probability_artifact_sha256") != sha256(candidate_path):
        raise ValueError("candidate manifest is not bound to the probability artifact")
    candidate_hits = candidate[candidate.category.eq("hits")]
    n_sims = infer_probability_lattice_denominator(
        candidate_hits.sim_p_over.to_numpy(float)
    )
    expected_n_sims = int(protocol["monte_carlo_draw_count"]["expected"])
    if n_sims != expected_n_sims:
        raise ValueError(
            f"candidate probability lattice implies {n_sims} draws, expected {expected_n_sims}"
        )
    compatibility = monte_carlo_compatibility(player, n_sims, by_slot)

    # Directional facts are reported line-by-line.  They nominate no model and
    # do not alter the locked bar or policy.
    confirmation = metrics[metrics.period.eq("confirmation")]
    line_assessment: dict[str, Any] = {}
    for line in ("0.5", "1.5"):
        table = confirmation[confirmation.line.eq(line)].set_index("arm")
        required = set(ARMS)
        if set(table.index) != required:
            raise ValueError(f"confirmation line {line} lacks a counterfactual arm")
        baseline = table.loc["candidate_mc"]
        aligned = table.loc["selector_mean_aligned"]
        oracle = table.loc["realised_pa_oracle"]
        line_assessment[line] = {
            "mean_aligned_brier_better": bool(aligned.brier < baseline.brier),
            "mean_aligned_capture_better": bool(aligned.capture > baseline.capture),
            "oracle_brier_better": bool(oracle.brier < baseline.brier),
            "oracle_capture_better": bool(oracle.capture > baseline.capture),
            "diagnostic_pa_support": bool(
                aligned.brier < baseline.brier
                and aligned.capture > baseline.capture
                and oracle.brier < baseline.brier
                and oracle.capture > baseline.capture
            ),
        }

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    player_path = out_dir / "player_counterfactuals.csv"
    rows_path = out_dir / "market_counterfactuals.csv"
    metrics_path = out_dir / "metrics_by_period_and_line.csv"
    slots_path = out_dir / "selector_slot_mean_alignment.csv"
    report_path = out_dir / "report.json"
    player.to_csv(player_path, index=False)
    rows.to_csv(rows_path, index=False)
    metrics.to_csv(metrics_path, index=False)
    slot_alignment.to_csv(slots_path, index=False)
    report = {
        "schema_version": SCHEMA,
        "built_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "protocol": {"path": str(protocol_path), "sha256": sha256(protocol_path)},
        "fit_report": {"path": str(fit_report_path), "sha256": sha256(fit_report_path)},
        "candidate_probabilities": {"path": str(candidate_path), "sha256": sha256(candidate_path)},
        "pa_distribution": {"path": str(pa_path), "sha256": sha256(pa_path)},
        "chronology": {
            "selector_dates": sorted(selector_dates),
            "confirmation_dates": sorted(confirmation_dates),
            "holdout_start": holdout_start,
            "may_opened": False,
        },
        "strict_funnel": funnel,
        "strict_rows": int(len(rows)),
        "player_games": int(len(player)),
        "fixed_min_expected_profit_per_unit": locked_threshold,
        "monte_carlo_compatibility": compatibility,
        "confirmation_directional_assessment_by_line": line_assessment,
        "interpretation_limits": [
            "The selector-mean-aligned PA distribution uses outcome-time PA from the open selector period; it is a diagnostic candidate, not independent validation.",
            "The realised-PA oracle uses postgame information and can never be deployed.",
            "P(H>=1) anchors the inferred per-PA hit rate; only P(H>=2) checks the analytic constant-q premise independently.",
            "Capture is recomputed at the already fitted threshold; no policy choice is changed.",
            "No result in this report opens May or authorizes betting.",
        ],
        "outputs": {
            "player_counterfactuals": {"path": str(player_path), "sha256": sha256(player_path)},
            "market_counterfactuals": {"path": str(rows_path), "sha256": sha256(rows_path)},
            "metrics": {"path": str(metrics_path), "sha256": sha256(metrics_path)},
            "slot_alignment": {"path": str(slots_path), "sha256": sha256(slots_path)},
        },
        "verdict": "DIAGNOSTIC_ONLY",
        "betting_authorized": False,
    }
    report_path.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")

    print("HITS PA COUNTERFACTUAL — OPEN MARCH/APRIL ONLY")
    print(f"  strict rows: {len(rows):,}; player-games: {len(player):,}; May opened: NO")
    print(f"  fixed expected-profit threshold: {locked_threshold:.12f} (not refit)")
    print(
        "  analytic P(H>=2) mean |MC difference|: "
        f"{compatibility['p_ge2_mean_absolute_difference']:.6f}"
    )
    for line, assessment in line_assessment.items():
        print(f"  hits {line}: diagnostic PA support={assessment['diagnostic_pa_support']}")
    print("  production changes: NONE; May opened: NO; betting authorized: NO")
    print(f"  wrote: {report_path}\n         {metrics_path}\n         {player_path}\n         {rows_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
