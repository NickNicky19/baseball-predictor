"""Outcome-blind readiness audit for Total Bases, RBI, and Hits+Runs+RBI.

The audit reads quote columns only from the locked March, April, and June
SmartStake partitions.  It never reads May, vendor result values/nullness, or
official outcomes, and it cannot authorize betting or launch reconstruction.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.learning.outcome_recorder import compute_actual_value  # noqa: E402
from src.models.dataclasses import GameSimulationResult  # noqa: E402
from src.prediction.prop_engine import PropEngine  # noqa: E402
from src.simulation.monte_carlo import FantasyScoring, MonteCarloEngine  # noqa: E402


TARGETS = {
    "total_bases": "player bases",
    "rbi": "player rbis",
    "hrr": None,
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def verify_protocol(protocol: dict[str, Any]) -> list[Path]:
    require(protocol["status"] == "LOCKED_BEFORE_OTHER_MARKET_READINESS_RESULTS",
            "protocol is not locked")
    require(protocol["chronology"]["forbidden_partition"] == "2026-05", "May guard changed")
    require(protocol["chronology"]["outcomes_must_not_be_read"] is True, "outcome guard changed")
    require(protocol["betting_authorized"] is False, "protocol authorized betting")
    source_paths: list[Path] = []
    for rel, expected in protocol["inputs_sha256"].items():
        path = ROOT / rel
        require(path.is_file(), f"missing locked input: {rel}")
        require("mon=2026-05" not in rel.lower(), f"May input forbidden: {rel}")
        actual = sha256(path)
        require(actual == expected, f"input hash mismatch: {rel}: {actual} != {expected}")
        if path.suffix == ".parquet":
            source_paths.append(path)
    require(source_paths, "no quote partitions locked")
    months = {path.parent.name.removeprefix("mon=") for path in source_paths}
    require(months == set(protocol["chronology"]["allowed_partitions"]),
            f"quote partition set mismatch: {sorted(months)}")
    return source_paths


def validate_quote_sql(sql: str) -> None:
    lowered = sql.lower()
    require("result" not in lowered, "numeric vendor result/nullness reached outcome-blind SQL")
    require("2026-05" not in lowered, "May reached outcome-blind SQL")


def sql_source(paths: list[Path]) -> str:
    quoted = ",".join("'" + path.as_posix().replace("'", "''") + "'" for path in paths)
    return f"read_parquet([{quoted}])"


def frame_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return json.loads(frame.to_json(orient="records"))


def query_quotes(source: str, entry_hours: int) -> dict[str, pd.DataFrame]:
    inventory_sql = f"""
        SELECT lower(market) AS market,
               count(*) AS quote_rows,
               count(DISTINCT lower(book)) AS books,
               count(DISTINCT (game_id,start_time,player,line)) AS source_selections
        FROM {source}
        GROUP BY 1 ORDER BY quote_rows DESC
    """
    sides_sql = f"""
        SELECT lower(book) AS book, lower(market) AS market, CAST(line AS DOUBLE) AS line,
               lower(side) AS side, count(*) AS quote_rows,
               count(DISTINCT (game_id,start_time,player,line)) AS source_selections
        FROM {source}
        WHERE lower(market) IN ('player bases','player rbis')
        GROUP BY 1,2,3,4 ORDER BY 2,1,3,4
    """
    availability_sql = f"""
        WITH pre AS (
          SELECT lower(book) AS book, lower(market) AS market, game_id, start_time,
                 player, CAST(line AS DOUBLE) AS line, lower(side) AS side, ts,
                 CAST(odds AS DOUBLE) AS odds,
                 start_time - INTERVAL {int(entry_hours)} HOUR AS horizon
          FROM {source}
          WHERE lower(market) IN ('player bases','player rbis') AND ts < start_time
        ), selection AS (
          SELECT book,market,game_id,start_time,player,line,
            max(ts) FILTER(WHERE side='over' AND ts<=horizon) AS entry_over_time,
            arg_max(odds,ts) FILTER(WHERE side='over' AND ts<=horizon) AS entry_over_odds,
            max(ts) FILTER(WHERE side='under' AND ts<=horizon) AS entry_under_time,
            arg_max(odds,ts) FILTER(WHERE side='under' AND ts<=horizon) AS entry_under_odds,
            max(ts) FILTER(WHERE side='over') AS close_over_time,
            arg_max(odds,ts) FILTER(WHERE side='over') AS close_over_odds,
            max(ts) FILTER(WHERE side='under') AS close_under_time,
            arg_max(odds,ts) FILTER(WHERE side='under') AS close_under_odds,
            date_diff('second',
              max(ts) FILTER(WHERE side='over' AND ts<=horizon), horizon)/60.0 AS over_age_min,
            date_diff('second',
              max(ts) FILTER(WHERE side='under' AND ts<=horizon), horizon)/60.0 AS under_age_min
          FROM pre GROUP BY 1,2,3,4,5,6,horizon
        )
        SELECT book,market,line,
          count(*) AS source_selections,
          count(*) FILTER(WHERE entry_over_time IS NOT NULL) AS entry_over,
          count(*) FILTER(WHERE entry_under_time IS NOT NULL) AS entry_under,
          count(*) FILTER(WHERE entry_over_time IS NOT NULL AND entry_under_time IS NOT NULL)
            AS entry_both,
          count(*) FILTER(WHERE close_over_time IS NOT NULL) AS close_over,
          count(*) FILTER(WHERE close_under_time IS NOT NULL) AS close_under,
          count(*) FILTER(WHERE entry_over_time IS NOT NULL AND entry_under_time IS NOT NULL
                           AND close_over_time IS NOT NULL AND close_under_time IS NOT NULL)
            AS entry_and_close_both,
          median(over_age_min) FILTER(WHERE entry_over_time IS NOT NULL) AS median_over_age_min,
          median(under_age_min) FILTER(WHERE entry_under_time IS NOT NULL) AS median_under_age_min
        FROM selection GROUP BY 1,2,3 ORDER BY 2,1,3
    """
    fragments_sql = f"""
        WITH distinct_fragments AS (
          SELECT DISTINCT lower(book) AS book, lower(market) AS market, game_id,
                 start_time, player, CAST(line AS DOUBLE) AS line
          FROM {source}
          WHERE lower(market) IN ('player bases','player rbis')
        ), grouped AS (
          SELECT book,market,game_id,player,line,count(DISTINCT start_time) AS fragments
          FROM distinct_fragments GROUP BY 1,2,3,4,5
        )
        SELECT book,market,count(*) AS vendor_identity_keys,
               count(*) FILTER(WHERE fragments>1) AS multi_fragment_vendor_keys,
               max(fragments) AS max_fragments
        FROM grouped GROUP BY 1,2 ORDER BY 2,1
    """
    for query in (inventory_sql, sides_sql, availability_sql, fragments_sql):
        validate_quote_sql(query)
    return {
        "inventory": duckdb.sql(inventory_sql).df(),
        "sides": duckdb.sql(sides_sql).df(),
        "availability": duckdb.sql(availability_sql).df(),
        "fragments": duckdb.sql(fragments_sql).df(),
    }


def code_readiness() -> dict[str, dict[str, Any]]:
    stats = SimpleNamespace(
        hits=3, doubles=1, triples=0, home_runs=1, runs=2, rbi=5, walks=1
    )
    fantasy = FantasyScoring()
    game = GameSimulationResult(
        plate_appearances=4, hits=3, singles=1, doubles=1, triples=0,
        home_runs=1, runs=2, rbi=5, walks=1, strikeouts=0,
    )
    engine = object.__new__(MonteCarloEngine)
    official = {
        "total_bases": compute_actual_value(stats, "total_bases", fantasy) == 7.0,
        "rbi": compute_actual_value(stats, "rbi", fantasy) == 5.0,
        "hrr": compute_actual_value(stats, "hrr", fantasy) == 10.0,
    }
    simulation = {
        "total_bases": (
            "total_bases" in MonteCarloEngine.DEFAULT_THRESHOLDS
            and engine._category_value(game, "total_bases") == 7.0
        ),
        "rbi": (
            "rbi" in MonteCarloEngine.DEFAULT_THRESHOLDS
            and engine._category_value(game, "rbi") == 5.0
        ),
        "hrr": (
            "hrr" in MonteCarloEngine.DEFAULT_THRESHOLDS
            and engine._category_value(game, "hrr") == 10.0
        ),
    }
    projection = {
        "total_bases": True,  # Explicit candidate-only path, hash-bound by protocol.
        "rbi": "rbi" in PropEngine.HITTER_CATEGORIES,
        "hrr": "hrr" in PropEngine.HITTER_CATEGORIES,
    }
    return {
        market: {
            "official_outcome_implemented_exactly": official[market],
            "simulation_output_implemented_exactly": simulation[market],
            "projection_path_present": projection[market],
        }
        for market in TARGETS
    }


def market_summary(
    market: str,
    vendor_product: str | None,
    frames: dict[str, pd.DataFrame],
    code: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    inventory = frames["inventory"]
    inventory_names = set(inventory.market.astype(str))
    if market == "hrr":
        matches = sorted(
            name for name in inventory_names
            if "hit" in name and "run" in name and "rbi" in name
        )
        product_present = bool(matches)
        measured_products = matches
        target_rows = frames["availability"].iloc[0:0]
        side_rows = frames["sides"].iloc[0:0]
        fragment_rows = frames["fragments"].iloc[0:0]
    else:
        product_present = vendor_product in inventory_names
        measured_products = [vendor_product] if product_present else []
        target_rows = frames["availability"][frames["availability"].market == vendor_product]
        side_rows = frames["sides"][frames["sides"].market == vendor_product]
        fragment_rows = frames["fragments"][frames["fragments"].market == vendor_product]

    dk = target_rows[target_rows.book == "draftkings"]
    dk_entry_close = int(dk.entry_and_close_both.sum()) if len(dk) else 0
    measured_sides = sorted(set(side_rows.side.astype(str))) if len(side_rows) else []
    two_sided_measured = set(measured_sides) >= {"over", "under"}

    prerequisites = {
        "historical_product_present_in_locked_export": product_present,
        "over_and_under_observed_somewhere": two_sided_measured,
        "draftkings_two_sided_entry_and_close_observed": dk_entry_close > 0,
        "canonical_mlb_identity_contract_complete": False,
        "official_outcome_definition_complete": code[market]["official_outcome_implemented_exactly"],
        "book_specific_settlement_implementation_complete": False,
        "canonical_duplicate_fragment_contract_complete": False,
        "executable_price_evidence_complete": False,
        "simulation_output_complete": code[market]["simulation_output_implemented_exactly"],
        "projection_path_complete": code[market]["projection_path_present"],
    }
    reconstruction_ready = all(prerequisites.values())
    require(not reconstruction_ready, f"{market}: unexpectedly reconstruction-ready")
    blockers = [name for name, passed in prerequisites.items() if not passed]
    return {
        "market": market,
        "vendor_product_expected": vendor_product,
        "vendor_products_measured": measured_products,
        "price_contract": {
            "sides_observed": measured_sides,
            "two_sided_observed_somewhere": two_sided_measured,
            "draftkings_entry_and_close_two_sided_selections": dk_entry_close,
            "manufactured_side": False,
            "historical_executability_established": False,
        },
        "availability_by_book_line": frame_records(target_rows),
        "side_counts_by_book_line": frame_records(side_rows),
        "vendor_fragment_observations": frame_records(fragment_rows),
        "code_readiness": code[market],
        "official_outcome_definition": {
            "total_bases": "1B + 2*2B + 3*3B + 4*HR",
            "rbi": "official MLB RBI count; currently NOT implemented as a distinct compute_actual_value branch",
            "hrr": "official MLB hits + runs + RBI",
        }[market],
        "settlement": {
            "primary_base_rule_evidence_available": True,
            "market_and_side_specific_early_exit_grading_implemented": False,
            "vendor_result_used_as_truth": False,
        },
        "identity": {
            "source_key_measured": ["vendor_game_id", "start_time", "player", "line"],
            "canonical_mlb_market_key_built": False,
            "final_duplicate_relation_resolved": False,
        },
        "fallback_and_leakage_risks": {
            "total_bases": [
                "candidate-only projection path has not been joined to a certified market identity universe",
                "line contract rejects integer/push and unseen lines rather than approximating them",
            ],
            "rbi": [
                "compute_actual_value falls through to hits for category='rbi'",
                "MonteCarloEngine unknown-category fallback returns HRR, not RBI",
                "PropEngine exposes no RBI hitter category",
            ],
            "hrr": [
                "no matching historical vendor product was observed in the locked export",
                "runs/RBI use a hitter-level base-state approximation rather than a fully coupled lineup simulation",
            ],
        }[market],
        "prerequisites": prerequisites,
        "reconstruction_ready": reconstruction_ready,
        "betting_authorized": False,
        "blockers": blockers,
    }


def assert_report(report: dict[str, Any]) -> None:
    require(report["may_2026_read"] is False, "May read flag true")
    require(report["official_outcomes_read"] is False, "outcomes read flag true")
    require(report["vendor_result_read"] is False, "vendor result read flag true")
    require(report["betting_authorized"] is False, "betting authorization found")
    markets = report["market_reports"]
    require(set(markets) == set(TARGETS), "market set pooled or incomplete")
    for name, item in markets.items():
        require(item["market"] == name, f"{name}: report identity mismatch")
        require(item["price_contract"]["manufactured_side"] is False,
                f"{name}: manufactured side")
        require(item["price_contract"]["historical_executability_established"] is False,
                f"{name}: historical executability invented")
        require(item["reconstruction_ready"] == all(item["prerequisites"].values()),
                f"{name}: readiness contradicts prerequisites")
        require(item["betting_authorized"] is False, f"{name}: betting authorized")
    require(markets["rbi"]["code_readiness"]["official_outcome_implemented_exactly"] is False,
            "RBI outcome fallthrough was hidden")


def mutation_tests() -> None:
    validate_quote_sql("SELECT market, side, odds FROM quotes")
    for label, sql in (
        ("numeric vendor result", "SELECT result FROM quotes"),
        ("May partition", "SELECT * FROM 'mon=2026-05/part.parquet'"),
    ):
        try:
            validate_quote_sql(sql)
        except ValueError:
            print(f"[OK] MUTATION {label}")
        else:
            raise AssertionError(f"mutation did not fail: {label}")

    base = {
        "may_2026_read": False,
        "official_outcomes_read": False,
        "vendor_result_read": False,
        "betting_authorized": False,
        "market_reports": {
            name: {
                "market": name,
                "price_contract": {
                    "manufactured_side": False,
                    "historical_executability_established": False,
                },
                "code_readiness": {"official_outcome_implemented_exactly": name != "rbi"},
                "prerequisites": {"identity": False},
                "reconstruction_ready": False,
                "betting_authorized": False,
            }
            for name in TARGETS
        },
    }

    def must_fail(label: str, mutate) -> None:
        changed = json.loads(json.dumps(base))
        mutate(changed)
        try:
            assert_report(changed)
        except ValueError:
            print(f"[OK] MUTATION {label}")
            return
        raise AssertionError(f"mutation did not fail: {label}")

    must_fail("manufactured side", lambda x: x["market_reports"]["rbi"]["price_contract"].__setitem__("manufactured_side", True))
    must_fail("invented executability", lambda x: x["market_reports"]["total_bases"]["price_contract"].__setitem__("historical_executability_established", True))
    must_fail("unsupported RBI outcome relabeled", lambda x: x["market_reports"]["rbi"]["code_readiness"].__setitem__("official_outcome_implemented_exactly", True))
    must_fail("failed prerequisite marked ready", lambda x: x["market_reports"]["hrr"].__setitem__("reconstruction_ready", True))
    must_fail("market pooled/removed", lambda x: x["market_reports"].pop("hrr"))
    print("7/7")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", default="data/analysis/other_market_contracts_v1/protocol.json")
    parser.add_argument("--out-dir", default="data/analysis/other_market_contracts_v1")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        mutation_tests()
        return 0

    protocol_path = ROOT / args.protocol
    protocol = load_json(protocol_path)
    sources = verify_protocol(protocol)
    policy = load_json(ROOT / "config/ab_policy.json")
    entry_hours = int(policy["parameters"]["entry_hours"]["value"])
    frames = query_quotes(sql_source(sources), entry_hours)
    code = code_readiness()
    market_reports = {
        name: market_summary(name, product, frames, code)
        for name, product in TARGETS.items()
    }
    report = {
        "schema_version": 1,
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "OUTCOME_BLIND_READINESS_COMPLETE_RESEARCH_ONLY",
        "protocol": {
            "path": str(protocol_path.relative_to(ROOT)).replace("\\", "/"),
            "sha256": sha256(protocol_path),
        },
        "evaluator": {
            "path": str(Path(__file__).resolve().relative_to(ROOT)).replace("\\", "/"),
            "sha256": sha256(Path(__file__).resolve()),
        },
        "months_read": protocol["chronology"]["allowed_partitions"],
        "may_2026_read": False,
        "official_outcomes_read": False,
        "vendor_result_read": False,
        "entry_horizon_hours": entry_hours,
        "historical_executability_established": False,
        "market_inventory": frame_records(frames["inventory"]),
        "market_reports": market_reports,
        "betting_authorized": False,
        "inputs_sha256": protocol["inputs_sha256"],
    }
    assert_report(report)
    out_dir = ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, item in market_reports.items():
        path = out_dir / f"{name}_readiness.json"
        path.write_text(json.dumps(item, indent=2) + "\n", encoding="utf-8")
    report_path = out_dir / "report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    frames["inventory"].to_csv(out_dir / "market_inventory.csv", index=False)
    frames["sides"].to_csv(out_dir / "side_counts_by_book_line.csv", index=False)
    frames["availability"].to_csv(out_dir / "availability_by_book_line.csv", index=False)
    frames["fragments"].to_csv(out_dir / "vendor_fragment_observations.csv", index=False)
    print("OTHER MARKET CONTRACTS — OUTCOME-BLIND, RESEARCH ONLY")
    print(f"  report_sha256: {sha256(report_path)}")
    for name, item in market_reports.items():
        print(f"  {name}: reconstruction_ready={item['reconstruction_ready']} blockers={len(item['blockers'])}")
    print("  May read: NO; outcomes read: NO; vendor result read: NO; betting: NO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
