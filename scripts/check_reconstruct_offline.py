#!/usr/bin/env python3
"""
Offline end-to-end check for run_reconstruct_date.py (A2).

Runs the REAL reconstruct() pipeline against canned MLB API fixtures — no
network needed — so the wiring can be validated anywhere (CI, sandbox,
laptop) before spending real API calls.

The critical assertion is the LEAKAGE CANARY: every player's fixture game
log contains an absurd stat line ON the target date (5-for-5 with 5 HR;
12-K start). If any of that bleeds into the as-of features, point-in-time
reconstruction is broken and this harness fails loudly.

Usage:  python scripts/check_reconstruct_offline.py
Exit 0 = all checks pass.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from run_reconstruct_date import AsOfMLBAPI, ResolutionLog, reconstruct
from src.data.point_in_time import PointInTimeStats
from src.learning.retrain_runner import RetrainRunner

TARGET = "2025-06-15"
SEASON = 2025
GAME_PK = 100
AWAY_HITTERS = list(range(1001, 1010))
HOME_HITTERS = list(range(2001, 2010))
AWAY_SP, HOME_SP = 9001, 9002

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def hitting_log(player_id: int) -> dict:
    """10 pre-date games + a LEAKAGE CANARY game ON the target date."""
    hits_per_game = 2 if player_id == 1001 else 1  # one hot hitter for variance
    splits = []
    for day in range(1, 11):  # 2025-06-01 .. 06-10, all strictly pre-date
        splits.append(
            {
                "date": f"2025-06-{day:02d}",
                "stat": {
                    "plateAppearances": 4,
                    "atBats": 4,
                    "hits": hits_per_game,
                    "doubles": 0,
                    "triples": 0,
                    "homeRuns": 0,
                    "rbi": 0,
                    "runs": 0,
                    "baseOnBalls": 0,
                    "strikeOuts": 1,
                },
            }
        )
    # CANARY: absurd line ON the as-of date — must be excluded (strict <).
    splits.append(
        {
            "date": TARGET,
            "stat": {
                "plateAppearances": 5,
                "atBats": 5,
                "hits": 5,
                "homeRuns": 5,
                "doubles": 0,
                "triples": 0,
                "rbi": 15,
                "runs": 5,
                "baseOnBalls": 0,
                "strikeOuts": 0,
            },
        }
    )
    return {"stats": [{"splits": splits}]}


def pitching_log(player_id: int) -> dict:
    """5 pre-date starts + a 12-K CANARY start ON the target date."""
    splits = [
        {
            "date": f"2025-06-{day:02d}",
            "stat": {
                "inningsPitched": "6.0",
                "strikeOuts": 7,
                "baseOnBalls": 2,
                "homeRuns": 1,
            },
        }
        for day in (1, 4, 7, 10, 13)
    ]
    splits.append(
        {
            "date": TARGET,
            "stat": {
                "inningsPitched": "9.0",
                "strikeOuts": 12,
                "baseOnBalls": 0,
                "homeRuns": 0,
            },
        }
    )
    return {"stats": [{"splits": splits}]}


def schedule_fixture() -> dict:
    return {
        "dates": [
            {
                "games": [
                    {
                        "gamePk": GAME_PK,
                        "venue": {"name": "Coors Field"},
                        "status": {"abstractGameState": "Final", "codedGameState": "F"},
                        "teams": {
                            "away": {
                                "team": {"name": "San Diego Padres"},
                                "probablePitcher": {
                                    "id": AWAY_SP,
                                    "fullName": "Away Ace",
                                    "pitchHand": {"code": "R"},
                                },
                            },
                            "home": {
                                "team": {"name": "Colorado Rockies"},
                                "probablePitcher": {
                                    "id": HOME_SP,
                                    "fullName": "Home Ace",
                                    "pitchHand": {"code": "L"},
                                },
                            },
                        },
                    }
                ]
            }
        ]
    }


def feed_fixture() -> dict:
    """Live feed: persisted battingOrder + boxscore actuals for the date."""

    def batting_actual(i: int) -> dict:
        return {
            "stats": {
                "batting": {
                    "plateAppearances": 4,
                    "atBats": 4,
                    "hits": 1 + (i % 2),  # alternate 1 and 2 hits
                    "homeRuns": 1 if i % 5 == 0 else 0,
                    "rbi": 1 if i % 3 == 0 else 0,
                    "runs": 1 if i % 2 == 0 else 0,
                    "baseOnBalls": 0,
                    "strikeOuts": 1,
                    "doubles": 0,
                    "triples": 0,
                }
            }
        }

    players_away = {f"ID{pid}": batting_actual(i) for i, pid in enumerate(AWAY_HITTERS)}
    players_home = {f"ID{pid}": batting_actual(i) for i, pid in enumerate(HOME_HITTERS)}
    players_away[f"ID{AWAY_SP}"] = {
        "stats": {"pitching": {"inningsPitched": "6.0", "strikeOuts": 8, "baseOnBalls": 1, "homeRuns": 1}}
    }
    players_home[f"ID{HOME_SP}"] = {
        "stats": {"pitching": {"inningsPitched": "5.0", "strikeOuts": 4, "baseOnBalls": 3, "homeRuns": 2}}
    }
    return {
        "liveData": {
            "boxscore": {
                "teams": {
                    "away": {"battingOrder": AWAY_HITTERS, "players": players_away},
                    "home": {"battingOrder": HOME_HITTERS, "players": players_home},
                }
            }
        }
    }


def person_fixture(player_id: int) -> dict:
    kind = "Pitcher" if player_id in (AWAY_SP, HOME_SP) else "Hitter"
    return {
        "people": [
            {
                "fullName": f"{kind} {player_id}",
                "batHand": {"code": "L" if player_id % 3 == 0 else "R"},
                "pitchHand": {"code": "R"},
            }
        ]
    }


class FixtureAPI(AsOfMLBAPI):
    """AsOfMLBAPI with the HTTP boundary replaced by canned responses."""

    def _get(self, url: str, params=None):
        params = params or {}
        if "/schedule" in url:
            return schedule_fixture()
        if f"/game/{GAME_PK}/feed/live" in url:
            return feed_fixture()
        if url.endswith("/stats") and params.get("stats") == "gameLog":
            pid = int(url.split("/people/")[1].split("/")[0])
            return pitching_log(pid) if params.get("group") == "pitching" else hitting_log(pid)
        if "/people/" in url:
            pid = int(url.rstrip("/").split("/people/")[1].split("/")[0])
            return person_fixture(pid)
        raise AssertionError(f"Unfixtured URL: {url} params={params}")


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def main() -> int:
    config = copy.deepcopy(RetrainRunner.load_config(None))
    config.setdefault("simulation", {})["n_sims"] = 400  # keep the check fast

    reslog = ResolutionLog()
    api = FixtureAPI(as_of_date=TARGET, pit=None, reslog=reslog, season=SEASON)  # type: ignore[arg-type]
    pit = PointInTimeStats(mlb_api=api, season=SEASON)
    api.pit = pit  # PIT fetches game logs through the same fixtured _get

    print("LEAKAGE CANARY — as-of stats must exclude the target-date game")
    season_h, recent_h = api.get_hitting_stats(1002)
    check("hitter PA excludes canary (40, not 45)", season_h.pa == 40, f"got {season_h.pa}")
    check("hitter AVG from pre-date rows only (.250)", abs(season_h.avg - 0.250) < 1e-9, f"got {season_h.avg}")
    check("hitter HR excludes canary 5-HR game (0)", season_h.home_runs == 0, f"got {season_h.home_runs}")
    hot_season, _ = api.get_hitting_stats(1001)
    check("per-player variance flows (.500 hitter)", abs(hot_season.avg - 0.500) < 1e-9, f"got {hot_season.avg}")

    season_p, recent_p = api.get_pitching_stats(AWAY_SP)
    check("pitcher IP excludes canary (30.0, not 39.0)", abs(season_p.innings_pitched - 30.0) < 1e-9, f"got {season_p.innings_pitched}")
    check("pitcher K excludes canary (35, not 47)", season_p.strikeouts == 35, f"got {season_p.strikeouts}")

    print("\nAS-OF EXPECTED IP — pitcher opportunity input is leakage-safe")
    pitchers = api.get_pitchers_for_date(TARGET)
    check("two probables built", len(pitchers) == 2, f"got {len(pitchers)}")
    if pitchers:
        ip = pitchers[0].expected_innings
        check("expected_innings from pre-date IP/GS (6.0)", abs(ip - 6.0) < 1e-9, f"got {ip}")

    print("\nFULL PIPELINE — reconstruct() end-to-end on fixtures")
    report = reconstruct(TARGET, config=config, api=api, pit=pit, reslog=reslog, include_pitchers=True)

    check("no pipeline error", "error" not in report, str(report.get("error")))
    check("18 bundles (both lineups)", report["slate"]["bundles"] == 18, f"got {report['slate']['bundles']}")
    hb = report["hitter_backtest"]
    check("54 matched hitter pairs (18 x 3 cats)", hb["matched_pairs"] == 54, f"got {hb['matched_pairs']}")
    check(
        "all three hitter categories scored",
        set(hb["metrics"]) == {"hits", "hrr", "home_runs"} and all(m["n_samples"] == 18 for m in hb["metrics"].values()),
        str({k: v["n_samples"] for k, v in hb["metrics"].items()}),
    )
    pb = report["pitcher_backtest"]
    check("pitcher K scored for both starters", pb and pb["metrics"]["strikeouts"]["n_samples"] == 2, str(pb))

    res = report["resolution"]
    def counts(src: str) -> dict:
        return res.get(src, {}).get("counts", {})

    print("\nRESOLUTION LOG — the A3 scoping evidence")
    check("lineups: 18 confirmed (persisted battingOrder)", counts("lineup").get("status_confirmed") == 18, str(counts("lineup")))
    check("opposing pitcher resolved for all 18", counts("opposing_pitcher").get("resolved") == 18, str(counts("opposing_pitcher")))
    check("statcast: 18 baseline fallbacks (policy)", counts("statcast_profiles").get("baseline_fallback") == 18, str(counts("statcast_profiles")))
    check("platoon splits neutralized (policy)", counts("platoon_splits").get("neutralized", 0) >= 18, str(counts("platoon_splits")))
    check("injury feed bypassed (policy)", counts("injury_feed").get("bypassed", 0) >= 18, str(counts("injury_feed")))
    check("park factors resolved (Coors in config)", counts("park_factors").get("resolved") == 18, str(counts("park_factors")))
    check("rolling PIT features resolved", counts("rolling_features_pit").get("resolved") == 18, str(counts("rolling_features_pit")))
    check("hitter actuals resolved for all 18", counts("actual_outcomes").get("resolved") == 18, str(counts("actual_outcomes")))
    check("weather defaulted (no network here)", counts("weather").get("default", 0) >= 1, str(counts("weather")))

    check("report is JSON-serializable", bool(json.dumps(report)))

    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
