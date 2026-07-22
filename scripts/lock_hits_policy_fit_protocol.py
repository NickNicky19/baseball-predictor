#!/usr/bin/env python3
"""Lock the March-April hits policy-fit method before reading fit results.

The lock is content-addressed and fail-on-change.  It reads manifests and file
hashes only; it does not load model probabilities, CLV, official outcomes, or
the sealed May holdout.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.evaluation.hits_policy_fit import split_fit_dates  # noqa: E402


SCHEMA = "hits-payout-policy-fit-protocol-v1"


def sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root is not an object: {path}")
    return payload


def reference(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(path)
    return {"path": str(path.as_posix()), "sha256": sha256(path)}


def atomic_write(path: Path, payload: dict) -> None:
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if path.exists():
        existing = path.read_text(encoding="utf-8-sig")
        if existing != encoded:
            raise ValueError(
                f"protocol already exists with different content: {path}. "
                "A locked protocol is immutable."
            )
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, raw = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    temp = Path(raw)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--chronology",
        default="data/analysis/market_policy_hits_2026/chronological_protocol_uncensored_policy_source_v4.json",
    )
    ap.add_argument(
        "--source-manifest",
        default="data/market/v3/policy_fit_mar_may_canonical/hits_policy_source_uncensored_original_starter_fit_mar_apr_manifest.json",
    )
    ap.add_argument(
        "--frozen-manifest",
        default="data/analysis/market_policy_hits_2026/uncensored_fit_v4/fit_frozen.manifest.json",
    )
    ap.add_argument(
        "--candidate-manifest",
        default="data/analysis/market_policy_hits_2026/uncensored_fit_v4/fit_candidate.manifest.json",
    )
    ap.add_argument(
        "--out",
        default="data/analysis/market_policy_hits_2026/policy_fit_protocol_v1.json",
    )
    args = ap.parse_args(argv)

    chronology_path = Path(args.chronology)
    source_manifest_path = Path(args.source_manifest)
    frozen_manifest_path = Path(args.frozen_manifest)
    candidate_manifest_path = Path(args.candidate_manifest)
    chronology = load_json(chronology_path)
    source_manifest = load_json(source_manifest_path)
    frozen_manifest = load_json(frozen_manifest_path)
    candidate_manifest = load_json(candidate_manifest_path)

    fit_dates = list(source_manifest.get("official_date_universe", []))
    if len(fit_dates) < 4 or fit_dates != sorted(set(fit_dates)):
        raise ValueError("fit source needs at least four unique chronological dates")
    if fit_dates != chronology["arms"]["fit"].get("dates", fit_dates):
        # Older chronology files store the count/range but not the explicit list.
        expected_count = int(chronology["arms"]["fit"]["dates"])
        if len(fit_dates) != expected_count:
            raise ValueError("fit date universe disagrees with chronology lock")
    if fit_dates != frozen_manifest.get("dates") or fit_dates != candidate_manifest.get("dates"):
        raise ValueError("source and reconstruction date universes differ")
    if frozen_manifest.get("chronological_protocol", {}).get("role") != "fit":
        raise ValueError("frozen reconstruction is not bound to chronology role='fit'")
    if candidate_manifest.get("chronological_protocol", {}).get("role") != "fit":
        raise ValueError("candidate reconstruction is not bound to chronology role='fit'")
    if frozen_manifest.get("outcome_artifact_sha256") != candidate_manifest.get(
        "outcome_artifact_sha256"
    ):
        raise ValueError("frozen/candidate official-outcome artifacts differ")

    holdout_start = str(chronology.get("holdout_start", ""))
    selector_dates, confirmation_dates = split_fit_dates(fit_dates, holdout_start)

    out_path = Path(args.out)
    if out_path.exists():
        locked_at = str(load_json(out_path).get("locked_at_utc", ""))
        if not locked_at:
            raise ValueError("existing protocol lacks locked_at_utc")
    else:
        locked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    payload = {
        "schema_version": SCHEMA,
        "status": "LOCKED_BEFORE_FIT_RESULTS",
        "locked_at_utc": locked_at,
        "scope": {
            "sportsbook": "draftkings",
            "category": "hits",
            "sides": ["over", "under"],
            "entry_horizon_hours": 4,
            "selection_model": "candidate",
            "comparison_model": "frozen",
        },
        "inputs": {
            "chronology": reference(chronology_path),
            "fit_source_manifest": reference(source_manifest_path),
            "fit_source": reference(Path(chronology["arms"]["fit"]["artifact"])),
            "frozen_manifest": reference(frozen_manifest_path),
            "frozen_probabilities": reference(Path(frozen_manifest["probability_artifact"])),
            "candidate_manifest": reference(candidate_manifest_path),
            "candidate_probabilities": reference(Path(candidate_manifest["probability_artifact"])),
            "official_outcomes": reference(Path(frozen_manifest["outcome_artifact"])),
        },
        "internal_chronology": {
            "method": "ordered_date_count_midpoint_without_outcomes",
            "selector_dates": selector_dates,
            "confirmation_dates": confirmation_dates,
            "selector_rows_or_results_read_to_choose_split": False,
            "confirmation_rows_or_results_read_to_choose_split": False,
            "may_holdout_start": holdout_start,
            "may_holdout_opened": False,
        },
        "freshness_contract": {
            "historical_reference_max_quote_age_minutes": 90,
            "status": "UNRESOLVED_TIMESTAMP_PROXY",
            "outcome_fitted": False,
            "reason": (
                "The outcome-blind March-April age distribution has no measured "
                "interior gap supporting 90 minutes. Historical timestamps cannot "
                "prove that an unchanged quote remained executable. The reference "
                "cutoff may support research replication only; live shadow capture "
                "must establish executable availability."
            ),
            "duplicate_rule": "apply freshness first, then exclude every row on a duplicated MODEL_KEY",
        },
        "selection_contract": {
            "economic_boundary": (
                "choose the side with greater model expected profit at the exact "
                "posted decimal entry price; negative expected-profit rows can "
                "never qualify"
            ),
            "threshold_parameter": "min_expected_profit_per_unit",
            "candidate_family": (
                "zero plus one outcome-blind empirical expected-profit quantile "
                "per independent selector date block"
            ),
            "objective": "maximum selector-period lower 95% date-block capture bound",
            "eligibility": "selected rows must occur on every selector date",
            "tie_break": ["more selected rows", "lower threshold"],
            "outcomes_used_to_select_threshold": False,
            "closing_prices_used_to_select_threshold": True,
        },
        "uncertainty_contract": {
            "resampling_unit": "official_game_date",
            "interval": "two-sided percentile 95%",
            "bootstrap_draws": 20000,
            "seed": 17,
            "round_minimum_bet_count_used": False,
            "minimum_evidence": (
                "all date-block draws valid and the predeclared lower confidence "
                "bounds clear their gates; sample count is reported, never used "
                "as an inherited round cutoff"
            ),
        },
        "confirmation_gate": {
            "candidate_capture_lower_bound_strictly_greater_than": 0.10,
            "candidate_minus_frozen_capture_lower_bound_strictly_greater_than": 0.0,
            "threshold_may_change_after_selector": False,
            "freshness_proxy_may_change_after_selector": False,
            "failure_action": "NO_POLICY_QUALIFIES_AND_MAY_REMAINS_SEALED",
            "success_action": (
                "freeze a research policy and permit one unchanged May evaluation; "
                "do not authorize betting"
            ),
        },
        "diagnostics_not_selection_objectives": [
            "flat_stake_roi",
            "win_rate",
            "Brier score",
            "calibration residual",
        ],
        "protected_invariants": [
            "May artifacts and outcomes are not read during fit selection or confirmation",
            "frozen and candidate MODEL_KEY sets are exact and equal",
            "MLB game-keyed actuals are the only scoring truth",
            "freshness precedes symmetric duplicate-key exclusion",
            "no category or sportsbook pooling",
            "historical timestamp proxy cannot authorize executable betting",
        ],
        "required_mutations": [
            "inject a May date into fit input -> hard fail",
            "remove one model key -> hard fail",
            "exclude duplicate keys before freshness -> fixture changes and test fails",
            "allow negative posted-price expected profit -> test fails",
            "use confirmation rows to build threshold family -> hard fail",
            "confirmation lower bound misses either gate -> May remains sealed",
        ],
        "authorization": {
            "betting_authorized": False,
            "reason": (
                "A historical fit/confirmation pass can only nominate a locked May "
                "research test. Executable forward-shadow replication and an "
                "immutable market authorization certificate are still required."
            ),
        },
    }
    atomic_write(out_path, payload)
    print("HITS POLICY-FIT PROTOCOL LOCKED")
    print(f"  selector dates: {len(selector_dates)} ({selector_dates[0]} to {selector_dates[-1]})")
    print(
        f"  confirmation dates: {len(confirmation_dates)} "
        f"({confirmation_dates[0]} to {confirmation_dates[-1]})"
    )
    print(f"  protocol sha256: {sha256(args.out)}")
    print(f"  May holdout remains sealed: {holdout_start}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
