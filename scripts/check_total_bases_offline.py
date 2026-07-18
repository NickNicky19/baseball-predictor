#!/usr/bin/env python3
"""Offline/mutation harness for the unpromoted total-bases candidate path."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import scripts.run_total_bases_gate as gate
from src.learning.gbm_dataset import FantasyWeights, derive_target
from src.learning.outcome_recorder import compute_actual_value
from src.models.dataclasses import GameSimulationResult, MonteCarloResult, PropProjection
from src.models.total_bases_contract import candidate_config, require_supported_total_bases_line
from src.prediction.edge_calculator import EdgeCalculator
from src.prediction.prop_engine import PropEngine
from src.simulation.monte_carlo import FantasyScoring, MonteCarloEngine


PASS = 0
FAIL = 0


def check(condition: bool, label: str) -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"[PASS] {label}")
    else:
        FAIL += 1
        print(f"[FAIL] {label}")


def must_raise(fn, label: str) -> None:
    try:
        fn()
    except (ValueError, SystemExit):
        check(True, label)
    else:
        check(False, label)


def write_csv(path: Path, rows: list[dict]) -> None:
    pd.DataFrame(rows).to_csv(path, index=False)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    sample = GameSimulationResult(
        plate_appearances=4, hits=4, singles=1, doubles=1, triples=1,
        home_runs=1, runs=0, rbi=0, walks=0, strikeouts=0,
    )
    check(sample.total_bases == 10, "simulation total bases uses 1B+2*2B+3*3B+4*HR")
    engine = MonteCarloEngine()
    check(engine._category_value(sample, "total_bases") == 10.0,
          "Monte Carlo category value uses the total-bases property")

    stats = SimpleNamespace(hits=4, doubles=1, triples=1, home_runs=1)
    check(compute_actual_value(stats, "total_bases", FantasyScoring()) == 10.0,
          "official actual total bases matches the simulation definition")
    frame = pd.DataFrame({"out_hits": [4], "out_doubles": [1], "out_triples": [1], "out_hr": [1]})
    target = derive_target(frame, "total_bases", FantasyWeights())
    check(np.allclose(target, [10.0]), "GBM total-bases target matches official actual definition")

    check(require_supported_total_bases_line(5.5) == 6.0,
          "observed 5.5 market line maps to exact >=6 tail")
    must_raise(lambda: require_supported_total_bases_line(6.5),
               "unsupported future line fails closed instead of approximating")
    must_raise(lambda: require_supported_total_bases_line(2.0),
               "integer push line cannot masquerade as a binary Over probability")

    mc = MonteCarloResult(100, "total_bases", 1.5, 1.0, 0.0, 3.0, {2.0: 0.77})
    projection = PropProjection(1, "P", "total_bases", "2026-07-01", 1.5, 0.7, simulation=mc)
    odds = SimpleNamespace(
        line=1.5,
        over_odds_american=-110,
        under_odds_american=-110,
        sportsbook="draftkings",
    )
    edge = EdgeCalculator().compute_edge(projection, odds)
    check(abs(edge.model_prob_over - 0.77) < 1e-9,
          "edge calculator uses exact integer-tail probability for half-point line")
    projection.simulation = MonteCarloResult(100, "total_bases", 1.5, 1.0, 0.0, 3.0, {})
    must_raise(lambda: EdgeCalculator().compute_edge(projection, odds),
               "mutation: missing total-bases tail cannot fall back to normal approximation")

    check("total_bases" not in PropEngine.HITTER_CATEGORIES,
          "candidate category is absent from normal live hitter output")
    candidate = candidate_config({"season": 2026})
    check(candidate["total_bases"]["status"] == "candidate_unpromoted",
          "candidate config explicitly forks provenance")

    tmp = Path(tempfile.mkdtemp())
    model, market, outcomes, selection = (tmp / name for name in ("model.csv", "market.csv", "outcomes.csv", "selection.csv"))
    rows_model, rows_market, rows_outcome, rows_selection = [], [], [], []
    policy_hash = "b" * 64
    for day, pk, actual in (("2026-05-01", 9001, 0), ("2026-05-02", 9002, 2), ("2026-05-03", 9003, 0), ("2026-05-04", 9004, 2)):
        for line, model_p, entry_p, close_p in ((0.5, 0.70, 0.60, 0.64), (1.5, 0.35, 0.25, 0.30)):
            close_p = 0.60 if actual > line else 0.40
            key = dict(mlb_game_pk=pk, player_id=101, game_date=day, category="total_bases", line=line)
            rows_model.append({**key, "sim_p_over": model_p})
            rows_market.append({
                **key, "book": "draftkings", "entry_p_over": entry_p, "close_p_over": close_p,
                "entry_quote_at_utc": f"{day}T16:00:00Z", "close_quote_at_utc": f"{day}T19:00:00Z",
                "official_start_time_utc": f"{day}T20:00:00Z", "void_status": "graded",
            })
            rows_selection.append({
                **{k: key[k] for k in ("mlb_game_pk", "player_id", "category", "line")},
                "side": "over", "policy_id": "frozen-policy-v1", "policy_sha256": policy_hash,
            })
        rows_outcome.append(dict(mlb_game_pk=pk, player_id=101, game_date=day, category="total_bases", actual_value=actual))
    write_csv(model, rows_model); write_csv(market, rows_market); write_csv(outcomes, rows_outcome); write_csv(selection, rows_selection)
    date_universe = tmp / "accepted_dates.json"
    dates = ["2026-05-01", "2026-05-02", "2026-05-03", "2026-05-04"]
    date_universe.write_text(json.dumps(dates), encoding="utf-8")
    date_universe_sha = sha256(date_universe)
    eligibility_definition = "hard-keyed, selected book, two-sided settled T-4h entry plus pregame close"
    model_manifest = tmp / "model.manifest.json"
    model_manifest.write_text(json.dumps({
        "total_bases_candidate": True,
        "dates": dates,
        "date_source": {"kind": "dates_file", "sha256": date_universe_sha},
        "config_sha256": "a" * 64,
        "model_version": "candidate-test-v1",
        "probability_artifact_sha256": sha256(model),
        "outcome_artifact_sha256": sha256(outcomes),
        "fail_on_flags": True,
        "require_statcast_profiles": True,
        "simulation_random_seed": 17,
    }), encoding="utf-8")
    market_manifest = tmp / "market.manifest.json"
    market_manifest.write_text(json.dumps({
        "crosswalk_consumer_sha256": "c" * 64,
        "date_universe_sha256": date_universe_sha,
        "date_universe_dates": dates,
        "eligibility_definition": eligibility_definition,
        "eligibility_definition_sha256": hashlib.sha256(eligibility_definition.encode("utf-8")).hexdigest(),
        "market_artifact_sha256": sha256(market),
    }), encoding="utf-8")
    selection_manifest = tmp / "selection.manifest.json"
    selection_manifest.write_text(json.dumps({
        "selection_artifact_sha256": sha256(selection),
        "policy_id": "frozen-policy-v1",
        "policy_sha256": policy_hash,
        "policy_source_sha256": "e" * 64,
        "date_universe_sha256": date_universe_sha,
        "crosswalk_consumer_sha256": "c" * 64,
        "eligibility_definition_sha256": hashlib.sha256(eligibility_definition.encode("utf-8")).hexdigest(),
    }), encoding="utf-8")
    metrics = tmp / "metrics.csv"
    rc = gate.main([
        "--model", str(model), "--model-manifest", str(model_manifest),
        "--market", str(market), "--market-manifest", str(market_manifest),
        "--outcomes", str(outcomes), "--selection", str(selection),
        "--selection-manifest", str(selection_manifest), "--book", "draftkings",
        "--capture-bar", "0.10", "--b", "100", "--out", str(metrics),
    ])
    got = pd.read_csv(metrics)
    check(
        rc == 0
        and metrics.exists()
        and "RESEARCH_ONLY_NOT_PROMOTED" in set(got["verdict"].dropna())
        and "hard_universe_coverage" in set(got["row_type"]),
        "hard-keyed candidate gate writes research-only evidence and its exact coverage denominator",
    )

    mismatched_market_manifest = tmp / "mismatched-market.manifest.json"
    mismatched_payload = json.loads(market_manifest.read_text(encoding="utf-8"))
    mismatched_payload["market_artifact_sha256"] = "f" * 64
    mismatched_market_manifest.write_text(json.dumps(mismatched_payload), encoding="utf-8")
    must_raise(
        lambda: gate.main([
            "--model", str(model), "--model-manifest", str(model_manifest),
            "--market", str(market), "--market-manifest", str(mismatched_market_manifest),
            "--outcomes", str(outcomes), "--selection", str(selection),
            "--selection-manifest", str(selection_manifest), "--book", "draftkings",
            "--capture-bar", "0.10", "--b", "100", "--out", str(tmp / "bad-manifest.csv"),
        ]),
        "mutation: mismatched market artifact hash hard-fails before scoring",
    )

    duplicate = tmp / "duplicate.csv"
    write_csv(duplicate, rows_model + [rows_model[0]])
    duplicate_manifest = tmp / "duplicate.manifest.json"
    duplicate_payload = json.loads(model_manifest.read_text(encoding="utf-8"))
    duplicate_payload["probability_artifact_sha256"] = sha256(duplicate)
    duplicate_manifest.write_text(json.dumps(duplicate_payload), encoding="utf-8")
    must_raise(
        lambda: gate.main([
            "--model", str(duplicate), "--model-manifest", str(duplicate_manifest),
            "--market", str(market), "--market-manifest", str(market_manifest),
            "--outcomes", str(outcomes), "--selection", str(selection),
            "--selection-manifest", str(selection_manifest), "--book", "draftkings",
            "--capture-bar", "0.10", "--b", "100", "--out", str(tmp / "bad.csv"),
        ]),
        "mutation: duplicate MODEL_KEY hard-fails before scoring",
    )

    print(f"\n{PASS}/{PASS + FAIL} checks passed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
