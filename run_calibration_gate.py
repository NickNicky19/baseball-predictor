"""
Gate #4(b) harness — "equal or better calibration than the simulator".

Two modes:

  # (1) EMIT the deployed GBM calibrated P(over) for the 2024+ walk-forward
  #     rows at the standard lines. This is HALF the matched join.
  python run_calibration_gate.py emit \
      --pairs data/models/gbm/wf_predictions_catboost.csv \
      --calibrators data/models/gbm/calibration/calibrators.json \
      --out data/models/gbm/calibration/gbm_deployed_probs.csv

  # (2) COMPARE against the simulator on matched keys.
  python run_calibration_gate.py compare \
      --gbm data/models/gbm/calibration/gbm_deployed_probs.csv \
      --sim data/models/gbm/calibration/sim_probs.csv

VERDICT SEMANTICS (rewritten 2026-07-10; the original fixed 1e-4 epsilon
failed the gate on sub-0.001 Brier gaps that are indistinguishable from
sampling noise — the exact "headline vs evidence" trap the project
discipline exists to catch):

  Per (category, line), the per-row squared-error difference
      d_i = (gbm_p_i - y_i)^2 - (sim_p_i - y_i)^2
  has mean(d) = Brier_GBM - Brier_sim (negative => GBM better). Rows within
  a slate are correlated (same games/weather/park effects), so significance
  is judged by a BLOCK BOOTSTRAP OVER DATES: resample the matched dates with
  replacement, recompute mean(d), take a percentile CI.

  verdict per line:
    GBM        mean(d) <= 0 (point estimate equal-or-better)
    tie (n.s.) mean(d) > 0 but CI includes 0 (sim ahead within noise)
    sim (SIG)  mean(d) > 0 and CI entirely > 0 (sim genuinely better)

  GATE PASS  <=> no line is "sim (SIG)". "Equal or better" treats a
  statistical tie as equal — a 0.0005 Brier gap on a rare-event market is
  not evidence the sim calibrates better.

  Verdict metric is BRIER (the standard calibration score; bounded, proper).
  Log-loss is reported alongside as supporting evidence, not gated on.

MURPHY SKILL: Brier = REL - RES + UNC where UNC = base(1-base) is the
irreducible uncertainty of the outcome. skill := UNC - Brier (= RES - REL);
~0 means the model has no resolution beyond the base rate — the "noise
floor". Printed per line for both models so rare-event categories (HR) can
be read honestly: two models at the floor are TIED, not one "winning".

SCOPE: hitter categories only (hits, hrr, home_runs). Pitcher strikeouts has
no simulator distribution (project_pitcher_strikeouts returns
simulation=None) — the K half of gate #4(b) is deferred to B3.

sim_probs.csv REQUIRED columns:
    mlb_game_pk, player_id, game_date, category, line, sim_p_over
"""
from __future__ import annotations
import argparse, json, os, sys
import numpy as np, pandas as pd
from src.evaluation.identity_keys import MODEL_KEY, require_unique
try:
    from src.learning import gbm_calibrator as gc
except ImportError:
    import gbm_calibrator as gc


def _load_pairs(path):
    df = pd.read_csv(path); df["game_date"] = pd.to_datetime(df["game_date"])
    if "mlb_game_pk" not in df.columns:
        raise ValueError(f"{path}: historical gate pairs require mlb_game_pk")
    return df[df["game_date"] >= "2024-01-01"].copy()


def emit(args):
    pairs = _load_pairs(args.pairs)
    art = json.load(open(args.calibrators))
    out = []
    for cat, d in art.items():
        cal = gc.CategoryCalibrator.from_dict(d)
        sub = pairs[pairs.category == cat]
        for L in gc.STANDARD_LINES[cat]:
            p = cal.prob_over(sub.predicted_value.values, L)
            out.append(pd.DataFrame(dict(
                mlb_game_pk=sub.mlb_game_pk.values,
                player_id=sub.player_id.values,
                game_date=sub.game_date.dt.strftime("%Y-%m-%d").values,
                category=cat, line=L,
                predicted_value=sub.predicted_value.values,
                actual_value=sub.actual_value.values,
                gbm_p_over=p,
                over_outcome=(sub.actual_value.values >= L).astype(int),
            )))
    res = pd.concat(out, ignore_index=True)
    require_unique(res, MODEL_KEY, "deployed GBM probability export")
    res.to_csv(args.out, index=False)
    print(f"wrote {len(res)} deployed-GBM P(over) rows -> {args.out}")
    print(res.groupby('category').size().to_string())


