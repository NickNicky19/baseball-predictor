"""
B2 entry point: fit + validate the GBM calibration layer on the walk-forward pairs.

  python run_calibrate_gbm.py --pairs data/models/gbm/wf_predictions_catboost.csv \
      --outdir data/models/gbm/calibration

Two passes:
  (1) VALIDATION  — fit on 2024 pairs, evaluate on the 2025 holdout. Decides,
      per category, the deploy config: count family (from conditional
      dispersion), whether the point-estimate mean recalibration transfers
      (guard: improves holdout ECE without worsening Brier/log-loss), and
      whether any post-hoc stage-2 beats the raw count model (it does not on
      this data -> identity). Emits reliability.png + metrics.csv.
  (2) PRODUCTION  — refit on ALL 2024+ pairs using the validated config and
      write the shipped artifact calibrators.json (+ gbm_deployed_probs.csv,
      the GBM half of the gate-#4b join).

Preprocessing per B2 KICKOFF: FILTER to game_date >= 2024-01-01 (2023-fold
rows are thin-trained). Temporal split OF THE PAIRS at --split.
"""
from __future__ import annotations
import argparse, json, os
import numpy as np, pandas as pd
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
try:
    from src.learning import gbm_calibrator as gc      # repo layout
except ImportError:
    import gbm_calibrator as gc                          # co-located fallback

CATS = ["hits", "hrr", "home_runs", "strikeouts"]


def load_pairs(path):
    df = pd.read_csv(path); df["game_date"] = pd.to_datetime(df["game_date"])
    return df[df["game_date"] >= "2024-01-01"].copy()  # required 2024+ filter


