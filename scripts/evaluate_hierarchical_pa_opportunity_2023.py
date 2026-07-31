"""One predeclared nested-fold test of hierarchical PA-slot shrinkage.

This evaluates only P(PA | known original starting slot). It does not test a
pregame lineup model, start probability, market predictions, or superiority.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
CONFIG = ROOT / "config/shared_pa_hierarchical_opportunity_2023_v1.json"
SUPPORT = tuple(range(0, 8))
METRICS = ("multiclass_brier", "multiclass_log_loss", "expected_pa_mae")


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _date(row: Mapping[str, Any]) -> date:
    return date.fromisoformat(str(row["official_date"]))


def counts(rows: Iterable[Mapping[str, Any]]) -> Counter[int]:
    result = Counter(int(row["out_pa"]) for row in rows)
    if not result:
        raise ValueError("training partition has no rows")
    if any(state not in SUPPORT for state in result):
        raise ValueError("PA support changed")
    return result


def pmf(observed: Counter[int]) -> dict[int, float]:
    total = sum(observed.values())
    return {state: observed[state] / total for state in SUPPORT}


def fit_family(rows: list[Mapping[str, Any]], weight: float):
    pooled = pmf(counts(rows))
    raw_slot = {
        slot: pmf(counts(row for row in rows if int(row["lineup_slot"]) == slot))
        for slot in range(1, 10)
    }
    hierarchical = {
        slot: {
            state: weight * raw_slot[slot][state] + (1.0 - weight) * pooled[state]
            for state in SUPPORT
        }
        for slot in range(1, 10)
    }
    return pooled, raw_slot, hierarchical


def row_losses(row: Mapping[str, Any], probability: Mapping[int, float]) -> dict[str, float]:
    observed = int(row["out_pa"])
    mean = sum(state * float(value) for state, value in probability.items())
    return {
        "multiclass_brier": sum(
            (float(probability.get(state, 0.0)) - float(state == observed)) ** 2
            for state in SUPPORT
        ),
        "multiclass_log_loss": -math.log(max(float(probability.get(observed, 0.0)), 1e-15)),
        "expected_pa_mae": abs(mean - observed),
    }


def score(rows: list[Mapping[str, Any]], probabilities) -> dict[str, float]:
    totals = {key: 0.0 for key in METRICS}
    if not rows:
        raise ValueError("scoring partition has no rows")
    for row in rows:
        losses = row_losses(row, probabilities(row))
        for key in METRICS:
            totals[key] += losses[key]
    return {"rows": len(rows), **{key: totals[key] / len(rows) for key in METRICS}}


def month_starts(first: str, train_end: date) -> list[date]:
    year, month = map(int, first.split("-"))
    current = date(year, month, 1)
    result = []
    while current <= train_end:
        result.append(current)
        current = date(current.year + (current.month == 12), 1 if current.month == 12 else current.month + 1, 1)
    return result


def aggregate_inner(
    rows: list[Mapping[str, Any]], train_end: date, weight: float, first_month: str
) -> tuple[dict[str, float], dict[str, float], int]:
    candidate_totals = {key: 0.0 for key in METRICS}
    pooled_totals = {key: 0.0 for key in METRICS}
    total_rows = 0
    starts = month_starts(first_month, train_end)
    for index, start in enumerate(starts):
        next_start = (
            date(start.year + 1, 1, 1) if start.month == 12
            else date(start.year, start.month + 1, 1)
        )
        end = min(train_end, next_start.fromordinal(next_start.toordinal() - 1))
        training = [row for row in rows if _date(row) < start]
        validation = [row for row in rows if start <= _date(row) <= end]
        if not training or not validation:
            continue
        pooled, _, hierarchical = fit_family(training, weight)
        pooled_score = score(validation, lambda row, value=pooled: value)
        candidate_score = score(
            validation, lambda row, values=hierarchical: values[int(row["lineup_slot"])]
        )
        total_rows += len(validation)
        for key in METRICS:
            pooled_totals[key] += pooled_score[key] * len(validation)
            candidate_totals[key] += candidate_score[key] * len(validation)
    if total_rows <= 0:
        raise ValueError("inner chronological folds are empty")
    return (
        {key: candidate_totals[key] / total_rows for key in METRICS},
        {key: pooled_totals[key] / total_rows for key in METRICS},
        total_rows,
    )


def paired_date_deltas(rows, pooled, hierarchical):
    grouped: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        candidate = row_losses(row, hierarchical[int(row["lineup_slot"])])
        baseline = row_losses(row, pooled)
        for key in METRICS:
            grouped[str(row["official_date"])][key].append(candidate[key] - baseline[key])
    return {
        day: {key: sum(values[key]) / len(values[key]) for key in METRICS}
        for day, values in sorted(grouped.items())
    }


def bootstrap_bounds(date_deltas, *, resamples: int, seed: int, confidence: float):
    days = sorted(date_deltas)
    generator = random.Random(seed)
    draws = {key: [] for key in METRICS}
    for _ in range(resamples):
        sample = [days[generator.randrange(len(days))] for _ in days]
        for key in METRICS:
            draws[key].append(sum(date_deltas[day][key] for day in sample) / len(sample))
    tail = (1.0 - confidence) / 2.0
    result = {}
    for key in METRICS:
        ordered = sorted(draws[key])
        lower = ordered[max(0, int(tail * resamples))]
        upper = ordered[min(resamples - 1, int((1.0 - tail) * resamples) - 1)]
        result[key] = {"lower": lower, "upper": upper}
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--projection", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    config_raw = CONFIG.read_bytes()
    config = json.loads(config_raw)
    projection_raw = args.projection.read_bytes()
    if hashlib.sha256(projection_raw).hexdigest() != config["source_projection_sha256"]:
        raise ValueError("qualified projection bytes differ from the locked source")
    payload = json.loads(projection_raw)
    rows = payload["projection"]["rows"]
    if payload.get("season") != 2023 or len(rows) != 43740:
        raise ValueError("qualified projection identity differs")

    results = []
    all_date_deltas = {}
    minimum = float(config["selection_rule"]["minimum_improvement"])
    for fold_index, fold in enumerate(config["outer_folds"]):
        train_end = date.fromisoformat(fold["train_end"])
        outer_start = date.fromisoformat(fold["validation_start"])
        outer_end = date.fromisoformat(fold["validation_end"])
        full_training = [row for row in rows if _date(row) <= train_end]
        outer_validation = [row for row in rows if outer_start <= _date(row) <= outer_end]
        choices = []
        for weight in config["weight_grid"]:
            candidate_score, pooled_score, inner_rows = aggregate_inner(
                rows, train_end, float(weight), config["inner_fold_first_month"]
            )
            choices.append({
                "weight": float(weight),
                "rows": inner_rows,
                "score": candidate_score,
                "pooled": pooled_score,
                "eligible": all(candidate_score[key] < pooled_score[key] - minimum for key in METRICS),
            })
        eligible = [choice for choice in choices if choice["eligible"]]
        if not eligible:
            raise ValueError("no predeclared weight improves every inner metric")
        selected = min(
            eligible,
            key=lambda choice: (
                choice["score"]["expected_pa_mae"],
                choice["score"]["multiclass_log_loss"],
                choice["weight"],
            ),
        )
        pooled, raw_slot, hierarchical = fit_family(full_training, selected["weight"])
        pooled_score = score(outer_validation, lambda row, value=pooled: value)
        raw_score = score(outer_validation, lambda row, value=raw_slot: value[int(row["lineup_slot"])])
        hierarchical_score = score(
            outer_validation, lambda row, value=hierarchical: value[int(row["lineup_slot"])]
        )
        date_deltas = paired_date_deltas(outer_validation, pooled, hierarchical)
        if set(all_date_deltas) & set(date_deltas):
            raise ValueError("outer validation dates overlap")
        all_date_deltas.update(date_deltas)
        results.append({
            "fold": fold,
            "training_rows": len(full_training),
            "validation_rows": len(outer_validation),
            "selected_weight": selected["weight"],
            "inner_candidates": choices,
            "outer_pooled": pooled_score,
            "outer_raw_slot": raw_score,
            "outer_hierarchical": hierarchical_score,
            "hierarchical_minus_pooled": {key: hierarchical_score[key] - pooled_score[key] for key in METRICS},
            "hierarchical_minus_raw_slot": {key: hierarchical_score[key] - raw_score[key] for key in METRICS},
        })

    uncertainty = config["uncertainty"]
    bounds = bootstrap_bounds(
        all_date_deltas,
        resamples=int(uncertainty["resamples"]),
        seed=int(uncertainty["seed"]),
        confidence=float(uncertainty["confidence"]),
    )
    total_rows = sum(result["validation_rows"] for result in results)
    weighted = {key: 0.0 for key in METRICS}
    versus_raw = {key: 0.0 for key in METRICS}
    for result in results:
        weight = result["validation_rows"] / total_rows
        for key in METRICS:
            weighted[key] += weight * result["hierarchical_minus_pooled"][key]
            versus_raw[key] += weight * result["hierarchical_minus_raw_slot"][key]
    passed = all(bounds[key]["upper"] < 0.0 for key in METRICS)
    unsigned = {
        "schema_version": "shared-pa-hierarchical-opportunity-evaluation-v1",
        "candidate_id": config["candidate_id"],
        "research_only": True,
        "betting_authorized": False,
        "config_sha256": hashlib.sha256(config_raw).hexdigest(),
        "projection_sha256": hashlib.sha256(projection_raw).hexdigest(),
        "interpretation_boundary": config["interpretation_boundary"],
        "fold_results": results,
        "weighted_hierarchical_minus_pooled": weighted,
        "weighted_hierarchical_minus_raw_slot": versus_raw,
        "paired_official_date_bootstrap_95": bounds,
        "validation_rows": total_rows,
        "component_gate_passed": passed,
        "component_result": (
            "HIERARCHICAL_COMPONENT_PASSED_PREDECLARED_GATE"
            if passed else "HIERARCHICAL_COMPONENT_FAILED_PREDECLARED_GATE"
        ),
        "market_superiority_claimed": False,
        "prospective_opportunity_proof_required": True,
    }
    result = {**unsigned, "report_sha256": hashlib.sha256(canonical(unsigned)).hexdigest()}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(result))
    print(json.dumps({
        "component_result": result["component_result"],
        "selected_weight_by_fold": [row["selected_weight"] for row in results],
        "weighted_hierarchical_minus_pooled": weighted,
        "weighted_hierarchical_minus_raw_slot": versus_raw,
        "paired_official_date_bootstrap_95": bounds,
        "report_sha256": result["report_sha256"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
