#!/usr/bin/env python3
"""
A6 -- K-error vs actual_ip analysis tooling (read-only, additive).

Feeds B4's gate. B4 (role-aware expected_innings) needs real evidence for its
role_innings constants (opener_innings, bulk_innings, the start_ratio cuts,
the starter innings clamp) before they're written into config.json and
gated. This tool supplies that evidence.

TWO LAYERS:

  1. HEADLINE (no extra files needed): reads the outcome pairs CSV that
     src/learning/outcome_recorder.py already writes, filters to pitcher-K
     rows, and reports K-error (actual - predicted) bucketed by actual_ip.
     This alone shows whether the LIVE model's error concentrates in short
     outings -- the documented "short-outing K over-projection".

  2. ROLE-AWARE BEFORE/AFTER (needs --roster): this imports the REAL
     src/prediction/role_innings.RoleAwareInningsEstimator -- not a
     reimplementation -- so role labels and expected_innings numbers are
     guaranteed identical to what B4 would actually ship. For each pitcher
     row it:
       a. reproduces the LEGACY expected_innings via
          RoleAwareInningsEstimator._legacy_expected_ip (the exact pre-B4
          heuristic, byte-identical to mlb_api._estimate_expected_ip);
       b. computes the CANDIDATE role-aware expected_innings via
          estimator.estimate_detailed() under a role_innings config (the
          shipped example placeholders by default, or your own candidate
          numbers via --role-innings-config);
       c. rescales predicted_value by (candidate / legacy) to get what the
          point estimate WOULD have been under that candidate config. This
          rescaling is EXACT, not approximate: prop_engine's k_prob does not
          depend on expected_innings at all (only the batters_faced
          multiplier does -- see prop_engine.project_pitcher_strikeouts),
          so predicted_value = k_prob * expected_innings * PA_PER_INNING and
          the ratio cancels k_prob cleanly.
     This gives an old-vs-new K-error comparison, by role and by actual_ip
     bucket, which is the direct "does this candidate config reduce the
     bias" evidence B4's gate decision needs.

READ-ONLY / ADDITIVE: only reads --pairs and optionally --roster /
--role-innings-config; never touches your real config.json, never calls
predict(), never forks model_version. The role_innings config used here is
built as an in-memory dict passed straight to a throwaway
RoleAwareInningsEstimator instance this script owns -- nothing is written
back anywhere. Writes a report (CSV + JSON + optional PNG) under --out-dir.

Usage:
    python run_analyze_k_error.py \
        --pairs data/learning/prediction_outcomes.csv \
        --out-dir data/analysis/k_error_vs_ip

    # with the role-aware before/after (uses config/role_innings.example.json
    # placeholders automatically if present, else this script's own copy):
    python run_analyze_k_error.py \
        --pairs data/learning/prediction_outcomes.csv \
        --roster data/analysis/pitcher_roles.csv \
        --out-dir data/analysis/k_error_vs_ip

    # testing YOUR OWN candidate constants instead of the placeholders:
    python run_analyze_k_error.py --pairs ... --roster ... \
        --role-innings-config my_candidate_role_innings.json

--roster format (CSV, one row per player_id; JSON {player_id: {...}} also
works):
    player_id,games,games_started,innings_pitched
    605483,31,31,178.1
    519242,58,0,61.2
    ...
`innings_pitched` matters: RoleAwareInningsEstimator requires ip > 0 to
assign anything other than role="unknown" (it can't route on start_ratio
without it), so without a real IP number a player's rows just fall back to
the shared default_innings on both sides of the comparison (ratio 1.0, no
change reported) -- correct, conservative behavior, not a bug, but it means
"no roster IP data" silently means "no role signal", not "role = reliever".
This is SEASON-level role signal (not point-in-time as-of-game_date) -- fine
for this descriptive fit; B4's actual gate reconstruction should use
point-in-time snapshots like the rest of this project does.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

# --- Schema: reuse the single source of truth from outcome_recorder.py when
# the full package is importable; fall back to a pinned copy of the documented
# schema otherwise, so this read-only tool never hard-fails just because some
# heavier src/ dependency chain (catboost, requests, etc.) isn't installed in
# a lightweight analysis-only environment. Same "one ruler" spirit as the rest
# of the project's try/except import shims.
try:
    from src.learning.outcome_recorder import PAIR_COLUMNS  # type: ignore
except Exception:  # pragma: no cover - defensive fallback only
    PAIR_COLUMNS = [
        "player_id", "player_name", "game_date", "category",
        "predicted_value", "actual_value", "confidence", "model_version",
        "actual_pa", "actual_hits", "actual_home_runs", "actual_runs",
        "actual_rbi", "actual_walks", "actual_strikeouts", "actual_ip",
        "actual_bb_allowed", "actual_hr_allowed",
    ]

# --- The real B4 estimator. This is the whole point of layer 2: no parallel
# reimplementation of role_innings' thresholds -- import the actual class so
# role labels / expected_innings can never drift from what B4 ships.
try:
    from src.prediction.role_innings import (  # type: ignore
        RoleAwareInningsEstimator,
        RoleInningsConfigError,
    )
    _ROLE_INNINGS_IMPORT_ERROR: Optional[str] = None
except Exception as exc:  # pragma: no cover - defensive fallback only
    RoleAwareInningsEstimator = None  # type: ignore
    RoleInningsConfigError = ValueError  # type: ignore
    _ROLE_INNINGS_IMPORT_ERROR = str(exc)

DEFAULT_IP_BINS = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 9.0, 15.0]

# Mirrors config/role_innings.example.json's placeholder numbers exactly, as
# a last-resort default when neither --role-innings-config nor
# config/role_innings.example.json is found. Keep in sync with that file;
# --role-innings-config always wins when given.
DEFAULT_ROLE_INNINGS_BLOCK: dict[str, Any] = {
    "starter_min_ratio": 0.80,
    "opener_max_ratio": 0.20,
    "min_games_for_role": 3,
    "starter_ip_floor": 4.0,
    "starter_ip_ceil": 7.0,
    "bulk_innings": 3.5,
    "opener_innings": 1.5,
    "default_innings": 5.5,
}


@dataclass
class _RecentStub:
    """
    Minimal stand-in for the PitchingStatsSnapshot RoleAwareInningsEstimator
    expects. It only reads .games / .games_started / .innings_pitched via
    getattr with defaults, so this plain object (no mlb_api import, no
    requests dependency) is all it needs.
    """

    games: int = 0
    games_started: int = 0
    innings_pitched: float = 0.0


# ---------------------------------------------------------------------------
# Loading: pairs CSV
# ---------------------------------------------------------------------------

def load_k_rows(pairs_path: Path, model_version: Optional[str] = None) -> pd.DataFrame:
    """
    Load the outcome pairs CSV and return only usable pitcher-strikeout rows.

    A row is usable when it has category == "strikeouts", a numeric
    predicted_value, a numeric actual_strikeouts, and a real actual_ip > 0.
    """
    if not pairs_path.exists():
        raise FileNotFoundError(
            f"Pairs CSV not found: {pairs_path}. This tool is read-only and "
            f"expects the file src/learning/outcome_recorder.py already writes."
        )

    df = pd.read_csv(pairs_path, dtype=str, keep_default_na=False, low_memory=False)
    missing_cols = [c for c in ("category", "predicted_value", "actual_strikeouts", "actual_ip",
                                 "player_id", "game_date") if c not in df.columns]
    if missing_cols:
        raise ValueError(
            f"{pairs_path} is missing expected columns {missing_cols}. "
            f"This looks like a legacy (pre-detail-column) pairs CSV -- run "
            f"OutcomeRecorder once (it auto-migrates the schema) before using this tool."
        )

    k = df[df["category"] == "strikeouts"].copy()
    n_total = len(k)

    for col in ("predicted_value", "actual_strikeouts", "actual_ip", "confidence"):
        k[col] = pd.to_numeric(k[col], errors="coerce")
    k["player_id"] = pd.to_numeric(k["player_id"], errors="coerce")

    before = len(k)
    k = k[k["predicted_value"].notna() & k["actual_strikeouts"].notna() & k["actual_ip"].notna()]
    k = k[k["actual_ip"] > 0]
    dropped = before - len(k)
    if dropped:
        print(f"[load] dropped {dropped} strikeouts row(s) missing predicted_value / "
              f"actual_strikeouts / actual_ip>0 (out of {before} strikeouts rows, "
              f"{n_total} total pair rows)")

    versions = sorted(v for v in k["model_version"].unique() if v)
    if len(versions) > 1:
        print(f"[load] WARNING: rows span {len(versions)} model_versions {versions}. "
              f"K's point-estimate formula is unchanged since B3 (additive), so mixing is "
              f"expected pre-B4 -- but re-check this the moment B4 forks the hash; a "
              f"role-aware version's rows must not be pooled with pre-B4 rows here.")
    if model_version:
        k = k[k["model_version"] == model_version]
        print(f"[load] filtered to model_version={model_version}: {len(k)} rows")

    if k.empty:
        raise ValueError(
            "No usable strikeouts rows after filtering. Nothing to analyze yet -- "
            "this is expected until forward K pairs accrue (see Known Issues: K sample "
            "is thin)."
        )

    k["k_error"] = k["actual_strikeouts"] - k["predicted_value"]
    k["abs_k_error"] = k["k_error"].abs()
    with np.errstate(divide="ignore", invalid="ignore"):
        k["pct_k_error"] = np.where(
            k["predicted_value"] > 0, k["k_error"] / k["predicted_value"], np.nan
        )
    return k.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Loading: roster + role_innings config
# ---------------------------------------------------------------------------

def load_roster(path: Path) -> pd.DataFrame:
    """
    Load an auxiliary player_id -> (games, games_started, innings_pitched)
    table. Accepts CSV or JSON ({player_id: {"games":.., "games_started":..,
    "innings_pitched":..}}). innings_pitched is optional (defaults to 0.0,
    with a warning -- see module docstring on why that means role="unknown").
    """
    if path.suffix.lower() == ".json":
        raw = json.loads(path.read_text(encoding="utf-8"))
        rows = [
            {"player_id": int(pid), "games": int(v.get("games", 0)),
             "games_started": int(v.get("games_started", 0)),
             "innings_pitched": float(v.get("innings_pitched", 0.0))}
            for pid, v in raw.items()
        ]
        roster = pd.DataFrame(rows)
    else:
        roster = pd.read_csv(path)
        required = {"player_id", "games", "games_started"}
        missing = required - set(roster.columns)
        if missing:
            raise ValueError(f"{path} is missing columns {sorted(missing)}")
        if "innings_pitched" not in roster.columns:
            print("[roster] no innings_pitched column -- RoleAwareInningsEstimator requires "
                  "ip>0 to assign any role other than 'unknown', so every row will classify as "
                  "unknown and the before/after comparison will show no change (correct "
                  "fallback behavior, not a bug). Add innings_pitched for a real role split.")
            roster["innings_pitched"] = 0.0
        for col in ("player_id", "games", "games_started", "innings_pitched"):
            roster[col] = pd.to_numeric(roster[col], errors="coerce")
    return roster


def load_role_innings_block(path: Optional[Path]) -> tuple[dict[str, Any], str]:
    """
    Build the role_innings config block used to construct this script's OWN
    throwaway RoleAwareInningsEstimator. Returns (block, source_note).

    enabled is always forced True on the in-memory copy: this has zero live-
    path effect (the estimator instance lives only inside this analysis run
    and nothing here reads or writes your real config.json), and without it
    every row would classify as legacy/disabled and there'd be nothing to
    compare.
    """
    if path is not None:
        payload = json.loads(path.read_text(encoding="utf-8"))
        block = dict(payload.get("role_innings", payload))
        source = str(path)
    else:
        default_path = Path("config/role_innings.example.json")
        if default_path.exists():
            payload = json.loads(default_path.read_text(encoding="utf-8"))
            block = dict(payload.get("role_innings", payload))
            source = str(default_path) + " (auto-discovered)"
        else:
            block = dict(DEFAULT_ROLE_INNINGS_BLOCK)
            source = "built-in placeholder mirror (config/role_innings.example.json not found)"

    was_enabled = bool(block.get("enabled", False))
    block["enabled"] = True
    note = f"role_innings config source: {source}"
    if not was_enabled:
        note += " (had enabled=false, as it should during collection -- forcing " \
                "enabled=true on an in-memory COPY only, for this analysis run)"
    print(f"[role] {note}")
    return block, source


# ---------------------------------------------------------------------------
# Role-aware before/after (layer 2)
# ---------------------------------------------------------------------------

def add_role_and_recompute(k: pd.DataFrame, roster: pd.DataFrame,
                            estimator: "RoleAwareInningsEstimator") -> pd.DataFrame:
    """
    Join roster onto k, classify each pitcher with the REAL estimator, and
    compute what predicted_value WOULD have been under that estimator's
    expected_innings (exact rescale -- see module docstring).
    """
    merged = k.merge(roster, on="player_id", how="left", suffixes=("", "_roster"))
    n_unmatched = int(merged["games"].isna().sum())
    if n_unmatched:
        print(f"[role] {n_unmatched} row(s) have no --roster match "
              f"(role='no_roster_row', excluded from role verdicts)")

    roles: list[str] = []
    ratios: list[Optional[float]] = []
    legacy_ip: list[float] = []
    b4_ip: list[float] = []
    for _, row in merged.iterrows():
        if pd.isna(row.get("games")):
            roles.append("no_roster_row")
            ratios.append(None)
            legacy_ip.append(np.nan)
            b4_ip.append(np.nan)
            continue
        stub = _RecentStub(
            games=int(row["games"]),
            games_started=int(row["games_started"]),
            innings_pitched=float(row["innings_pitched"]),
        )
        legacy = RoleAwareInningsEstimator._legacy_expected_ip(stub)
        result = estimator.estimate_detailed(stub)
        roles.append(result.role)
        ratios.append(result.start_ratio)
        legacy_ip.append(legacy)
        b4_ip.append(result.expected_innings)

    merged["role"] = roles
    merged["start_ratio"] = ratios
    merged["legacy_expected_innings"] = legacy_ip
    merged["b4_expected_innings"] = b4_ip

    can_recompute = merged["legacy_expected_innings"].notna() & (merged["legacy_expected_innings"] > 0)
    ratio_mult = pd.Series(np.nan, index=merged.index)
    ratio_mult[can_recompute] = (
        merged.loc[can_recompute, "b4_expected_innings"]
        / merged.loc[can_recompute, "legacy_expected_innings"]
    )
    merged["new_predicted_k"] = merged["predicted_value"] * ratio_mult
    merged["new_k_error"] = merged["actual_strikeouts"] - merged["new_predicted_k"]

    low_info = int(((merged["innings_pitched"].fillna(0) <= 0) & merged["games"].notna()).sum())
    if low_info:
        print(f"[role] {low_info} matched row(s) have innings_pitched<=0 in --roster -- "
              f"both legacy and B4 fall back to their own default_innings for these (ratio "
              f"~1.0, no change reported: correct, not a bug, just no signal to act on).")
    return merged


def _rmse(series: pd.Series) -> float:
    series = series.dropna()
    if series.empty:
        return float("nan")
    return float(np.sqrt((series ** 2).mean()))


def role_ip_recompute_report(merged: pd.DataFrame, ip_bins: list[float]) -> pd.DataFrame:
    """Old-vs-new K-error, cross-tabbed by (role, actual_ip bucket)."""
    valid = merged.dropna(subset=["new_k_error"]).copy()
    if valid.empty:
        return pd.DataFrame()
    labels = [f"[{ip_bins[i]:g},{ip_bins[i+1]:g})" for i in range(len(ip_bins) - 1)]
    valid["ip_bucket"] = pd.cut(valid["actual_ip"], bins=ip_bins, labels=labels, right=False,
                                 include_lowest=True)
    rows = []
    for (role, ip_bucket), group in valid.groupby(["role", "ip_bucket"], observed=True):
        if group.empty:
            continue
        rows.append({
            "role": role, "ip_bucket": ip_bucket, "n": int(len(group)),
            "mean_actual_ip": round(float(group["actual_ip"].mean()), 2),
            "old_mean_error": round(float(group["k_error"].mean()), 3),
            "old_rmse": round(_rmse(group["k_error"]), 3),
            "new_mean_error": round(float(group["new_k_error"].mean()), 3),
            "new_rmse": round(_rmse(group["new_k_error"]), 3),
            "rmse_improvement": round(_rmse(group["k_error"]) - _rmse(group["new_k_error"]), 3),
        })
    cols = ["role", "ip_bucket", "n", "mean_actual_ip", "old_mean_error", "old_rmse",
            "new_mean_error", "new_rmse", "rmse_improvement"]
    out = pd.DataFrame(rows)
    return out[cols] if not out.empty else out


def per_role_summary(merged: pd.DataFrame) -> pd.DataFrame:
    """One row per role: empirical mean actual_ip vs the config's assigned expected_innings."""
    valid = merged.dropna(subset=["new_k_error"]).copy()
    if valid.empty:
        return pd.DataFrame()
    rows = []
    for role, group in valid.groupby("role", observed=True):
        rows.append({
            "role": role, "n": int(len(group)),
            "mean_actual_ip": round(float(group["actual_ip"].mean()), 2),
            "config_expected_innings": round(float(group["b4_expected_innings"].mean()), 2),
            "old_rmse": round(_rmse(group["k_error"]), 3),
            "new_rmse": round(_rmse(group["new_k_error"]), 3),
            "old_mean_error": round(float(group["k_error"].mean()), 3),
            "new_mean_error": round(float(group["new_k_error"].mean()), 3),
        })
    cols = ["role", "n", "mean_actual_ip", "config_expected_innings", "old_rmse", "new_rmse",
            "old_mean_error", "new_mean_error"]
    return pd.DataFrame(rows)[cols]


