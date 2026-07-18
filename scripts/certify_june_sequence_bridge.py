#!/usr/bin/env python3
"""Certify the corrected June original-starter bridge before scoring.

This is an identity/integrity certificate, not an evaluator.  It binds the
corrected official-role bridge, strict market universe, contemporaneous model
arms, and official outcomes.  The required mutations prove that stale roles,
missing model coverage, and May leakage are visible to the certificate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd
from pandas.testing import assert_frame_equal


MODEL_KEY = ["mlb_game_pk", "player_id", "category", "line"]
OUTCOME_KEY = ["mlb_game_pk", "player_id", "category"]
BRIDGE_KEY = ["mlb_game_pk", "player_id"]


def sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _load_json(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def _require_columns(frame: pd.DataFrame, columns: list[str], label: str) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"{label} missing required columns {missing}")


def _require_unique(frame: pd.DataFrame, columns: list[str], label: str) -> None:
    if frame[columns].isna().any().any():
        raise ValueError(f"{label} has null identity fields")
    duplicate = frame.duplicated(columns, keep=False)
    if duplicate.any():
        raise ValueError(
            f"{label} has {int(duplicate.sum())} duplicate identity row(s)"
        )


def _normalise_dates(frame: pd.DataFrame, column: str, label: str) -> pd.Series:
    if column not in frame.columns:
        raise ValueError(f"{label} missing {column}")
    out = pd.to_datetime(frame[column], errors="coerce").dt.strftime("%Y-%m-%d")
    if out.isna().any():
        raise ValueError(f"{label} has invalid {column}")
    return out


def _reject_may(dates: pd.Series | list[str], label: str) -> None:
    values = [str(value) for value in dates]
    leaked = sorted({value for value in values if value.startswith("2026-05")})
    if leaked:
        raise ValueError(f"{label} contains sealed May date(s): {leaked}")


def validate_bridge(bridge: pd.DataFrame) -> dict[str, Any]:
    required = [
        *BRIDGE_KEY,
        "official_game_date",
        "is_starter",
        "official_lineup_slot",
        "starter_replaced_in_slot",
        "official_pa",
        "official_hits",
    ]
    _require_columns(bridge, required, "corrected official bridge")
    _require_unique(bridge, BRIDGE_KEY, "corrected official bridge")
    dates = _normalise_dates(bridge, "official_game_date", "corrected official bridge")
    _reject_may(dates.tolist(), "corrected official bridge")
    if not all(value.startswith("2026-06") for value in dates):
        raise ValueError("corrected official bridge contains a non-June date")

    starters = bridge.is_starter.astype(bool)
    per_game = bridge.assign(_starter=starters).groupby("mlb_game_pk")._starter.sum()
    bad = per_game[per_game != 18]
    if len(bad):
        raise ValueError(
            "corrected official bridge does not have exactly eighteen original "
            f"starters in {len(bad)} game(s): {bad.head(10).to_dict()}"
        )
    starter_slots = pd.to_numeric(
        bridge.loc[starters, "official_lineup_slot"], errors="coerce"
    )
    if starter_slots.isna().any() or not starter_slots.between(1, 9).all():
        raise ValueError("corrected official bridge has an invalid starter lineup slot")
    return {
        "rows": int(len(bridge)),
        "games": int(bridge.mlb_game_pk.nunique()),
        "dates": sorted(dates.unique().tolist()),
        "starters": int(starters.sum()),
        "substitutes": int((~starters).sum()),
        "starters_per_game": 18,
    }


def validate_strict(strict: pd.DataFrame, manifest: dict[str, Any]) -> dict[str, Any]:
    _require_columns(
        strict,
        [*MODEL_KEY, "official_game_date", "is_starter", "official_pa"],
        "corrected strict artifact",
    )
    _require_unique(strict, MODEL_KEY, "corrected strict artifact")
    dates = _normalise_dates(strict, "official_game_date", "corrected strict artifact")
    _reject_may(dates.tolist(), "corrected strict artifact")
    observed = sorted(dates.unique().tolist())
    expected = sorted(str(value) for value in manifest["official_date_universe"])
    if observed != expected:
        raise ValueError("corrected strict artifact date universe disagrees with manifest")
    if set(strict.category.astype(str)) != {"hits"}:
        raise ValueError("corrected strict artifact is not Hits-only")
    if not strict.is_starter.astype(bool).all():
        raise ValueError("corrected strict artifact contains a nonstarter")
    if not (pd.to_numeric(strict.official_pa, errors="coerce") >= 1).all():
        raise ValueError("corrected strict artifact contains a zero-PA row")
    return {"rows": int(len(strict)), "dates": observed}


def validate_coverage(
    strict: pd.DataFrame,
    baseline: pd.DataFrame,
    candidate: pd.DataFrame,
    baseline_outcomes: pd.DataFrame,
    candidate_outcomes: pd.DataFrame,
) -> dict[str, Any]:
    for label, frame in (("baseline", baseline), ("candidate", candidate)):
        _require_columns(frame, [*MODEL_KEY, "game_date", "sim_p_over"], label)
        _require_unique(frame, MODEL_KEY, label)
        _reject_may(_normalise_dates(frame, "game_date", label).tolist(), label)
        probability = pd.to_numeric(frame.sim_p_over, errors="coerce")
        if probability.isna().any() or not probability.between(0.0, 1.0).all():
            raise ValueError(f"{label} has an invalid probability")

    baseline_keys = set(map(tuple, baseline[MODEL_KEY].to_numpy()))
    candidate_keys = set(map(tuple, candidate[MODEL_KEY].to_numpy()))
    if baseline_keys != candidate_keys:
        raise ValueError("contemporaneous baseline/candidate MODEL_KEY sets differ")
    strict_keys = set(map(tuple, strict[MODEL_KEY].to_numpy()))
    missing_baseline = strict_keys - baseline_keys
    missing_candidate = strict_keys - candidate_keys
    if missing_baseline or missing_candidate:
        raise ValueError(
            "corrected strict universe lacks exact model coverage: "
            f"baseline={len(missing_baseline)} candidate={len(missing_candidate)}"
        )

    for label, frame in (
        ("baseline outcomes", baseline_outcomes),
        ("candidate outcomes", candidate_outcomes),
    ):
        _require_columns(frame, [*OUTCOME_KEY, "game_date", "actual_value"], label)
        _require_unique(frame, OUTCOME_KEY, label)
        _reject_may(_normalise_dates(frame, "game_date", label).tolist(), label)
    left = baseline_outcomes.sort_values(OUTCOME_KEY).reset_index(drop=True)
    right = candidate_outcomes.sort_values(OUTCOME_KEY).reset_index(drop=True)
    try:
        assert_frame_equal(left, right, check_exact=True)
    except AssertionError as exc:
        raise ValueError("official outcomes differ between model arms") from exc
    truth_keys = set(map(tuple, left[OUTCOME_KEY].to_numpy()))
    missing_truth = {
        (game, player, category)
        for game, player, category, _line in strict_keys
        if (game, player, category) not in truth_keys
    }
    if missing_truth:
        raise ValueError(
            f"corrected strict universe lacks {len(missing_truth)} official target(s)"
        )
    return {
        "baseline_model_rows": int(len(baseline)),
        "candidate_model_rows": int(len(candidate)),
        "strict_keys": int(len(strict_keys)),
        "missing_baseline": 0,
        "missing_candidate": 0,
        "outcomes_identical": True,
    }


def require_hash(path: str | Path, expected: str, label: str) -> None:
    actual = sha256(path)
    if actual != expected:
        raise ValueError(f"{label} hash mismatch: expected {expected}, got {actual}")


def expect_failure(name: str, action: Callable[[], Any]) -> dict[str, Any]:
    try:
        action()
    except (AssertionError, KeyError, TypeError, ValueError) as exc:
        return {"name": name, "caught": True, "message": str(exc)}
    raise AssertionError(f"mutation did not fail: {name}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--protocol", required=True)
    ap.add_argument("--bridge", required=True)
    ap.add_argument("--bridge-manifest", required=True)
    ap.add_argument("--strict", required=True)
    ap.add_argument("--strict-manifest", required=True)
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--baseline-outcomes", required=True)
    ap.add_argument("--candidate-outcomes", required=True)
    ap.add_argument("--baseline-manifest", required=True)
    ap.add_argument("--candidate-manifest", required=True)
    ap.add_argument("--feature-certificate", required=True)
    ap.add_argument("--superseded-bridge")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    paths = {name: Path(value) for name, value in vars(args).items() if value and name != "out"}
    for name, path in paths.items():
        if not path.exists():
            raise FileNotFoundError(f"{name}: {path}")
        _reject_may([str(path).replace("\\", "/")], f"input path {name}")

    protocol = _load_json(paths["protocol"])
    if protocol.get("status") != "locked_after_integrity_failure_before_any_candidate_metric":
        raise ValueError("correction protocol is not in the required locked state")
    if protocol["trigger"].get("candidate_metrics_read_before_lock") is not False:
        raise ValueError("correction protocol does not prove pre-metric lock")

    bridge_manifest = _load_json(paths["bridge_manifest"])
    strict_manifest = _load_json(paths["strict_manifest"])
    feature_certificate = _load_json(paths["feature_certificate"])
    if feature_certificate.get("status") != "valid" or feature_certificate.get("may_2026_read") is not False:
        raise ValueError("feature certificate is invalid or does not preserve May")

    bridge_hash = sha256(paths["bridge"])
    strict_hash = sha256(paths["strict"])
    if not bridge_hash.startswith(bridge_manifest["artifacts"]["eligibility_sha256"]):
        raise ValueError("corrected bridge hash disagrees with its manifest")
    if not strict_hash.startswith(strict_manifest["hashes"]["artifact"]):
        raise ValueError("corrected strict artifact hash disagrees with its manifest")

    bridge = pd.read_csv(paths["bridge"])
    strict = pd.read_csv(paths["strict"])
    baseline = pd.read_csv(paths["baseline"])
    candidate = pd.read_csv(paths["candidate"])
    baseline_outcomes = pd.read_csv(paths["baseline_outcomes"])
    candidate_outcomes = pd.read_csv(paths["candidate_outcomes"])

    bridge_summary = validate_bridge(bridge)
    strict_summary = validate_strict(strict, strict_manifest)
    coverage_summary = validate_coverage(
        strict, baseline, candidate, baseline_outcomes, candidate_outcomes
    )
    if bridge_summary["rows"] != int(bridge_manifest["hitter_rows"]):
        raise ValueError("corrected bridge row count disagrees with manifest")
    if bridge_summary["games"] != int(bridge_manifest["games"]):
        raise ValueError("corrected bridge game count disagrees with manifest")
    if strict_summary["rows"] != int(strict_manifest["funnel"]["artifact_rows"]):
        raise ValueError("corrected strict row count disagrees with manifest")

    mutated_bridge = bridge.copy()
    substitute_index = mutated_bridge.index[~mutated_bridge.is_starter.astype(bool)][0]
    mutated_bridge.loc[substitute_index, "is_starter"] = True
    mutated_baseline = baseline.drop(index=baseline.index[
        baseline[MODEL_KEY].apply(tuple, axis=1).isin(
            {tuple(strict.iloc[0][MODEL_KEY].tolist())}
        )
    ]).copy()
    mutated_strict = strict.copy()
    mutated_strict.loc[0, "official_game_date"] = "2026-05-01"
    stale_hash = protocol["bound_inputs"]["stale_official_bridge"]["sha256"]
    mutations = [
        expect_failure(
            "sequence_1_substitute_relabelled_as_starter",
            lambda: validate_bridge(mutated_bridge),
        ),
        expect_failure(
            "one_corrected_strict_key_removed_from_baseline_arm",
            lambda: validate_coverage(
                strict,
                mutated_baseline,
                candidate,
                baseline_outcomes,
                candidate_outcomes,
            ),
        ),
        expect_failure(
            "stale_bridge_hash_substituted",
            lambda: require_hash(paths["bridge"], stale_hash, "corrected bridge"),
        ),
        expect_failure(
            "sealed_may_date_introduced",
            lambda: validate_strict(mutated_strict, strict_manifest),
        ),
    ]

    inputs = {
        name: {"path": str(path), "sha256": sha256(path)} for name, path in paths.items()
    }
    payload = {
        "schema_version": "june-sequence-bridge-correction-certificate-v1",
        "built_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "VALID_IDENTITY_CORRECTION_RESEARCH_ONLY",
        "betting_authorized": False,
        "may_2026_read": False,
        "market": "hits",
        "scope": "June 2026 open replication",
        "bridge": bridge_summary,
        "strict_universe": strict_summary,
        "coverage": coverage_summary,
        "mutations": mutations,
        "superseded_intermediate": (
            inputs.get("superseded_bridge")
            if "superseded_bridge" in inputs
            else None
        ),
        "inputs": inputs,
        "interpretation": (
            "The corrected original-starter identity bridge and strict market "
            "universe are valid for research scoring. This certificate contains "
            "no candidate metric and cannot authorize betting."
        ),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("JUNE SEQUENCE BRIDGE CERTIFIED")
    print(f"  bridge rows: {bridge_summary['rows']:,} across {bridge_summary['games']} games")
    print(f"  strict keys: {strict_summary['rows']:,}")
    print(f"  mutations caught: {sum(item['caught'] for item in mutations)}/{len(mutations)}")
    print(f"  wrote: {out}")
    print(f"  sha256: {sha256(out)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
