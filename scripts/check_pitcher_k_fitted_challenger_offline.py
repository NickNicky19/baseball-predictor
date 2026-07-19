#!/usr/bin/env python3
"""Mutation checks for the fitted pitcher-K challenger contract."""
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

import src.evaluation.pitcher_k_fitted_challenger as challenger  # noqa: E402


def expect_failure(label: str, operation) -> None:
    try:
        operation()
    except (AssertionError, TypeError, ValueError):
        print(f"  [OK] {label}")
        return
    raise AssertionError(f"mutation did not fail: {label}")


def main() -> int:
    protocol_path = ROOT / "config/pitcher_k_fitted_challenger_protocol.json"
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    challenger.validate_protocol(protocol, repo_root=ROOT, evidence_root=ROOT.parent)
    print("  [OK] locked protocol and source hashes")
    for label, mutate in (
        ("May chronology injection", lambda value: value["chronology"].update({"forbidden": []})),
        ("outcome feature injection", lambda value: value["candidate"].update({"features": value["candidate"]["features"] + ["out_k"]})),
        ("economic eligibility", lambda value: value.update({"economic_evidence_eligible": True})),
        ("production mutation", lambda value: value.update({"production_unchanged": False})),
        ("market pooling", lambda value: value["evaluation"].update({"market_pooling_forbidden": False})),
    ):
        changed = copy.deepcopy(protocol)
        mutate(changed)
        expect_failure(label, lambda changed=changed: challenger.validate_protocol(changed, repo_root=ROOT, evidence_root=ROOT.parent))
    y = np.array([0, 1, 1, 0], dtype=float)
    mean = np.array([0.5, 4.0, 6.0, 2.0])
    probability = challenger.tail_probability(mean, 4.5, 0.1)
    assert np.isfinite(probability).all() and ((probability >= 0) & (probability <= 1)).all()
    assert challenger.count_nll(np.array([0, 4, 6, 2]), mean, 0.1) > 0
    scores = challenger.binary_metrics(y, np.array([0.1, 0.7, 0.8, 0.3]))
    assert scores["brier"] >= 0 and scores["log_loss"] >= 0
    print("  [OK] count/tail/proper-score probability boundaries")
    print("PITCHER-K FITTED CHALLENGER OFFLINE CHECKS PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
