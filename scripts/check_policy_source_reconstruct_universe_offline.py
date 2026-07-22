#!/usr/bin/env python3
"""Mutation checks for policy-source reconstruction date selection."""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from run_gate_reconstruct import (  # noqa: E402
    load_policy_source_manifest,
    load_strict_market_manifest,
)
from src.evaluation.hits_policy_source import (  # noqa: E402
    DEFERRED_FRESHNESS_RULE,
    POLICY_SOURCE_KEY,
    POLICY_SOURCE_KIND,
)


def row(*, start: str, entry: float = 0.51) -> dict:
    return {
        "mlb_game_pk": 700001,
        "player_id": 111,
        "category": "hits",
        "line": 0.5,
        "vendor_game_id": "vendor-1",
        "start_time": start,
        "player": "Exact Vendor Name",
        "player_key": "exact vendor name",
        "official_game_date": "2026-04-21",
        "entry_over_age_min": 120.0,
        "entry_under_age_min": 125.0,
        "entry_age_min": 125.0,
        "entry_p_over": entry,
        "close_p_over": 0.52,
        "entry_overround": 0.04,
        "entry_over_odds_decimal": 2.0,
        "entry_under_odds_decimal": 1.9,
        "close_over_odds_decimal": 1.95,
        "close_under_odds_decimal": 1.95,
        "is_starter": True,
        "official_pa": 4,
        "base_rule_eligible": True,
    }


def write_source(root: Path, rows: list[dict]) -> tuple[Path, Path]:
    artifact = root / "policy_source.csv"
    pd.DataFrame(rows).to_csv(artifact, index=False)
    manifest = root / "policy_source_manifest.json"
    manifest.write_text(json.dumps({
        "artifact_kind": POLICY_SOURCE_KIND,
        "selection_rule": "dk_base_pregame_starter_and_pa_v1",
        "price_freshness_rule": DEFERRED_FRESHNESS_RULE,
        "max_quote_age": None,
        "source_key": POLICY_SOURCE_KEY,
        "raw_decimal_odds_included": True,
        "markets": ["hits"],
        "official_date_universe": ["2026-04-21"],
        "hashes": {
            "artifact": hashlib.sha256(artifact.read_bytes()).hexdigest()[:16],
        },
        "funnel": {"policy_source_rows": len(rows)},
    }), encoding="utf-8")
    return manifest, artifact


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
        # Same final MARKET_KEY, different exact vendor fragment identity. The
        # policy source must retain both; strictness comes after fitted freshness.
        rows = [
            row(start="2026-04-21 19:00:00", entry=0.51),
            row(start="2026-04-21 19:01:00", entry=0.55),
        ]
        manifest, artifact = write_source(root, rows)
        assert load_policy_source_manifest(manifest) == ["2026-04-21"]
        print("[PASS] policy source permits duplicate final keys at unique fragment grain")

        expect_error(
            lambda: load_strict_market_manifest(manifest),
            "two-sided price freshness",
        )
        print("[PASS] MUTATION uncensored policy source cannot masquerade as strict")

        assert load_policy_source_manifest(
            manifest, smoke_date="2026-04-21"
        ) == ["2026-04-21"]
        expect_error(
            lambda: load_policy_source_manifest(manifest, smoke_date="2026-04-22"),
            "not in the certified policy-source universe",
        )
        print("[PASS] MUTATION smoke date cannot expand the source universe")

        original = artifact.read_bytes()
        artifact.write_bytes(original + b"\n")
        expect_error(
            lambda: load_policy_source_manifest(manifest),
            "artifact hash mismatch",
        )
        artifact.write_bytes(original)
        print("[PASS] MUTATION changed source bytes cannot select dates")

        duplicate_rows = [rows[0], dict(rows[0], entry_p_over=0.99)]
        duplicate_manifest, _ = write_source(root, duplicate_rows)
        expect_error(
            lambda: load_policy_source_manifest(duplicate_manifest),
            "duplicate exact vendor source keys",
        )
        print("[PASS] MUTATION duplicate exact source identity hard-fails")

        bad_payload = json.loads(duplicate_manifest.read_text(encoding="utf-8"))
        bad_payload["max_quote_age"] = 90
        duplicate_manifest.write_text(json.dumps(bad_payload), encoding="utf-8")
        expect_error(
            lambda: load_policy_source_manifest(duplicate_manifest),
            "already censored",
        )
        print("[PASS] MUTATION inherited freshness censor cannot enter fit source")

    print("6/6 checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