# ---------------------------------------------------------------------------
# Layer 1: actual_ip-only bucketing (no roster needed)
# ---------------------------------------------------------------------------

def _bucket_stats(group: pd.DataFrame) -> dict[str, Any]:
    return {
        "n": int(len(group)),
        "mean_actual_ip": round(float(group["actual_ip"].mean()), 2),
        "mean_predicted_k": round(float(group["predicted_value"].mean()), 2),
        "mean_actual_k": round(float(group["actual_strikeouts"].mean()), 2),
        "mean_error": round(float(group["k_error"].mean()), 3),
        "median_error": round(float(group["k_error"].median()), 3),
        "mean_abs_error": round(float(group["abs_k_error"].mean()), 3),
        "rmse": round(_rmse(group["k_error"]), 3),
        "mean_pct_error": (
            round(float(group["pct_k_error"].mean()), 3)
            if group["pct_k_error"].notna().any() else None
        ),
        "pct_over_projected": round(float((group["k_error"] < 0).mean()), 3),
    }


def ip_bucket_report(k: pd.DataFrame, ip_bins: list[float]) -> pd.DataFrame:
    """The headline K-error-vs-actual_ip split, bucketed by actual_ip."""
    labels = [f"[{ip_bins[i]:g},{ip_bins[i+1]:g})" for i in range(len(ip_bins) - 1)]
    k = k.copy()
    k["ip_bucket"] = pd.cut(k["actual_ip"], bins=ip_bins, labels=labels, right=False,
                             include_lowest=True)
    rows = []
    for label, group in k.groupby("ip_bucket", observed=True):
        if len(group) == 0:
            continue
        stats = _bucket_stats(group)
        stats["ip_bucket"] = label
        rows.append(stats)
    cols = ["ip_bucket", "n", "mean_actual_ip", "mean_predicted_k", "mean_actual_k",
            "mean_error", "median_error", "mean_abs_error", "rmse", "mean_pct_error",
            "pct_over_projected"]
    out = pd.DataFrame(rows)
    return out[cols] if not out.empty else out


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def print_table(df: pd.DataFrame, title: str) -> None:
    print(f"\n{title}")
    print("-" * len(title))
    if df.empty:
        print("(no rows)")
        return
    print(df.to_string(index=False))


