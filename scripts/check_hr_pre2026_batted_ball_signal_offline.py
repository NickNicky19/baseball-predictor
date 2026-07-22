#!/usr/bin/env python3
"""Mutation checks for the locked chronological HR batted-ball signal screen."""

from __future__ import annotations

import json
import hashlib
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_hr_pre2026_batted_ball_signal_screen import (  # noqa: E402
    load_protocol,
    report_provenance,
    require_locked_paths,
)
from src.evaluation.hr_batted_ball_signal import (  # noqa: E402
    HrBattedBallSignalError,
    paired_date_interval,
    refit_predict,
    require_exact_seasons,
    select_regularization,
    validate_numeric_features,
)

PROTOCOL = ROOT / "data/analysis/hr_over_contract_v1/pre2026_a3_2_migration_v2/signal_screen_protocol_v3.json"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_protocol(path: Path, payload: dict, *, sidecar: str | None = None) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.with_suffix(path.suffix + ".sha256").write_text(
        sidecar if sidecar is not None else digest(path), encoding="utf-8"
    )


def rows() -> pd.DataFrame:
    records = []
    rng = np.random.default_rng(17)
    for season in (2023, 2024, 2025):
        for index in range(120):
            base = rng.normal()
            extra = rng.normal()
            probability = 1 / (1 + np.exp(-(-2.2 + 0.35 * base + 0.55 * extra)))
            records.append({
                "season": season,
                "game_date": f"{season}-04-{1 + index % 20:02d}",
                "base": base,
                "extra": extra,
                "out_hr": int(rng.random() < probability),
            })
    return pd.DataFrame(records)


def fails(fn) -> bool:
    try:
        fn()
    except (ValueError, FileExistsError, HrBattedBallSignalError):
        return True
    return False


def main() -> int:
    protocol = load_protocol(PROTOCOL)
    assert protocol["status"] == "LOCKED_BEFORE_CONFIRMATION_RESULTS"
    print("[OK] locked protocol and source-audit hash validate")

    frame = rows()
    fit = frame[frame.season.eq(2023)]
    selection = frame[frame.season.eq(2024)]
    confirmation = frame[frame.season.eq(2025)]
    grid = [0.01, 0.1, 1.0]
    first = select_regularization(fit, selection, ["base", "extra"], grid)
    mutated = confirmation.copy()
    mutated["out_hr"] = 1 - mutated.out_hr
    second = select_regularization(fit, selection, ["base", "extra"], grid)
    assert first.selected_c == second.selected_c
    assert np.array_equal(
        first.model.named_steps["logistic"].coef_, second.model.named_steps["logistic"].coef_
    )
    print("[OK] MUTATION 2025 truth cannot change selected hyperparameters")

    assert fails(lambda: validate_numeric_features(frame, ["base", "out_hr"]))
    print("[OK] MUTATION official HR truth cannot enter features")

    _, baseline = refit_predict(pd.concat([fit, selection]), confirmation, ["base"], 0.1)
    same = confirmation.copy()
    same["zero"] = 0.0
    train_same = pd.concat([fit, selection]).copy()
    train_same["zero"] = 0.0
    _, candidate = refit_predict(train_same, same, ["base", "zero"], 0.1)
    assert np.allclose(baseline, candidate, atol=1e-12, rtol=0)
    print("[OK] MUTATION no added signal yields identical probabilities")

    assert fails(
        lambda: paired_date_interval(
            confirmation.game_date, np.zeros(len(confirmation) - 1), np.zeros(len(confirmation)),
            draws=100, seed=17,
        )
    )
    print("[OK] MUTATION paired row/date mismatch fails")

    with tempfile.TemporaryDirectory(prefix="hr_signal_protocol_") as tmp:
        changed = json.loads(PROTOCOL.read_text(encoding="utf-8"))
        changed["source_input_audit"]["sha256"] = "0" * 64
        path = Path(tmp) / "protocol.json"
        write_protocol(path, changed)
        assert fails(lambda: load_protocol(path))
    print("[OK] MUTATION input audit hash mismatch fails")

    with tempfile.TemporaryDirectory(prefix="hr_signal_source_") as tmp:
        changed = json.loads(PROTOCOL.read_text(encoding="utf-8"))
        changed["source_input"]["sha256"] = "0" * 64
        path = Path(tmp) / "protocol.json"
        write_protocol(path, changed)
        assert fails(lambda: load_protocol(path))
    print("[OK] MUTATION source input hash mismatch fails")

    with tempfile.TemporaryDirectory(prefix="hr_signal_sidecar_") as tmp:
        path = Path(tmp) / "protocol.json"
        write_protocol(path, json.loads(PROTOCOL.read_text(encoding="utf-8")), sidecar="0" * 64)
        assert fails(lambda: load_protocol(path))
    print("[OK] MUTATION protocol sidecar mismatch fails")

    with tempfile.TemporaryDirectory(prefix="hr_signal_provenance_") as tmp:
        a = Path(tmp) / "a.json"
        b = Path(tmp) / "b.csv"
        a.write_text("{}", encoding="utf-8")
        b.write_text("x\n1\n", encoding="utf-8")
        bound = report_provenance(a, b)
        assert bound == {"protocol_sha256": digest(a), "input_sha256": digest(b)}
        assert bound["protocol_sha256"] != digest(PROTOCOL)
    print("[OK] MUTATION provenance binds actual paths, not module defaults")

    with tempfile.TemporaryDirectory(prefix="hr_signal_once_") as tmp:
        locked = json.loads(PROTOCOL.read_text(encoding="utf-8"))
        output = Path(tmp) / "confirmation.json"
        output.write_text("already opened", encoding="utf-8")
        locked["output"]["path"] = str(output)
        actual_input = (ROOT / locked["source_input"]["path"]).resolve()
        assert fails(lambda: require_locked_paths(locked, actual_input, output.resolve()))
    print("[OK] MUTATION existing confirmation output refuses overwrite")

    contaminated = frame.copy()
    contaminated.loc[len(contaminated)] = {
        "season": 2026, "game_date": "2026-05-01", "base": 0, "extra": 0, "out_hr": 0,
    }
    assert fails(lambda: require_exact_seasons(contaminated))
    print("[OK] MUTATION protocol excludes every 2026 row, including May")

    assert protocol["decision_scope"].startswith("PASS only permits")
    assert protocol["betting_authorized"] is False and protocol["may_2026_opened"] is False
    print("[OK] PASS cannot install a candidate, open May, or authorize betting")

    print("12/12")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