def pooled(fn, pred, actual, lines):
    P, Y, L = [], [], []
    for ln in lines:
        P.append(np.asarray(fn(pred, ln), float))
        Y.append((actual >= ln).astype(float)); L.append(np.full(len(pred), ln))
    return np.concatenate(P), np.concatenate(Y), np.concatenate(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", default="wf_predictions_catboost.csv")
    ap.add_argument("--outdir", default="calibration_out")
    ap.add_argument("--split", default="2025-01-01")
    ap.add_argument("--bins", type=int, default=10)
    args = ap.parse_args()
    os.makedirs(args.outdir, exist_ok=True)

    df = load_pairs(args.pairs)
    tr, va = df[df.game_date < args.split], df[df.game_date >= args.split]
    print(f"2024+ pairs: {len(df)} | train(<{args.split}): {len(tr)} | val: {len(va)}")

    rows, config = [], {}
    fig, axes = plt.subplots(2, 2, figsize=(11, 10))

    # ---------------- PASS 1: validation on the temporal split ----------------
    for ax, cat in zip(axes.ravel(), CATS):
        lines = gc.STANDARD_LINES[cat]
        tcat, vcat = tr[tr.category == cat], va[va.category == cat]
        if len(tcat) == 0 or len(vcat) == 0:
            print(f"[skip] {cat}: train={len(tcat)} val={len(vcat)}"); continue
        pv, av = vcat.predicted_value.values, vcat.actual_value.values

        cal_id = gc.fit_category(cat, tcat.predicted_value.values, tcat.actual_value.values, lines, "identity", mean_scale=1.0)
        cal_rc = gc.fit_category(cat, tcat.predicted_value.values, tcat.actual_value.values, lines, "identity")

        def _pm(c):
            P, Y, _ = pooled(lambda p, L: c.raw_prob_over(p, L), pv, av, lines)
            return gc.brier(P, Y), gc.log_loss(P, Y), gc.ece(P, Y, args.bins)
        b0, l0, e0 = _pm(cal_id); b1, l1, e1 = _pm(cal_rc)
        use_rc = (e1 <= e0 - 2e-3) and (b1 <= b0 + 1e-4) and (l1 <= l0 + 1e-4)
        cal_raw = cal_rc if use_rc else cal_id
        ms = cal_raw.mean_scale

        cal_iso = gc.fit_category(cat, tcat.predicted_value.values, tcat.actual_value.values, lines, "isotonic", mean_scale=ms)
        cal_pl = gc.fit_category(cat, tcat.predicted_value.values, tcat.actual_value.values, lines, "platt", mean_scale=ms)
        base = gc.fit_direct_baseline(cat, tcat.predicted_value.values, tcat.actual_value.values, lines)
        methods = {"raw": lambda p, L: cal_raw.raw_prob_over(p, L),
                   "isotonic": lambda p, L: cal_iso.prob_over(p, L),
                   "platt": lambda p, L: cal_pl.prob_over(p, L),
                   "direct(b)": lambda p, L: base.prob_over(p, L)}

        pooled_m = {}
        for name, fn in methods.items():
            P, Y, Lc = pooled(fn, pv, av, lines)
            for ln in lines:
                m = Lc == ln
                rows.append(dict(category=cat, line=ln, method=name, n=int(m.sum()),
                                 base_rate=round(float(Y[m].mean()), 4),
                                 brier=round(gc.brier(P[m], Y[m]), 4),
                                 log_loss=round(gc.log_loss(P[m], Y[m]), 4),
                                 ece=round(gc.ece(P[m], Y[m], min(args.bins, max(3, m.sum()//30))), 4)))
            rows.append(dict(category=cat, line="ALL", method=name, n=int(len(Y)),
                             base_rate=round(float(Y.mean()), 4),
                             brier=round(gc.brier(P, Y), 4), log_loss=round(gc.log_loss(P, Y), 4),
                             ece=round(gc.ece(P, Y, args.bins), 4)))
            pooled_m[name] = (gc.brier(P, Y), gc.log_loss(P, Y))

        # stage-2 deploys only if it beats raw on BOTH Brier and log-loss
        b_raw, ll_raw = pooled_m["raw"]; chosen = "identity"
        for name in ("isotonic", "platt"):
            b, ll = pooled_m[name]
            if b < b_raw - 1e-3 and ll < ll_raw - 1e-3:
                chosen, b_raw, ll_raw = name, b, ll
        config[cat] = dict(stage2=chosen, mean_scale_on=use_rc, lines=lines)

        for name, fn, st in [("raw (deployed)" if chosen == "identity" else "raw", methods["raw"], "s-"),
                             ("isotonic", methods["isotonic"], "o--"),
                             ("platt", methods["platt"], "^:"),
                             ("direct(b)", methods["direct(b)"], "x-.")]:
            P, Y, _ = pooled(fn, pv, av, lines)
            mp, of, _ = gc.reliability(P, Y, args.bins)
            ax.plot(mp, of, st, ms=4, lw=1.2, label=name, alpha=0.85)
        ax.plot([0, 1], [0, 1], "k-", lw=0.8, alpha=0.4)
        fam = cal_raw.family + (f" a={cal_raw.alpha:.2f}" if cal_raw.alpha > 0 else "")
        ms_tag = "" if not use_rc else f" c={ms:.3f}"
        ax.set_title(f"{cat}  val n={len(vcat)}  [{fam}{ms_tag}]  deploy={chosen}", fontsize=10)
        ax.set_xlabel("predicted P(over)"); ax.set_ylabel("observed freq")
        ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.legend(fontsize=8); ax.grid(alpha=0.25)

    fig.suptitle("B2 calibration — reliability on 2025 holdout (fit on 2024 pairs)", fontsize=12)
    fig.tight_layout(rect=[0, 0, 1, 0.97])
    fig.savefig(os.path.join(args.outdir, "reliability.png"), dpi=130)
    pd.DataFrame(rows).to_csv(os.path.join(args.outdir, "metrics.csv"), index=False)

    print("\n=== VALIDATION: pooled (line=ALL) Brier / log-loss / ECE on 2025 holdout ===")
    res = pd.DataFrame(rows)
    print(res[res.line == "ALL"].pivot_table(index="category", columns="method",
          values=["brier", "log_loss", "ece"]).to_string())

    # ---------------- PASS 2: production refit on ALL 2024+ pairs -------------
    artifacts = {}
    print("\n=== PRODUCTION: refit on ALL 2024+ pairs with validated config ===")
    for cat in CATS:
        if cat not in config:
            continue
        d = df[df.category == cat]
        ms = None if config[cat]["mean_scale_on"] else 1.0     # None -> fit c on all data
        cal = gc.fit_category(cat, d.predicted_value.values, d.actual_value.values,
                              config[cat]["lines"], config[cat]["stage2"], mean_scale=ms)
        cal.deployed_stage2 = config[cat]["stage2"]
        artifacts[cat] = cal.to_dict()
        print(f"  {cat:11s} family={cal.family:8s} alpha={cal.alpha:.3f} "
              f"mean_scale={cal.mean_scale:.3f} stage2={cal.deployed_stage2} n={cal.n_train}")

    with open(os.path.join(args.outdir, "calibrators.json"), "w") as fh:
        json.dump(artifacts, fh, indent=2)

    # emit GBM half of the gate-#4b join, from the production artifact
    out = []
    for cat, d in artifacts.items():
        cal = gc.CategoryCalibrator.from_dict(d); sub = df[df.category == cat]
        for L in gc.STANDARD_LINES[cat]:
            out.append(pd.DataFrame(dict(
                player_id=sub.player_id.values,
                game_date=sub.game_date.dt.strftime("%Y-%m-%d").values,
                category=cat, line=L, predicted_value=sub.predicted_value.values,
                actual_value=sub.actual_value.values,
                gbm_p_over=cal.prob_over(sub.predicted_value.values, L),
                over_outcome=(sub.actual_value.values >= L).astype(int))))
    pd.concat(out, ignore_index=True).to_csv(os.path.join(args.outdir, "gbm_deployed_probs.csv"), index=False)

    print(f"\nartifacts -> {args.outdir}/calibrators.json  |  metrics -> {args.outdir}/metrics.csv"
          f"  |  plot -> {args.outdir}/reliability.png  |  gate-probs -> {args.outdir}/gbm_deployed_probs.csv")


if __name__ == "__main__":
    main()
