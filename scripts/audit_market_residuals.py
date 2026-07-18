#!/usr/bin/env python3
"""Diagnose clean strict-market residuals without changing a model or policy.

The input is a completed market A/B manifest, not ad-hoc file paths.  Its
content hashes pin the exact model arms, official MLB targets, and strict
market universe that produced the baseline.  This audit refuses a partial or
substituted universe, then reports calibration shape separately by line.

It cannot say *why* a residual exists.  It does not fit a calibration layer,
select bets, change a threshold, or authorize betting.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.market_residuals import (  # noqa: E402
    PROBABILITY_EDGES,
    build_scored_pairs,
    by_date_metrics,
    global_metrics,
    reliability_bins,
)
from src.evaluation.strict_market_artifact import artifact_path_for_manifest  # noqa: E402


def sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]


def checked_input(payload: dict, name: str) -> Path:
    try:
        record = payload["inputs"][name]
        path = Path(record["path"])
        expected = record["sha"]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"A/B manifest lacks hashed {name!r} input") from exc
    if not path.exists():
        raise FileNotFoundError(f"A/B manifest input missing: {path}")
    actual = sha256(path)
    if actual != expected:
        raise ValueError(f"A/B manifest {name} hash mismatch: expected {expected}, "
                         f"got {actual}. Do not diagnose substituted inputs.")
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--market-ab-manifest", required=True,
                    help="completed strict A/B provenance manifest")
    ap.add_argument("--out", default=None,
                    help="JSON summary; defaults beside the A/B manifest")
    args = ap.parse_args(argv)

    ab_manifest_path = Path(args.market_ab_manifest)
    ab = json.loads(ab_manifest_path.read_text(encoding="utf-8"))
    if ab.get("mode") != "strict" or not isinstance(ab.get("strict"), dict):
        raise ValueError("residual audit requires a completed STRICT A/B manifest")
    if int(ab.get("counts", {}).get("scored", -1)) <= 0:
        raise ValueError("A/B manifest records no scored rows")

    frozen_path = checked_input(ab, "frozen")
    candidate_path = checked_input(ab, "candidate")
    official_path = checked_input(ab, "official")
    strict_manifest_path = Path(ab["strict"].get("manifest", ""))
    if not strict_manifest_path.exists():
        raise FileNotFoundError("A/B strict manifest is absent")
    if sha256(strict_manifest_path) != ab["strict"].get("manifest_sha"):
        raise ValueError("strict manifest changed after the A/B; residual audit refuses it")
    artifact_path = artifact_path_for_manifest(strict_manifest_path)
    if sha256(artifact_path) != ab["strict"].get("artifact_sha"):
        raise ValueError("strict artifact changed after the A/B; residual audit refuses it")

    market = pd.read_csv(artifact_path)
    frozen = pd.read_csv(frozen_path)
    candidate = pd.read_csv(candidate_path)
    official = pd.read_csv(official_path)
    pairs = build_scored_pairs(market, frozen, candidate, official)
    if len(pairs) != int(ab["counts"]["scored"]):
        raise ValueError(f"A/B scored {ab['counts']['scored']:,} rows but reconstructed "
                         f"residual universe has {len(pairs):,}")
    certified_dates = sorted(ab["strict"].get("date_universe", []))
    observed_dates = sorted(pairs.official_game_date.unique().tolist())
    if observed_dates != certified_dates:
        raise ValueError("residual date universe differs from certified A/B universe")

    metrics = pd.DataFrame([
        global_metrics(pairs, "frozen"),
        global_metrics(pairs, "candidate"),
    ])
    reliability = pd.concat([
        reliability_bins(pairs, "frozen"),
        reliability_bins(pairs, "candidate"),
    ], ignore_index=True)
    by_date = pd.concat([
        by_date_metrics(pairs, "frozen"),
        by_date_metrics(pairs, "candidate"),
    ], ignore_index=True)

    out = (Path(args.out) if args.out else
           ab_manifest_path.with_name(ab_manifest_path.stem.replace("_manifest", "") +
                                      "_residuals.json"))
    out.parent.mkdir(parents=True, exist_ok=True)
    pairs_path = out.with_name(out.stem + "_pairs.csv")
    metrics_path = out.with_name(out.stem + "_metrics.csv")
    reliability_path = out.with_name(out.stem + "_reliability.csv")
    dates_path = out.with_name(out.stem + "_by_date.csv")
    pairs.to_csv(pairs_path, index=False)
    metrics.to_csv(metrics_path, index=False)
    reliability.to_csv(reliability_path, index=False)
    by_date.to_csv(dates_path, index=False)

    payload = dict(
        _comment=(
            "Factual residual diagnostic on the exact completed strict A/B "
            "universe. It is not a calibration fit, bet-selection policy, or "
            "mechanism attribution."
        ),
        source_market_ab_manifest=str(ab_manifest_path),
        source_market_ab_manifest_sha256=sha256(ab_manifest_path),
        research_only=bool(ab.get("policy", {}).get("research_only", True)),
        certified_dates=certified_dates,
        strict_rows=int(len(pairs)),
        probability_bin_edges=[round(float(x), 2) for x in PROBABILITY_EDGES],
        global_metrics=metrics.to_dict("records"),
        outputs=dict(
            pairs=dict(path=str(pairs_path), sha256=sha256(pairs_path)),
            metrics=dict(path=str(metrics_path), sha256=sha256(metrics_path)),
            reliability=dict(path=str(reliability_path), sha256=sha256(reliability_path)),
            by_date=dict(path=str(dates_path), sha256=sha256(dates_path)),
        ),
        verdict=(
            "DIAGNOSTIC_ONLY: residuals locate calibration/line patterns but do "
            "not identify a causal component. No model or policy was changed."
        ),
    )
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    print("STRICT MARKET RESIDUAL DIAGNOSTIC")
    print(f"  certified rows={len(pairs):,}; dates={len(certified_dates)}; "
          f"research_only={payload['research_only']}")
    print("  " + metrics.to_string(index=False).replace("\n", "\n  "))
    print("  No causal mechanism is claimed; inspect line/bucket/date artifacts next.")
    print(f"wrote {out}\n      {pairs_path}\n      {metrics_path}\n      "
          f"{reliability_path}\n      {dates_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
