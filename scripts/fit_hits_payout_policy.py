#!/usr/bin/env python3
"""Fit the locked March-April DraftKings hits payout policy.

This command is permitted to read only the content-addressed fit artifacts
named by the already-locked protocol.  It never discovers or opens May.  A
passing result freezes a research policy for one later May evaluation; it does
not authorize betting because historical quote age remains an executability
proxy and prospective shadow evidence does not yet exist.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.evaluation.hits_policy_fit import (  # noqa: E402
    ARM_COLUMNS,
    arm_policy_rows,
    build_policy_pairs,
    choose_threshold,
    confirmation_gate,
    date_block_interval,
    paired_capture_change_interval,
    policy_metrics,
)


PROTOCOL_SCHEMA = "hits-payout-policy-fit-protocol-v1"
REPORT_SCHEMA = "hits-payout-policy-fit-report-v1"
POLICY_SCHEMA = "hits-payout-policy-research-v1"


def sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root is not an object: {path}")
    return payload


def resolve_reference(raw: Any) -> Path:
    if not isinstance(raw, dict):
        raise ValueError("protocol input reference is not an object")
    path = Path(str(raw.get("path", "")))
    if not path.is_absolute():
        path = ROOT / path
    if not path.is_file():
        raise FileNotFoundError(path)
    expected = str(raw.get("sha256", ""))
    actual = sha256(path)
    if actual != expected:
        raise ValueError(f"protocol input hash mismatch for {path}: {expected} != {actual}")
    return path


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
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


def arm_summary(
    rows: pd.DataFrame,
    threshold: float,
    *,
    block_dates: list[str],
    bootstrap: int,
    seed: int,
) -> dict:
    return {
        "point": asdict(policy_metrics(rows, threshold)),
        "capture_interval": asdict(
            date_block_interval(
                rows,
                threshold,
                block_dates=block_dates,
                bootstrap=bootstrap,
                seed=seed,
                metric="capture",
            )
        ),
        "flat_stake_roi_interval": asdict(
            date_block_interval(
                rows,
                threshold,
                block_dates=block_dates,
                bootstrap=bootstrap,
                seed=seed,
                metric="flat_stake_roi",
            )
        ),
    }


def ensure_exact_dates(frame: pd.DataFrame, dates: list[str], label: str) -> None:
    observed = sorted(frame.official_game_date.astype(str).unique().tolist())
    if observed != dates:
        raise ValueError(f"{label} dates differ from protocol: {observed} != {dates}")


def ensure_subset_dates(frame: pd.DataFrame, dates: list[str], label: str) -> None:
    observed = set(frame.official_game_date.astype(str).unique())
    unexpected = observed - set(dates)
    if unexpected:
        raise ValueError(f"{label} has dates outside the protocol: {sorted(unexpected)}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--protocol",
        default="data/analysis/market_policy_hits_2026/policy_fit_protocol_v1.json",
    )
    ap.add_argument(
        "--out-dir",
        default="data/analysis/market_policy_hits_2026/policy_fit_v1",
    )
    args = ap.parse_args(argv)

    protocol_path = Path(args.protocol)
    protocol = load_json(protocol_path)
    if protocol.get("schema_version") != PROTOCOL_SCHEMA:
        raise ValueError("unknown policy-fit protocol schema")
    if protocol.get("status") != "LOCKED_BEFORE_FIT_RESULTS":
        raise ValueError("policy-fit protocol is not locked")
    if protocol.get("authorization", {}).get("betting_authorized") is not False:
        raise ValueError("fit protocol unexpectedly authorizes betting")

    inputs = protocol.get("inputs")
    if not isinstance(inputs, dict):
        raise ValueError("protocol has no input references")
    resolved = {name: resolve_reference(reference) for name, reference in inputs.items()}
    # A fitter that knows a May path has already violated the lock.
    forbidden = [
        name
        for name, path in resolved.items()
        if "holdout" in name.lower() or "holdout" in path.name.lower() or "may" in path.name.lower()
    ]
    if forbidden:
        raise ValueError(f"sealed holdout reference reached the fit protocol: {forbidden}")

    chronology = protocol["internal_chronology"]
    selector_dates = list(chronology["selector_dates"])
    confirmation_dates = list(chronology["confirmation_dates"])
    fit_dates = selector_dates + confirmation_dates
    if set(selector_dates) & set(confirmation_dates):
        raise ValueError("selector and confirmation dates overlap")
    if max(selector_dates) >= min(confirmation_dates):
        raise ValueError("internal fit chronology is not ordered")
    if max(fit_dates) >= str(chronology["may_holdout_start"]):
        raise ValueError("May leaked into fit protocol")

    source = pd.read_csv(resolved["fit_source"])
    frozen = pd.read_csv(resolved["frozen_probabilities"])
    candidate = pd.read_csv(resolved["candidate_probabilities"])
    official = pd.read_csv(resolved["official_outcomes"])
    max_age = float(
        protocol["freshness_contract"]["historical_reference_max_quote_age_minutes"]
    )
    bootstrap = int(protocol["uncertainty_contract"]["bootstrap_draws"])
    seed = int(protocol["uncertainty_contract"]["seed"])
    capture_bar = float(
        protocol["confirmation_gate"][
            "candidate_capture_lower_bound_strictly_greater_than"
        ]
    )

    pairs, funnel = build_policy_pairs(
        source,
        frozen,
        candidate,
        official,
        max_quote_age=max_age,
        allowed_dates=fit_dates,
    )
    ensure_subset_dates(pairs, fit_dates, "strict fit universe")
    selector_pairs = pairs[pairs.official_game_date.isin(selector_dates)].copy()
    confirmation_pairs = pairs[pairs.official_game_date.isin(confirmation_dates)].copy()
    ensure_exact_dates(selector_pairs, selector_dates, "selector universe")
    ensure_subset_dates(confirmation_pairs, confirmation_dates, "confirmation universe")

    selector_rows = {
        arm: arm_policy_rows(selector_pairs, arm) for arm in ARM_COLUMNS
    }
    confirmation_rows = {
        arm: arm_policy_rows(confirmation_pairs, arm) for arm in ARM_COLUMNS
    }
    threshold, search = choose_threshold(
        selector_rows["candidate"],
        expected_dates=selector_dates,
        bootstrap=bootstrap,
        seed=seed,
    )

    selector_summary = {
        arm: arm_summary(
            rows,
            threshold,
            block_dates=selector_dates,
            bootstrap=bootstrap,
            seed=seed,
        )
        for arm, rows in selector_rows.items()
    }
    selector_summary["candidate_minus_frozen_capture_interval"] = asdict(
        paired_capture_change_interval(
            selector_rows["frozen"],
            selector_rows["candidate"],
            threshold,
            block_dates=selector_dates,
            bootstrap=bootstrap,
            seed=seed,
        )
    )
    confirmation_summary = {
        arm: arm_summary(
            rows,
            threshold,
            block_dates=confirmation_dates,
            bootstrap=bootstrap,
            seed=seed,
        )
        for arm, rows in confirmation_rows.items()
    }
    paired_confirmation = paired_capture_change_interval(
        confirmation_rows["frozen"],
        confirmation_rows["candidate"],
        threshold,
        block_dates=confirmation_dates,
        bootstrap=bootstrap,
        seed=seed,
    )
    confirmation_summary["candidate_minus_frozen_capture_interval"] = asdict(
        paired_confirmation
    )
    candidate_confirmation_interval = date_block_interval(
        confirmation_rows["candidate"],
        threshold,
        block_dates=confirmation_dates,
        bootstrap=bootstrap,
        seed=seed,
        metric="capture",
    )
    passed, gate_reason = confirmation_gate(
        candidate_confirmation_interval,
        paired_confirmation,
        expected_draws=bootstrap,
        capture_bar=capture_bar,
    )

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    search_path = out_dir / "selector_threshold_search.csv"
    search.to_csv(search_path, index=False)
    report_path = out_dir / "fit_report.json"
    policy_path = out_dir / "locked_research_policy.json"
    report = {
        "schema_version": REPORT_SCHEMA,
        "built_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "protocol": {"path": str(protocol_path), "sha256": sha256(protocol_path)},
        "inputs": {
            name: {"path": str(path), "sha256": sha256(path)}
            for name, path in resolved.items()
        },
        "historical_freshness_proxy": {
            "max_quote_age_minutes": max_age,
            "status": protocol["freshness_contract"]["status"],
            "approved_as_executable": False,
        },
        "strict_funnel": funnel,
        "strict_rows": int(len(pairs)),
        "selected_min_expected_profit_per_unit": threshold,
        "selector": selector_summary,
        "confirmation": confirmation_summary,
        "threshold_search": {
            "path": str(search_path),
            "sha256": sha256(search_path),
            "candidates": int(len(search)),
        },
        "fit_gate_passed": passed,
        "fit_gate_reason": gate_reason,
        "may_holdout_permitted_to_open_once": passed,
        "betting_authorized": False,
        "verdict": "FIT_CONFIRMED_RESEARCH_POLICY" if passed else "NO_POLICY_QUALIFIES",
        "verdict_reason": (
            "The locked internal confirmation gates passed. The threshold may be "
            "frozen for one unchanged May research evaluation; executable forward "
            "shadow evidence is still required."
            if passed
            else "The locked internal confirmation gates did not both pass. May remains sealed."
        ),
    }
    atomic_json(report_path, report)

    if passed:
        research_policy = {
            "schema_version": POLICY_SCHEMA,
            "status": "LOCKED_RESEARCH_ONLY",
            "sportsbook": "draftkings",
            "category": "hits",
            "selection_sides": ["over", "under"],
            "entry_horizon_hours": 4,
            "historical_reference_max_quote_age_minutes": max_age,
            "freshness_status": protocol["freshness_contract"]["status"],
            "min_expected_profit_per_unit": threshold,
            "evidence_rule": {
                "candidate_capture_lower_95_gt": capture_bar,
                "candidate_minus_frozen_capture_lower_95_gt": 0.0,
                "resampling_unit": "official_game_date",
                "bootstrap_draws": bootstrap,
                "round_minimum_bet_count": None,
            },
            "fit_report": {"path": str(report_path), "sha256": sha256(report_path)},
            "protocol": {"path": str(protocol_path), "sha256": sha256(protocol_path)},
            "may_holdout_opened": False,
            "betting_authorized": False,
            "authorization_blockers": [
                "one-shot May holdout not evaluated",
                "historical quote-age proxy does not prove executable availability",
                "prospective forward-shadow replication not completed",
                "immutable market authorization certificate not issued",
            ],
        }
        atomic_json(policy_path, research_policy)
    elif policy_path.exists():
        raise ValueError(
            "a prior locked research policy exists even though the current fit gate failed"
        )

    print("HITS PAYOUT POLICY FIT COMPLETE")
    print(f"  protocol sha256: {sha256(protocol_path)}")
    print(f"  strict rows: {len(pairs):,}  dates: {len(fit_dates)}")
    print(f"  selected min expected profit/unit: {threshold:.8f}")
    print(
        "  confirmation candidate capture 95%: "
        f"[{candidate_confirmation_interval.lower:+.4f}, "
        f"{candidate_confirmation_interval.upper:+.4f}]"
    )
    print(
        "  confirmation candidate-frozen 95%: "
        f"[{paired_confirmation.lower:+.4f}, {paired_confirmation.upper:+.4f}]"
    )
    print(f"  verdict: {report['verdict']} — {gate_reason}")
    print("  betting authorized: NO")
    print(f"  wrote: {report_path}")
    if passed:
        print(f"         {policy_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