def maybe_plot(k: pd.DataFrame, ip_report: pd.DataFrame, out_dir: Path) -> Optional[Path]:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("[plot] matplotlib not available; skipping PNG (CSV/JSON reports still written)")
        return None

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))

    ax1.scatter(k["actual_ip"], k["k_error"], alpha=0.25, s=14)
    ax1.axhline(0.0, color="black", linewidth=0.8)
    ax1.set_xlabel("actual_ip")
    ax1.set_ylabel("K error (actual - predicted)")
    ax1.set_title("Per-row K error vs actual_ip")

    x = np.arange(len(ip_report))
    ax2.bar(x, ip_report["mean_error"], yerr=ip_report["rmse"], capsize=3)
    ax2.axhline(0.0, color="black", linewidth=0.8)
    ax2.set_xticks(x)
    ax2.set_xticklabels(ip_report["ip_bucket"], rotation=45, ha="right")
    ax2.set_ylabel("mean K error")
    ax2.set_title("Mean K error by actual_ip bucket (error bar = RMSE)")

    fig.tight_layout()
    out_path = out_dir / "k_error_vs_actual_ip.png"
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return out_path


def suggest_constants(ip_report: pd.DataFrame, role_summary_df: Optional[pd.DataFrame],
                       role_block: Optional[dict[str, Any]]) -> None:
    """
    Print descriptive suggestions only -- this tool never edits config.json.
    B4's own doc is explicit that the config edit happens once, at the gate,
    by a person deciding the fork is warranted; these numbers are candidate
    inputs to that decision, not a recommendation to apply them unreviewed.
    """
    print("\nSUGGESTIONS FOR B4 (descriptive only -- review before editing config.json)")
    print("-" * 72)
    if ip_report.empty:
        print("(no actual_ip buckets to suggest from)")
        return
    worst = ip_report.loc[ip_report["mean_error"].abs().idxmax()]
    print(f"Largest bias bucket (current live model): {worst['ip_bucket']} (n={worst['n']}, "
          f"mean_error={worst['mean_error']:+.2f})")
    near_zero = ip_report[ip_report["mean_error"].abs() <= ip_report["rmse"].median() * 0.25]
    if not near_zero.empty:
        first_ok = near_zero.iloc[0]
        print(f"Bias first looks noise-level around actual_ip bucket {first_ok['ip_bucket']} "
              f"(mean_error={first_ok['mean_error']:+.2f}).")

    if role_summary_df is None or role_summary_df.empty:
        print("No --roster given, so this is the actual_ip-only view; pass --roster to compare "
              "the candidate role_innings config against realized actual_ip directly.")
        return

    for _, row in role_summary_df.iterrows():
        if row["role"] not in ("opener", "bulk", "starter"):
            continue
        config_key = {"opener": "opener_innings", "bulk": "bulk_innings",
                      "starter": "starter_ip_floor/ceil"}[row["role"]]
        config_val = row["config_expected_innings"]
        gap = row["mean_actual_ip"] - config_val
        print(f"role={row['role']:<8} config {config_key}={config_val:.2f}  "
              f"empirical mean_actual_ip={row['mean_actual_ip']:.2f}  (gap={gap:+.2f}, n={row['n']})  "
              f"RMSE old={row['old_rmse']:.2f} -> new={row['new_rmse']:.2f}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    args = parse_args(argv)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    k = load_k_rows(Path(args.pairs), model_version=args.model_version)

    print(f"\n{'=' * 72}\nA6 -- K-error vs actual_ip  ({len(k)} strikeouts rows)\n{'=' * 72}")
    overall = {
        "n": int(len(k)),
        "mean_error": round(float(k["k_error"].mean()), 3),
        "median_error": round(float(k["k_error"].median()), 3),
        "mean_abs_error": round(float(k["abs_k_error"].mean()), 3),
        "rmse": round(_rmse(k["k_error"]), 3),
        "pct_over_projected": round(float((k["k_error"] < 0).mean()), 3),
    }
    print("\nOVERALL (current live model)")
    print("-----------------------------")
    for key, val in overall.items():
        print(f"  {key}: {val}")

    ip_bins = [float(x) for x in args.ip_bins.split(",")]
    ip_report = ip_bucket_report(k, ip_bins)
    print_table(ip_report, "K-ERROR BY actual_ip BUCKET (the headline split)")

    role_ip_df = None
    role_sum_df = None
    role_block = None
    if args.roster:
        if RoleAwareInningsEstimator is None:
            print(f"\n[role] --roster given but src/prediction/role_innings.py could not be "
                  f"imported ({_ROLE_INNINGS_IMPORT_ERROR}). Make sure you're running from the "
                  f"repo root. Skipping the role-aware comparison; the actual_ip split above "
                  f"is unaffected.")
        else:
            roster = load_roster(Path(args.roster))
            role_config_path = Path(args.role_innings_config) if args.role_innings_config else None
            estimator = None
            try:
                role_block, _ = load_role_innings_block(role_config_path)
                estimator = RoleAwareInningsEstimator({"role_innings": role_block})
            except RoleInningsConfigError as exc:
                print(f"[role] role_innings config is invalid: {exc}. Skipping the role-aware "
                      f"comparison; the actual_ip split above is unaffected.")
            if estimator is not None:
                merged = add_role_and_recompute(k, roster, estimator)
                role_ip_df = role_ip_recompute_report(merged, ip_bins)
                print_table(role_ip_df, "ROLE x actual_ip: OLD vs NEW K-error (candidate role_innings config)")
                role_sum_df = per_role_summary(merged)
                print_table(role_sum_df, "PER-ROLE SUMMARY (config expected_innings vs empirical actual_ip)")
    else:
        print("\n(no --roster given -- skipping the role-aware before/after; "
              "the actual_ip split above is still the headline A6 deliverable)")

    suggest_constants(ip_report, role_sum_df, role_block)

    # Write artifacts (gitignored-friendly location; regenerable from --pairs).
    ip_report.to_csv(out_dir / "k_error_by_actual_ip.csv", index=False)
    summary_payload = {"overall": overall, "ip_bins": ip_bins, "n_rows": len(k)}
    if role_ip_df is not None:
        role_ip_df.to_csv(out_dir / "k_error_role_ip_recompute.csv", index=False)
        role_sum_df.to_csv(out_dir / "k_error_role_summary.csv", index=False)
        summary_payload["role_innings_block_used"] = role_block
    (out_dir / "k_error_summary.json").write_text(
        json.dumps(summary_payload, indent=2, default=str), encoding="utf-8"
    )

    if not args.no_plot:
        png = maybe_plot(k, ip_report, out_dir)
        if png:
            print(f"\nPlot: {png}")

    print(f"\nReports written to {out_dir}/")
    print("Reminder: this is descriptive evidence for B4, not a gate verdict. The gate "
          "itself is run_gate_reconstruct.py + the block-bootstrap-over-dates compare "
          "in run_calibration_gate.py, AFTER role_innings constants are chosen from this "
          "and written into config.json.")
    return 0


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="A6: K-error vs actual_ip analysis tooling (feeds B4's gate).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--pairs", default="data/learning/prediction_outcomes.csv",
                    help="Outcome pairs CSV (default: %(default)s)")
    p.add_argument("--out-dir", default="data/analysis/k_error_vs_ip",
                    help="Where to write report artifacts (default: %(default)s)")
    p.add_argument("--ip-bins", default=",".join(str(x) for x in DEFAULT_IP_BINS),
                    help="Comma-separated actual_ip bin edges (default: %(default)s)")
    p.add_argument("--roster", default=None,
                    help="Optional player_id->games/games_started/innings_pitched CSV or JSON "
                         "for the role-aware before/after comparison")
    p.add_argument("--role-innings-config", default=None,
                    help="Optional path to a role_innings config JSON (same shape as "
                         "config/role_innings.example.json) to test candidate constants. "
                         "Default: auto-discover config/role_innings.example.json, else use "
                         "this script's built-in copy of the shipped placeholders.")
    p.add_argument("--model-version", default=None,
                    help="Filter to a single model_version (default: use all present rows)")
    p.add_argument("--no-plot", action="store_true", help="Skip the PNG (CSV/JSON only)")
    return p.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())
