#!/usr/bin/env python3
"""
PA GATE VERDICT — frozen sim vs PA-candidate sim, block bootstrap over dates.

Same machinery as run_b4_gate_verdict.py and run_hrr_gate_verdict.py, with the
ROLES SET FOR A PLATE-APPEARANCE CHANGE.

  B4  was a PITCHER change  -> strikeouts was the verdict, hitters the controls.
  HRR was a runs/RBI change -> hrr was the verdict, hits/HR/K the controls.
  THIS is a PLATE-APPEARANCE change:
      * `hits` is THE VERDICT. PA drives hits more directly than anything else.
      * `strikeouts` is a HARD control -- the fix is hitter-only and CANNOT
        touch pitcher K. Measured on the smoke: drift EXACTLY 0.00000.
      * `home_runs` is a SOFT control. It SHOULD move a little (fewer PAs ->
        fewer HR chances) but far less than hits. Measured on the smoke:
        0.00588 vs the 0.00362 noise floor -- 1.6x, small but not zero. Do not
        treat a small HR move as a leak; treat a LARGE one as one.
      * `hrr` INHERITS the change (hrr = hits + runs + rbi) and is reported,
        not gated. Note there is NO hrr MARKET at any book -- it cannot be bet.

Running run_hrr_gate_verdict.py unmodified here would INVERT the logic: it would
flag `hits` moving 4.5x the noise floor as a LEAK -- when that is the entire
point of the change.

=============================================================================
*** THE PLUMBING CHECK IS THE POINT ***
=============================================================================
If `hits` drift ~ 0, that is NOT "the fix is neutral". It means THE FIX DID NOT
HAPPEN -- the candidate config never reached GameSimulator.__init__ through
reconstruct_objects -> PropEngine -> _build_monte_carlo. That exact seam
silently nulled the B4 gate once already (see the scar comment in
run_reconstruct_date.py). We exit 2 rather than report a TIE.

MEASURED on the single-date smoke (2026-06-16), so we know what to expect:
    hits        0.02513   (noise floor 0.00563)  -> 4.5x
    hrr         0.02219   (noise floor 0.00606)  -> 3.7x
    home_runs   0.00588   (noise floor 0.00362)  -> 1.6x
    strikeouts  0.00000   (noise floor 0.00000)  -> exact

=============================================================================
DUAL GRADING REGIMES -- AND WHY THIS ONE MATTERS MORE THAN USUAL  (rule 3)
=============================================================================
The books VOID low-PA games:
    DraftKings  voids pa == 1 for a starting-lineup batter (their house rules,
                verbatim), and voids substitutes entirely.  -> gradeable pa>=2
    PrizePicks  voids pa <= 2 ("reboot").                    -> gradeable pa>=3

THE PA FIX SPECIFICALLY CHANGES BEHAVIOUR ON LOW-PA GAMES -- which are EXACTLY
the rows the books throw away. So the choice of grading regime is not a detail
here; it is the difference between:

    ALL rows  -> the fix gets credit for correctly predicting zeros in games
                 that NEVER SETTLE. Flattering, and not what you would bet.
    pa >= 2   -> the fix is measured on rows that ACTUALLY GRADE. Honest, and
                 harsher.

We already know this can flip a verdict: excluding void rows moved the HRR gate
from FROZEN to TIE.

BOTH are reported. **pa>=2 (DK-gradeable) IS THE ONE THAT DECIDES PROMOTION.**
That is stated HERE, before the number is seen, so it cannot be chosen after.

HONEST CAVEAT (rule 8): DK's void rule EXEMPTS the "Under" selection -- an under
bet on a pa==1 batter STILL GRADES and wins. Our Brier is TWO-SIDED and the
pairs carry no side column, so excluding void rows is strictly correct only if
you bet overs exclusively. The truth is a mixture. Not a clean fix; stated, not
hidden.

NOISE FLOOR IS NOT OPTIONAL. A single-date smoke has n_dates=1, which makes the
block bootstrap degenerate (ci_lo == ci_hi on every row) and "significance"
meaningless. The floor used above was MEASURED by a frozen-vs-frozen control.

Usage:
  # the verdict
  python run_pa_gate_verdict.py \
      --frozen data/analysis/pa/gate_frozen.csv \
      --candidate data/analysis/pa/gate_cand.csv \
      --pairs data/models/gbm/wf_predictions_catboost.csv \
      --out data/analysis/pa/pa_gate_metrics.csv

  # frozen-vs-frozen noise floor (BOTH sides the frozen config)
  python run_pa_gate_verdict.py \
      --frozen data/analysis/pa/gate_frozen.csv \
      --candidate data/analysis/pa/gate_frozen2.csv \
      --pairs data/models/gbm/wf_predictions_catboost.csv \
      --noise-floor
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from src.evaluation.identity_keys import MODEL_KEY, OUTCOME_KEY, require_unique

KEYS = MODEL_KEY
OUTCOME_KEYS = OUTCOME_KEY
ACTUAL_COL_CANDIDATES = ("actual_value", "actual", "y_true", "actual_outcome")

# ROLES for a PLATE-APPEARANCE change.
VERDICT_CATEGORY = "hits"
HARD_CONTROLS = ("strikeouts",)          # CANNOT move. Hitter-only change.
SOFT_CONTROLS = ("home_runs",)           # SHOULD move a little; large = leak.
INHERITED = ("hrr",)                     # reported, not gated. No market exists.

# min_pa = the smallest PA count that still GRADES at that book.
REGIMES = {
    "ALL rows": 0,
    "DK (pa>=2)": 2,          # <- THE PROMOTION REGIME. Stated before the run.
    "PrizePicks (pa>=3)": 3,
}
PROMOTION_REGIME = "DK (pa>=2)"


def _load_probs(path: Path, prob_col: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    missing = [c for c in KEYS + ["game_date", prob_col] if c not in df.columns]
    if missing:
        raise ValueError(f"{path} missing columns {missing}; has {list(df.columns)}")
    df = df[KEYS + ["game_date", prob_col]].copy()
    df["mlb_game_pk"] = pd.to_numeric(df["mlb_game_pk"], errors="coerce").astype("Int64")
    df["player_id"] = pd.to_numeric(df["player_id"], errors="coerce").astype("Int64")
    df["line"] = pd.to_numeric(df["line"], errors="coerce")
    df[prob_col] = pd.to_numeric(df[prob_col], errors="coerce")
    df = df.dropna(subset=KEYS + [prob_col])
    require_unique(df, KEYS, str(path))
    return df


def _load_outcomes(path: Path, actual_col: Optional[str]) -> pd.DataFrame:
    df = pd.read_csv(path, low_memory=False)
    if actual_col is None:
        for cand in ACTUAL_COL_CANDIDATES:
            if cand in df.columns:
                actual_col = cand
                break
    if actual_col is None or actual_col not in df.columns:
        raise ValueError(
            f"No actuals column in {path}. Tried {ACTUAL_COL_CANDIDATES}; "
            f"available: {list(df.columns)}. Pass --actual-col.")
    out = df[OUTCOME_KEYS + ["game_date", actual_col]].copy()
    out["mlb_game_pk"] = pd.to_numeric(out["mlb_game_pk"], errors="coerce").astype("Int64")
    out["player_id"] = pd.to_numeric(out["player_id"], errors="coerce").astype("Int64")
    out[actual_col] = pd.to_numeric(out[actual_col], errors="coerce")
    out = out.dropna(subset=OUTCOME_KEYS + [actual_col])
    require_unique(out, OUTCOME_KEYS, f"outcomes from {path}")
    print(f"[load] outcomes from {path} column '{actual_col}' ({len(out)} rows)")
    return out.rename(columns={actual_col: "actual"})


def _load_selection(path: Path) -> pd.DataFrame:
    """Load the walk-forward row universe without borrowing its old target.

    The rebuilt walk-forward pairs provide the hard player-game-category
    selection key.  Actuals still come only from the game-scoped reconstruction
    artifact, so a legacy player/date outcome can never leak back into scoring.
    """
    df = pd.read_csv(path, low_memory=False)
    missing = [col for col in OUTCOME_KEYS if col not in df.columns]
    if missing:
        raise ValueError(f"{path} missing selection columns {missing}")
    cols = OUTCOME_KEYS + (["game_date"] if "game_date" in df.columns else [])
    out = df[cols].copy()
    out["mlb_game_pk"] = pd.to_numeric(out["mlb_game_pk"], errors="coerce").astype("Int64")
    out["player_id"] = pd.to_numeric(out["player_id"], errors="coerce").astype("Int64")
    out = out.dropna(subset=OUTCOME_KEYS)
    require_unique(out, OUTCOME_KEYS, f"selection rows from {path}")
    print(f"[load] hard selection keys from {path} ({len(out)} rows)")
    return out


def _load_pa(training: Path) -> pd.DataFrame:
    """out_pa = ACTUAL box-score plate appearances -- the trial count the books
    settle on. NOT expected_pa (a projection), which would be circular here:
    the whole point of this change is that expected_pa's distribution was wrong."""
    tr = pd.read_csv(training, low_memory=False)
    if "out_pa" not in tr.columns:
        raise SystemExit(f"FATAL: {training} has no out_pa column.")
    required = ["game_pk", "player_id", "out_pa"]
    missing = [col for col in required if col not in tr.columns]
    if missing:
        raise SystemExit(f"FATAL: {training} missing PA identity columns {missing}.")
    pa = tr[["game_pk", "player_id", "out_pa"]].rename(columns={"game_pk": "mlb_game_pk"})
    pa["mlb_game_pk"] = pd.to_numeric(pa["mlb_game_pk"], errors="coerce").astype("Int64")
    pa["player_id"] = pd.to_numeric(pa["player_id"], errors="coerce").astype("Int64")
    pa = pa.dropna(subset=["mlb_game_pk", "player_id", "out_pa"])
    # The historical training extract contains a small number of exact repeated
    # player-game records.  They are not a license to silently deduplicate:
    # prove first that every repeated key has one and only one observed PA
    # value, log the normalization, and fail if the target conflicts.
    pa_key = ["mlb_game_pk", "player_id"]
    repeated = pa.duplicated(pa_key, keep=False)
    if repeated.any():
        repeated_rows = pa.loc[repeated]
        pa_values = repeated_rows.groupby(pa_key, dropna=False)["out_pa"].nunique()
        conflicting = pa_values[pa_values > 1]
        if not conflicting.empty:
            raise ValueError(
                f"PA training rows from {training}: {len(conflicting)} duplicate "
                f"player-game keys disagree on out_pa; examples:\n"
                f"{conflicting.head(20).to_string()}"
            )
        print(
            f"[load] PA training: normalizing {len(repeated_rows)} exact duplicate "
            f"rows across {len(pa_values)} player-game keys after equality check"
        )
        pa = pa.drop_duplicates(pa_key, keep="first")
    require_unique(pa, pa_key, f"PA training rows from {training}")
    return pa


