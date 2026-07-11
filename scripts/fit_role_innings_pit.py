"""
B4 gate prep — fit role->innings constants from POINT-IN-TIME labelled rows.

WHY THIS EXISTS AND NOT scripts/fit_role_innings.py
---------------------------------------------------
fit_role_innings.py routes through run_analyze_k_error.add_role_and_recompute,
which does:

    merged = k.merge(roster, on="player_id", how="left", ...)

It merges the roster on player_id ALONE. That is correct for A6 (a descriptive,
season-level before/after on the forward pairs), but it structurally cannot
carry a per-DATE role: every pitcher collapses to ONE label for the whole
season. A guy who opened in April and started in September gets a single
season-level role.

That is exactly the ruler the gate does NOT use. The gate labels roles from a
5-game trailing POINT-IN-TIME window (see build_pit_role_rows.py's docstring).
scripts/build_pit_role_rows.py already computed that per-row label with the
REAL RoleAwareInningsEstimator. Re-deriving it through a season-level join
would throw it away.

(There is also a hard blocker: load_k_rows reads its file with dtype=str while
load_roster infers dtypes, so pointing BOTH at one file makes the merge keep
the STRING games/innings_pitched columns and add_role_and_recompute dies with
"TypeError: '<=' not supported between 'str' and 'int'".)

WHAT THIS REUSES (one ruler, deliberately)
------------------------------------------
The FITTING LOGIC is not reimplemented. This tool imports the REAL
fit_constants() from scripts/fit_role_innings.py, so the min-n thin-sample
rule, the opener<bulk ordering guard, the bulk<=starter_ip_floor clamp, the
"which keys are structural vs fitted" policy, and the final
RoleAwareInningsEstimator validation are all the SAME code. The only thing
this module changes is WHERE the (role, actual_ip) frame comes from: the
point-in-time rows, not a season-level roster join.

STRUCTURAL vs FITTED is unchanged from fit_role_innings' own policy:
  fitted     : opener_innings, bulk_innings   (magnitudes, from actual_ip)
  structural : the ratio CUTS, the starter clamp, min_games_for_role,
               default_innings  -- role assignment must not be re-fit from the
               innings it produces.

READ-ONLY beyond --out: never edits config.json. Prints the exact candidate
block and the old/new model_version so the human doing the gated flip sees the
fork BEFORE making it.

Usage (from repo root, no network):
    python scripts/fit_role_innings_pit.py \
        --rows data/analysis/b4/k_rows_pit_2023.csv \
        --out data/analysis/b4/candidate_role_innings.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from run_analyze_k_error import DEFAULT_ROLE_INNINGS_BLOCK  # noqa: E402
from src.prediction.role_innings import RoleInningsConfigError  # noqa: E402

# THE REAL FITTER. Not reimplemented -- same min-n rule, same ordering guards,
# same clamp, same estimator validation.
from scripts.fit_role_innings import fit_constants  # noqa: E402

FIT_ROLES = ("opener", "bulk", "starter")


def load_rows(path: Path) -> pd.DataFrame:
    """Point-in-time rows from build_pit_role_rows.py. Needs only role + actual_ip."""
    if not path.exists():
        raise FileNotFoundError(
            f"rows file not found: {path}. Build it first with "
            f"scripts/build_pit_role_rows.py."
        )
    df = pd.read_csv(path)
    need = {"role", "actual_ip"}
    missing = need - set(df.columns)
    if missing:
        raise ValueError(
            f"{path} missing columns {sorted(missing)}. This tool consumes "
            f"build_pit_role_rows.py's output, not a pairs CSV."
        )
    df["actual_ip"] = pd.to_numeric(df["actual_ip"], errors="coerce")
    before = len(df)
    df = df[df["actual_ip"].notna() & (df["actual_ip"] > 0)]
    if len(df) != before:
        print(f"[fit] dropped {before - len(df)} row(s) with actual_ip <= 0 / NaN")
    return df


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Fit B4 role_innings constants from POINT-IN-TIME labelled rows.")
    ap.add_argument("--rows", required=True,
                    help="output of scripts/build_pit_role_rows.py")
    ap.add_argument("--out", default="data/analysis/b4/candidate_role_innings.json")
    ap.add_argument("--config", default="config/config.json",
                    help="real config, used ONLY to preview the model_version fork")
    ap.add_argument("--min-n", type=int, default=15,
                    help="min rows per role to fit that role's constant (default 15)")
    args = ap.parse_args(argv)

    df = load_rows(Path(args.rows))

    print(f"[fit] {len(df)} point-in-time rows from {args.rows}")
    print("\nROLE DISTRIBUTION (the population being fitted)")
    print("-" * 72)
    dist = df.groupby("role", observed=True).agg(
        n=("actual_ip", "size"),
        mean_actual_ip=("actual_ip", "mean"),
        median_actual_ip=("actual_ip", "median"),
    ).round(3)
    print(dist.to_string())

    # 'unknown' / 'legacy' rows are the honest thin-sample fallback: the
    # estimator refused to assign a role. They must not enter the fit -- fitting
    # a role constant from rows that HAVE NO ROLE is the wrong population (the
    # same argument fit_role_innings makes for not fitting default_innings from
    # rows that had data).
    usable = df[df["role"].isin(FIT_ROLES)].copy()
    n_excluded = len(df) - len(usable)
    if n_excluded:
        print(f"\n[fit] excluded {n_excluded} row(s) with role not in {FIT_ROLES} "
              f"(thin-sample 'unknown' — no role to fit from; correct, not a bug)")

    if usable.empty:
        print("[fit] no usable rows — nothing to fit", file=sys.stderr)
        return 2

    placeholder = dict(DEFAULT_ROLE_INNINGS_BLOCK)
    try:
        block, report = fit_constants(usable, placeholder, min_n=args.min_n)
    except RoleInningsConfigError as exc:
        print(f"[fit] fitted block failed estimator validation: {exc}", file=sys.stderr)
        return 2

    report["source"] = str(args.rows)
    report["ruler"] = ("point-in-time, 5-game trailing window (matches the gate's "
                       "RoleAwareInningsEstimator input); NOT a season-level roster")
    report["n_rows_total"] = int(len(df))
    report["n_rows_fitted_on"] = int(len(usable))
    report["n_rows_excluded_unknown"] = int(n_excluded)

    print("\nFIT REPORT")
    print("-" * 72)
    print(json.dumps(report, indent=2, default=str))

    print("\nCANDIDATE role_innings BLOCK")
    print("-" * 72)
    print(json.dumps({"role_innings": block}, indent=2))

    both_thin = {"opener_innings", "bulk_innings"} <= set(report["kept_placeholder"])
    if both_thin:
        print("\n*** DO-NOT-GATE-YET: both opener and bulk kept their UNVALIDATED "
              "placeholders. Do not fork model_version on numbers the data cannot "
              "support. ***")

    # WRITE FIRST. The fit is the expensive, valuable artifact; the hash preview
    # is a convenience. An import/parse problem in the preview must never
    # destroy a completed fit (it did exactly that once during development).
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"role_innings": block, "_fit_report": report},
                              indent=2, default=str), encoding="utf-8")
    print(f"\nWrote {out}")

    # model_version fork preview against the REAL config.
    cfg_path = Path(args.config)
    if cfg_path.exists():
        try:
            from src.utils.model_version import model_version  # noqa: E402
            config = json.loads(cfg_path.read_text(encoding="utf-8"))
            old = model_version(config)
            new = model_version(dict(config, role_innings=block))
            print(f"\nmodel_version preview: {old} (frozen)  ->  {new} (with this block)")
            if "role_innings" in config:
                print("WARNING: config.json ALREADY contains a role_innings block — the hash "
                      "above is not the frozen baseline. Investigate before proceeding.")
            if old == new:
                print("WARNING: the hash did NOT move. role_innings must be in "
                      "MODEL_CONFIG_KEYS or B4 would change pitcher output while the "
                      "version stayed fixed (silent provenance corruption).")
        except Exception as exc:  # noqa: BLE001 — preview is a convenience, not the artifact
            print(f"\n[fit] model_version preview unavailable ({type(exc).__name__}: {exc}). "
                  f"The fit above was still written.", file=sys.stderr)
    else:
        print(f"\n[fit] {cfg_path} not found — skipping model_version preview")

    print("\nNext: build config/config.b4.json = real config + this role_innings block, "
          "then run the single-date smoke test BEFORE the full gate run.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
