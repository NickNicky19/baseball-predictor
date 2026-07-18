#!/usr/bin/env python3
"""Offline regression harness for the MLB game-identity migration.

Each check is a former failure mode, not a test of the new implementation in
isolation.  It fails on the pre-migration code: that code had no game-scoped
actuals accessor, accepted no ``mlb_game_pk`` on records, overwrote duplicate
model keys, and had no strict vendor/roster crosswalk.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.build_smartstake_crosswalk import build_crosswalk  # noqa: E402
from src.data.mlb_api import MLBStatsAPI  # noqa: E402
from src.evaluation.backtest_engine import BacktestEngine, OutcomeRecord  # noqa: E402
from src.evaluation.identity_keys import MODEL_KEY, require_unique  # noqa: E402
from src.models.dataclasses import PropProjection  # noqa: E402
import run_pa_gate_verdict as pa_gate  # noqa: E402


PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    results.append((PASS if condition else FAIL, name, detail))


def expect_value_error(name: str, func) -> None:
    try:
        func()
    except ValueError:
        check(name, True)
    except Exception as exc:  # noqa: BLE001
        check(name, False, f"unexpected {exc!r}")
    else:
        check(name, False, "did not fail")


# 1. Pre-fix get_actuals_for_date used dict.update and retained only the late
#    doubleheader appearance.  The game-scoped accessor must retain both.
api = object.__new__(MLBStatsAPI)
api.get_final_game_pks = lambda _date: [1001, 1002]
api.get_game_boxscore_stats = lambda game_pk: (
    {77: {"game": game_pk}}, {88: {"game": game_pk}}
)
actuals = MLBStatsAPI.get_actuals_by_game_for_date(api, "2024-06-01")
check("A1 doubleheader actuals retain both games",
      actuals[1001][0][77]["game"] == 1001 and actuals[1002][0][77]["game"] == 1002)


def projection(game_pk: int, value: float) -> PropProjection:
    return PropProjection(player_id=77, player_name="Same Player", category="hits",
                          game_date="2024-06-01", projected_value=value,
                          confidence=0.0, mlb_game_pk=game_pk)


outcomes = [
    OutcomeRecord(77, "Same Player", "2024-06-01", "hits", 1.0, mlb_game_pk=1001),
    OutcomeRecord(77, "Same Player", "2024-06-01", "hits", 3.0, mlb_game_pk=1002),
]
report = BacktestEngine().evaluate_predictions([projection(1001, 1.0), projection(1002, 3.0)], outcomes)
check("A2 one player doubleheader scores two separate outcomes",
      report.matched_pairs == 2 and report.metrics_by_category["hits"].mae == 0.0,
      str(report.to_dict()))


duplicate_model = pd.DataFrame([
    {"mlb_game_pk": 1001, "player_id": 77, "category": "hits", "line": 0.5},
    {"mlb_game_pk": 1001, "player_id": 77, "category": "hits", "line": 0.5},
])
expect_value_error("A3 duplicate MODEL_KEY hard-fails", lambda: require_unique(
    duplicate_model, MODEL_KEY, "duplicate model"
))


# 4. A duplicate normalized name in one game roster must not be picked by
#    display name.  Strict crosswalk construction rejects it.
vendor = pd.DataFrame([{
    "vendor_game_id": "vendor-1", "start_time": "2024-06-01T23:10:00Z",
    "away_team": "Chicago Cubs", "home_team": "St. Louis Cardinals", "player": "J. Smith",
}])
games = pd.DataFrame([{
    "mlb_game_pk": 1001, "start_time": "2024-06-01T23:10:00Z",
    "away_team": "Chicago Cubs", "home_team": "St. Louis Cardinals",
}])
ambiguous_roster = pd.DataFrame([
    {"mlb_game_pk": 1001, "player_id": 10, "player_name": "J Smith"},
    {"mlb_game_pk": 1001, "player_id": 11, "player_name": "J. Smith"},
])
expect_value_error("A4 ambiguous vendor-player mapping hard-fails in strict mode", lambda: build_crosswalk(
    vendor, games, ambiguous_roster, strict=True
))

valid_roster = pd.DataFrame([{"mlb_game_pk": 1001, "player_id": 10, "player_name": "J. Smith"}])
crosswalk = build_crosswalk(vendor, games, valid_roster, strict=True)
check("A5 valid one-to-one vendor/MLB pair is preserved unchanged",
      len(crosswalk) == 1 and int(crosswalk.player_id.iloc[0]) == 10
      and int(crosswalk.mlb_game_pk.iloc[0]) == 1001,
      crosswalk.to_string(index=False))


# This is specifically the scoring-loader guard: old code printed that it
# "dropped" duplicates and continued, so this check fails on the broken path.
tmp = Path(tempfile.mkdtemp()) / "duplicate_probs.csv"
duplicate_model.assign(game_date="2024-06-01", sim_p_over=0.5).to_csv(tmp, index=False)
expect_value_error("A6 PA gate refuses duplicate model probabilities", lambda: pa_gate._load_probs(tmp, "sim_p_over"))


n_fail = sum(status == FAIL for status, _, _ in results)
width = max(len(name) for _, name, _ in results)
for status, name, detail in results:
    line = f"[{status}] {name.ljust(width)}"
    if status == FAIL:
        line += f" -- {detail}"
    print(line)
print(f"\n{len(results) - n_fail}/{len(results)} checks passed")
raise SystemExit(1 if n_fail else 0)
