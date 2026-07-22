"""Validate the predeclared one-date HR batted-ball candidate smoke."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd


MODEL_KEY = ["mlb_game_pk", "player_id", "category", "line"]
OUTCOME_KEY = ["mlb_game_pk", "player_id", "category"]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _config_differences(left: Any, right: Any, prefix: str = "") -> list[str]:
    if isinstance(left, dict) and isinstance(right, dict):
        differences: list[str] = []
        for key in sorted(set(left) | set(right)):
            path = f"{prefix}.{key}" if prefix else key
            if key not in left or key not in right:
                differences.append(path)
            else:
                differences.extend(_config_differences(left[key], right[key], path))
        return differences
    return [] if left == right else [prefix]


def _without_candidate_fields(bundle: dict[str, Any]) -> dict[str, Any]:
    row = copy.deepcopy(bundle)
    statcast = row.get("statcast") or {}
    statcast.pop("barrel_rate", None)
    statcast.pop("hard_hit_rate", None)
    distribution = statcast.get("distribution")
    if isinstance(distribution, dict):
        distribution.pop("barrel_rate", None)
        distribution.pop("hard_hit_rate", None)
    matchup = row.get("matchup") or {}
    matchup.pop("pitcher_archetype_similarity", None)
    rich = row.get("rich_features") or {}
    rich.pop("barrel_rate", None)
    rich.pop("hard_hit_rate", None)
    metadata = row.get("metadata") or {}
    metadata.pop("rich_features", None)
    metadata.pop("matchup_archetype_sim", None)
    # Feature vectors and interactions are deterministic downstream consumers
    # of the candidate rates, so they are expected to change.
    row.pop("features", None)
    return row


def _rate_summary(bundles: list[dict[str, Any]]) -> dict[str, dict[str, float | int]]:
    paths = {
        "core_barrel_rate": lambda row: (row.get("statcast") or {}).get("barrel_rate"),
        "core_hard_hit_rate": lambda row: (row.get("statcast") or {}).get("hard_hit_rate"),
        "distribution_barrel_rate": lambda row: (
            ((row.get("statcast") or {}).get("distribution") or {}).get("barrel_rate")
        ),
        "distribution_hard_hit_rate": lambda row: (
            ((row.get("statcast") or {}).get("distribution") or {}).get("hard_hit_rate")
        ),
    }
    result: dict[str, dict[str, float | int]] = {}
    for name, getter in paths.items():
        values = [float(value) for row in bundles if (value := getter(row)) is not None]
        assert values, f"no values for {name}"
        assert all(0.0 <= value <= 1.0 for value in values), name
        result[name] = {
            "rows": len(values),
            "unique": len(set(values)),
            "min": min(values),
            "max": max(values),
        }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()

    root = Path(args.root)
    paths = {
        "frozen": root / "frozen.csv",
        "candidate": root / "candidate.csv",
        "outcomes_frozen": root / "outcomes_frozen.csv",
        "outcomes_candidate": root / "outcomes_candidate.csv",
        "manifest_frozen": root / "frozen.manifest.json",
        "manifest_candidate": root / "candidate.manifest.json",
        "features_frozen": root / "features_frozen" / args.date / "bundles.json",
        "features_candidate": root / "features_candidate" / args.date / "bundles.json",
    }
    missing = [str(path) for path in paths.values() if not path.is_file()]
    assert not missing, f"missing smoke artifacts: {missing}"

    frozen = pd.read_csv(paths["frozen"]).sort_values(MODEL_KEY).reset_index(drop=True)
    candidate = pd.read_csv(paths["candidate"]).sort_values(MODEL_KEY).reset_index(drop=True)
    assert frozen[MODEL_KEY].equals(candidate[MODEL_KEY]), "MODEL_KEY drift"
    assert not frozen.duplicated(MODEL_KEY).any()
    assert frozen[MODEL_KEY].notna().all().all()
    assert frozen["sim_p_over"].between(0, 1).all()
    assert candidate["sim_p_over"].between(0, 1).all()
    probability_drift = (frozen["sim_p_over"] - candidate["sim_p_over"]).abs()
    assert (probability_drift > 1e-12).any(), "candidate is inert"

    outcomes_frozen = pd.read_csv(paths["outcomes_frozen"]).sort_values(OUTCOME_KEY).reset_index(drop=True)
    outcomes_candidate = pd.read_csv(paths["outcomes_candidate"]).sort_values(OUTCOME_KEY).reset_index(drop=True)
    assert outcomes_frozen.equals(outcomes_candidate), "official outcome drift"

    manifest_frozen = _read_json(paths["manifest_frozen"])
    manifest_candidate = _read_json(paths["manifest_candidate"])
    assert manifest_frozen["dates"] == manifest_candidate["dates"] == [args.date]
    assert manifest_frozen["simulation_random_seed"] == manifest_candidate["simulation_random_seed"]
    assert manifest_frozen["pa_distribution_sha256"] == manifest_candidate["pa_distribution_sha256"]
    assert (
        manifest_frozen["source_snapshot"]["source_tree_sha256"]
        == manifest_candidate["source_snapshot"]["source_tree_sha256"]
    )
    differences = _config_differences(
        manifest_frozen["effective_config"], manifest_candidate["effective_config"]
    )
    assert differences == ["feature_factory.derive_batted_ball_rates"], differences
    assert manifest_frozen["effective_config"].get("feature_factory", {}).get(
        "derive_batted_ball_rates", False
    ) is False
    assert manifest_candidate["effective_config"]["feature_factory"][
        "derive_batted_ball_rates"
    ] is True

    bundles_frozen = _read_json(paths["features_frozen"])
    bundles_candidate = _read_json(paths["features_candidate"])
    assert len(bundles_frozen) == len(bundles_candidate) > 0
    identity = lambda row: (
        int(row["hitter"]["player"]["mlb_id"]),
        int(row["hitter"]["game"]["game_pk"]),
    )
    bundles_frozen = sorted(bundles_frozen, key=identity)
    bundles_candidate = sorted(bundles_candidate, key=identity)
    assert [identity(row) for row in bundles_frozen] == [
        identity(row) for row in bundles_candidate
    ], "feature identity drift"
    for left, right in zip(bundles_frozen, bundles_candidate):
        assert _without_candidate_fields(left) == _without_candidate_fields(right), (
            f"unrelated feature drift for {identity(left)}"
        )

    frozen_rates = _rate_summary(bundles_frozen)
    candidate_rates = _rate_summary(bundles_candidate)
    assert frozen_rates["core_barrel_rate"]["unique"] == 1
    assert frozen_rates["core_hard_hit_rate"]["unique"] == 1
    assert candidate_rates["core_barrel_rate"]["unique"] > 1
    assert candidate_rates["core_hard_hit_rate"]["unique"] > 1
    assert candidate_rates["distribution_barrel_rate"]["unique"] > 1

    report = {
        "schema_version": "hr-batted-ball-candidate-smoke-certificate-v1",
        "status": "SMOKE_VALID_RESEARCH_ONLY",
        "betting_authorized": False,
        "may_opened": False,
        "date": args.date,
        "model_rows": len(frozen),
        "outcome_rows": len(outcomes_frozen),
        "feature_rows": len(bundles_frozen),
        "changed_probabilities": int((probability_drift > 1e-12).sum()),
        "mean_abs_probability_drift": float(probability_drift.mean()),
        "max_abs_probability_drift": float(probability_drift.max()),
        "effective_config_differences": differences,
        "source_tree_sha256": manifest_frozen["source_snapshot"]["source_tree_sha256"],
        "pa_distribution_sha256": manifest_frozen["pa_distribution_sha256"],
        "frozen_rates": frozen_rates,
        "candidate_rates": candidate_rates,
        "artifacts": {name: {"path": str(path.resolve()), "sha256": _sha256(path)} for name, path in paths.items()},
        "verdict": "PASS_SMOKE_ONLY_FULL_OPEN_EVALUATION_REQUIRED",
    }
    destination = Path(args.report)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(destination)

    print("HR BATTED-BALL CANDIDATE SMOKE VALID")
    print(f"  model_rows: {len(frozen)}")
    print(f"  feature_rows: {len(bundles_frozen)}")
    print(f"  changed_probabilities: {report['changed_probabilities']}")
    print(f"  mean_abs_probability_drift: {report['mean_abs_probability_drift']:.8f}")
    print(f"  candidate_core_barrel_unique: {candidate_rates['core_barrel_rate']['unique']}")
    print(f"  candidate_core_hard_hit_unique: {candidate_rates['core_hard_hit_rate']['unique']}")
    print(f"  candidate_distribution_barrel_unique: {candidate_rates['distribution_barrel_rate']['unique']}")
    print("  verdict: PASS_SMOKE_ONLY_FULL_OPEN_EVALUATION_REQUIRED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
