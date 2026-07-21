from __future__ import annotations

import numpy as np
import pandas as pd

from scripts.select_direct_batter_pa_foundation import _component, _scores


def frame() -> pd.DataFrame:
    rows = [{
        "out_pa": 4, "out_ab": 4, "out_hits": 2, "out_doubles": 1,
        "out_triples": 0, "out_hr": 1, "out_bb": 0, "out_k": 1,
    } for _ in range(5)]
    rows[1].update(out_hits=1, out_doubles=0, out_hr=0, out_k=2)
    rows[2].update(out_hits=1, out_doubles=0, out_triples=1, out_hr=0, out_k=0)
    rows[3].update(out_hits=0, out_doubles=0, out_hr=0, out_k=3)
    rows[4].update(out_hits=2, out_doubles=2, out_hr=0, out_k=1)
    return pd.DataFrame(rows)


def test_components_preserve_exposure() -> None:
    p = np.tile(np.array([[.25, .05, .20, .10, .01, .04, .30, .05]]), (5, 1))
    for name in ("hits", "hr_over_0_5", "total_bases"):
        counts, probability = _component(frame(), p, name)
        assert counts.sum() == 20
        assert np.allclose(probability.sum(axis=1), 1)
        metrics = _scores(counts, probability)
        assert metrics["brier"] >= 0 and metrics["log_loss"] >= 0
