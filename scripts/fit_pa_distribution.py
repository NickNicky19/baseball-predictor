#!/usr/bin/env python3
"""
Fit the empirical per-lineup-slot plate-appearance distribution.

=============================================================================
WHY (all measured this session -- nothing here is asserted)
=============================================================================
`_sample_pa_count` emits ONLY floor(expected_pa) and floor(expected_pa)+1.
Verified against the real method, 100k draws per value:
    expected_pa = 4.3  ->  {4: 0.698, 5: 0.302}.   P(pa <= 3) = EXACTLY 0.0000.

    simulator PA : {3: 0.070, 4: 0.766, 5: 0.164}
    reality      : {1: .072, 2: .030, 3: .107, 4: .543, 5: .231, 6: .017, 7: .001}
                   (out_pa, n = 152,683 training rows)

The simulator produces ZERO games at pa<=2 (10.1% of reality) and ZERO at pa=6
(1.7% of reality). BOTH tails are missing. And the mean is wrong too: simulator
4.095 vs empirical 3.886 -- the model gives every hitter ~0.21 EXTRA plate
appearances.

The PA counterfactual MEASURED the cost: substituting this fitted distribution
removes +0.0339 of the +0.0890 `P(hits>=1)` bias on DK-gradeable rows (~38%),
landing inside the pre-stated 0.020-0.040 range from an independent synthetic
mechanism check.

=============================================================================
WHY PER SLOT, AND WHY THIS IS A FIT AND NOT A PLACEHOLDER  (rule 2)
=============================================================================
Plate appearances depend strongly and monotonically on batting order. MEASURED
from the training set (~17,000 rows per slot, 2023-2026):

    slot 1  mean out_pa 4.341        slot 6  3.788
    slot 2             4.262         slot 7  3.649
    slot 3             4.162         slot 8  3.465
    slot 4             4.070         slot 9  3.288
    slot 5             3.926

Every slot has >= 16,818 rows. This is a genuine empirical fit, NOT a structural
placeholder -- the first parameter in this project that is properly FITTED under
rule 2 rather than chosen and flagged.

NOTE, and it matters: config's `simulation_slot_pa` factors are NOT monotone --
slot 9 (0.951) is HIGHER than slots 6/7/8 (0.964/0.947/0.932 -> and slot 9 is
0.951, above slot 8's 0.932). Reality is strictly monotone. So the shipped slot
factors are wrong in SHAPE, not merely in level. The fitted distribution
replaces both.

=============================================================================
WHAT THIS SCRIPT DOES
=============================================================================
Reads the training set, fits P(out_pa = k | lineup_slot) for slots 1-9, and
writes it to a JSON artifact the simulator loads. It NEVER edits config.json.

Provenance is written INTO the artifact (source file, row counts, date range,
fit timestamp) so a stale or mis-sourced fit can be detected rather than
silently trusted.

Usage:
    python scripts/fit_pa_distribution.py
    python scripts/fit_pa_distribution.py --out data/learning/pa_distribution.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils.provenance import sha256_file  # noqa: E402

# Rule 2: a slot with fewer rows than this is NOT fitted separately -- it is
# pooled into the league-wide distribution. Fitting 7 probabilities on 200 rows
# is noise wearing a fit's clothes. MEASURED: the thinnest slot has 16,818 rows,
# so this never fires on the current data. It exists so that it CANNOT silently
# start fitting noise if the training set is ever rebuilt smaller.
MIN_ROWS_PER_SLOT = 500

# Rule 7 -- sanity ranges, stated BEFORE the run. An out-of-range value means
# the FIT is broken (bad join, wrong column, stale file), not that baseball
# changed.
SANITY_PROFILES = {
    "legacy_final_occupants": {
    "mean_pa_slot1": (4.1, 4.6),   # measured 4.341
    "mean_pa_slot9": (3.1, 3.6),   # measured 3.288
    "p_pa_le2_pooled": (0.05, 0.16),  # measured 0.101
    "rows_per_slot_min": 5000,
    },
    # Locked after the a3.2 identity diagnostic proved that the old PA<=2
    # range was dominated by final lineup occupants (mostly substitutes), not
    # the original starters the simulator prices. The numeric range is broad;
    # the stronger guard is agreement between the independently observed 2023
    # and 2024 seasons. The legacy profile above remains unchanged.
    "original_starters_a3_2": {
        "mean_pa_slot1": (4.1, 4.7),
        "mean_pa_slot9": (3.1, 3.7),
        "p_pa_le2_pooled": (0.01, 0.10),
        "rows_per_slot_min": 5000,
        "rows_per_season_min": 40000,
        "year_p_le2_max_gap": 0.015,
        "year_mean_pa_max_gap": 0.10,
    },
}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--training",
                    default="data/training/training_hitters_2023_2026.csv.gz")
    ap.add_argument("--out", default="data/learning/pa_distribution.json")
    ap.add_argument(
        "--fit-before",
        default=None,
        help=(
            "exclusive YYYY-MM-DD chronology boundary. Only games before this "
            "date may contribute to the fitted distribution"
        ),
    )
    ap.add_argument(
        "--sanity-profile",
        choices=sorted(SANITY_PROFILES),
        default="legacy_final_occupants",
        help="population-specific, predeclared fit sanity contract",
    )
    args = ap.parse_args(argv)
    sanity = SANITY_PROFILES[args.sanity_profile]

    src = Path(args.training)
    if not src.exists():
        print(f"FATAL: {src} not found.", file=sys.stderr)
        return 2

    tr = pd.read_csv(src, low_memory=False)
    need = {"out_pa", "lineup_slot", "game_date"}
    missing = need - set(tr.columns)
    if missing:
        print(f"FATAL: {src} missing columns {sorted(missing)}.", file=sys.stderr)
        return 2

    source_dates = pd.to_datetime(tr["game_date"], errors="coerce")
    if source_dates.isna().any():
        print("FATAL: game_date contains missing or unparsable values.", file=sys.stderr)
        return 2
    tr = tr.copy()
    tr["game_date"] = source_dates.dt.strftime("%Y-%m-%d")

    d = tr[(tr.out_pa.notna()) & (tr.out_pa > 0)
           & (tr.lineup_slot.between(1, 9))].copy()
    fit_before = None
    if args.fit_before:
        fit_before = pd.Timestamp(args.fit_before)
        d = d[pd.to_datetime(d["game_date"]) < fit_before].copy()
    d["out_pa"] = d["out_pa"].astype(int)
    d["lineup_slot"] = d["lineup_slot"].astype(int)

    if d.empty:
        print("FATAL: no usable rows (need out_pa > 0 and lineup_slot in 1..9).",
              file=sys.stderr)
        return 2

    pooled = d["out_pa"].value_counts(normalize=True).sort_index()
    print("=" * 74)
    print(f"FIT  from {src.name}   ({len(d):,} rows, "
          f"{d.game_date.min()} .. {d.game_date.max()})")
    print("=" * 74)

    by_slot: dict[str, dict[str, float]] = {}
    n_by_slot: dict[str, int] = {}
    print(f"  {'slot':>4s} {'n':>7s} {'mean':>6s}  distribution")
    fails: list[str] = []

    for slot in range(1, 10):
        s = d[d.lineup_slot == slot]
        n_by_slot[str(slot)] = int(len(s))
        if len(s) < MIN_ROWS_PER_SLOT:
            print(f"  {slot:4d} {len(s):7,d}  POOLED (below {MIN_ROWS_PER_SLOT} rows; "
                  f"rule 2 -- do not fit on noise)")
            dist = pooled
        else:
            dist = s["out_pa"].value_counts(normalize=True).sort_index()
        by_slot[str(slot)] = {str(int(k)): float(v) for k, v in dist.items()}
        mean = float(sum(int(k) * v for k, v in by_slot[str(slot)].items()))
        shown = " ".join(f"{k}:{v:.3f}" for k, v in sorted(
            by_slot[str(slot)].items(), key=lambda kv: int(kv[0])) if v >= 0.005)
        print(f"  {slot:4d} {len(s):7,d} {mean:6.3f}  {shown}")

        if len(s) < sanity["rows_per_slot_min"]:
            fails.append(f"slot {slot} has only {len(s):,} rows "
                         f"(expected >= {sanity['rows_per_slot_min']:,})")

    # ---- sanity (rule 7): out-of-range means the FIT is broken -----------
    m1 = sum(int(k) * v for k, v in by_slot["1"].items())
    m9 = sum(int(k) * v for k, v in by_slot["9"].items())
    p_le2 = float(pooled[pooled.index <= 2].sum())

    print()
    print("  SANITY (ranges stated BEFORE the fit):")
    for label, got, (lo, hi) in (
        ("mean pa, slot 1", m1, sanity["mean_pa_slot1"]),
        ("mean pa, slot 9", m9, sanity["mean_pa_slot9"]),
        ("P(pa<=2), pooled", p_le2, sanity["p_pa_le2_pooled"]),
    ):
        ok = lo <= got <= hi
        print(f"    {label:18s} {got:7.3f}   expect [{lo}, {hi}]   "
              f"{'OK' if ok else '*** OUT OF RANGE ***'}")
        if not ok:
            fails.append(f"{label} = {got:.3f}, outside [{lo}, {hi}]")

    # monotonicity: mean PA must DECREASE from slot 1 to slot 9. Reality does.
    means = [sum(int(k) * v for k, v in by_slot[str(s)].items()) for s in range(1, 10)]
    mono = all(means[i] >= means[i + 1] for i in range(8))
    print(f"    mean pa monotone decreasing 1->9: "
          f"{'OK' if mono else '*** NOT MONOTONE ***'}")
    if not mono:
        fails.append("mean out_pa is not monotone decreasing across slots -- "
                     "either the fit is broken or lineup_slot is mis-joined")

    season_sanity: dict[str, dict[str, float | int]] = {}
    if args.sanity_profile == "original_starters_a3_2":
        for season in (2023, 2024):
            sample = d[pd.to_datetime(d["game_date"]).dt.year == season]
            season_sanity[str(season)] = {
                "n_rows": int(len(sample)),
                "mean_pa": float(sample["out_pa"].mean()),
                "p_pa_le2": float((sample["out_pa"] <= 2).mean()),
            }
            if len(sample) < sanity["rows_per_season_min"]:
                fails.append(
                    f"season {season} has only {len(sample):,} rows "
                    f"(expected >= {sanity['rows_per_season_min']:,})"
                )
        p_gap = abs(
            float(season_sanity["2023"]["p_pa_le2"])
            - float(season_sanity["2024"]["p_pa_le2"])
        )
        mean_gap = abs(
            float(season_sanity["2023"]["mean_pa"])
            - float(season_sanity["2024"]["mean_pa"])
        )
        print(
            f"    2023/2024 P(pa<=2) gap: {p_gap:.4f} "
            f"(max {sanity['year_p_le2_max_gap']:.4f})"
        )
        print(
            f"    2023/2024 mean PA gap: {mean_gap:.4f} "
            f"(max {sanity['year_mean_pa_max_gap']:.4f})"
        )
        if p_gap > sanity["year_p_le2_max_gap"]:
            fails.append("original-starter P(pa<=2) is not stable across 2023/2024")
        if mean_gap > sanity["year_mean_pa_max_gap"]:
            fails.append("original-starter mean PA is not stable across 2023/2024")

    if fails:
        print("\n*** FIT REJECTED ***", file=sys.stderr)
        for f in fails:
            print(f"  - {f}", file=sys.stderr)
        print("\nAn out-of-range fit means the MEASUREMENT is broken (bad join, "
              "wrong column, stale file), not that baseball changed. Nothing "
              "was written.", file=sys.stderr)
        return 1

    # ---- emit, with provenance -------------------------------------------
    artifact = {
        "_comment": (
            "Empirical P(plate_appearances = k | lineup_slot), FITTED from the "
            "training set. Replaces GameSimulator._sample_pa_count's "
            "floor/floor+1 two-point distribution, which produces ZERO games at "
            "pa<=2 (10.1% of reality) and ZERO at pa=6 (1.7%). Measured to remove "
            "+0.0339 of the +0.0890 P(hits>=1) bias on DK-gradeable rows. "
            "This is a FIT (>=16,818 rows per slot), not a structural placeholder."
        ),
        "provenance": {
            "source": str(src),
            "source_sha256": sha256_file(src),
            "fitted_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "n_rows": int(len(d)),
            "date_min": str(d.game_date.min()),
            "date_max": str(d.game_date.max()),
            "fit_before_exclusive": (
                fit_before.strftime("%Y-%m-%d") if fit_before is not None else None
            ),
            "source_date_min": str(tr.game_date.min()),
            "source_date_max": str(tr.game_date.max()),
            "n_rows_by_slot": n_by_slot,
            "min_rows_per_slot_for_fit": MIN_ROWS_PER_SLOT,
            "sanity_profile": args.sanity_profile,
            "sanity_contract": sanity,
            "season_sanity": season_sanity,
        },
        "by_lineup_slot": by_slot,
        "pooled": {str(int(k)): float(v) for k, v in pooled.items()},
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(artifact, f, indent=2)
        f.write("\n")

    print(f"\nwrote {out}")
    print(f"  fitted mean pa (pooled): "
          f"{sum(int(k)*v for k, v in artifact['pooled'].items()):.3f}")
    print(f"  simulator's current mean: 4.095   (measured)")
    print(f"  -> the model over-projects PA by "
          f"{4.095 - sum(int(k)*v for k, v in artifact['pooled'].items()):+.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
