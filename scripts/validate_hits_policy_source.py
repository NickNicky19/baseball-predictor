#!/usr/bin/env python3
"""Validate an uncensored policy source against a certified strict baseline."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.evaluation.hits_policy_source import (  # noqa: E402
    assert_reproduces_strict_baseline, validate_policy_source,
)
from src.evaluation.strict_market_artifact import artifact_path_for_manifest  # noqa: E402


def sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-manifest", required=True)
    ap.add_argument("--baseline-manifest", required=True)
    args = ap.parse_args(argv)

    source_manifest_path = Path(args.source_manifest)
    baseline_manifest_path = Path(args.baseline_manifest)
    source_path = artifact_path_for_manifest(source_manifest_path)
    baseline_path = artifact_path_for_manifest(baseline_manifest_path)
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    baseline_manifest = json.loads(baseline_manifest_path.read_text(encoding="utf-8"))

    if source_manifest.get("artifact_kind") != "hits_policy_source_fragment_grain_v1":
        raise ValueError("source manifest does not declare the uncensored policy-source contract")
    if source_manifest.get("price_freshness_rule") != "deferred_to_chronological_policy_fit_v1":
        raise ValueError("source manifest did not defer freshness")
    if source_manifest.get("max_quote_age") is not None:
        raise ValueError("source manifest applied a quote-age cutoff")
    expected_source_hash = source_manifest.get("hashes", {}).get("artifact")
    if not sha256(source_path).startswith(str(expected_source_hash)):
        raise ValueError("policy-source artifact hash mismatch")
    expected_baseline_hash = baseline_manifest.get("hashes", {}).get("artifact")
    if not sha256(baseline_path).startswith(str(expected_baseline_hash)):
        raise ValueError("strict baseline artifact hash mismatch")

    source = pd.read_csv(source_path)
    baseline = pd.read_csv(baseline_path)
    validate_policy_source(source)
    cutoff = baseline_manifest.get("max_quote_age")
    if cutoff is None:
        raise ValueError("strict baseline manifest lacks max_quote_age")
    counts = assert_reproduces_strict_baseline(source, baseline, float(cutoff))

    print("HITS POLICY SOURCE VALID")
    print(f"  source_rows: {len(source)}")
    print(f"  source_dates: {source.official_game_date.nunique()}")
    print(f"  certified_cutoff_reproduced: {cutoff} minutes")
    for key, value in counts.items():
        print(f"  {key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
