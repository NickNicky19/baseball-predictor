"""
B4 offline harness — role-aware expected_innings.

Validates B4 WITHOUT touching the live path. Run before any smoke test:
    python scripts/check_b4_offline.py

Checks fall in four groups:
  COLLECTION-SAFETY (the most important): disabled B4 is byte-for-byte the
  pre-B4 heuristic, and the config hash does not move.
  FORK: enabling B4 forks model_version by construction.
  BIAS-FIX: openers/relievers get short expected_innings (the actual point of
  B4), and starters are unchanged from an honest per-start length.
  GUARDS: config validation, clamps, thin-sample fallback, monotonicity.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

# Make repo root importable when run from anywhere.
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.prediction.role_innings import (  # noqa: E402
    RoleAwareInningsEstimator,
    RoleInningsConfigError,
)
from src.utils.model_version import model_version  # noqa: E402


@dataclass
class FakeSnap:
    """Minimal duck-typed stand-in for PitchingStatsSnapshot."""
    innings_pitched: float = 0.0
    games_started: int = 0
    games: int = 0


# A config that mimics a real config.json: several unrelated blocks plus B4.
BASE_CONFIG = {
    "season": 2026,
    "weights": {"season": 0.35, "recent": 0.65},
    "league_avg": {"k_per_9": 8.8},
    "pitcher_k_dispersion": 0.0,
}

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


def _legacy_reference(recent: FakeSnap) -> float:
    """Independent re-implementation of the ORIGINAL pre-B4 heuristic."""
    if recent.games_started > 0 and recent.innings_pitched > 0:
        avg_ip = recent.innings_pitched / recent.games_started
        return round(max(4.0, min(7.5, avg_ip)), 1)
    return 5.5


PASS = "PASS"
FAIL = "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((PASS if cond else FAIL, name, detail))


# A grid of snapshots spanning starters, openers, swing arms, and edge cases.
GRID = [
    FakeSnap(innings_pitched=60.0, games_started=10, games=10),   # workhorse starter
    FakeSnap(innings_pitched=30.0, games_started=6, games=6),     # 5.0 IP/start starter
    FakeSnap(innings_pitched=48.0, games_started=6, games=6),     # 8.0 IP/start (clamp)
    FakeSnap(innings_pitched=12.0, games_started=6, games=6),     # 2.0 IP/start (floor)
    FakeSnap(innings_pitched=8.0, games_started=1, games=8),      # opener: 1 start / 8 app
    FakeSnap(innings_pitched=20.0, games_started=0, games=15),    # pure reliever (0 GS)
    FakeSnap(innings_pitched=25.0, games_started=3, games=6),     # swing / bulk (0.5 ratio)
    FakeSnap(innings_pitched=0.0, games_started=0, games=0),      # no data
    FakeSnap(innings_pitched=10.0, games_started=2, games=2),     # thin sample (games<3)
]


# ---------------------------------------------------------------------------
# GROUP 0 — PACKAGE-GRAPH IMPORT SMOKE (standing note from BUG 1).
#
# This harness imports role_innings DIRECTLY, which passed 24/24 while a
# top-level import in mlb_api.py had the whole package graph broken
# (mlb_api -> prediction pkg -> correction_manager -> learning pkg ->
# outcome_recorder -> mlb_api). So: also import the ACTUAL production module
# the way real callers do. If anyone ever re-hoists the RoleAwareInningsEstimator
# import back to mlb_api module top level, 0a fails loudly right here.
# ---------------------------------------------------------------------------
import importlib

try:
    _mlb_mod = importlib.import_module("src.data.mlb_api")
    check("0a plain `import src.data.mlb_api` through the package graph", True)
except Exception as exc:  # noqa: BLE001 — any import-time failure is the bug
    _mlb_mod = None
    check("0a plain `import src.data.mlb_api` through the package graph", False,
          f"{type(exc).__name__}: {exc} — circular import regression? see [BUG1]")

if _mlb_mod is not None:
    # Exercise the LAZY import site the BUG-1 fix touched: the module-tail
    # _estimate_expected_ip helper must still delegate to the one legacy
    # definition (guards the two-copies-drift class of bug end to end).
    try:
        _deleg_ok = all(
            _mlb_mod._estimate_expected_ip(s) == _legacy_reference(s) for s in GRID
        )
        check("0b mlb_api._estimate_expected_ip delegates to the legacy branch",
              _deleg_ok)
    except Exception as exc:  # noqa: BLE001
        check("0b mlb_api._estimate_expected_ip delegates to the legacy branch",
              False, repr(exc))


# ---------------------------------------------------------------------------
# GROUP 1 — COLLECTION SAFETY: disabled == legacy, byte for byte.
# ---------------------------------------------------------------------------
est_disabled = RoleAwareInningsEstimator(BASE_CONFIG)  # no role_innings block
check("1a disabled estimator reports enabled=False", est_disabled.enabled is False)

all_match = True
for i, snap in enumerate(GRID):
    got = est_disabled.estimate(snap)
    ref = _legacy_reference(snap)
    if got != ref:
        all_match = False
        check(f"1b[{i}] disabled==legacy", False, f"got {got} != legacy {ref} for {snap}")
check("1b disabled path == legacy heuristic on full grid", all_match)

# Block present but enabled=false must also be legacy.
cfg_off = dict(BASE_CONFIG, role_innings=dict(B4_BLOCK, enabled=False))
est_off = RoleAwareInningsEstimator(cfg_off)
check("1c enabled=false block reports enabled=False", est_off.enabled is False)
off_match = all(est_off.estimate(s) == _legacy_reference(s) for s in GRID)
check("1c enabled=false path == legacy heuristic", off_match)

# A half-authored disabled block must NOT raise (can't break collection).
try:
    RoleAwareInningsEstimator(dict(BASE_CONFIG, role_innings={"enabled": False}))
    check("1d partial disabled block does not raise", True)
except Exception as exc:  # noqa: BLE001
    check("1d partial disabled block does not raise", False, repr(exc))


# ---------------------------------------------------------------------------
# GROUP 2 — FORK: model_version moves when the role_innings block is present.
#
# The real model_version hashes an ALLOWLIST (MODEL_CONFIG_KEYS). role_innings
# must be on that allowlist or B4 would change pitcher output while the version
# stayed fixed -- silent provenance corruption. These checks fail loudly if the
# key was never added to the allowlist.
# ---------------------------------------------------------------------------
from src.utils.model_version import MODEL_CONFIG_KEYS  # noqa: E402

check("2a role_innings is in the model_version allowlist",
      "role_innings" in MODEL_CONFIG_KEYS,
      "add 'role_innings' to MODEL_CONFIG_KEYS in src/utils/model_version.py")

v_base = model_version(BASE_CONFIG)                       # no role_innings block
cfg_on = dict(BASE_CONFIG, role_innings=dict(B4_BLOCK))   # enabled block
v_on = model_version(cfg_on)
v_off = model_version(cfg_off)                            # disabled block present

# Enabling B4 (adding an enabled block) must fork vs a config with no block.
check("2b enabling B4 forks model_version vs no-block base", v_on != v_base,
      f"base={v_base} on={v_on}")

# The enable flag itself must be reflected in the hash: an enabled block and a
# disabled block are different model states and must not collide.
check("2c enabled vs disabled block hashes differ", v_on != v_off,
      f"off={v_off} on={v_on}")

check("2d model_version deterministic", model_version(cfg_on) == v_on)


# ---------------------------------------------------------------------------
# GROUP 3 — BIAS FIX: the actual point of B4.
# ---------------------------------------------------------------------------
est = RoleAwareInningsEstimator(cfg_on)
check("3a enabled estimator reports enabled=True", est.enabled is True)

opener = FakeSnap(innings_pitched=8.0, games_started=1, games=8)
r_opener = est.estimate_detailed(opener)
check("3a opener classified as opener", r_opener.role == "opener", r_opener.role)
check("3a opener innings short (< legacy 4.0 floor)",
      r_opener.expected_innings < 4.0,
      f"{r_opener.expected_innings} vs legacy {_legacy_reference(opener)}")

reliever = FakeSnap(innings_pitched=20.0, games_started=0, games=15)
r_rel = est.estimate_detailed(reliever)
# Pre-B4 this returned 5.5 (games_started floored to 1 killed the ratio); B4
# sees a true 0/15 start ratio -> opener bucket -> short.
check("3b pure reliever no longer projects starter length",
      r_rel.expected_innings < 4.0 and _legacy_reference(reliever) == 5.5,
      f"b4={r_rel.expected_innings} legacy={_legacy_reference(reliever)} role={r_rel.role}")

bulk = FakeSnap(innings_pitched=25.0, games_started=3, games=6)
r_bulk = est.estimate_detailed(bulk)
check("3c swing arm classified as bulk", r_bulk.role == "bulk", r_bulk.role)
check("3c bulk innings between opener and starter floor",
      B4_BLOCK["opener_innings"] < r_bulk.expected_innings <= B4_BLOCK["starter_ip_floor"],
      str(r_bulk.expected_innings))

starter = FakeSnap(innings_pitched=60.0, games_started=10, games=10)
r_start = est.estimate_detailed(starter)
check("3d genuine starter classified as starter", r_start.role == "starter", r_start.role)
check("3d starter uses honest per-start length (6.0)",
      r_start.expected_innings == 6.0, str(r_start.expected_innings))


# ---------------------------------------------------------------------------
# GROUP 4 — GUARDS: validation, clamps, thin sample, monotonicity.
# ---------------------------------------------------------------------------
# Missing required key when enabled -> loud error.
try:
    bad = dict(B4_BLOCK); del bad["opener_innings"]
    RoleAwareInningsEstimator(dict(BASE_CONFIG, role_innings=bad))
    check("4a missing required key raises", False)
except RoleInningsConfigError:
    check("4a missing required key raises", True)

# Nonsensical ordering -> loud error.
try:
    bad = dict(B4_BLOCK, starter_min_ratio=0.10, opener_max_ratio=0.50)
    RoleAwareInningsEstimator(dict(BASE_CONFIG, role_innings=bad))
    check("4b overlapping ratios raise", False)
except RoleInningsConfigError:
    check("4b overlapping ratios raise", True)

# Starter clamp: 8.0 IP/start -> ceil 7.0; 2.0 IP/start -> floor 4.0.
hi = est.estimate(FakeSnap(innings_pitched=48.0, games_started=6, games=6))
lo = est.estimate(FakeSnap(innings_pitched=12.0, games_started=6, games=6))
check("4c starter clamps to ceil", hi == 7.0, str(hi))
check("4c starter clamps to floor", lo == 4.0, str(lo))

# Thin sample (games < min_games_for_role) -> role "unknown", not invented.
thin = FakeSnap(innings_pitched=10.0, games_started=2, games=2)
r_thin = est.estimate_detailed(thin)
check("4d thin sample flagged unknown", r_thin.role == "unknown", r_thin.role)

# No-data -> default innings, flagged unknown.
r_none = est.estimate_detailed(FakeSnap())
check("4e no-data -> default_innings", r_none.expected_innings == 5.5, str(r_none.expected_innings))

# Monotonicity: within the starter band, more IP/start -> >= innings.
seq = [
    est.estimate(FakeSnap(innings_pitched=ip, games_started=6, games=6))
    for ip in (24.0, 30.0, 36.0, 42.0)
]
check("4f starter innings monotone non-decreasing in IP/start",
      all(a <= b for a, b in zip(seq, seq[1:])), str(seq))


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
sys.exit(1 if n_fail else 0)
