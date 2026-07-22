#!/usr/bin/env python3
"""
Offline end-to-end check for the A3 training-set builder — no network.

Fixtures sit UNDER the disk cache (at _network_get), so the cache, rate
limiter, manifest resume, shard writing, and assembly all run for real.

Canaries: every game log contains stat lines ON the build dates; as-of
columns must exclude them. The same player is built on two consecutive
dates to prove as-of features ADVANCE correctly (day 2 includes day 1).

Usage:  python scripts/check_training_builder_offline.py
Exit 0 = all checks pass.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from src.data.http_cache import RateLimiter
from src.data.mlb_api import MLBStatsAPI
from src.data.point_in_time import PointInTimeStats
from src.learning.retrain_runner import RetrainRunner
from src.learning.training_set_builder import (
    CachedMLBAPI,
    TrainingSetBuilder,
)
from src.utils.errors import DataFetchError

SEASON = 2025
DATE1, DATE2 = "2025-06-15", "2025-06-16"
AWAY = list(range(1001, 1010))
HOME = list(range(2001, 2010))
AWAY_SUB = 1099
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


def hitting_log(pid: int) -> dict:
    splits = [
        {
            "date": f"2025-06-{d:02d}",
            "stat": {
                "plateAppearances": 4, "atBats": 4, "hits": 1, "doubles": 0,
                "triples": 0, "homeRuns": 0, "rbi": 0, "runs": 0,
                "baseOnBalls": 0, "strikeOuts": 1,
            },
        }
        for d in range(1, 11)
    ]
    # Canaries ON both build dates — must never enter as-of features.
    for d in (15, 16):
        splits.append(
            {
                "date": f"2025-06-{d:02d}",
                "stat": {
                    "plateAppearances": 5, "atBats": 5, "hits": 5, "homeRuns": 5,
                    "doubles": 0, "triples": 0, "rbi": 15, "runs": 5,
                    "baseOnBalls": 0, "strikeOuts": 0,
                },
            }
        )
    return {"stats": [{"splits": splits}]}


def pitching_log(pid: int) -> dict:
    splits = [
        {
            "date": f"2025-06-{d:02d}",
            "stat": {
                "inningsPitched": "6.0",
                "strikeOuts": 7,
                "baseOnBalls": 2,
                "homeRuns": 1,
                "gamesStarted": 1,
            },
        }
        for d in (1, 4, 7, 10, 13)
    ]
    splits.append({"date": DATE1, "stat": {"inningsPitched": "9.0", "strikeOuts": 12, "baseOnBalls": 0, "homeRuns": 0, "gamesStarted": 1}})
    splits.append({"date": DATE2, "stat": {"inningsPitched": "8.0", "strikeOuts": 13, "baseOnBalls": 1, "homeRuns": 0, "gamesStarted": 1}})
    return {"stats": [{"splits": splits}]}


def game(game_pk: int, game_type: str, state: str, with_probables: bool) -> dict:
    g = {
        "gamePk": game_pk,
        "gameType": game_type,
        "venue": {"name": "Coors Field"},
        "status": {"abstractGameState": state, "codedGameState": "F" if state == "Final" else "I"},
        "teams": {
            "away": {"team": {"name": "San Diego Padres"}},
            "home": {"team": {"name": "Colorado Rockies"}},
        },
    }
    if with_probables:
        g["teams"]["away"]["probablePitcher"] = {"id": AWAY_SP, "fullName": "Away Ace"}
        g["teams"]["home"]["probablePitcher"] = {"id": HOME_SP, "fullName": "Home Ace"}
    return g


def schedule(date_str: str) -> dict:
    if date_str == DATE1:
        games = [
            game(100, "R", "Final", with_probables=True),
            game(101, "S", "Final", with_probables=True),   # spring: must be skipped
            game(102, "R", "Live", with_probables=True),    # not final: must be skipped
        ]
    elif date_str == DATE2:
        games = [game(200, "R", "Final", with_probables=False)]  # forces actual-starter fallback
    else:
        return {"dates": []}
    return {"dates": [{"games": games}]}


def feed(game_pk: int) -> dict:
    official_date = DATE1 if game_pk == 100 else DATE2
    def batting(i: int) -> dict:
        return {"stats": {"batting": {
            "plateAppearances": 4, "atBats": 4, "hits": 1 + (i % 2),
            "homeRuns": 1 if i % 5 == 0 else 0, "rbi": i % 3, "runs": i % 2,
            "baseOnBalls": 0, "strikeOuts": 1, "doubles": 0, "triples": 0,
        }}}

    away_players = {f"ID{pid}": batting(i) for i, pid in enumerate(AWAY)}
    home_players = {f"ID{pid}": batting(i) for i, pid in enumerate(HOME)}
    for slot, pid in enumerate(AWAY, start=1):
        away_players[f"ID{pid}"]["battingOrder"] = f"{slot}00"
    for slot, pid in enumerate(HOME, start=1):
        home_players[f"ID{pid}"]["battingOrder"] = f"{slot}00"
    away_players[f"ID{AWAY_SUB}"] = batting(99)
    away_players[f"ID{AWAY_SUB}"]["battingOrder"] = "101"
    away_players[f"ID{AWAY_SP}"] = {"stats": {"pitching": {"inningsPitched": "6.0", "strikeOuts": 8, "baseOnBalls": 1, "homeRuns": 1}}}
    home_players[f"ID{HOME_SP}"] = {"stats": {"pitching": {"inningsPitched": "5.0", "strikeOuts": 4, "baseOnBalls": 3, "homeRuns": 2}}}
    return {
        "gameData": {
            "datetime": {"officialDate": official_date},
            "weather": {"condition": "Sunny", "temp": "85", "wind": "10 mph, Out To CF"},
        },
        "liveData": {"boxscore": {
            "officials": [{"officialType": "Home Plate", "official": {"id": 501, "fullName": "Test Ump"}}],
            "teams": {
                "away": {"battingOrder": [AWAY_SUB, *AWAY[1:]], "pitchers": [AWAY_SP, 9101], "players": away_players},
                "home": {"battingOrder": HOME, "pitchers": [HOME_SP, 9102], "players": home_players},
            },
        }},
    }


def person(pid: int) -> dict:
    bats = "L" if pid == 1001 else "R"
    throws = "L" if pid == HOME_SP else "R"
    return {"people": [{"fullName": f"Player {pid}", "batSide": {"code": bats}, "pitchHand": {"code": throws}}]}


class FixtureAPI(CachedMLBAPI):
    """Fixtures UNDER the disk cache: cache/limiter/manifest all run for real."""

    network_calls = 0

    def _network_get(self, url: str, params=None):
        FixtureAPI.network_calls += 1
        params = params or {}
        if "/schedule" in url:
            return schedule(params.get("date", ""))
        if "/feed/live" in url:
            pk = int(url.split("/game/")[1].split("/")[0])
            assert pk in (100, 200), f"feed fetched for skipped game {pk}"
            return feed(pk)
        if url.endswith("/stats") and params.get("stats") == "gameLog":
            pid = int(url.split("/people/")[1].split("/")[0])
            return pitching_log(pid) if params.get("group") == "pitching" else hitting_log(pid)
        if "/people/" in url:
            pid = int(url.rstrip("/").split("/people/")[1].split("/")[0])
            return person(pid)
        raise AssertionError(f"Unfixtured URL: {url} params={params}")


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="a3check_"))
    cache_dir, out_dir = work / "cache", work / "training"
    config = RetrainRunner.load_config(None)

    def make_builder() -> TrainingSetBuilder:
        api = FixtureAPI(season=SEASON, cache_dir=cache_dir, rate_limiter=RateLimiter(0))
        pit = PointInTimeStats(mlb_api=api, season=SEASON)
        return TrainingSetBuilder(api, pit, config, out_dir=out_dir)

    try:
        print("DUAL-TEAM IDENTITY CANARY")
        dual_player = 643376
        dual_away = {
            f"ID{100 + slot}": {"battingOrder": f"{slot}00"}
            for slot in range(1, 10)
        }
        dual_away.pop("ID107")
        dual_away[f"ID{dual_player}"] = {"battingOrder": "700"}
        dual_home = {
            f"ID{200 + slot}": {"battingOrder": f"{slot}00"}
            for slot in range(1, 10)
        }
        dual_home[f"ID{dual_player}"] = {"battingOrder": "701"}
        dual_feed = {"liveData": {"boxscore": {"teams": {
            "away": {"players": dual_away},
            "home": {"players": dual_home},
        }}}}
        dual_order = MLBStatsAPI.completed_game_original_batting_order_from_feed(
            746942, dual_feed
        )
        check(
            "dual-team player remains the away slot-7 original starter",
            dual_order["away"][6] == dual_player,
        )
        check(
            "dual-team player does not alter the home original starters",
            dual_order["home"] == list(range(201, 210)),
        )
        try:
            MLBStatsAPI.completed_game_batting_roles_from_feed(746942, dual_feed)
        except DataFetchError as exc:
            ambiguous_failed = "player-keyed role projection is ambiguous" in str(exc)
        else:
            ambiguous_failed = False
        check(
            "mutation: player-keyed dual-team role projection hard-fails",
            ambiguous_failed,
        )

        print("BUILD — two dates, mixed game types")
        b = make_builder()
        results = b.build_dates([DATE1, DATE2])
        r1, r2 = results[0], results[1]
        check("date1: 1 game built (R + Final only)", r1.games == 1, f"got {r1.games}")
        check("date1: spring game skipped", r1.skipped_non_regular == 1, f"got {r1.skipped_non_regular}")
        check("date1: live game skipped", r1.skipped_not_final == 1, f"got {r1.skipped_not_final}")
        check("date1: 18 hitter rows", r1.hitter_rows == 18, f"got {r1.hitter_rows}")
        h1_ids = set(pd.read_csv(out_dir / str(SEASON) / f"hitters_{DATE1}.csv").player_id)
        check(
            "date1: original starter retained and final substitute excluded",
            AWAY[0] in h1_ids and AWAY_SUB not in h1_ids,
            f"starter={AWAY[0] in h1_ids} substitute={AWAY_SUB in h1_ids}",
        )
        check("date1: 2 pitcher rows (actual starters)", r1.pitcher_rows == 2, f"got {r1.pitcher_rows}")
        check("date2: rows built without probables", r2.hitter_rows == 18 and r2.pitcher_rows == 2)

        print("\nLEAKAGE CANARIES + AS-OF ADVANCEMENT")
        h1 = pd.read_csv(out_dir / str(SEASON) / f"hitters_{DATE1}.csv")
        h2 = pd.read_csv(out_dir / str(SEASON) / f"hitters_{DATE2}.csv")
        p1 = pd.read_csv(out_dir / str(SEASON) / f"pitchers_{DATE1}.csv")
        p2 = pd.read_csv(out_dir / str(SEASON) / f"pitchers_{DATE2}.csv")

        row1 = h1[h1.player_id == 1002].iloc[0]
        row2 = h2[h2.player_id == 1002].iloc[0]
        check("date1 pit_pa excludes both canaries (40)", row1.pit_pa == 40, f"got {row1.pit_pa}")
        check("date1 pit_hr is 0 (5-HR canary excluded)", row1.pit_hr == 0, f"got {row1.pit_hr}")
        check("date2 pit_pa advanced to include date1 (45)", row2.pit_pa == 45, f"got {row2.pit_pa}")
        check("date2 pit_hr includes date1 canary game (5)", row2.pit_hr == 5, f"got {row2.pit_hr}")
        sp1 = p1[p1.player_id == AWAY_SP].iloc[0]
        sp2 = p2[p2.player_id == AWAY_SP].iloc[0]
        check("date1 SP as-of IP=30.0 K=35", sp1.pit_ip == 30.0 and sp1.pit_k == 35, f"got {sp1.pit_ip}/{sp1.pit_k}")
        check("date2 SP as-of advanced IP=39.0 K=47", sp2.pit_ip == 39.0 and sp2.pit_k == 47, f"got {sp2.pit_ip}/{sp2.pit_k}")

        print("\nROW CONTENT")
        check("opp_sp_source=probable on date1", set(h1.opp_sp_source) == {"probable"}, str(set(h1.opp_sp_source)))
        check("opp_sp_source=actual_starter on date2", set(h2.opp_sp_source) == {"actual_starter"}, str(set(h2.opp_sp_source)))
        check("away hitters face home SP", set(h1[h1.is_home == 0].opp_sp_id) == {HOME_SP})
        lefty = h1[h1.player_id == 1001].iloc[0]
        righty = h1[h1.player_id == 1002].iloc[0]
        check("platoon: L bat vs L SP = 0", lefty.platoon_adv == 0, f"got {lefty.platoon_adv}")
        check("platoon: R bat vs L SP = 1", righty.platoon_adv == 1, f"got {righty.platoon_adv}")
        check("weather parsed from feed (85F, wind 10)", row1.weather_temp == 85.0 and row1.weather_wind == 10.0 and row1.weather_resolved == 1)
        check("umpire parsed from officials (501)", row1.umpire_id == 501 and row1.umpire_resolved == 1)
        check("park resolved (Coors in config)", row1.park_resolved == 1)
        check("hitter outcomes recorded", row1.out_pa == 4 and row1.out_hits in (1, 2))
        check("pitcher outcome recorded (6 IP, 8 K)", sp1.out_ip == 6.0 and sp1.out_k == 8)
        check("recent_ip_per_gs computed", sp1.pit_recent_ip_per_gs == 6.0, f"got {sp1.pit_recent_ip_per_gs}")
        check("has_prior_data flag set", row1.has_prior_data == 1)

        print("\nRESUME — manifest skip, then disk-cache hit on rebuild")
        calls_after_first = FixtureAPI.network_calls
        b2 = make_builder()
        r_again = b2.build_dates([DATE1, DATE2])
        check("second run builds nothing (manifest)", len(r_again) == 0, f"built {len(r_again)}")
        check("second run made zero network calls", FixtureAPI.network_calls == calls_after_first, f"{FixtureAPI.network_calls} vs {calls_after_first}")
        b3 = make_builder()
        b3.build_dates([DATE1], rebuild=True)
        check("rebuild run served entirely from disk cache", FixtureAPI.network_calls == calls_after_first, f"{FixtureAPI.network_calls} vs {calls_after_first}")

        print("\nASSEMBLY")
        # A corrected postponed/suspended-game date can produce zero rows on a
        # shard that was previously non-empty.  The empty write must replace
        # it, otherwise assembly silently restores the stale player-game rows.
        stale_date = "2025-06-17"
        b3._write_shard("hitters", stale_date, list(h1.columns), [h1.iloc[0].to_dict()])
        b3._write_shard("hitters", stale_date, list(h1.columns), [])
        stale = pd.read_csv(out_dir / str(SEASON) / f"hitters_{stale_date}.csv")
        check("empty shard overwrites prior stale rows", stale.empty, f"got {len(stale)} rows")
        outputs = b3.assemble([SEASON])
        hit = pd.read_csv(outputs["hitters"])
        pit_df = pd.read_csv(outputs["pitchers"])
        check("assembled hitters: 36 rows, gz-readable", len(hit) == 36 and str(outputs["hitters"]).endswith(".csv.gz"), f"got {len(hit)}")
        check("assembled pitchers: 4 rows", len(pit_df) == 4, f"got {len(pit_df)}")
        check("builder_schema stamped on every row", set(hit.builder_schema) == {"a3.2"})

        print(f"\n{PASS} passed, {FAIL} failed")
        return 0 if FAIL == 0 else 1
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
