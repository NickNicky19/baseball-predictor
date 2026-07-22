#!/usr/bin/env python3
"""Offline contract and mutation tests for the Hits contact adapter candidate."""

from __future__ import annotations

import copy
from datetime import date, timedelta
import json
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.evaluation.hits_contact_adapter import (  # noqa: E402
    HitsContactAdapterSettings,
    build_contact_adapter_evidence,
    consume_fitted_contact_xba,
)
from src.models.dataclasses import StatcastProfile  # noqa: E402
from src.simulation.pa_simulator import HybridPASimulator  # noqa: E402


def check(condition: bool, label: str) -> None:
    if not condition:
        raise AssertionError(label)
    print(f"  [OK] {label}")


def expect_failure(fn, label: str) -> None:
    try:
        fn()
    except Exception:
        print(f"  [OK] {label}")
        return
    raise AssertionError(label)


def candidate_config() -> dict:
    return json.loads(
        (ROOT / "config/config.kbb.hits_contact_adapter.json").read_text(
            encoding="utf-8"
        )
    )


def fixture() -> pd.DataFrame:
    rows = []
    start = date(2026, 2, 15)
    for offset in range(31):
        game_date = start + timedelta(days=offset)
        rows.append(
            {
                "batter": 10,
                "game_date": game_date.isoformat(),
                "estimated_ba_using_speedangle": 0.10 if offset == 0 else 0.30,
            }
        )
        rows.append(
            {
                "batter": 20,
                "game_date": game_date.isoformat(),
                "estimated_ba_using_speedangle": 0.40,
            }
        )
    return pd.DataFrame(rows)


def main() -> int:
    print("HITS CONTACT ADAPTER OFFLINE CONTRACT")
    settings = HitsContactAdapterSettings.from_config(candidate_config())
    check(settings is not None, "hash-bound candidate configuration loads")

    evidence = build_contact_adapter_evidence(
        fixture(),
        active_player_ids=[10, 20, 30],
        target_date="2026-04-01",
        settings=settings,
    )
    check(set(evidence) == {10, 20, 30}, "active-player accounting is exhaustive")
    check(
        evidence[10]["first_evidence_date"] == "2026-02-16"
        and evidence[10]["player_bip"] == 30,
        "the last 30 player game dates are used (not 30 calendar days)",
    )
    check(
        evidence[30]["status"] == "baseline_fallback"
        and evidence[30]["reason"] == "no_valid_player_bip",
        "missing player evidence preserves and labels the baseline fallback",
    )

    contaminated = fixture()
    contaminated.loc[len(contaminated)] = [
        10,
        "2026-04-01",
        0.99,
    ]
    expect_failure(
        lambda: build_contact_adapter_evidence(
            contaminated,
            active_player_ids=[10],
            target_date="2026-04-01",
            settings=settings,
        ),
        "MUTATION target-date Statcast evidence hard-fails chronology",
    )

    tampered = candidate_config()
    tampered["feature_factory"]["hits_contact_adapter"][
        "selection_evidence_sha256"
    ] = "0" * 64
    expect_failure(
        lambda: HitsContactAdapterSettings.from_config(tampered),
        "MUTATION tampered selection-evidence hash fails",
    )
    expect_failure(
        lambda: settings.assert_target_allowed("2026-05-10"),
        "MUTATION sealed May date fails before Statcast is read",
    )

    profile = StatcastProfile(
        player_id=10,
        player_name="Fixture",
        sample_pa=80,
        xwoba=0.320,
        xba=0.310,
        xslg=0.520,
        barrel_rate=0.08,
        hard_hit_rate=0.40,
        contact_rate=0.80,
    )
    simulator = HybridPASimulator(random_seed=17)
    absent = simulator.expected_outcome_probabilities(statcast=profile)
    empty = simulator.expected_outcome_probabilities(
        statcast=profile, rich_features={}
    )
    check(absent == empty, "disabled/absent candidate is byte-identical")

    fitted = float(evidence[10]["fitted_contact_xba"])
    consumed = consume_fitted_contact_xba(
        fitted_contact_xba=fitted,
        legacy_xba_shrunk=0.25,
    )
    check(consumed == fitted, "fitted contact xBA is consumed exactly")
    double_shrunk = (80.0 * fitted + 120.0 * 0.326) / 200.0
    check(
        abs(consumed - double_shrunk) > 1e-9,
        "MUTATION double-shrinking moves the fixture and is caught",
    )

    altered = copy.deepcopy(evidence)
    altered[999] = altered.pop(10)
    expect_failure(
        lambda: (
            None
            if set(altered) == {10, 20, 30}
            else (_ for _ in ()).throw(ValueError("identity mismatch"))
        ),
        "MUTATION changed player identity fails exact key alignment",
    )

    missing_label = copy.deepcopy(evidence)
    missing_label[30].pop("status")
    expect_failure(
        lambda: (
            None
            if all("status" in row for row in missing_label.values())
            else (_ for _ in ()).throw(ValueError("missing status"))
        ),
        "MUTATION removing a fallback label fails accounting",
    )
    print("\n12/12")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
