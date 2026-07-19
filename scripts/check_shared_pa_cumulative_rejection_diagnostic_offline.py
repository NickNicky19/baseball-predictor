#!/usr/bin/env python3
"""Mutation checks for the locked rejection diagnostic boundary and math."""
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

from src.evaluation.shared_pa_rejection_diagnostic import (  # noqa: E402
    _binary_numerators, _bootstrap_counts, _class_numerators, _interval,
    load_protocol,
)


def caught(fn: object) -> bool:
    try:
        fn()  # type: ignore[operator]
    except (KeyError, TypeError, ValueError):
        return True
    return False


def main() -> int:
    protocol_path = ROOT / "config/shared_pa_cumulative_rejection_diagnostic_protocol.json"
    evidence_root = Path(r"C:\Projects\baseball_predictor")
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    checks: list[tuple[str, bool]] = []
    checks.append(("locked protocol accepted", bool(load_protocol(protocol_path, code_root=ROOT, evidence_root=evidence_root))))
    mutations = [
        ("authorization", lambda p: p.update(betting_authorized=True)),
        ("model fitting", lambda p: p.update(model_fitting_forbidden=False)),
        ("candidate selection", lambda p: p.update(candidate_selection_forbidden=False)),
        ("2025", lambda p: p.update(confirmation_2025_forbidden=False)),
        ("May", lambda p: p.update(may_2026_forbidden=False)),
        ("production", lambda p: p.update(production_unchanged=False)),
        ("identity", lambda p: p.update(identity_key=["player_id"])),
        ("arm", lambda p: p.update(candidate_arm="canonical_v1")),
        ("market", lambda p: p["derived_markets"].pop()),
        ("draws", lambda p: p["metrics"].update(bootstrap_draws=9999)),
        ("input hash", lambda p: p["inputs"]["selection_oof"].update(sha256="0" * 64)),
        ("invariant", lambda p: p["protected_invariants"].update(no_may_read=False)),
    ]
    for name, mutate in mutations:
        changed = copy.deepcopy(protocol)
        mutate(changed)
        temp = ROOT / f"config/.shared_pa_rejection_{name.replace(' ', '_')}.tmp.json"
        temp.write_text(json.dumps(changed), encoding="utf-8")
        try:
            checks.append((f"{name} mutation rejected", caught(lambda: load_protocol(temp, code_root=ROOT, evidence_root=evidence_root))))
        finally:
            temp.unlink(missing_ok=True)

    actual = np.array([[1, 0, 2, 0, 0, 0, 1, 0], [0, 1, 0, 1, 0, 0, 2, 0]], dtype=float)
    candidate = np.array([[.2, .1, .25, .08, .01, .03, .3, .03], [.2, .1, .25, .08, .01, .03, .3, .03]])
    baseline = np.array([[.22, .09, .23, .07, .01, .03, .32, .03], [.22, .09, .23, .07, .01, .03, .32, .03]])
    dates = pd.Series(["2024-04-01", "2024-04-02"])
    date_index, weights = _bootstrap_counts(dates, draws=200, seed=7)
    c_log, c_brier = _class_numerators(actual, candidate, 2)
    b_log, b_brier = _class_numerators(actual, baseline, 2)
    exposure = actual.sum(axis=1)
    class_interval = _interval(c_log, b_log, exposure, date_index, weights)
    checks.append(("class interval preserves two date blocks", class_interval["dates"] == 2 and class_interval["draws"] == 200))
    y = np.array([1.0, 0.0])
    c_binary = _binary_numerators(y, np.array([.6, .2]))
    b_binary = _binary_numerators(y, np.array([.5, .3]))
    binary_interval = _interval(c_binary[1], b_binary[1], np.ones(2), date_index, weights)
    checks.append(("binary Brier improvement has negative point", float(binary_interval["point"]) < 0.0))
    identical = _interval(c_brier, c_brier, exposure, date_index, weights)
    checks.append(("identical arms are exactly zero", identical["point"] == 0.0 and identical["lower"] == 0.0 and identical["upper"] == 0.0))

    failed = [name for name, passed in checks if not passed]
    if failed:
        raise SystemExit(f"rejection diagnostic checks failed: {failed}")
    print(f"SHARED PA REJECTION DIAGNOSTIC CHECKS VALID: {len(checks)}/{len(checks)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