def block_bootstrap_ci(d: np.ndarray, dates: np.ndarray, b: int, seed: int,
                       alpha: float = 0.05) -> tuple[float, float]:
    """Percentile CI of mean(d), resampling DATES (the block).

    Rows within a slate share a pitcher, a park, and a game script -- they are
    NOT independent. With n_dates == 1 this is DEGENERATE: ci_lo == ci_hi and
    every row reads 'significant'.
    """
    uniq, inv = np.unique(dates, return_inverse=True)
    n = len(uniq)
    sums = np.zeros(n); cnts = np.zeros(n)
    np.add.at(sums, inv, d); np.add.at(cnts, inv, 1.0)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n, size=(b, n))
    rep = sums[idx].sum(axis=1) / cnts[idx].sum(axis=1)
    lo, hi = np.percentile(rep, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return float(lo), float(hi)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="PA gate: frozen-sim vs PA-candidate-sim, block-bootstrap verdict.")
    ap.add_argument("--frozen", required=True)
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--pairs", default="data/models/gbm/wf_predictions_catboost.csv")
    ap.add_argument("--selection", default=None,
                    help="optional fresh game-keyed walk-forward row universe; "
                         "actuals still come from --pairs")
    ap.add_argument("--training",
                    default="data/training/training_hitters_2023_2026.csv.gz",
                    help="source of out_pa for the DK/PP grading regimes")
    ap.add_argument("--actual-col", default=None)
    ap.add_argument("--prob-col", default="sim_p_over")
    ap.add_argument("--b", type=int, default=4000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default=None)
    ap.add_argument("--noise-floor", action="store_true",
                    help="frozen-vs-frozen control: BOTH inputs are the frozen "
                         "config. Inverts the plumbing check (drift MUST be ~0) "
                         "and reports the MC noise floor instead of a verdict.")
    args = ap.parse_args(argv)

    frozen = _load_probs(Path(args.frozen), args.prob_col)
    cand = _load_probs(Path(args.candidate), args.prob_col)
    outcomes = _load_outcomes(Path(args.pairs), args.actual_col)
    if args.selection:
        selection = _load_selection(Path(args.selection))
        if "game_date" in selection.columns:
            selected = selection.merge(
                outcomes, on=OUTCOME_KEYS, how="left", validate="one_to_one",
                suffixes=("_selection", ""), indicator=True,
            )
            missing_actuals = selected.loc[selected["_merge"] != "both", OUTCOME_KEYS]
            if not missing_actuals.empty:
                raise ValueError(
                    f"{len(missing_actuals)} hard selection keys have no official "
                    f"game-scoped outcome; examples:\n{missing_actuals.head(20).to_string(index=False)}"
                )
            bad_dates = selected["game_date_selection"].astype(str) != selected["game_date"].astype(str)
            if bad_dates.any():
                raise ValueError(
                    "selection/outcome game_date disagreement for hard player-game keys; "
                    f"examples:\n{selected.loc[bad_dates, OUTCOME_KEYS + ['game_date_selection', 'game_date']].head(20).to_string(index=False)}"
                )
            outcomes = selected.drop(columns=["game_date_selection", "_merge"])
        else:
            outcomes = selection.merge(
                outcomes, on=OUTCOME_KEYS, how="inner", validate="one_to_one"
            )
        print(f"[select] official outcomes restricted to {len(outcomes)} fresh walk-forward keys")
    pa = _load_pa(Path(args.training))

    m = (frozen.rename(columns={args.prob_col: "p_frozen"})
         .merge(cand.drop(columns="game_date").rename(columns={args.prob_col: "p_candidate"}),
                on=KEYS, how="inner", validate="one_to_one"))
    print(f"[match] frozen={len(frozen)} candidate={len(cand)} matched={len(m)}")
    m = m.merge(outcomes.drop(columns="game_date"), on=OUTCOME_KEYS, how="inner",
                # Each model line has one outcome, while one player-game outcome
                # legitimately settles more than one model line.
                validate="many_to_one")
    print(f"[match] with outcomes: {len(m)}")
    if m.empty:
        raise SystemExit(
            "FATAL: no matched rows with outcomes.\n"
            "  The gate dates must exist in --pairs. wf_predictions_catboost.csv\n"
            "  spans 2023-06-30 .. 2025-07-01 -- a 2026 date will match ZERO\n"
            "  outcomes. Use --n-dates/--seed sampling (which draws FROM the\n"
            "  pairs file), not a hand-picked 2026 date.")

    n0 = len(m)
    m = m.merge(pa, on=["mlb_game_pk", "player_id"], how="left", validate="many_to_one")
    pa_rows = m[~m["category"].isin(HARD_CONTROLS)]
    pa_rate = pa_rows["out_pa"].notna().mean()
    print(f"[match] hitter rows with out_pa: {int(pa_rows['out_pa'].notna().sum())}/{len(pa_rows)} ({pa_rate:.1%}); "
          f"pitcher rows are ALL-only and do not use PA")
    if pa_rate < 0.90:
        raise SystemExit(
            f"FATAL: only {pa_rate:.1%} of rows resolved an out_pa. The DK/PP\n"
            f"  grading regimes need the ACTUAL trial count. A partial join is a\n"
            f"  BIASED SUBSAMPLE -- and the rows most likely to be missing are the\n"
            f"  LOW-PA ones, which are exactly what this change is about.")

    m["over"] = (m["actual"] >= np.ceil(m["line"])).astype(float)
    n_dates = int(m["game_date"].nunique())

    # ---- drift (the plumbing check) --------------------------------------
    m["abs_dp"] = (m["p_candidate"] - m["p_frozen"]).abs()
    drift = (m.groupby("category", observed=True)["abs_dp"]
             .agg(mean_abs_dp="mean", max_abs_dp="max", n="count")
             .round(5).reset_index())
    print("\nPROBABILITY DRIFT (candidate vs frozen)")
    print(drift.to_string(index=False))
    print("\n  measured on the 2026-06-16 smoke, for reference:")
    print("    hits 0.02513 (4.5x floor) | hrr 0.02219 | home_runs 0.00588 | K 0.00000")

    hits_drift = drift[drift.category == VERDICT_CATEGORY]
    hits_moved = (not hits_drift.empty) and float(hits_drift.mean_abs_dp.iloc[0]) > 1e-6
    k_drift = drift[drift.category.isin(HARD_CONTROLS)]
    k_moved = (not k_drift.empty) and float(k_drift.mean_abs_dp.max()) > 1e-6

    if n_dates <= 1:
        print(f"\n*** WARNING: n_dates = {n_dates}. The block bootstrap resamples "
              f"DATES, so a single-date run is DEGENERATE: ci_lo == ci_hi and every "
              f"line reads 'significant'. This is a plumbing smoke test ONLY. ***")

    # ---- per-line verdicts, per grading regime ---------------------------
    rows = []
    for cat in sorted(m.category.unique()):
        for line in sorted(m[m.category == cat].line.unique()):
            base = m[(m.category == cat) & (m.line == line)]
            regimes = {"ALL rows": 0} if cat in HARD_CONTROLS else REGIMES
            for regime, min_pa in regimes.items():
                s = base if min_pa == 0 else base[base.out_pa >= min_pa]
                if len(s) < 50:
                    continue
                y = s["over"].to_numpy(float)
                bf = float(((s.p_frozen - s.over) ** 2).mean())
                bc = float(((s.p_candidate - s.over) ** 2).mean())
                d = ((s.p_candidate - s.over) ** 2 - (s.p_frozen - s.over) ** 2).to_numpy()
                lo, hi = block_bootstrap_ci(d, s.game_date.to_numpy(),
                                            b=args.b, seed=args.seed)
                v = "CANDIDATE" if hi < 0 else ("FROZEN" if lo > 0 else "TIE")
                unc = float(y.mean() * (1 - y.mean()))
                rows.append(dict(
                    category=cat, line=float(line), regime=regime, n=len(s),
                    n_dates=int(s.game_date.nunique()), base_rate=round(y.mean(), 4),
                    brier_frozen=round(bf, 5), brier_candidate=round(bc, 5),
                    dbrier=round(bc - bf, 5), ci_lo=round(lo, 5), ci_hi=round(hi, 5),
                    murphy_frozen=round(unc - bf, 5),
                    murphy_candidate=round(unc - bc, 5), verdict=v,
                ))
    metrics = pd.DataFrame(rows)
    if metrics.empty:
        raise SystemExit("FATAL: no line/regime had >= 50 rows.")

    print("\nPER-LINE VERDICTS  (dbrier = candidate - frozen; NEGATIVE favors the "
          "PA fix. Read the CI, not the point winner.)")
    print(metrics.to_string(index=False))

    # ---- noise-floor mode -------------------------------------------------
    if args.noise_floor:
        print("\n" + "=" * 78)
        print("NOISE-FLOOR CONTROL (frozen vs frozen -- BOTH arms are the SAME model)")
        print("=" * 78)
        print(drift.to_string(index=False))
        sig = metrics[metrics.verdict != "TIE"]
        if not sig.empty:
            print(f"\n{len(sig)} line(s) read 'significant' on IDENTICAL models. That is\n"
                  f"the gate's FALSE-POSITIVE RATE, not a result -- which is the whole\n"
                  f"point of running this control.\n")
            print(sig.to_string(index=False))
        print("\nCandidate drift must EXCEED this floor to mean anything.")
        return 0

    # ---- the verdict ------------------------------------------------------
    print("\n" + "=" * 78)
    print("GATE SUMMARY")
    print("=" * 78)

    if not hits_moved:
        print("FAIL-TO-RUN: `hits` probabilities are IDENTICAL between frozen and\n"
              "  candidate. THE FIX DID NOT HAPPEN. This is NOT a tie.\n"
              "    (a) config.pa.json never reached GameSimulator.__init__ -- check\n"
              "        reconstruct_objects -> PropEngine -> _build_monte_carlo, the\n"
              "        exact seam that silently nulled the B4 gate; or\n"
              "    (b) base_running.pa_distribution_path is missing/unreadable, so\n"
              "        _sample_pa_count fell back to the legacy floor/floor+1 draw.\n"
              "  Do not read the verdicts above as evidence.")
        return 2

    if k_moved:
        print(f"WARNING: a HARD control moved. `strikeouts` drift is\n"
              f"  {float(k_drift.mean_abs_dp.max()):.5f}, and it must be EXACTLY 0 --\n"
              f"  the PA fix is hitter-only and cannot touch pitcher K. Something\n"
              f"  leaked. Investigate BEFORE trusting the hits verdict.\n")

    # THE promotion regime, named before the run.
    dec = metrics[(metrics.category == VERDICT_CATEGORY)
                  & (metrics.regime == PROMOTION_REGIME)]
    if dec.empty:
        print(f"FAIL-TO-RUN: no `{VERDICT_CATEGORY}` lines under the promotion "
              f"regime '{PROMOTION_REGIME}'.")
        return 2

    frozen_wins = dec[dec.verdict == "FROZEN"]
    cand_wins = dec[dec.verdict == "CANDIDATE"]

    print(f"PROMOTION REGIME: {PROMOTION_REGIME}  (declared BEFORE the run -- the\n"
          f"  books void pa==1, and this fix changes behaviour precisely on low-PA\n"
          f"  games, so scoring rows that never settle would flatter it.)\n")
    print(dec.to_string(index=False))
    print()

    if not frozen_wins.empty:
        print(f"FAIL: frozen is significantly better on {len(frozen_wins)} of "
              f"{len(dec)} `{VERDICT_CATEGORY}` line(s) under {PROMOTION_REGIME}.")
        rc = 1
    elif not cand_wins.empty:
        print(f"PASS (STRONG): the PA fix is significantly BETTER on "
              f"{len(cand_wins)} of {len(dec)} `{VERDICT_CATEGORY}` line(s), and "
              f"frozen wins ZERO.")
        rc = 0
    else:
        print(f"PASS (TIE): frozen is significantly better on ZERO of {len(dec)} "
              f"`{VERDICT_CATEGORY}` line(s). 'Equal or better' is the bar.\n"
              f"  But a TIE is NOT evidence the fix helps -- only that it does not\n"
              f"  hurt. The counterfactual MEASURED the fix removing +0.0339 of the\n"
              f"  +0.0890 P(hits>=1) bias; if that did not convert into Brier, the\n"
              f"  remaining ~56% (the per-PA HIT RATE) is likely dominating.")
        rc = 0

    # soft controls -- report, do not fail on
    soft = metrics[(metrics.category.isin(SOFT_CONTROLS))
                   & (metrics.regime == PROMOTION_REGIME)
                   & (metrics.verdict != "TIE")]
    if not soft.empty:
        print(f"\nNOTE: {len(soft)} SOFT-control line(s) moved significantly "
              f"({', '.join(SOFT_CONTROLS)}). home_runs SHOULD move a little -- fewer\n"
              f"  PAs means fewer HR chances (smoke: 1.6x the floor). A LARGE move\n"
              f"  would be a leak; a small one is the fix working as designed.\n")
        print(soft.to_string(index=False))

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        metrics.to_csv(args.out, index=False)
        print(f"\nwrote {args.out}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
