"""Evaluate the new PA-volume component on locked chronological 2023 folds.

This script evaluates only P(PA | original starting slot) against a pooled PA
distribution.  It must not be interpreted as a historical test of projected
lineups, start probability, pinch-hit opportunity, or market performance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
CONFIG = ROOT / "config/shared_pa_source_bound_opportunity_component_2023_v1.json"


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def distribution(rows: Iterable[Mapping[str, Any]]) -> dict[int, float]:
    counts = Counter(int(row["out_pa"]) for row in rows)
    total = sum(counts.values())
    if total <= 0:
        raise ValueError("training fold has no rows")
    return {state: count / total for state, count in sorted(counts.items())}


def score(rows: list[Mapping[str, Any]], probabilities) -> dict[str, float]:
    support = sorted({int(row["out_pa"]) for row in rows} | set(range(0, 8)))
    brier = log_loss = absolute = 0.0
    floor = 1e-15
    for row in rows:
        observed = int(row["out_pa"])
        pmf = probabilities(row)
        brier += sum((float(pmf.get(state, 0.0)) - float(state == observed)) ** 2 for state in support)
        log_loss -= math.log(max(float(pmf.get(observed, 0.0)), floor))
        mean = sum(state * float(value) for state, value in pmf.items())
        absolute += abs(mean - observed)
    count = len(rows)
    return {
        "rows": count,
        "multiclass_brier": brier / count,
        "multiclass_log_loss": log_loss / count,
        "expected_pa_mae": absolute / count,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--projection", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    config_raw = CONFIG.read_bytes()
    config = json.loads(config_raw)
    payload = json.loads(args.projection.read_bytes())
    rows = payload["projection"]["rows"]
    if payload.get("season") != 2023 or len(rows) != 43740:
        raise ValueError("qualified projection identity differs")
    results = []
    for fold in config["folds"]:
        train_end = date.fromisoformat(fold["train_end"])
        start = date.fromisoformat(fold["validation_start"])
        end = date.fromisoformat(fold["validation_end"])
        training = [row for row in rows if date.fromisoformat(row["official_date"]) <= train_end]
        validation = [row for row in rows if start <= date.fromisoformat(row["official_date"]) <= end]
        pooled = distribution(training)
        by_slot = {
            slot: distribution(row for row in training if int(row["lineup_slot"]) == slot)
            for slot in range(1, 10)
        }
        pooled_score = score(validation, lambda row, value=pooled: value)
        slot_score = score(validation, lambda row, values=by_slot: values[int(row["lineup_slot"])])
        results.append({
            "fold": fold,
            "training_rows": len(training),
            "validation_rows": len(validation),
            "pooled": pooled_score,
            "slot_conditional": slot_score,
            "slot_minus_pooled": {
                key: slot_score[key] - pooled_score[key]
                for key in ("multiclass_brier", "multiclass_log_loss", "expected_pa_mae")
            },
        })
    totals = {key: 0.0 for key in ("multiclass_brier", "multiclass_log_loss", "expected_pa_mae")}
    row_total = sum(result["validation_rows"] for result in results)
    for result in results:
        weight = result["validation_rows"] / row_total
        for key in totals:
            totals[key] += weight * result["slot_minus_pooled"][key]
    unsigned = {
        "schema_version": "shared-pa-source-bound-opportunity-component-evaluation-v1",
        "candidate_id": config["candidate_id"],
        "research_only": True,
        "betting_authorized": False,
        "config_sha256": hashlib.sha256(config_raw).hexdigest(),
        "projection_sha256": hashlib.sha256(args.projection.read_bytes()).hexdigest(),
        "interpretation_boundary": config["interpretation_boundary"],
        "fold_results": results,
        "weighted_slot_minus_pooled": totals,
        "validation_rows": row_total,
        "component_result": (
            "SLOT_CONDITIONAL_COMPONENT_IMPROVED_ALL_PREDECLARED_METRICS"
            if all(value < 0.0 for value in totals.values())
            else "SLOT_CONDITIONAL_COMPONENT_DID_NOT_IMPROVE_ALL_PREDECLARED_METRICS"
        ),
        "market_superiority_claimed": False,
        "prospective_opportunity_proof_required": True,
    }
    result = {**unsigned, "report_sha256": hashlib.sha256(canonical(unsigned)).hexdigest()}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(result))
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
