#!/usr/bin/env python3
"""
Offline harness for B1 — validates every leakage-critical piece with NO
catboost/lightgbm and NO network, on a tiny synthetic fixture. Run this FIRST
(discipline: validate offline harness, then real smoke test, then commit).

Checks:
  1. Schema gate accepts a3.2/a4.1 and REJECTS mismatches / missing roller.
  2. Targets exactly equal outcome_recorder.compute_actual_value (incl. fantasy
     from config weights, incl. the singles = hits - 2b - 3b - hr formula).
  3. Temporal split never puts a holdout date into train (no leakage), and the
     holdout is the most-recent slice.
  4. A4 blanks ("") load as NaN, not object strings that would kill a feature.
  5. Category guard raises when a category is missing from a comparison, and
     passes when the compared set equals the expected gated set.
  6. GBM predictions wrap into PropProjections that score cleanly through the
     real BacktestEngine.compare_reports.
"""

from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from src.evaluation.backtest_engine import BacktestEngine, OutcomeRecord
from src.learning.gbm_benchmark import assert_comparable, score_reports
from src.learning.gbm_dataset import (
    FantasyWeights,
    assert_schema,
    build_category_data,
    choose_split_date,
    derive_target,
)
from src.models.dataclasses import PropProjection

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((PASS if cond else FAIL, name, detail))


# Reference implementation copied from outcome_recorder.compute_actual_value so
# the harness fails if gbm_dataset.derive_target ever drifts from it.
def ref_actual(row, cat, fw):
    h, d, t, hr = row["out_hits"], row["out_doubles"], row["out_triples"], row["out_hr"]
    if cat == "hits":
        return float(h)
    if cat == "home_runs":
        return float(hr)
    if cat == "total_bases":
        return float(h + d + 2 * t + 3 * hr)
    if cat == "hrr":
        return float(h + row["out_runs"] + row["out_rbi"])
    if cat == "strikeouts":
        return float(row["out_k"])
    if cat == "fantasy":
        singles = max(0, h - d - t - hr)
        return float(singles * fw.single + d * fw.double + t * fw.triple
                     + hr * fw.home_run + row["out_rbi"] * fw.rbi
                     + row["out_runs"] * fw.run + row["out_bb"] * fw.walk)
    raise ValueError(cat)


def make_hitters(n=40):
    rng = np.random.default_rng(7)
    dates = pd.date_range("2024-04-01", periods=n, freq="D").strftime("%Y-%m-%d")
    df = pd.DataFrame({
        "builder_schema": "a3.2", "roller_schema": "a4.1",
        "season": 2024, "game_date": dates,
        "game_pk": range(n), "player_id": rng.integers(1000, 1100, n),
        "player_name": [f"P{i}" for i in range(n)],
        "team": rng.choice(["NYY", "BOS", "LAD"], n),
        "opponent": rng.choice(["NYY", "BOS", "LAD"], n),
        "venue": rng.choice(["Fenway", "Dodger Stadium"], n),
        "is_home": rng.integers(0, 2, n), "lineup_slot": rng.integers(1, 10, n),
        "bats": rng.choice(["L", "R", "S"], n),
        "pit_avg": rng.uniform(.2, .32, n), "pit_obp": rng.uniform(.28, .4, n),
        "pit_slg": rng.uniform(.35, .55, n),
        "opp_sp_throws": rng.choice(["L", "R"], n),
        "opp_sp_source": "probable",
        "opp_sp_k9": rng.uniform(6, 12, n),
        "umpire_id": rng.choice([101, 102, 103], n),
        "platoon_adv": rng.integers(0, 2, n),
        "has_prior_data": 1,
        # A4 rolling: make a chunk BLANK ("") to test NaN coercion.
        "roll15_games": rng.integers(0, 16, n),
        "roll15_xwoba": [("" if i % 5 == 0 else round(v, 3))
                         for i, v in enumerate(rng.uniform(.28, .42, n))],
        "roll30_barrel_rate": [("" if i % 7 == 0 else round(v, 3))
                               for i, v in enumerate(rng.uniform(.02, .16, n))],
        # outcomes
        "out_pa": rng.integers(3, 6, n), "out_ab": rng.integers(3, 5, n),
        "out_hits": rng.integers(0, 4, n), "out_doubles": rng.integers(0, 2, n),
        "out_triples": rng.integers(0, 1, n), "out_hr": rng.integers(0, 2, n),
        "out_rbi": rng.integers(0, 4, n), "out_runs": rng.integers(0, 3, n),
        "out_bb": rng.integers(0, 2, n), "out_k": rng.integers(0, 3, n),
    })
    # keep hits >= 2b+3b+hr so singles is non-negative in most rows (still test max(0,...))
    return df


