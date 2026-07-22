#!/usr/bin/env python3
"""Mutation checks for an uncensored fit/holdout chronology lock."""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.evaluation.chronological_market_protocol import (  # noqa: E402
    build_protocol,
    verify_protocol_role,
)
from src.evaluation.hits_policy_source import (  # noqa: E402
    DEFERRED_FRESHNESS_RULE,
    POLICY_SOURCE_KEY,
    POLICY_SOURCE_KIND,
)


def source_row(*, game: int, player: int, date: str, start: str, price: float) -> dict:
    return {
        "mlb_game_pk": game,
        "player_id": player,
        "category": "hits",
        "line": 0.5,
        "vendor_game_id": f"vendor-{game}",
        "start_time": start,
        "player": f"Player {player}",
        "player_key": f"player {player}",
        "official_game_date": date,
        "entry_over_age_min": 100.0,
        "entry_under_age_min": 105.0,
        "entry_age_min": 105.0,
        "entry_p_over": price,
        "close_p_over": price + 0.01,
        "entry_overround": 0.04,
        "entry_over_odds_decimal": 2.0,
        "entry_under_odds_decimal": 1.9,
        "close_over_odds_decimal": 1.95,
        "close_under_odds_decimal": 1.95,
        "is_starter": True,
        "official_pa": 4,
        "base_rule_eligible": True,
    }


def write_arm(root: Path, name: str, rows: list[dict]) -> Path:
    artifact = root / f"{name}.csv"
    frame = pd.DataFrame(rows)
    frame.to_csv(artifact, index=False)
    manifest = root / f"{name}_manifest.json"
    manifest.write_text(json.dumps({
        "artifact_kind": POLICY_SOURCE_KIND,
        "selection_rule": "dk_base_pregame_starter_and_pa_v1",
        "price_freshness_rule": DEFERRED_FRESHNESS_RULE,
        "max_quote_age": None,
        "source_key": POLICY_SOURCE_KEY,
        "raw_decimal_odds_included": True,
        "markets": ["hits"],
        "book": "draftkings",
        "entry_hours": 4,
        "hashes": {
            "artifact": hashlib.sha256(artifact.read_bytes()).hexdigest()[:16],
            "policy": "POLICYSHA",
        },
        "funnel": {"policy_source_rows": len(frame)},
        "official_date_universe": sorted(frame.official_game_date.unique().tolist()),
    }), encoding="utf-8")
    return manifest


def expect_error(fn, text: str) -> None:
    try:
        fn()
    except ValueError as exc:
        assert text in str(exc), (str(exc), text)
    else:
        raise AssertionError(f"expected failure containing {text!r}")


def main() -> int:
    with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
        root = Path(tmp)
        # Two fragment identities share a final market key in fit. This must be
        # retained until candidate freshness is applied.
        fit_rows = [
            source_row(game=1, player=10, date="2026-04-30",
                       start="2026-04-30 19:00:00", price=0.51),
            source_row(game=1, player=10, date="2026-04-30",
                       start="2026-04-30 19:01:00", price=0.55),
        ]
        hold_rows = [
            source_row(game=2, player=20, date="2026-05-01",
                       start="2026-05-01 19:00:00", price=0.52),
        ]
        fit = write_arm(root, "fit_source", fit_rows)
        hold = write_arm(root, "holdout_source", hold_rows)
        parent = write_arm(root, "parent_source", fit_rows + hold_rows)
        protocol = root / "source_protocol.json"

        payload = build_protocol(
            parent_manifest=parent,
            fit_manifest=fit,
            holdout_manifest=hold,
            holdout_start="2026-05-01",
            output=protocol,
        )
        assert payload["identity_key"] == POLICY_SOURCE_KEY
        assert verify_protocol_role(
            protocol, role="fit", market_manifest=fit
        )["rows"] == 2
        print("[PASS] exact source-grain chronology retains final-key collisions")

        expect_error(
            lambda: verify_protocol_role(protocol, role="fit", market_manifest=hold),
            "not the protocol's locked fit manifest",
        )
        print("[PASS] MUTATION holdout source cannot masquerade as fit")

        fit_artifact = root / "fit_source.csv"
        original = fit_artifact.read_text(encoding="utf-8")
        fit_artifact.write_text(original.replace("0.51", "0.61"), encoding="utf-8")
        expect_error(
            lambda: verify_protocol_role(protocol, role="fit", market_manifest=fit),
            "artifact hash mismatch",
        )
        fit_artifact.write_text(original, encoding="utf-8")
        print("[PASS] MUTATION a changed source price cannot enter locked fit")

        drift = [dict(hold_rows[0], entry_p_over=0.72)]
        drift_manifest = write_arm(root, "holdout_drift", drift)
        expect_error(
            lambda: build_protocol(
                parent_manifest=parent,
                fit_manifest=fit,
                holdout_manifest=drift_manifest,
                holdout_start="2026-05-01",
                output=root / "bad.json",
            ),
            "evaluator-consumed value drifted",
        )
        print("[PASS] MUTATION full price values, not only keys, define the partition")

        duplicate_fit = write_arm(root, "fit_duplicate", [fit_rows[0], fit_rows[0]])
        expect_error(
            lambda: build_protocol(
                parent_manifest=parent,
                fit_manifest=duplicate_fit,
                holdout_manifest=hold,
                holdout_start="2026-05-01",
                output=root / "duplicate.json",
            ),
            "duplicate exact vendor source keys",
        )
        print("[PASS] MUTATION duplicate exact source identity hard-fails")

    print("5/5 checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
