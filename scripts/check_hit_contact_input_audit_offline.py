#!/usr/bin/env python3
"""Mutation checks for the chronology-safe hitter contact-input audit."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.audit_hit_contact_inputs import extract_contact_outcomes  # noqa: E402
from src.evaluation.hit_contact_skill import (  # noqa: E402
    add_daily_anchors,
    add_probability,
    choose_window_on_selector,
    fit_prior_strength_on_selector,
    paired_date_score_interval,
    validate_contact_rows,
)


def fixture() -> pd.DataFrame:
    rows = []
    dates = pd.date_range("2023-04-01", periods=8, freq="D")
    for i, date in enumerate(dates):
        for player in range(1, 5):
            x15 = 0.20 + player * 0.035
            x30 = 0.24 + player * 0.018
            contacts = 4
            # Selector favors 15-game ranking. Confirmation is intentionally
            # not consulted by the selection functions.
            hits = min(contacts, max(0, round(contacts * x15)))
            rows.append({
                "game_pk": 1000 + i,
                "player_id": player,
                "game_date": date.strftime("%Y-%m-%d"),
                "lineup_slot": player,
                "roll15_xba": x15,
                "roll15_bip": 20 + player,
                "recent_pa_15": 35 + player,
                "roll30_xba": x30,
                "roll30_bip": 45 + player,
                "recent_pa_30": 75 + player,
                "contact_events": contacts,
                "hits_on_contact": hits,
            })
    return add_daily_anchors(validate_contact_rows(pd.DataFrame(rows)))


def expect_failure(callable_) -> bool:
    try:
        callable_()
    except (ValueError, FileNotFoundError):
        return True
    return False


def main() -> int:
    rows = fixture()
    selector = rows[rows.game_date <= "2023-04-04"].copy()
    confirmation = rows[rows.game_date > "2023-04-04"].copy()

    duplicate = pd.concat([rows, rows.iloc[[0]]], ignore_index=True)
    assert expect_failure(lambda: validate_contact_rows(duplicate))
    print("[OK] MUTATION duplicate player-game key fails")

    pa = add_probability(
        rows, output="pa", window=30, evidence_column="recent_pa_30", prior_strength=120
    )
    bip = add_probability(
        pa, output="bip", window=30, evidence_column="roll30_bip", prior_strength=120
    )
    assert (bip.pa - bip.bip).abs().max() > 1e-6
    print("[OK] MUTATION PA evidence and BIP evidence produce different probabilities")

    window = choose_window_on_selector(selector)
    strength = fit_prior_strength_on_selector(selector, window)
    mutated = rows.copy()
    mutated.loc[mutated.game_date > "2023-04-04", "hits_on_contact"] = 0
    mutated_selector = mutated[mutated.game_date <= "2023-04-04"].copy()
    assert choose_window_on_selector(mutated_selector) == window
    assert fit_prior_strength_on_selector(mutated_selector, window) == strength
    print("[OK] MUTATION confirmation truth cannot move selector choices")

    confirmation = add_probability(
        confirmation,
        output="bip",
        window=30,
        evidence_column="roll30_bip",
        prior_strength=120,
    )
    confirmation = add_probability(
        confirmation,
        output="pa",
        window=30,
        evidence_column="recent_pa_30",
        prior_strength=120,
    )
    confirmation = add_probability(
        confirmation,
        output="same_a",
        window=30,
        evidence_column="roll30_bip",
        prior_strength=120,
    )
    confirmation["same_b"] = confirmation.same_a
    interval = paired_date_score_interval(
        confirmation,
        candidate_column="same_a",
        baseline_column="same_b",
        metric="brier",
        declared_dates=confirmation.game_date.unique(),
        bootstrap=1000,
        seed=17,
    )
    assert interval.lower == interval.upper == 0.0
    print("[OK] identical arms yield an exactly zero paired interval")

    before = paired_date_score_interval(
        confirmation,
        candidate_column="bip",
        baseline_column="pa",
        metric="log_loss",
        declared_dates=confirmation.game_date.unique(),
        bootstrap=1000,
        seed=17,
    )
    changed = confirmation.copy()
    changed["hits_on_contact"] = changed.contact_events - changed.hits_on_contact
    after = paired_date_score_interval(
        changed,
        candidate_column="bip",
        baseline_column="pa",
        metric="log_loss",
        declared_dates=changed.game_date.unique(),
        bootstrap=1000,
        seed=17,
    )
    assert (before.lower, before.upper) != (after.lower, after.upper)
    print("[OK] MUTATION confirmation truth moves paired score evidence")

    with tempfile.TemporaryDirectory(prefix="contact_audit_") as tmp:
        root = Path(tmp) / "2025"
        root.mkdir()
        pd.DataFrame([{
            "game_date": "2025-04-01", "game_pk": 1, "batter": 222,
            "type": "X", "events": "single", "at_bat_number": 1,
        }]).to_csv(root / "batter_111.csv", index=False)
        assert expect_failure(lambda: extract_contact_outcomes([root]))
    print("[OK] MUTATION raw Statcast batter/file identity mismatch fails")

    print("6/6")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