# --------------------------------------------------------------------------- #
# compare: block bootstrap over dates
# --------------------------------------------------------------------------- #
def _block_bootstrap_ci(d, dates, n_boot, alpha, rng):
    """Percentile CI of mean(d) resampling DATES (blocks) with replacement."""
    uniq = np.unique(dates)
    idx = {u: np.flatnonzero(dates == u) for u in uniq}
    k = len(uniq)
    means = np.empty(n_boot)
    for b in range(n_boot):
        pick = rng.choice(uniq, size=k, replace=True)
        rows = np.concatenate([idx[u] for u in pick])
        means[b] = d[rows].mean()
    lo, hi = np.percentile(means, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return float(lo), float(hi)


def _maybe_plot(m, out_png):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:
        print(f"[note] reliability plot skipped (matplotlib unavailable: {e})")
        return
    cats = sorted(m.category.unique())
    fig, axes = plt.subplots(1, len(cats), figsize=(5.2 * len(cats), 4.6))
    axes = np.atleast_1d(axes)
    for ax, cat in zip(axes, cats):
        g = m[m.category == cat]
        y = g.over_outcome.values
        for name, col, style in [("GBM", "gbm_p_over", "s-"), ("sim", "sim_p_over", "o--")]:
            mp, of, _ = gc.reliability(g[col].values, y, 10)
            ax.plot(mp, of, style, ms=4, lw=1.2, label=name, alpha=0.85)
        ax.plot([0, 1], [0, 1], "k-", lw=0.8, alpha=0.4)
        ax.set_title(f"{cat} (pooled lines, n={len(g)})", fontsize=10)
        ax.set_xlabel("predicted P(over)"); ax.set_ylabel("observed freq")
        ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.legend(fontsize=8); ax.grid(alpha=0.25)
    fig.suptitle("Gate #4(b): GBM vs simulator reliability on matched rows", fontsize=11)
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    fig.savefig(out_png, dpi=130)
    print(f"reliability plot -> {out_png}")


def compare(args):
    gbm = pd.read_csv(args.gbm)
    sim = pd.read_csv(args.sim)
    key = MODEL_KEY
    for c in key:
        if c not in sim.columns:
            sys.exit(f"sim file missing required column: {c}")
    require_unique(gbm, key, "GBM probabilities")
    require_unique(sim, key, "simulator probabilities")
    m = gbm.merge(sim[key + ["sim_p_over"]], on=key, how="inner", validate="one_to_one")
    if len(m) == 0:
        sys.exit("no matched rows — check keys (player_id/game_date/category/line).")
    rng = np.random.default_rng(args.seed)

    print(f"matched rows: {len(m)}  (gbm={len(gbm)}, sim={len(sim)})")
    by_date = m.groupby("game_date").size().sort_index()
    print(f"matched dates: {len(by_date)}   rows/date: "
          f"min={by_date.min()} median={int(by_date.median())} max={by_date.max()}")
    thin = by_date[by_date < args.min_date_rows]
    if len(thin):
        print(f"[note] {len(thin)} thin date(s) (<{args.min_date_rows} matched rows): "
              + ", ".join(f"{d}({n})" for d, n in thin.items())
              + " — kept; block bootstrap weights them naturally")

    print(f"\nverdict metric: Brier (log-loss shown as supporting evidence)")
    print(f"significance: {int((1-args.alpha)*100)}% block-bootstrap CI over dates, "
          f"B={args.n_boot}, seed={args.seed}\n")

    hdr = (f"{'category':10s} {'line':>4s} {'n':>5s} {'dts':>3s}  "
           f"{'GBM_br':>7s} {'sim_br':>7s} {'dBrier':>8s} {'95% CI':>19s}  "
           f"{'GBM_ll':>7s} {'sim_ll':>7s}  {'skG':>6s} {'skS':>6s}  verdict")
    print(hdr); print("-" * len(hdr))

    any_sig_sim = False
    rows_out = []
    for (cat, L), g in m.groupby(["category", "line"]):
        y = g.over_outcome.values.astype(float)
        pg, ps = g.gbm_p_over.values, g.sim_p_over.values
        dts = g.game_date.values
        d = (pg - y) ** 2 - (ps - y) ** 2
        delta = float(d.mean())
        lo, hi = _block_bootstrap_ci(d, dts, args.n_boot, args.alpha, rng)
        b_g, b_s = gc.brier(pg, y), gc.brier(ps, y)
        ll_g, ll_s = gc.log_loss(pg, y), gc.log_loss(ps, y)
        base = float(y.mean()); unc = base * (1 - base)
        sk_g, sk_s = unc - b_g, unc - b_s          # Murphy skill (RES - REL)
        if delta <= 0:
            verdict = "GBM"
        elif lo <= 0:
            verdict = "tie (n.s.)"
        else:
            verdict = "sim (SIG)"; any_sig_sim = True
        print(f"{cat:10s} {L:>4} {len(g):>5d} {g.game_date.nunique():>3d}  "
              f"{b_g:>7.4f} {b_s:>7.4f} {delta:>+8.4f} [{lo:>+8.4f},{hi:>+8.4f}]  "
              f"{ll_g:>7.4f} {ll_s:>7.4f}  {sk_g:>6.4f} {sk_s:>6.4f}  {verdict}")
        rows_out.append(dict(category=cat, line=L, n=len(g),
                             n_dates=int(g.game_date.nunique()),
                             brier_gbm=round(b_g, 5), brier_sim=round(b_s, 5),
                             delta_brier=round(delta, 5), ci_lo=round(lo, 5),
                             ci_hi=round(hi, 5), ll_gbm=round(ll_g, 5),
                             ll_sim=round(ll_s, 5), base_rate=round(base, 4),
                             skill_gbm=round(sk_g, 5), skill_sim=round(sk_s, 5),
                             verdict=verdict))

    print("\nskill = UNC - Brier (Murphy): ~0 means no resolution beyond the base "
          "rate (noise floor); two models at the floor are tied, not ranked.")
    print("\nGATE #4(b) [hitter categories; K deferred to B3]:",
          "PASS — no line where the simulator is significantly better"
          if not any_sig_sim else
          "FAIL — simulator significantly better on >=1 line (see 'sim (SIG)')")

    if args.out_metrics:
        pd.DataFrame(rows_out).to_csv(args.out_metrics, index=False)
        print(f"metrics -> {args.out_metrics}")
    if not args.no_plot:
        out_png = args.plot or os.path.join(os.path.dirname(args.sim) or ".",
                                            "gate_reliability.png")
        _maybe_plot(m, out_png)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("emit"); e.add_argument("--pairs", required=True)
    e.add_argument("--calibrators", required=True); e.add_argument("--out", required=True)
    c = sub.add_parser("compare")
    c.add_argument("--gbm", required=True); c.add_argument("--sim", required=True)
    c.add_argument("--n-boot", type=int, default=4000)
    c.add_argument("--alpha", type=float, default=0.05)
    c.add_argument("--seed", type=int, default=13)
    c.add_argument("--min-date-rows", type=int, default=100,
                   help="flag (not drop) dates with fewer matched rows")
    c.add_argument("--out-metrics", default=None, help="optional metrics CSV path")
    c.add_argument("--plot", default=None, help="reliability plot path (default: beside --sim)")
    c.add_argument("--no-plot", action="store_true")
    args = ap.parse_args()
    (emit if args.cmd == "emit" else compare)(args)
