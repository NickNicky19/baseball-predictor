"""
B4 gate prep — fit role->innings constants from real actual_ip data.

WHAT IS FITTED vs WHAT IS STRUCTURAL
------------------------------------
Fitted (per-role, from realized actual_ip on graded K rows):
  opener_innings, bulk_innings          — the actual short-outing bias fix.
Structural (kept at the shipped placeholders; printed as evidence only):
  starter_min_ratio / opener_max_ratio  — role CUTS; role assignment must not
                                          be circularly re-fit from the
                                          innings it produces.
  starter_ip_floor / starter_ip_ceil    — a clamp on per-start AVG innings;
                                          single-game actual_ip percentiles
                                          conflate outing variance with
                                          per-start averages, so they are
                                          reported for a human to weigh, not
                                          auto-written.
  min_games_for_role, default_innings   — thin-sample / no-data fallbacks;
                                          fitting a fallback from rows that
                                          HAD data is the wrong population.

THIN-SAMPLE HONESTY (the recurring PROJECT_CONTEXT discipline): a role with
fewer than --min-n graded rows keeps its placeholder, loudly flagged. If BOTH
opener and bulk are thin, this prints DO-NOT-GATE-YET — per the doc, the K
sample was ~4 short-outing rows as of 2026-07-10 and magnitudes were not
fittable; this tool refuses to pretend otherwise.

Role labels come from the REAL RoleAwareInningsEstimator via A6's own
add_role_and_recompute (imported, not reimplemented — same one-ruler move as
[A6]), classified under the placeholder cuts.

READ-ONLY beyond --out: never edits config.json. It PRINTS the exact block
and the old/new model_version so the human doing the gated flip can see the
fork before making it.

Usage (from repo root):
    python scripts/fit_role_innings.py \
        --pairs data/learning/prediction_outcomes.csv \
        --roster data/analysis/pitcher_roles.csv \
        --out data/analysis/candidate_role_innings.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from run_analyze_k_error import (  # noqa: E402 — A6's loaders/classifier, one ruler
    DEFAULT_ROLE_INNINGS_BLOCK,
    add_role_and_recompute,
    load_k_rows,
    load_roster,
)
from src.prediction.role_innings import (  # noqa: E402
    RoleAwareInningsEstimator,
    RoleInningsConfigError,
)


def fit_constants(
    merged: pd.DataFrame,
    placeholder: dict[str, Any],
    min_n: int = 15,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Returns (fitted_block, fit_report). fitted_block is a full role_innings
    block (enabled=True) ready for --role-innings-config testing and, after
    the gate, for config.json. fit_report records per-role evidence and
    which values were fitted vs placeholder-kept.
    """
    block = dict(placeholder)
    block["enabled"] = True
    report: dict[str, Any] = {"min_n": min_n, "roles": {}, "fitted": [], "kept_placeholder": []}

    by_role: dict[str, pd.DataFrame] = {
        role: grp for role, grp in merged.groupby("role", observed=True)
    }

    for role, key in (("opener", "opener_innings"), ("bulk", "bulk_innings")):
        grp = by_role.get(role, pd.DataFrame())
        n = int(len(grp))
        stats = {
            "n": n,
            "mean_actual_ip": round(float(grp["actual_ip"].mean()), 2) if n else None,
            "median_actual_ip": round(float(grp["actual_ip"].median()), 2) if n else None,
        }
        report["roles"][role] = stats
        if n >= min_n:
            fitted = round(float(grp["actual_ip"].mean()), 1)
            if fitted <= 0:  # estimator validation requires > 0
                report["kept_placeholder"].append(key)
                stats["decision"] = f"kept placeholder {block[key]} (fitted {fitted} <= 0)"
                continue
            block[key] = fitted
            report["fitted"].append(key)
            stats["decision"] = f"fitted {key} = {fitted} from n={n} rows"
        else:
            report["kept_placeholder"].append(key)
            stats["decision"] = (f"kept placeholder {block[key]} "
                                 f"(n={n} < min_n={min_n} — not fittable yet)")

    # Starter evidence: reported only; the clamp stays structural (see docstring).
    starters = by_role.get("starter", pd.DataFrame())
    if len(starters):
        p10, p50, p90 = (float(np.percentile(starters["actual_ip"], q)) for q in (10, 50, 90))
        report["roles"]["starter"] = {
            "n": int(len(starters)),
            "actual_ip_p10": round(p10, 2),
            "actual_ip_p50": round(p50, 2),
            "actual_ip_p90": round(p90, 2),
            "decision": (f"clamp kept at [{block['starter_ip_floor']}, "
                         f"{block['starter_ip_ceil']}] (structural); percentiles are "
                         f"evidence for a HUMAN adjustment only"),
        }

    # Ordering sanity the estimator itself enforces only partially: keep the
    # fitted values consistent with the role semantics.
    if block["opener_innings"] >= block["bulk_innings"]:
        report["ordering_note"] = (
            f"fitted opener_innings {block['opener_innings']} >= bulk_innings "
            f"{block['bulk_innings']} — reverting BOTH to placeholders; data too "
            f"thin/odd to trust")
        block["opener_innings"] = placeholder["opener_innings"]
        block["bulk_innings"] = placeholder["bulk_innings"]
        report["fitted"] = [k for k in report["fitted"]
                            if k not in ("opener_innings", "bulk_innings")]
        for k in ("opener_innings", "bulk_innings"):
            if k not in report["kept_placeholder"]:
                report["kept_placeholder"].append(k)
    if block["bulk_innings"] > block["starter_ip_floor"]:
        report.setdefault("ordering_note", "")
        report["ordering_note"] += (
            f" bulk_innings {block['bulk_innings']} > starter_ip_floor "
            f"{block['starter_ip_floor']} — clamping bulk to the floor.")
        block["bulk_innings"] = block["starter_ip_floor"]

    # The block must construct cleanly through the REAL estimator or it is
    # not a candidate at all.
    RoleAwareInningsEstimator({"role_innings": block})  # raises on bad config
    return block, report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Fit B4 role_innings constants from realized actual_ip.")
    ap.add_argument("--pairs", default="data/learning/prediction_outcomes.csv")
    ap.add_argument("--roster", required=True, help="from scripts/build_pitcher_roster.py")
    ap.add_argument("--out", default="data/analysis/candidate_role_innings.json")
    ap.add_argument("--config", default="config/config.json",
                    help="real config, used ONLY to preview the model_version fork")
    ap.add_argument("--min-n", type=int, default=15,
                    help="min graded K rows per role to fit that role's constant (default 15)")
    ap.add_argument("--model-version", default=None,
                    help="filter pairs to one model_version (recommended: the frozen hash)")
    args = ap.parse_args(argv)

    k = load_k_rows(Path(args.pairs), model_version=args.model_version)
    roster = load_roster(Path(args.roster))
    placeholder = dict(DEFAULT_ROLE_INNINGS_BLOCK)
    estimator = RoleAwareInningsEstimator({"role_innings": dict(placeholder, enabled=True)})
    merged = add_role_and_recompute(k, roster, estimator)
    usable = merged[merged["role"].isin(["opener", "bulk", "starter", "unknown"])]

    try:
        block, report = fit_constants(usable, placeholder, min_n=args.min_n)
    except RoleInningsConfigError as exc:
        print(f"[fit] fitted block failed estimator validation: {exc}", file=sys.stderr)
        return 2

    print("\nFIT REPORT")
    print("-" * 60)
    print(json.dumps(report, indent=2, default=str))
    print("\nCANDIDATE role_innings BLOCK (for --role-innings-config, then config.json AT THE GATE)")
    print("-" * 60)
    print(json.dumps({"role_innings": block}, indent=2))

    both_thin = {"opener_innings", "bulk_innings"} <= set(report["kept_placeholder"])
    if both_thin:
        print("\n*** DO-NOT-GATE-YET: both opener and bulk are below --min-n. The constants "
              "above are the UNVALIDATED placeholders. Keep collecting forward K pairs "
              "(run_slate.py daily) and re-run this fit — do not fork model_version on "
              "numbers the data cannot support. ***")

    # model_version fork preview against the REAL config.
    cfg_path = Path(args.config)
    if cfg_path.exists():
        from src.utils.model_version import model_version  # noqa: E402
        config = json.loads(cfg_path.read_text(encoding="utf-8"))
        old = model_version(config)
        new = model_version(dict(config, role_innings=block))
        print(f"\nmodel_version preview: {old} (frozen)  ->  {new} (if this block is added)")
        if "role_innings" in config:
            print("WARNING: config.json ALREADY contains a role_innings block — the hash "
                  "above is not the frozen baseline. Investigate before proceeding.")
    else:
        print(f"\n[fit] {cfg_path} not found — skipping model_version preview")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"role_innings": block, "_fit_report": report},
                              indent=2, default=str), encoding="utf-8")
    print(f"\nWrote {out}")
    print("Next: python run_analyze_k_error.py --pairs {p} --roster {r} "
          "--role-innings-config {o}   (before/after evidence, then the gate)".format(
              p=args.pairs, r=args.roster, o=out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
