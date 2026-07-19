#!/usr/bin/env python3
"""Synthetic mutation checks for corrected shared-PA row accounting."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.shared_pa_training_data import validate_hitter_frame  # noqa: E402


def main() -> int:
    protocol = json.loads((ROOT / "config/shared_pa_benchmark_protocol.json").read_text(encoding="utf-8"))
    columns = set(protocol["forbidden_features"])
    for group in protocol["feature_group_contract"].values():
        columns.update(group)
    rows = []
    for player in range(18):
        row = {column: 0 for column in columns}
        row.update({
            "builder_schema": "a3.2", "roller_schema": "a4.1", "season": 2023,
            "game_date": "2023-04-01", "game_pk": 1, "player_id": player + 1,
            "player_name": f"p{player}", "lineup_slot": player % 9 + 1,
            "out_pa": 4, "out_ab": 3, "out_hits": 1, "out_doubles": 0,
            "out_triples": 0, "out_hr": 0, "out_rbi": 0, "out_runs": 0,
            "out_bb": 1, "out_k": 1,
        })
        rows.append(row)
    # Include all required seasons without changing the 18-hitter game invariant.
    rows += [{**row, "season": 2024, "game_date": "2024-04-01", "game_pk": 2, "player_id": row["player_id"]} for row in rows[:18]]
    rows += [{**row, "season": 2025, "game_date": "2025-04-01", "game_pk": 3, "player_id": row["player_id"]} for row in rows[:18]]
    original = pd.DataFrame(rows)
    validate_hitter_frame(original, protocol)
    print("[OK] valid corrected synthetic rows")

    mutations = [
        ("old schema", lambda d: d.__setitem__("builder_schema", "a3.1")),
        ("duplicate identity", lambda d: d.__setitem__("player_id", [1] * len(d))),
        ("missing starter", lambda d: d.drop(index=d.index[0], inplace=True)),
    ]
    for label, mutate in mutations:
        candidate = original.copy(deep=True)
        mutate(candidate)
        try:
            validate_hitter_frame(candidate, protocol)
        except ValueError:
            print(f"[OK] MUTATION {label} fails")
        else:
            raise AssertionError(f"mutation unexpectedly passed: {label}")
    candidate = original.copy(deep=True)
    candidate.loc[candidate.index[0], "out_doubles"] = 2
    try:
        validate_hitter_frame(candidate, protocol)
    except ValueError:
        print("[OK] MUTATION negative single fails")
    else:
        raise AssertionError("mutation unexpectedly passed: negative single")
    candidate = original.copy(deep=True)
    candidate.loc[candidate.index[0], "out_ab"] = 5
    try:
        validate_hitter_frame(candidate, protocol)
    except ValueError:
        print("[OK] MUTATION incomplete PA accounting fails")
    else:
        raise AssertionError("mutation unexpectedly passed: incomplete PA accounting")
    print("6/6")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
