#!/usr/bin/env python3
"""Build deterministic, non-authorizing Statcast sample and full request plans."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.shared_pa_statcast_source_v1 import (
    canonical_json_bytes,
    load_contract,
    sha256_bytes,
    sha256_file,
)


SAMPLE_DATE = "2023-07-25"
QUERY_BASE = {
    "all": "true",
    "hfPT": "",
    "hfAB": "",
    "hfBBT": "",
    "hfPR": "",
    "hfZ": "",
    "stadium": "",
    "hfBBL": "",
    "hfNewZones": "",
    "hfGT": "R|",
    "hfSea": "",
    "hfSit": "",
    "player_type": "pitcher",
    "hfOuts": "",
    "opponent": "",
    "pitcher_throws": "",
    "batter_stands": "",
    "hfSA": "",
    "hfInn": "",
    "team": "",
    "position": "",
    "hfRO": "",
    "home_road": "",
    "hfFlag": "",
    "metric_1": "",
    "min_pitches": "0",
    "min_results": "0",
    "group_by": "name",
    "sort_col": "pitches",
    "player_event_sort": "h_launch_speed",
    "sort_order": "desc",
    "min_abs": "0",
    "type": "details",
}


def _safe_new_dir(path: Path) -> Path:
    target = Path(os.path.abspath(os.fspath(path)))
    if target.exists():
        raise ValueError("output directory already exists")
    target.mkdir(parents=True, exist_ok=False)
    return target


def _write(path: Path, value: object) -> None:
    with path.open("xb") as handle:
        handle.write(canonical_json_bytes(value))


def _request(official_date: str, games: dict[int, dict[str, int]], contract: dict) -> dict:
    query = dict(QUERY_BASE)
    query.update({"game_date_gt": official_date, "game_date_lt": official_date})
    query = {key: query[key] for key in sorted(query)}
    base = f"{contract['source']['scheme']}://{contract['source']['host']}{contract['source']['path']}"
    return {
        "request_id": f"statcast-{official_date}",
        "method": "GET",
        "url": base,
        "query": query,
        "full_url": f"{base}?{urlencode(query)}",
        "expected": {
            "official_date": official_date,
            "certified_games": [
                {
                    "game_pk": game_pk, **games[game_pk],
                    "home_team_code": contract["team_source_codes"][str(games[game_pk]["home_team_id"])],
                    "away_team_code": contract["team_source_codes"][str(games[game_pk]["away_team_id"])],
                }
                for game_pk in sorted(games)
            ],
        },
    }


def _plan(kind: str, dates: list[str], games: dict[str, dict[int, dict[str, int]]], contract: dict, contract_sha: str) -> dict:
    requests = [_request(value, games[value], contract) for value in dates]
    return {
        "schema_version": "shared-pa-statcast-request-plan-v1",
        "status": "PREPARED_NOT_AUTHORIZED",
        "plan_kind": kind,
        "research_only": True,
        "external_requests_permitted": False,
        "season": 2023,
        "historical_scope": contract["historical_scope"],
        "source_contract_sha256": contract_sha,
        "request_policy": contract["transport_policy"],
        "requests": requests,
        "request_count": len(requests),
        "certified_game_count": sum(len(games[value]) for value in dates),
        "protected_boundaries": contract["protected_boundaries"],
    }


def build(label_release: Path, contract_path: Path, output_dir: Path) -> dict:
    contract = load_contract(contract_path)
    manifest = json.loads((label_release / "release_manifest.json").read_text(encoding="utf-8"))
    expected_release = contract["historical_scope"]["certified_game_universe_release_sha256"]
    if manifest.get("release_sha256") != expected_release:
        raise ValueError("certified label release identity differs")
    start = contract["historical_scope"]["source_start"]
    end = contract["historical_scope"]["source_end"]
    games: dict[str, dict[int, dict[str, int]]] = defaultdict(dict)
    with (label_release / "canonical_rows.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            official_date = row["official_date"]
            if start <= official_date <= end:
                game_pk = int(row["game_pk"])
                identity = games[official_date].setdefault(game_pk, {})
                identity[f"{row['team_side']}_team_id"] = int(row["team_id"])
    canonical_games = {key: {game_pk: value[game_pk] for game_pk in sorted(value)} for key, value in sorted(games.items())}
    for by_game in canonical_games.values():
        for identity in by_game.values():
            if set(identity) != {"home_team_id", "away_team_id"}:
                raise ValueError("certified game lacks exactly one home and away team identity")
    dates = sorted(canonical_games)
    if len(dates) != contract["transport_policy"]["expected_full_request_count"]:
        raise ValueError("schedule-derived Statcast request count differs")
    if SAMPLE_DATE not in canonical_games:
        raise ValueError("sample date is absent from certified game universe")
    contract_sha = sha256_file(contract_path)
    full = _plan("FULL_CAPTURE_LATER", dates, canonical_games, contract, contract_sha)
    sample = _plan("BOUNDED_SAMPLE", [SAMPLE_DATE], canonical_games, contract, contract_sha)
    root = _safe_new_dir(output_dir)
    _write(root / "full_request_plan.json", full)
    _write(root / "sample_request_plan.json", sample)
    summary = {
        "schema_version": "shared-pa-statcast-source-preparation-summary-v1",
        "status": "PREPARED_NOT_AUTHORIZED",
        "source_contract_sha256": contract_sha,
        "label_release_sha256": expected_release,
        "full_request_plan_sha256": sha256_file(root / "full_request_plan.json"),
        "sample_request_plan_sha256": sha256_file(root / "sample_request_plan.json"),
        "full_request_count": len(full["requests"]),
        "full_certified_game_count": full["certified_game_count"],
        "sample_date": SAMPLE_DATE,
        "sample_game_count": sample["certified_game_count"],
        "external_request_count": 0,
        "expected_contact_direct_fields": "INELIGIBLE",
        "feature_fitting_performed": False,
        "market_scoring_performed": False,
        "may_2026_accessed": False,
    }
    _write(root / "source_preparation_summary.json", summary)
    package = {
        "schema_version": "shared-pa-statcast-source-preparation-manifest-v1",
        "status": "SOURCE_PREPARATION_COMPLETE_NOT_AUTHORIZED",
        "files": [
            {"path": name, "size": (root / name).stat().st_size, "sha256": sha256_file(root / name)}
            for name in ("full_request_plan.json", "sample_request_plan.json", "source_preparation_summary.json")
        ],
        "external_request_count": 0,
    }
    package["package_sha256"] = sha256_bytes(canonical_json_bytes(package))
    _write(root / "package_manifest.json", package)
    return summary | {"package_sha256": package["package_sha256"]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label-release", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.label_release, args.contract, args.output_dir), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
