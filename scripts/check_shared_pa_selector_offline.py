#!/usr/bin/env python3
"""Offline chronology mutation checks for shared-PA calibration selection."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.select_shared_pa_challenger import cross_fitted_temperature_probabilities  # noqa: E402
from src.evaluation.multi_market_foundation import PA_OUTCOMES  # noqa: E402


def rows() -> pd.DataFrame:
    items = []
    for fold in range(4):
        for player in range(2):
            items.append({
                "game_pk": 100 + fold,
                "player_id": player + 1,
                "game_date": f"2024-0{4 + fold}-01",
                "_selection_fold": fold,
                "out_pa": 4,
                "out_ab": 4,
                "out_hits": 1 + ((fold + player) % 2),
                "out_doubles": 0,
                "out_triples": 0,
                "out_hr": 0,
                "out_bb": 0,
                "out_k": 1,
            })
    return pd.DataFrame(items)


def main() -> int:
    protocol = json.loads((ROOT / "config/shared_pa_benchmark_protocol.json").read_text(encoding="utf-8"))
    frame = rows()
    base = np.tile(np.array([0.20, 0.08, 0.20, 0.05, 0.01, 0.03, 0.40, 0.03]), (len(frame), 1))
    calibrated, records = cross_fitted_temperature_probabilities(frame, base, protocol)
    first = frame["_selection_fold"].to_numpy() == 0
    assert np.allclose(calibrated[first], base[first])
    assert records[0]["trained_on_prior_oof_rows"] == 0
    assert all(records[index]["trained_on_prior_oof_rows"] > 0 for index in range(1, 4))
    print("[OK] first fold uses locked identity calibration; later folds use prior OOF rows")

    future_mutation = base.copy()
    future_mutation[frame["_selection_fold"].to_numpy() == 3] = np.roll(
        future_mutation[frame["_selection_fold"].to_numpy() == 3], 1, axis=1
    )
    mutated, _ = cross_fitted_temperature_probabilities(frame, future_mutation, protocol)
    earlier = frame["_selection_fold"].to_numpy() < 3
    assert np.allclose(calibrated[earlier], mutated[earlier])
    print("[OK] MUTATION future-fold probabilities cannot change earlier calibration")

    bad_frame = copy.deepcopy(frame)
    bad_frame.loc[bad_frame["_selection_fold"] == 3, "_selection_fold"] = 4
    try:
        cross_fitted_temperature_probabilities(bad_frame, base, protocol)
    except ValueError:
        print("[OK] MUTATION missing/relabelled fold fails closed")
    else:
        raise AssertionError("relabelled calibration fold passed")
    print("3/3")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
