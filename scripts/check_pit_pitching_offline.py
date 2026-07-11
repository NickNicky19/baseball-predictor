"""
Point-in-time PITCHING offline harness — the guard for the B4 POINT-IN-TIME BUG.

Run before any gate smoke test:
    python scripts/check_pit_pitching_offline.py

WHAT THIS GUARDS
----------------
src/data/point_in_time.py::_aggregate_pitching used to (1) never set `games`
and (2) set `games_started=len(rows)` (total appearances, not real starts).
Because RoleAwareInningsEstimator.estimate_detailed returns role="unknown"
whenever `games <= 0`, EVERY reconstructed pitcher fell back to
default_innings -- so the B4 gate would have silently tested NOTHING while
appearing to pass. Defect (2) was masked by defect (1); fixing only (1) would
have made start_ratio ~1.0 and labelled every reliever a STARTER, which is
worse than "unknown". Both are fixed together, and this harness is what proves
it stays fixed.

DESIGN (deliberately mirrors check_b4_offline.py's discipline, plus the
OFFLINE HARNESS IMPORT-PATH BLIND SPOT lesson from [BUG1]):

  * It drives the REAL PointInTimeStats._fetch_game_log parser and the REAL
    _aggregate_pitching, and feeds their output to the REAL
    RoleAwareInningsEstimator. Nothing is reimplemented -- a harness that
    restates the fix cannot catch the fix regressing. The network is stubbed
    at the mlb_api._get seam only.
  * It imports the ACTUAL production modules the way real callers do
    (import src.data.point_in_time / src.data.mlb_api), so a circular-import
    or package-graph regression fails loudly here (check 0a), exactly the line
    that would have caught BUG 1.

THE CENTRAL ASSERTIONS (the ones the gate depends on):
  reliever  -> games > 0, games_started < games, start_ratio ~ 0 (NOT ~1.0),
               role in {opener, bulk}, expected_innings < the legacy 4.0 floor
  starter   -> high start_ratio, role == "starter"
If a reliever ever comes back start_ratio ~1.0 again, or games == 0, the gate
is testing nothing and this harness must fail.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# Make repo root importable when run from anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PASS = "PASS"
FAIL = "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((PASS if cond else FAIL, name, detail))


# ---------------------------------------------------------------------------
# GROUP 0 — PACKAGE-GRAPH IMPORT SMOKE (standing note from [BUG1]).
# Import the real production modules through the real package graph.
# ---------------------------------------------------------------------------
import importlib  # noqa: E402

try:
    _pit_mod = importlib.import_module("src.data.point_in_time")
    importlib.import_module("src.data.mlb_api")
    check("0a plain `import src.data.point_in_time` through the package graph", True)
except Exception as exc:  # noqa: BLE001 — any import-time failure is the bug
    _pit_mod = None
    check("0a plain `import src.data.point_in_time` through the package graph", False,
          f"{type(exc).__name__}: {exc} — circular import regression? see [BUG1]")

if _pit_mod is None:
    for status, name, detail in results:
        print(f"[{status}] {name}   -- {detail}")
    print("\nFATAL: cannot import the module under test.")
    sys.exit(1)

from src.data.point_in_time import GameLogRow, PointInTimeStats  # noqa: E402
from src.data.mlb_api import PitchingStatsSnapshot  # noqa: E402
from src.prediction.role_innings import RoleAwareInningsEstimator  # noqa: E402


# ---------------------------------------------------------------------------
# Synthetic MLB StatsAPI gameLog payloads + a stub client.
#
# The stub satisfies exactly the surface _fetch_game_log touches: .BASE_URL,
# .season, ._get(url, params). No network, no mocking library.
# ---------------------------------------------------------------------------
def _split(date_str: str, ip: str, k: int, gs: int, bb: int = 0, hr: int = 0) -> dict[str, Any]:
    """One gameLog split, shaped like the real per-game payload."""
    return {
        "date": date_str,
        "stat": {
            "inningsPitched": ip,      # MLB notation: "5.2" == 5 and 2/3
            "strikeOuts": k,
            "baseOnBalls": bb,
            "homeRuns": hr,
            "gamesStarted": gs,        # 0 or 1 per game — the key under test
        },
    }


class StubAPI:
    """Minimal stand-in for MLBStatsAPI: serves canned gameLog payloads."""

    BASE_URL = "https://stub.invalid/api/v1"

    def __init__(self, logs: dict[int, list[dict[str, Any]]], season: int = 2024):
        self.season = season
        self._logs = logs
        self.calls: list[tuple[str, dict]] = []

    def _get(self, url: str, params: dict | None = None) -> dict[str, Any]:
        params = params or {}
        self.calls.append((url, params))
        pid = int(url.rstrip("/").split("/")[-2])
        if params.get("group") != "pitching":
            return {"stats": [{"splits": []}]}
        return {"stats": [{"splits": self._logs.get(pid, [])}]}


# --- the cast -------------------------------------------------------------
# 101 PURE RELIEVER: 15 appearances, 0 starts, ~1.3 IP each. The case the old
#     code got catastrophically wrong (start_ratio would read 1.0 -> "starter").
RELIEVER_ID = 101
RELIEVER_LOG = [
    _split(f"2024-04-{d:02d}", "1.1", 1, 0) for d in range(1, 16)
]

# 102 TRUE STARTER: 10 appearances, 10 starts, 6.0 IP each.
STARTER_ID = 102
STARTER_LOG = [
    _split(f"2024-04-{d:02d}", "6.0", 7, 1) for d in range(1, 11)
]

# 103 OPENER: 12 appearances, 1 of them a "start" (the opener nod), ~1.0 IP.
#     NOTE the as-of cutoff (2024-04-10, strict <) leaves only games 01-09, so
#     the ratio the estimator actually sees is 1/9 = 0.111 <= opener_max_ratio
#     0.20 -> opener. (A 2-start version gives 2/9 = 0.222 > 0.20 and lands in
#     BULK — correct behaviour, but it makes this a bulk fixture, not an opener
#     one. Fixture arithmetic must be done POST-cutoff, not on the raw log.)
OPENER_ID = 103
OPENER_LOG = [
    _split(f"2024-04-{d:02d}", "1.0", 1, 1 if d <= 1 else 0) for d in range(1, 13)
]

# 104 SWING/BULK: 10 appearances, 5 starts -> ratio 0.5 -> bulk bucket.
BULK_ID = 104
BULK_LOG = [
    _split(f"2024-04-{d:02d}", "3.0", 3, 1 if d <= 5 else 0) for d in range(1, 11)
]

# 105 LEAKAGE FIXTURE: games straddling the as-of cutoff, incl. a start ON the
#     as-of date itself (must be EXCLUDED — strict <).
LEAK_ID = 105
LEAK_LOG = [
    _split("2024-04-01", "1.0", 1, 0),
    _split("2024-04-02", "1.0", 1, 0),
    _split("2024-04-10", "7.0", 9, 1),   # the as-of date itself — must not leak
    _split("2024-04-11", "7.0", 9, 1),   # future — must not leak
]

STUB_LOGS = {
    RELIEVER_ID: RELIEVER_LOG,
    STARTER_ID: STARTER_LOG,
    OPENER_ID: OPENER_LOG,
    BULK_ID: BULK_LOG,
    LEAK_ID: LEAK_LOG,
}

AS_OF = "2024-04-10"

# The B4 block, same shape/values as check_b4_offline.py's, so the two harnesses
# agree on what "opener"/"bulk"/"starter" mean.
B4_BLOCK = {
    "enabled": True,
    "starter_min_ratio": 0.80,
    "opener_max_ratio": 0.20,
    "min_games_for_role": 3,
    "starter_ip_floor": 4.0,
    "starter_ip_ceil": 7.0,
    "bulk_innings": 3.5,
    "opener_innings": 1.5,
    "default_innings": 5.5,
}
LEGACY_IP_FLOOR = 4.0  # the floor B4 exists to break for short-outing arms


def pit_for(logs: dict[int, list[dict[str, Any]]]) -> PointInTimeStats:
    return PointInTimeStats(mlb_api=StubAPI(logs), season=2024)


# recent_games is large enough here that season and recent cover the same rows,
# which keeps the fixtures readable; the estimator reads the RECENT snapshot in
# production, so both are asserted below.
pit = pit_for(STUB_LOGS)
est = RoleAwareInningsEstimator({"role_innings": dict(B4_BLOCK)})


# ---------------------------------------------------------------------------
# GROUP 1 — THE PARSER captures the per-game start indicator at all.
# (Root of defect 2: GameLogRow never carried gamesStarted.)
# ---------------------------------------------------------------------------
_HAS_GS_FIELD = "games_started" in GameLogRow.__dataclass_fields__
check("1a GameLogRow has a games_started field",
      _HAS_GS_FIELD,
      "the per-game start indicator must exist on the row -- without it, "
      "_aggregate_pitching cannot compute real starts and the gate tests nothing")


def _starts(row: Any) -> int:
    """Read a row's start flag, tolerating the PRE-FIX row that lacks the field.

    Without this, a regression that DELETES the field would blow up with an
    AttributeError traceback instead of printing a readable [FAIL] naming the
    broken invariant. A guard should report the bug, not crash on it.
    """
    return int(getattr(row, "games_started", 0) or 0)


rows = pit._pitching_log(RELIEVER_ID)
check("1b parser produced one row per game", len(rows) == 15, f"got {len(rows)}")
check("1c reliever rows all carry games_started=0",
      _HAS_GS_FIELD and all(_starts(r) == 0 for r in rows),
      "GameLogRow.games_started missing" if not _HAS_GS_FIELD
      else str([_starts(r) for r in rows]))

srows = pit._pitching_log(STARTER_ID)
check("1d starter rows all carry games_started=1",
      _HAS_GS_FIELD and all(_starts(r) == 1 for r in srows),
      "GameLogRow.games_started missing (starts are invisible to the aggregator)"
      if not _HAS_GS_FIELD else str([_starts(r) for r in srows]))

orows = pit._pitching_log(OPENER_ID)
check("1e opener rows carry exactly 1 start across 12 games",
      _HAS_GS_FIELD and sum(_starts(r) for r in orows) == 1 and len(orows) == 12,
      "GameLogRow.games_started missing" if not _HAS_GS_FIELD
      else f"starts={sum(_starts(r) for r in orows)} games={len(orows)}")

# A malformed/absent gamesStarted must degrade to 0 (a relief appearance), not
# crash and not silently become a start.
weird = pit_for({999: [
    {"date": "2024-04-01", "stat": {"inningsPitched": "1.0", "strikeOuts": 1}},           # key absent
    {"date": "2024-04-02", "stat": {"inningsPitched": "1.0", "strikeOuts": 1,
                                    "gamesStarted": None}},                                # null
    {"date": "2024-04-03", "stat": {"inningsPitched": "1.0", "strikeOuts": 1,
                                    "gamesStarted": "1"}},                                 # string "1"
]})
wrows = weird._pitching_log(999)
check("1f missing/null gamesStarted -> 0 (not a phantom start)",
      _HAS_GS_FIELD and _starts(wrows[0]) == 0 and _starts(wrows[1]) == 0,
      "GameLogRow.games_started missing" if not _HAS_GS_FIELD
      else str([_starts(r) for r in wrows]))
check("1g string \"1\" gamesStarted parses as a real start",
      _HAS_GS_FIELD and _starts(wrows[2]) == 1,
      "GameLogRow.games_started missing" if not _HAS_GS_FIELD
      else str(_starts(wrows[2])))


# ---------------------------------------------------------------------------
# GROUP 2 — THE AGGREGATOR sets `games` and a TRUE `games_started`.
# This is the defect that made the gate test nothing.
# ---------------------------------------------------------------------------
season_rel, recent_rel = pit.get_pitching_stats_as_of(RELIEVER_ID, AS_OF, recent_games=15)

check("2a reliever point-in-time snapshot has games > 0  [THE BUG]",
      season_rel.games > 0,
      f"games={season_rel.games} — if 0, estimator returns role='unknown' and "
      f"the B4 gate silently tests NOTHING")

check("2b reliever games_started < games  [THE BUG]",
      season_rel.games_started < season_rel.games,
      f"games_started={season_rel.games_started} games={season_rel.games} — equal "
      f"means appearances were conflated with starts (the old len(rows))")

check("2c reliever games_started is the SUM OF REAL STARTS (0), not len(rows)",
      season_rel.games_started == 0 and season_rel.games == 9,
      f"games_started={season_rel.games_started} (want 0), games={season_rel.games} (want 9)")

# The estimator reads the RECENT snapshot in production — it must be fixed too.
check("2d RECENT snapshot also carries games/games_started (the path the "
      "estimator actually reads)",
      recent_rel.games > 0 and recent_rel.games_started == 0,
      f"games={recent_rel.games} games_started={recent_rel.games_started}")

season_st, _ = pit.get_pitching_stats_as_of(STARTER_ID, AS_OF, recent_games=15)
check("2e starter games_started == games (9 starts / 9 games before cutoff)",
      season_st.games_started == 9 and season_st.games == 9,
      f"gs={season_st.games_started} g={season_st.games}")

# Empty log -> a default snapshot, and NOT a crash. games stays 0 = "unknown",
# which is the honest fallback (not a bug).
empty_pit = pit_for({})
season_empty, _ = empty_pit.get_pitching_stats_as_of(777, AS_OF)
check("2f no games before as-of -> empty snapshot, games=0 (honest 'unknown')",
      season_empty == PitchingStatsSnapshot() and season_empty.games == 0)

# games must NOT be floored to >=1 (the season parser floors games_started, the
# point-in-time path must not — a reliever's true 0 starts is the opener signal).
check("2g point-in-time games_started is UNFLOORED (a real 0 stays 0)",
      season_rel.games_started == 0,
      "flooring to >=1 here would reintroduce the phantom-start bias B4 removes")


# ---------------------------------------------------------------------------
# GROUP 3 — LEAKAGE BOUNDARY still holds after the change (strict <).
# The as-of date's own game must never enter the snapshot.
# ---------------------------------------------------------------------------
season_leak, _ = pit.get_pitching_stats_as_of(LEAK_ID, AS_OF, recent_games=15)
check("3a as-of-date game EXCLUDED (strict <) — 2 relief games, not 3",
      season_leak.games == 2,
      f"games={season_leak.games} — 3 means the 2024-04-10 start leaked in")
check("3b the leaked game's START did not enter games_started",
      season_leak.games_started == 0,
      f"games_started={season_leak.games_started} — 1 means the as-of start leaked")
check("3c the leaked game's IP did not enter innings_pitched",
      abs(season_leak.innings_pitched - 2.0) < 1e-9,
      f"ip={season_leak.innings_pitched} — 9.0 means the 7-IP start leaked in")


# ---------------------------------------------------------------------------
# GROUP 4 — END TO END: the REAL estimator on the REAL point-in-time snapshot.
# This is the assertion the gate's validity rests on.
# ---------------------------------------------------------------------------
r_rel = est.estimate_detailed(recent_rel)
check("4a reliever role is opener/bulk — NOT starter, NOT unknown  [THE POINT]",
      r_rel.role in ("opener", "bulk"),
      f"role={r_rel.role} start_ratio={r_rel.start_ratio} — 'unknown' means games==0 "
      f"(bug 1); 'starter' means appearances were counted as starts (bug 2)")
check("4b reliever start_ratio ~0.0, NOT ~1.0",
      r_rel.start_ratio is not None and r_rel.start_ratio < 0.2,
      f"start_ratio={r_rel.start_ratio}")
check("4c reliever expected_innings breaks the legacy 4.0 floor",
      r_rel.expected_innings < LEGACY_IP_FLOOR,
      f"expected_innings={r_rel.expected_innings} (legacy floor {LEGACY_IP_FLOOR})")
check("4d reliever used_b4 is True (the B4 path actually ran)", r_rel.used_b4 is True)

_, recent_op = pit.get_pitching_stats_as_of(OPENER_ID, AS_OF, recent_games=15)
r_op = est.estimate_detailed(recent_op)
check("4e opener (1 start / 9 games before cutoff, ratio 0.11) -> opener bucket",
      r_op.role == "opener",
      f"role={r_op.role} start_ratio={r_op.start_ratio}")
check("4f opener innings == opener_innings",
      r_op.expected_innings == B4_BLOCK["opener_innings"], str(r_op.expected_innings))

_, recent_bulk = pit.get_pitching_stats_as_of(BULK_ID, AS_OF, recent_games=15)
r_bulk = est.estimate_detailed(recent_bulk)
check("4g swing arm -> bulk bucket",
      r_bulk.role == "bulk",
      f"role={r_bulk.role} start_ratio={r_bulk.start_ratio}")

# The opener/bulk CUT is load-bearing and easy to get subtly wrong (this very
# harness first mis-sized its own opener fixture and got 'bulk'). Pin the
# boundary explicitly: ratio exactly AT opener_max_ratio is an opener
# (estimator uses <=); a hair above it is bulk.
_at = pit_for({201: [_split(f"2024-04-{d:02d}", "1.0", 1, 1 if d <= 2 else 0)
                     for d in range(1, 11)]})          # 2 starts / 10 games = 0.20 exactly
_, _recent_at = _at.get_pitching_stats_as_of(201, "2024-04-11", recent_games=15)
r_at = est.estimate_detailed(_recent_at)
check("4g' start_ratio exactly AT opener_max_ratio (0.20) -> opener (boundary is <=)",
      r_at.role == "opener" and abs(r_at.start_ratio - 0.20) < 1e-9,
      f"role={r_at.role} start_ratio={r_at.start_ratio}")

_above = pit_for({202: [_split(f"2024-04-{d:02d}", "1.0", 1, 1 if d <= 3 else 0)
                        for d in range(1, 11)]})       # 3 starts / 10 games = 0.30
_, _recent_above = _above.get_pitching_stats_as_of(202, "2024-04-11", recent_games=15)
r_above = est.estimate_detailed(_recent_above)
check("4g'' start_ratio just above the cut -> bulk (not opener)",
      r_above.role == "bulk", f"role={r_above.role} start_ratio={r_above.start_ratio}")

_, recent_st = pit.get_pitching_stats_as_of(STARTER_ID, AS_OF, recent_games=15)
r_st = est.estimate_detailed(recent_st)
check("4h true starter -> starter role, high start_ratio",
      r_st.role == "starter" and r_st.start_ratio == 1.0,
      f"role={r_st.role} start_ratio={r_st.start_ratio}")
check("4i starter innings is the honest per-start length (6.0)",
      r_st.expected_innings == 6.0, str(r_st.expected_innings))


# ---------------------------------------------------------------------------
# GROUP 5 — THE REGRESSION SENTINEL: prove the OLD code would FAIL this.
#
# A guard that passes on both the broken and fixed code guards nothing. So we
# reconstruct the pre-fix aggregation (games never set, games_started=len(rows))
# and assert the estimator misbehaves on it exactly as described in the bug
# report. If this group ever "passes" in the broken direction, the harness has
# lost its teeth.
# ---------------------------------------------------------------------------
old_snap = PitchingStatsSnapshot(
    innings_pitched=recent_rel.innings_pitched,
    strikeouts=recent_rel.strikeouts,
    games_started=recent_rel.games,   # the OLD len(rows) conflation
    # games never set -> stays 0
)
r_old = est.estimate_detailed(old_snap)
check("5a OLD behaviour reproduced: games=0 -> role='unknown' (gate tests nothing)",
      r_old.role == "unknown" and old_snap.games == 0,
      f"role={r_old.role}")
check("5b OLD behaviour: falls back to default_innings (B4 changes NOTHING)",
      r_old.expected_innings == B4_BLOCK["default_innings"],
      f"{r_old.expected_innings}")
check("5c FIXED behaviour differs from OLD (the fix is load-bearing)",
      r_rel.expected_innings != r_old.expected_innings,
      f"fixed={r_rel.expected_innings} old={r_old.expected_innings} — if equal, the "
      f"fix changed nothing and the gate would still test nothing")

# And the defect-2-only failure mode: games set, but games_started=len(rows).
half_fixed = PitchingStatsSnapshot(
    innings_pitched=recent_rel.innings_pitched,
    strikeouts=recent_rel.strikeouts,
    games=recent_rel.games,
    games_started=recent_rel.games,   # conflated
)
r_half = est.estimate_detailed(half_fixed)
check("5d HALF-FIX (games set, games_started=len(rows)) mislabels reliever a STARTER",
      r_half.role == "starter",
      f"role={r_half.role} — this is why both defects must be fixed together")
check("5e FIXED reliever is NOT a starter (the half-fix trap is avoided)",
      r_rel.role != "starter", f"role={r_rel.role}")


# ---------------------------------------------------------------------------
# GROUP 6 — K-PROJECTION IMPACT: the drift the gate's smoke test looks for.
#
# projected_k = k_prob * expected_innings * PA_PER_INNING (prop_engine), so the
# K projection moves in exact proportion to expected_innings. If this ratio is
# 1.0 for short-outing arms, run_b4_gate_verdict.py will exit 2 ("K probs
# identical") — this check is the cheap offline preview of that guard.
# ---------------------------------------------------------------------------
legacy_ip_reliever = RoleAwareInningsEstimator._legacy_expected_ip(recent_rel)
ratio = r_rel.expected_innings / legacy_ip_reliever if legacy_ip_reliever else 0.0
check("6a reliever K projection SHRINKS vs legacy (the short-outing bias fix)",
      ratio < 1.0,
      f"expected_innings {legacy_ip_reliever} -> {r_rel.expected_innings} "
      f"(ratio {ratio:.3f}); K scales linearly with this")
check("6b starter K projection is ~unchanged (B4 must not move real starters)",
      abs(r_st.expected_innings
          - RoleAwareInningsEstimator._legacy_expected_ip(recent_st)) < 1e-9,
      f"b4={r_st.expected_innings} "
      f"legacy={RoleAwareInningsEstimator._legacy_expected_ip(recent_st)}")


# ---------------------------------------------------------------------------
# GROUP 7 — CONFIG-THREADING SEAM (the SECOND silent-failure bug).
#
# Fixing point_in_time alone is NOT enough. MLBStatsAPI.__init__ builds the
# RoleAwareInningsEstimator from config["role_innings"], and AsOfMLBAPI
# INHERITS get_pitchers_for_date, which calls self._innings_estimator to set
# expected_innings. reconstruct_objects() originally constructed both clients
# with `season=` ONLY -- so the estimator was built from {} -> enabled=False,
# and a B4-enabled --config was silently ignored. The candidate run would have
# reproduced the frozen run exactly (K drift 0.0), and the gate would have
# tested nothing -- the same failure mode as the games=0 bug, different cause.
#
# These checks pin BOTH halves: the estimator must be enabled when a B4 config
# is passed, AND must stay byte-identical to legacy when it is not (collection
# safety -- the frozen baseline and the live daily slate must not move).
# ---------------------------------------------------------------------------
from src.data.mlb_api import MLBStatsAPI  # noqa: E402

_api_no_cfg = MLBStatsAPI(season=2024)
_api_b4 = MLBStatsAPI(season=2024, config={"role_innings": dict(B4_BLOCK)})

check("7a MLBStatsAPI accepts a config kwarg (the threading seam exists)",
      hasattr(_api_b4, "_innings_estimator"),
      "no _innings_estimator -- the B4 wiring in MLBStatsAPI.__init__ is gone")

check("7b MLBStatsAPI(config=B4) -> estimator ENABLED  [SECOND BUG]",
      _api_b4._innings_estimator.enabled is True,
      "if False, a B4 --config is silently ignored and the gate tests NOTHING")

check("7c MLBStatsAPI(season only) -> estimator DISABLED (legacy, collection-safe)",
      _api_no_cfg._innings_estimator.enabled is False)

# The estimator reached through the client must produce the SAME role-aware
# answer as a directly-constructed one -- i.e. the config really landed, it
# wasn't just accepted and dropped.
_rel_via_client = _api_b4._innings_estimator.estimate_detailed(recent_rel)
check("7d config actually LANDS: client's estimator classifies the reliever as opener/bulk",
      _rel_via_client.role in ("opener", "bulk") and _rel_via_client.used_b4 is True,
      f"role={_rel_via_client.role} used_b4={_rel_via_client.used_b4}")

check("7e without config the SAME reliever falls back to legacy default (proves 7d is load-bearing)",
      _api_no_cfg._innings_estimator.estimate(recent_rel)
      != _api_b4._innings_estimator.estimate(recent_rel),
      f"no_cfg={_api_no_cfg._innings_estimator.estimate(recent_rel)} "
      f"b4={_api_b4._innings_estimator.estimate(recent_rel)} -- equal means the config "
      f"never reached the estimator")

# COLLECTION SAFETY: with the real frozen config (no role_innings block) the
# threaded config must be INERT -- byte-identical to the legacy heuristic.
_frozen_cfg = {"season": 2026, "weights": {"season": 0.35}, "league_avg": {"k_per_9": 8.8}}
_api_frozen = MLBStatsAPI(season=2024, config=_frozen_cfg)
_inert = all(
    _api_frozen._innings_estimator.estimate(s)
    == RoleAwareInningsEstimator._legacy_expected_ip(s)
    for s in (recent_rel, recent_st, recent_op, recent_bulk)
)
check("7f threading a FROZEN config (no role_innings) is INERT == legacy "
      "(live path + gate baseline unmoved)",
      _inert,
      "threading config must not change frozen behaviour, or it contaminates the "
      "gate's frozen side and the live daily slate")


# ---------------------------------------------------------------------------
# GROUP 8 — THE ACTUAL CALL SITE. Group 7 proves MLBStatsAPI *can* take a
# config; it does NOT prove run_reconstruct_date.py actually PASSES one. That
# was the broken line. Importing run_reconstruct_date pulls the whole
# reconstruction graph (FeatureFactory, PropEngine, ...), which is heavy and
# not what this harness is for -- so we assert on the SOURCE, which is exactly
# the property that regressed: every MLBStatsAPI(...) / AsOfMLBAPI(...)
# construction inside run_reconstruct_date.py must pass config=.
#
# A plain `MLBStatsAPI(season=season)` there silently disables B4 for the whole
# gate. This check is cheap insurance against that line coming back.
# ---------------------------------------------------------------------------
import re  # noqa: E402

_rrd = Path(__file__).resolve().parents[1] / "run_reconstruct_date.py"
if not _rrd.exists():
    check("8a run_reconstruct_date.py found", False, f"missing at {_rrd}")
else:
    _src = _rrd.read_text(encoding="utf-8")
    # Strip comments so the explanatory prose above the fix can't satisfy the
    # check (a comment mentioning MLBStatsAPI(season=season) must not count).
    _code = "\n".join(line.split("#", 1)[0] for line in _src.splitlines())
    # Constructor calls, tolerant of the multi-line formatting the fix uses.
    _calls = re.findall(r"(?<!class )\b(?:MLBStatsAPI|AsOfMLBAPI)\s*\(([^)]*)\)",
                        _code, re.DOTALL)
    _ctor_calls = [c for c in _calls if "season" in c or "as_of_date" in c]
    _missing = [c for c in _ctor_calls if "config" not in c]
    check("8a every MLBStatsAPI/AsOfMLBAPI construction in run_reconstruct_date.py "
          "passes config=  [SECOND BUG, at the real call site]",
          bool(_ctor_calls) and not _missing,
          f"{len(_missing)} construction(s) WITHOUT config -> B4 silently disabled in the "
          f"gate: {[' '.join(c.split()) for c in _missing]}"
          if _missing else "no constructor calls found -- did the file move?")
    check("8b run_reconstruct_date.py constructs both clients (sanity: the seam exists)",
          len(_ctor_calls) >= 2, f"found {len(_ctor_calls)} constructor call(s)")


# ---------------------------------------------------------------------------
# REPORT
# ---------------------------------------------------------------------------
n_fail = sum(1 for r in results if r[0] == FAIL)
width = max(len(name) for _, name, _ in results)
for status, name, detail in results:
    line = f"[{status}] {name.ljust(width)}"
    if detail and status == FAIL:
        line += f"   -- {detail}"
    print(line)

total = len(results)
print(f"\n{total - n_fail}/{total} checks passed")
if n_fail:
    print("\nFAILED — do NOT run the B4 gate. A red check here means the "
          "reconstruction path cannot see pitcher roles, and the gate would "
          "test nothing while appearing to pass.")
sys.exit(1 if n_fail else 0)
