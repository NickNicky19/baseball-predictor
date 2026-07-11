"""
Offline harness for scripts/build_pit_role_rows.py — the B4 historical fit's
row builder. Run before spending any API calls:

    python scripts/check_pit_role_rows_offline.py

WHAT THIS GUARDS
----------------
The builder labels each historical (pitcher, game_date) with the role the GATE
would have assigned that day, then hands actual_ip to fit_role_innings. Two
things can silently ruin the fitted constants:

  1. MLB NOTATION. The A3 training set's out_ip is MLB notation ('5.2' == 5
     and 2/3, NOT 5.2 -- verified: its decimal digit is only ever 0/1/2 across
     all 14,816 rows). Averaging it raw understates innings and skews
     opener_innings / bulk_innings DOWNWARD. Group 1 pins the converter.

  2. THE WRONG RULER. The gate labels roles from a 5-GAME TRAILING
     POINT-IN-TIME window (MLBStatsAPI.get_pitchers_for_date calls
     estimate(recent), and AsOfMLBAPI's `recent` is
     PointInTimeStats.get_pitching_stats_as_of -> last 5 appearances strictly
     before the date). Fitting under a SEASON-level ruler would describe a
     different population than the constants get applied to. Group 3 pins the
     builder to the same 5-game point-in-time ruler, through the REAL
     PointInTimeStats and the REAL RoleAwareInningsEstimator -- imported, never
     reimplemented.

It also guards the reason we cannot fit from the training set's own columns:
pit_gs counts APPEARANCES, not starts (the same len(rows) conflation this
session fixed in point_in_time). Group 4 proves the builder does NOT inherit
that, i.e. a reliever comes back with games_started < games.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PASS = "PASS"
FAIL = "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((PASS if cond else FAIL, name, detail))


# ---------------------------------------------------------------------------
# GROUP 0 — package-graph import smoke (standing note from [BUG1]).
# ---------------------------------------------------------------------------
try:
    import importlib
    _mod = importlib.import_module("scripts.build_pit_role_rows")
    check("0a import scripts.build_pit_role_rows through the package graph", True)
except Exception as exc:  # noqa: BLE001
    # Fall back to a path import (scripts/ may not be a package).
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import build_pit_role_rows as _mod  # type: ignore
        check("0a import scripts.build_pit_role_rows through the package graph", True)
    except Exception as exc2:  # noqa: BLE001
        _mod = None
        check("0a import scripts.build_pit_role_rows through the package graph", False,
              f"{type(exc2).__name__}: {exc2}")

if _mod is None:
    for s, n, d in results:
        print(f"[{s}] {n}   -- {d}")
    print("\nFATAL: cannot import the module under test.")
    sys.exit(1)

from src.data.point_in_time import PointInTimeStats  # noqa: E402
from src.prediction.role_innings import RoleAwareInningsEstimator  # noqa: E402

mlb_ip_to_float = _mod.mlb_ip_to_float
label_rows_point_in_time = _mod.label_rows_point_in_time
load_training_rows = _mod.load_training_rows

import pandas as pd  # noqa: E402

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
EST = RoleAwareInningsEstimator({"role_innings": dict(B4_BLOCK)})


# ---------------------------------------------------------------------------
# GROUP 1 — MLB NOTATION. '5.2' is 5 and 2/3, not 5.2.
# Getting this wrong biases every fitted constant DOWNWARD.
# ---------------------------------------------------------------------------
check("1a '5.0' -> 5.0", mlb_ip_to_float("5.0") == 5.0, str(mlb_ip_to_float("5.0")))
check("1b '5.1' -> 5.333 (one out, NOT 5.1)",
      abs(mlb_ip_to_float("5.1") - (5 + 1 / 3)) < 1e-9,
      f"got {mlb_ip_to_float('5.1')}")
check("1c '5.2' -> 5.667 (two outs, NOT 5.2)",
      abs(mlb_ip_to_float("5.2") - (5 + 2 / 3)) < 1e-9,
      f"got {mlb_ip_to_float('5.2')}")
check("1d numeric 6.0 -> 6.0", mlb_ip_to_float(6.0) == 6.0)
check("1e '0.1' -> 0.333 (a single out)",
      abs(mlb_ip_to_float("0.1") - (1 / 3)) < 1e-9)
check("1f garbage -> 0.0 (never raises)", mlb_ip_to_float("abc") == 0.0)
check("1g None -> 0.0", mlb_ip_to_float(None) == 0.0)

# The bias this prevents, made concrete: a 1.2-IP opener is really 1.667 IP.
# Fitting on the raw value would set opener_innings ~28% too low.
_raw = 1.2
_true = mlb_ip_to_float("1.2")
check("1h converter materially changes the fit target (not a no-op)",
      abs(_true - _raw) > 0.4,
      f"raw {_raw} vs true {_true:.3f} — averaging raw would understate innings")


# ---------------------------------------------------------------------------
# Stub game logs: the network seam is PointInTimeStats' mlb_api._get.
# ---------------------------------------------------------------------------
def _split(date_str: str, ip: str, k: int, gs: int) -> dict[str, Any]:
    return {"date": date_str,
            "stat": {"inningsPitched": ip, "strikeOuts": k, "baseOnBalls": 0,
                     "homeRuns": 0, "gamesStarted": gs}}


class StubAPI:
    BASE_URL = "https://stub.invalid/api/v1"

    def __init__(self, logs: dict[int, list[dict[str, Any]]], season: int = 2023):
        self.season = season
        self._logs = logs

    def _get(self, url: str, params: dict | None = None) -> dict[str, Any]:
        params = params or {}
        pid = int(url.rstrip("/").split("/")[-2])
        if params.get("group") != "pitching":
            return {"stats": [{"splits": []}]}
        return {"stats": [{"splits": self._logs.get(pid, [])}]}


# 501 OPENER: appears often, rarely starts. In the 5 games before 2023-05-01
# he has 0 starts -> start_ratio 0.0 -> opener. He DID start on 05-01 (that is
# why he is a training row), throwing 1.2 MLB (= 1.667 real).
OPENER = 501
OPENER_LOG = [_split(f"2023-04-{d:02d}", "1.0", 1, 0) for d in range(20, 30)]
OPENER_LOG.append(_split("2023-05-01", "1.2", 2, 1))   # the row being labelled

# 502 TRUE STARTER: starts every time, ~6 IP.
STARTER = 502
STARTER_LOG = [_split(f"2023-04-{d:02d}", "6.0", 7, 1) for d in range(20, 30)]
STARTER_LOG.append(_split("2023-05-01", "6.1", 8, 1))

# 503 BULK/SWING: 5 games before the date, 2 of them starts -> ratio 0.4 -> bulk.
BULK = 503
BULK_LOG = [_split("2023-04-20", "3.0", 3, 1),
            _split("2023-04-22", "3.0", 3, 0),
            _split("2023-04-24", "3.0", 3, 1),
            _split("2023-04-26", "3.0", 3, 0),
            _split("2023-04-28", "3.0", 3, 0),
            _split("2023-05-01", "3.1", 4, 1)]

# 504 THIN: only 2 appearances before the date -> games < min_games_for_role
#     -> role 'unknown' (honest fallback, excluded from the fit).
THIN = 504
THIN_LOG = [_split("2023-04-28", "1.0", 1, 0),
            _split("2023-04-29", "1.0", 1, 0),
            _split("2023-05-01", "2.0", 2, 1)]

LOGS = {OPENER: OPENER_LOG, STARTER: STARTER_LOG, BULK: BULK_LOG, THIN: THIN_LOG}

ROWS = pd.DataFrame([
    {"player_id": OPENER,  "game_date": "2023-05-01", "actual_ip": mlb_ip_to_float("1.2"),
     "actual_strikeouts": 2},
    {"player_id": STARTER, "game_date": "2023-05-01", "actual_ip": mlb_ip_to_float("6.1"),
     "actual_strikeouts": 8},
    {"player_id": BULK,    "game_date": "2023-05-01", "actual_ip": mlb_ip_to_float("3.1"),
     "actual_strikeouts": 4},
    {"player_id": THIN,    "game_date": "2023-05-01", "actual_ip": mlb_ip_to_float("2.0"),
     "actual_strikeouts": 2},
])

pit = PointInTimeStats(mlb_api=StubAPI(LOGS), season=2023)
LAB = label_rows_point_in_time(ROWS, pit, EST, recent_games=5,
                               model_version_tag="dab23f4fbac8")
by_pid = {int(r["player_id"]): r for _, r in LAB.iterrows()}


# ---------------------------------------------------------------------------
# GROUP 2 — SCHEMA: the output must satisfy load_k_rows AND load_roster.
# ---------------------------------------------------------------------------
K_REQ = {"category", "predicted_value", "actual_strikeouts", "actual_ip",
         "player_id", "game_date"}
R_REQ = {"player_id", "games", "games_started", "innings_pitched"}
check("2a output satisfies load_k_rows' required columns",
      K_REQ <= set(LAB.columns), str(sorted(K_REQ - set(LAB.columns))))
check("2b output satisfies load_roster' required columns (so it is its own --roster)",
      R_REQ <= set(LAB.columns), str(sorted(R_REQ - set(LAB.columns))))
check("2c category is 'strikeouts' (load_k_rows filters on it)",
      (LAB["category"] == "strikeouts").all())
check("2d predicted_value is the numeric sentinel (loader needs it; fit ignores it)",
      (LAB["predicted_value"] == 1.0).all())
check("2e actual_ip > 0 on every row (load_k_rows drops <= 0)",
      (LAB["actual_ip"] > 0).all())

# load_k_rows ALSO coerces `confidence` and reads `model_version` -- neither is
# in its declared missing_cols check, so a file without them passes the stated
# contract and then dies with a KeyError. This harness caught exactly that.
# Pin both.
check("2f output carries `confidence` (load_k_rows coerces it — undeclared requirement)",
      "confidence" in LAB.columns,
      "load_k_rows line ~186 does pd.to_numeric(k['confidence']) -> KeyError without it")
check("2g output carries `model_version` (load_k_rows reads .unique() on it)",
      "model_version" in LAB.columns,
      "load_k_rows line ~200 reads k['model_version'].unique() -> KeyError without it")
check("2h model_version is a single consistent value (not blank)",
      LAB["model_version"].nunique() == 1 and LAB["model_version"].iloc[0] not in ("", None),
      str(LAB["model_version"].unique()))


# ---------------------------------------------------------------------------
# GROUP 3 — THE RULER: point-in-time, 5-game trailing, strictly before.
# ---------------------------------------------------------------------------
op = by_pid[OPENER]
check("3a OPENER labelled 'opener' via the REAL point-in-time path  [THE POINT]",
      op["role"] == "opener",
      f"role={op['role']} start_ratio={op['start_ratio']} games={op['games']} "
      f"gs={op['games_started']}")
check("3b opener start_ratio ~0.0 (0 starts in the 5 games before the date)",
      float(op["start_ratio"]) < 0.2, str(op["start_ratio"]))

st = by_pid[STARTER]
check("3c STARTER labelled 'starter'", st["role"] == "starter",
      f"role={st['role']} start_ratio={st['start_ratio']}")

bk = by_pid[BULK]
check("3d BULK/swing labelled 'bulk' (2 starts / 5 games = 0.4)",
      bk["role"] == "bulk",
      f"role={bk['role']} start_ratio={bk['start_ratio']}")

th = by_pid[THIN]
check("3e THIN sample (2 games < min_games_for_role) -> 'unknown', not invented",
      th["role"] == "unknown", f"role={th['role']}")

# LEAKAGE: the labelled game itself must NOT be in its own snapshot. The opener
# started on 05-01; if that start leaked in, his ratio would rise above 0.
check("3f the game being labelled is EXCLUDED from its own snapshot (strict <)",
      int(op["games_started"]) == 0 and int(op["games"]) == 5,
      f"games={op['games']} games_started={op['games_started']} — a nonzero gs means "
      f"the 05-01 start leaked into the features that label it")

# The window is 5, not the whole season: the opener has 10 prior appearances,
# but the snapshot must see only the last 5.
check("3g window is the last 5 appearances, not season-to-date",
      int(op["games"]) == 5,
      f"games={op['games']} — 10 would mean a season-level ruler, which is NOT what "
      f"the gate uses")


# ---------------------------------------------------------------------------
# GROUP 4 — WE DO NOT INHERIT THE TRAINING SET'S BUG.
#
# training_pitchers_*.csv.gz's pit_gs counts APPEARANCES, not starts (verified
# on Tyler Holton). If the builder inherited that, every reliever would come
# back start_ratio ~1.0 and be labelled a STARTER, and opener_innings would be
# fitted from a population with no openers in it.
# ---------------------------------------------------------------------------
check("4a reliever/opener has games_started < games (NOT the pit_gs conflation)",
      int(op["games_started"]) < int(op["games"]),
      f"gs={op['games_started']} games={op['games']}")
check("4b a real 0-start window stays 0 (unfloored — the opener signal)",
      int(op["games_started"]) == 0, str(op["games_started"]))
check("4c starter's ratio is genuinely high (labels are not all collapsed)",
      float(st["start_ratio"]) >= 0.8, str(st["start_ratio"]))
check("4d the three roles are DISTINCT (an all-'starter' labelling is the bug)",
      len({op["role"], st["role"], bk["role"]}) == 3,
      f"{op['role']}/{st['role']}/{bk['role']}")


# ---------------------------------------------------------------------------
# GROUP 5 — THE FIT TARGET survives intact (this is what gets averaged).
# ---------------------------------------------------------------------------
check("5a opener's actual_ip is the CONVERTED 1.667, not raw 1.2",
      abs(float(op["actual_ip"]) - (1 + 2 / 3)) < 1e-9, str(op["actual_ip"]))
check("5b starter's actual_ip is the CONVERTED 6.333, not raw 6.1",
      abs(float(st["actual_ip"]) - (6 + 1 / 3)) < 1e-9, str(st["actual_ip"]))

# End to end: group by role and average, exactly as fit_constants does.
_means = LAB.groupby("role")["actual_ip"].mean().to_dict()
check("5c grouping by role yields a SHORT opener mean and a LONG starter mean",
      _means.get("opener", 9) < 3.0 < _means.get("starter", 0),
      str({k: round(v, 2) for k, v in _means.items()}))


# ---------------------------------------------------------------------------
# GROUP 6 — LOADER SMOKE: the file actually round-trips through A6's loaders.
# A schema that "looks right" but trips load_k_rows is worthless.
# ---------------------------------------------------------------------------
import tempfile  # noqa: E402

with tempfile.TemporaryDirectory() as td:
    p = Path(td) / "k_rows.csv"
    LAB.to_csv(p, index=False)
    try:
        from run_analyze_k_error import load_k_rows, load_roster  # noqa: E402
        k = load_k_rows(p)
        check("6a output round-trips through the REAL load_k_rows",
              len(k) == len(LAB), f"{len(k)} of {len(LAB)} rows survived")
        r = load_roster(p)
        check("6b output round-trips through the REAL load_roster",
              len(r) > 0 and {"player_id", "games", "games_started"} <= set(r.columns))
    except Exception as exc:  # noqa: BLE001
        check("6a output round-trips through the REAL load_k_rows", False, repr(exc))
        check("6b output round-trips through the REAL load_roster", False, "see 6a")


# ---------------------------------------------------------------------------
# REPORT
# ---------------------------------------------------------------------------
n_fail = sum(1 for r in results if r[0] == FAIL)
width = max(len(n) for _, n, _ in results)
for status, name, detail in results:
    line = f"[{status}] {name.ljust(width)}"
    if detail and status == FAIL:
        line += f"   -- {detail}"
    print(line)

total = len(results)
print(f"\n{total - n_fail}/{total} checks passed")
if n_fail:
    print("\nFAILED — do NOT spend API calls on the historical build. A red check here "
          "means the fitted constants would be wrong (bad ruler, bad units, or the "
          "training set's appearances-as-starts bug inherited).")
sys.exit(1 if n_fail else 0)