def make_pitchers(n=20):
    rng = np.random.default_rng(11)
    dates = pd.date_range("2024-04-01", periods=n, freq="D").strftime("%Y-%m-%d")
    return pd.DataFrame({
        "builder_schema": "a3.2", "season": 2024, "game_date": dates,
        "game_pk": range(n), "player_id": rng.integers(2000, 2100, n),
        "player_name": [f"SP{i}" for i in range(n)],
        "team": rng.choice(["NYY", "BOS"], n), "opponent": rng.choice(["NYY", "BOS"], n),
        "venue": rng.choice(["Fenway", "Dodger Stadium"], n), "throws": rng.choice(["L", "R"], n),
        "pit_k9": rng.uniform(6, 12, n), "pit_bb9": rng.uniform(1, 4, n),
        "umpire_id": rng.choice([101, 102], n), "has_prior_data": 1,
        "out_ip": rng.uniform(3, 7, n), "out_k": rng.integers(0, 11, n),
        "out_bb": rng.integers(0, 4, n), "out_hr": rng.integers(0, 3, n),
    })


def main() -> int:
    fw = FantasyWeights(single=3, double=5, triple=8, home_run=10, rbi=2, run=2, walk=2)
    hitters, pitchers = make_hitters(), make_pitchers()

    # 1. Schema gate
    try:
        assert_schema(hitters, kind="hitter"); assert_schema(pitchers, kind="pitcher")
        check("schema gate accepts valid a3.2/a4.1", True)
    except Exception as e:
        check("schema gate accepts valid a3.2/a4.1", False, str(e))

    bad = hitters.drop(columns=["roller_schema"])
    try:
        assert_schema(bad, kind="hitter"); check("schema gate rejects missing roller", False)
    except ValueError:
        check("schema gate rejects missing roller", True)

    bad2 = hitters.copy(); bad2["builder_schema"] = "a3.0"
    try:
        assert_schema(bad2, kind="hitter"); check("schema gate rejects wrong builder", False)
    except ValueError:
        check("schema gate rejects wrong builder", True)

    leaked = hitters.copy()
    leaked.loc[0, "opp_sp_source"] = "actual_starter"
    try:
        assert_schema(leaked, kind="hitter")
        check("schema gate rejects post-game opposing starter", False)
    except ValueError:
        check("schema gate rejects post-game opposing starter", True)

    # pitcher frame must NOT require roller (A4 is hitter-only)
    try:
        assert_schema(pitchers, kind="pitcher")
        check("pitcher gate does not require roller", True)
    except Exception as e:
        check("pitcher gate does not require roller", False, str(e))

    # 2. Targets match compute_actual_value exactly
    for cat in ("hits", "home_runs", "total_bases", "hrr", "fantasy"):
        got = derive_target(hitters, cat, fw)
        want = np.array([ref_actual(r, cat, fw) for _, r in hitters.iterrows()])
        check(f"target '{cat}' == compute_actual_value", np.allclose(got, want),
              f"max diff {np.max(np.abs(got-want)) if len(got) else 0}")
    gotk = derive_target(pitchers, "strikeouts", fw)
    wantk = pitchers["out_k"].to_numpy(dtype=float)
    check("target 'strikeouts' == out_k", np.allclose(gotk, wantk))

    # 3. Temporal split: no holdout date leaks into train; holdout is most recent
    split = choose_split_date(hitters, 0.25)
    data = build_category_data(hitters, "hits", kind="hitter", split_date=split,
                               fantasy_weights=fw)
    train_dates = set(pd.to_datetime(
        hitters[hitters["game_date"] < split]["game_date"]))
    holdout_dates = set(pd.to_datetime(data.holdout_ids["game_date"]))
    check("no holdout date in train (temporal, no leakage)",
          train_dates.isdisjoint(holdout_dates))
    check("holdout is the most-recent slice",
          (min(holdout_dates) >= max(train_dates)) if train_dates and holdout_dates else False)
    check("split produced non-empty train and holdout",
          len(data.y_train) > 0 and len(data.y_holdout) > 0,
          f"train={len(data.y_train)} holdout={len(data.y_holdout)}")

    # 4. A4 blanks -> NaN (numeric), not object strings
    xw = data.X_train["roll15_xwoba"] if "roll15_xwoba" in data.X_train else None
    check("roll15_xwoba coerced numeric", xw is not None and pd.api.types.is_float_dtype(xw))
    check("A4 blank became NaN (not '')",
          xw is not None and xw.isna().any() and not (xw == "").any())
    # categorical stays string with a filled sentinel (no NaN that CatBoost rejects)
    check("categorical 'team' is string w/o NaN",
          (data.X_train["team"].dtype == object
           or pd.api.types.is_string_dtype(data.X_train["team"]))
          and not data.X_train["team"].isna().any())

    # 5. Category guard
    engine = BacktestEngine()
    # Build baseline+candidate projections for the 4 gated cats and score.
    cats = ("hits", "hrr", "home_runs", "strikeouts")
    base_projs, cand_projs, outs = [], [], []
    rng = np.random.default_rng(3)
    for i in range(30):
        pid, game_pk, gd = 5000 + i, 900000 + i, "2024-09-01"
        for c in cats:
            actual = float(rng.integers(0, 3))
            outs.append(OutcomeRecord(pid, f"X{i}", gd, c, actual, mlb_game_pk=game_pk))
            base_projs.append(
                PropProjection(pid, f"X{i}", c, gd, actual + 0.9, 0.0, mlb_game_pk=game_pk)
            )
            cand_projs.append(
                PropProjection(pid, f"X{i}", c, gd, actual + 0.3, 0.0, mlb_game_pk=game_pk)
            )  # better
    b_rep, c_rep, comparison = score_reports(base_projs, cand_projs, outs, cats, engine)
    try:
        compared = assert_comparable(comparison, cats, baseline=b_rep, candidate=c_rep)
        check("category guard passes when all 4 present", sorted(compared) == sorted(cats))
    except Exception as e:
        check("category guard passes when all 4 present", False, str(e))

    # drop strikeouts from candidate -> compare_reports still lists it with
    # candidate n=0 and mae=0.0 (phantom win). Guard must RAISE on zero samples.
    cand_missing = [p for p in cand_projs if p.category != "strikeouts"]
    b2, c2, comp2 = score_reports(base_projs, cand_missing, outs, cats, engine)
    try:
        assert_comparable(comp2, cats, baseline=b2, candidate=c2)
        check("category guard rejects a phantom (zero-sample) category", False,
              "guard passed despite candidate having 0 strikeout predictions")
    except ValueError:
        check("category guard rejects a phantom (zero-sample) category", True)

    # 6. Better candidate should show improvement (sanity of the whole chain)
    check("better candidate lowers combined score",
          comparison["candidate_score"] < comparison["baseline_score"],
          f"base={comparison['baseline_score']} cand={comparison['candidate_score']}")

    # ---- report ----
    npass = sum(1 for r in results if r[0] == PASS)
    print("\nB1 OFFLINE HARNESS")
    print("=" * 64)
    for status, name, detail in results:
        line = f"  [{status}] {name}"
        if status == FAIL and detail:
            line += f"  -- {detail}"
        print(line)
    print("=" * 64)
    print(f"  {npass}/{len(results)} passed")
    return 0 if npass == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
