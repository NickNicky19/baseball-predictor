#!/usr/bin/env python3
"""Small dependency smoke for the shared CatBoost probability seam."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.multi_market_foundation import PA_OUTCOMES  # noqa: E402
from src.learning.shared_pa_model import fit_catboost  # noqa: E402


def main() -> int:
    rows = []
    for index in range(16):
        outcome = index % len(PA_OUTCOMES)
        counts = [0] * len(PA_OUTCOMES)
        counts[outcome] = 1
        strikeout, walk, single, double, triple, home_run, bip_out, other = counts
        hits = single + double + triple + home_run
        rows.append({
            "x": float(index),
            "bats": "L" if index % 2 else "R",
            "out_pa": 1,
            "out_ab": strikeout + hits + bip_out,
            "out_hits": hits,
            "out_doubles": double,
            "out_triples": triple,
            "out_hr": home_run,
            "out_bb": walk,
            "out_k": strikeout,
        })
    frame = pd.DataFrame(rows)
    model = fit_catboost(
        frame,
        features=["x", "bats"],
        params={
            "depth": 2,
            "l2_leaf_reg": 3.0,
            "learning_rate": 0.05,
            "iterations": 5,
            "thread_count": 1,
        },
        seed=260719,
    )
    probabilities = model.predict_proba(frame)
    assert probabilities.shape == (len(frame), len(PA_OUTCOMES))
    assert np.isfinite(probabilities).all()
    assert np.allclose(probabilities.sum(axis=1), 1.0, atol=1e-9)
    print("[OK] CatBoost preserves the locked eight-class probability contract")

    missing_class = frame.loc[frame.index % len(PA_OUTCOMES) != len(PA_OUTCOMES) - 1].copy()
    try:
        reduced = fit_catboost(
            missing_class,
            features=["x", "bats"],
            params={
                "depth": 2,
                "l2_leaf_reg": 3.0,
                "learning_rate": 0.05,
                "iterations": 5,
                "thread_count": 1,
            },
            seed=260719,
        )
        reduced.predict_proba(missing_class)
    except ValueError:
        print("[OK] MUTATION incomplete class set fails closed")
    else:
        raise AssertionError("incomplete CatBoost class set passed")
    print("2/2")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
