"""Validate the full 56-date HR batted-ball candidate reconstruction.

This is an artifact/provenance validator only.  It never scores outcomes,
opens May, advances a candidate, or authorizes betting.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from validate_hr_batted_ball_candidate_smoke import (
    MODEL_KEY,
    OUTCOME_KEY,
    _config_differences,
    _read_json,
    _sha256,
    _without_candidate_fields,
)


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_CONFIG_DIFFERENCE = ["feature_factory.derive_batted_ball_rates"]


def _resolve(path: str | Path) -> Path:
    value = Path(path)
    return value if value.is_absolute() else ROOT / value


def _verify_file(path: Path, expected_sha256: str, label: str) -> None:
    if not path.is_file():
        raise AssertionError(f"missing {label}: {path}")
    actual = _sha256(path)
    if actual != expected_sha256:
        raise AssertionError(
            f"{label} hash mismatch: expected {expected_sha256}, got {actual}"
        )


def _identity(row: dict[str, Any]) -> tuple[int, int]:
    return (
        int(row["hitter"]["player"]["mlb_id"]),
        int(row["hitter"]["game"]["game_pk"]),
    )


RATE_GETTERS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "core_barrel_rate": lambda row: (row.get("statcast") or {}).get("barrel_rate"),
    "core_hard_hit_rate": lambda row: (row.get("statcast") or {}).get("hard_hit_rate"),
    "distribution_barrel_rate": lambda row: (
        ((row.get("statcast") or {}).get("distribution") or {}).get("barrel_rate")
    ),
    "distribution_hard_hit_rate": lambda row: (
        ((row.get("statcast") or {}).get("distribution") or {}).get("hard_hit_rate")
    ),
}


def _empty_rate_accumulator() -> dict[str, dict[str, Any]]:
    return {
        name: {"rows": 0, "unique": set(), "min": None, "max": None}
        for name in RATE_GETTERS
    }


def _accumulate_rates(
    accumulator: dict[str, dict[str, Any]], bundles: list[dict[str, Any]]
) -> None:
    for name, getter in RATE_GETTERS.items():
        for row in bundles:
            value = getter(row)
            if value is None:
                continue
            number = float(value)
            if not 0.0 <= number <= 1.0:
                raise AssertionError(f"invalid {name}: {number}")
            item = accumulator[name]
            item["rows"] += 1
            item["unique"].add(number)
            item["min"] = number if item["min"] is None else min(item["min"], number)
            item["max"] = number if item["max"] is None else max(item["max"], number)


def _rate_summary(accumulator: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for name, item in accumulator.items():
        if not item["rows"]:
            raise AssertionError(f"no values for {name}")
        result[name] = {
            "rows": item["rows"],
            "unique": len(item["unique"]),
            "min": item["min"],
            "max": item["max"],
        }
    return result


def _verify_top_level_artifacts(
    root: Path,
    source_manifest_path: Path,
    source_manifest: dict[str, Any],
    frozen_manifest: dict[str, Any],
    candidate_manifest: dict[str, Any],
) -> None:
    source_manifest_sha = _sha256(source_manifest_path)
    for label, manifest in (("frozen", frozen_manifest), ("candidate", candidate_manifest)):
        if manifest.get("schema_version") != 2:
            raise AssertionError(f"{label} provenance is not schema v2")
        if manifest["date_source"]["sha256"] != source_manifest_sha:
            raise AssertionError(f"{label} source-manifest hash drift")
        for key, filename, hash_key in (
            ("probability", f"{label}.csv", "probability_artifact_sha256"),
            ("outcome", f"outcomes_{label}.csv", "outcome_artifact_sha256"),
        ):
            _verify_file(root / filename, manifest[hash_key], f"{label} {key} artifact")
        snapshot = manifest["source_snapshot"]
        _verify_file(_resolve(snapshot["path"]), snapshot["sha256"], f"{label} source snapshot")
        _verify_file(
            _resolve(manifest["pa_distribution_path"]),
            manifest["pa_distribution_sha256"],
            f"{label} PA distribution",
        )
        if manifest.get("pairs_path"):
            _verify_file(
                _resolve(manifest["pairs_path"]),
                manifest["pairs_sha256"],
                f"{label} market source",
            )
        else:
            _verify_file(
                source_manifest_path.with_name("hr_over_reconstruct_source.csv"),
                source_manifest["hashes"]["artifact"],
                f"{label} strict market source",
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument(
        "--source-manifest",
        default="data/analysis/hr_over_contract_v1/hr_over_reconstruct_source_manifest.json",
    )
    parser.add_argument(
        "--protocol",
        default="data/analysis/hr_over_contract_v1/batted_ball_candidate_v1/protocol.json",
    )
    parser.add_argument("--report", required=True)
    args = parser.parse_args()

    root = _resolve(args.root)
    source_manifest_path = _resolve(args.source_manifest)
    protocol_path = _resolve(args.protocol)
    source_manifest = _read_json(source_manifest_path)
    protocol = _read_json(protocol_path)
    if protocol.get("status") != "PREDECLARED_CANDIDATE_RESEARCH_ONLY":
        raise AssertionError("candidate protocol is not locked research-only")
    if protocol.get("betting_authorized") or protocol.get("may_opened"):
        raise AssertionError("candidate protocol opened May or authorized betting")

    dates = list(source_manifest["official_date_universe"])
    if len(dates) != 56 or len(set(dates)) != 56:
        raise AssertionError("source universe is not exactly 56 unique dates")
    if any(date.startswith("2026-05") for date in dates):
        raise AssertionError("May entered the source universe")

    paths = {
        "frozen": root / "frozen.csv",
        "candidate": root / "candidate.csv",
        "outcomes_frozen": root / "outcomes_frozen.csv",
        "outcomes_candidate": root / "outcomes_candidate.csv",
        "manifest_frozen": root / "frozen.manifest.json",
        "manifest_candidate": root / "candidate.manifest.json",
    }
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise AssertionError(f"missing full artifacts: {missing}")

    frozen_manifest = _read_json(paths["manifest_frozen"])
    candidate_manifest = _read_json(paths["manifest_candidate"])
    if frozen_manifest["dates"] != dates or candidate_manifest["dates"] != dates:
        raise AssertionError("provenance date universe drift")
    if frozen_manifest["simulation_random_seed"] != candidate_manifest["simulation_random_seed"]:
        raise AssertionError("simulation seed drift")
    if frozen_manifest["pa_distribution_sha256"] != candidate_manifest["pa_distribution_sha256"]:
        raise AssertionError("PA artifact drift")
    if frozen_manifest["source_sha256"] != candidate_manifest["source_sha256"]:
        raise AssertionError("source-file hash drift")
    if (
        frozen_manifest["source_snapshot"]["source_tree_sha256"]
        != candidate_manifest["source_snapshot"]["source_tree_sha256"]
    ):
        raise AssertionError("source-tree drift")
    _verify_top_level_artifacts(
        root,
        source_manifest_path,
        source_manifest,
        frozen_manifest,
        candidate_manifest,
    )

    differences = _config_differences(
        frozen_manifest["effective_config"], candidate_manifest["effective_config"]
    )
    if differences != EXPECTED_CONFIG_DIFFERENCE:
        raise AssertionError(f"unexpected effective-config differences: {differences}")
    if frozen_manifest["effective_config"].get("feature_factory", {}).get(
        "derive_batted_ball_rates", False
    ) is not False:
        raise AssertionError("frozen candidate flag is enabled")
    if candidate_manifest["effective_config"].get("feature_factory", {}).get(
        "derive_batted_ball_rates"
    ) is not True:
        raise AssertionError("candidate flag is not enabled")

    frozen = pd.read_csv(paths["frozen"]).sort_values(MODEL_KEY).reset_index(drop=True)
    candidate = pd.read_csv(paths["candidate"]).sort_values(MODEL_KEY).reset_index(drop=True)
    if not frozen[MODEL_KEY].equals(candidate[MODEL_KEY]):
        raise AssertionError("MODEL_KEY drift")
    if frozen.duplicated(MODEL_KEY).any() or frozen[MODEL_KEY].isna().any().any():
        raise AssertionError("MODEL_KEY is duplicated or null")
    if not frozen.sim_p_over.between(0, 1).all() or not candidate.sim_p_over.between(0, 1).all():
        raise AssertionError("invalid model probability")
    probability_drift = (frozen.sim_p_over - candidate.sim_p_over).abs()
    if not (probability_drift > 1e-12).any():
        raise AssertionError("candidate is inert")

    outcomes_frozen = (
        pd.read_csv(paths["outcomes_frozen"]).sort_values(OUTCOME_KEY).reset_index(drop=True)
    )
    outcomes_candidate = (
        pd.read_csv(paths["outcomes_candidate"]).sort_values(OUTCOME_KEY).reset_index(drop=True)
    )
    if not outcomes_frozen.equals(outcomes_candidate):
        raise AssertionError("official outcome drift")

    frozen_snapshots = frozen_manifest["feature_snapshots"]
    candidate_snapshots = candidate_manifest["feature_snapshots"]
    if list(frozen_snapshots) != dates or list(candidate_snapshots) != dates:
        raise AssertionError("feature-snapshot date drift")

    rate_accumulators = {
        "frozen": _empty_rate_accumulator(),
        "candidate": _empty_rate_accumulator(),
    }
    feature_rows = 0
    for date in dates:
        arm_bundles: dict[str, list[dict[str, Any]]] = {}
        for label, top_manifest, snapshots in (
            ("frozen", frozen_manifest, frozen_snapshots),
            ("candidate", candidate_manifest, candidate_snapshots),
        ):
            record = snapshots[date]
            bundle_path = _resolve(record["bundle_path"])
            feature_manifest_path = _resolve(record["manifest_path"])
            _verify_file(bundle_path, record["bundle_sha256"], f"{label}/{date} bundles")
            _verify_file(
                feature_manifest_path,
                record["manifest_sha256"],
                f"{label}/{date} feature manifest",
            )
            feature_manifest = _read_json(feature_manifest_path)
            bundles = _read_json(bundle_path)
            if not isinstance(bundles, list) or not bundles:
                raise AssertionError(f"{label}/{date} bundles are empty or malformed")
            if len(bundles) != record["bundle_count"] != 0:
                raise AssertionError(f"{label}/{date} bundle count drift")
            if feature_manifest["bundle_count"] != len(bundles):
                raise AssertionError(f"{label}/{date} feature-manifest count drift")
            if feature_manifest["game_date"] != date:
                raise AssertionError(f"{label}/{date} feature-manifest date drift")
            artifact = feature_manifest["artifacts"]["json"]
            if artifact["sha256"] != record["bundle_sha256"]:
                raise AssertionError(f"{label}/{date} nested bundle hash drift")
            provenance = feature_manifest["provenance"]
            if provenance["config_sha256"] != top_manifest["config_sha256"]:
                raise AssertionError(f"{label}/{date} feature config drift")
            if provenance["source_tree_sha256"] != top_manifest["source_snapshot"][
                "source_tree_sha256"
            ]:
                raise AssertionError(f"{label}/{date} feature source-tree drift")
            if provenance["game_date"] != date:
                raise AssertionError(f"{label}/{date} feature provenance date drift")
            arm_bundles[label] = sorted(bundles, key=_identity)
            _accumulate_rates(rate_accumulators[label], bundles)

        frozen_bundles = arm_bundles["frozen"]
        candidate_bundles = arm_bundles["candidate"]
        frozen_identity = [_identity(row) for row in frozen_bundles]
        candidate_identity = [_identity(row) for row in candidate_bundles]
        if frozen_identity != candidate_identity or len(frozen_identity) != len(set(frozen_identity)):
            raise AssertionError(f"{date} feature identity drift or duplication")
        for left, right in zip(frozen_bundles, candidate_bundles):
            if _without_candidate_fields(left) != _without_candidate_fields(right):
                raise AssertionError(f"unrelated feature drift for {date}/{_identity(left)}")
        feature_rows += len(frozen_bundles)

    frozen_rates = _rate_summary(rate_accumulators["frozen"])
    candidate_rates = _rate_summary(rate_accumulators["candidate"])
    if frozen_rates["core_barrel_rate"]["unique"] != 1:
        raise AssertionError("frozen core barrel rate is not the legacy constant")
    if frozen_rates["core_hard_hit_rate"]["unique"] != 1:
        raise AssertionError("frozen core hard-hit rate is not the legacy constant")
    for name in RATE_GETTERS:
        if candidate_rates[name]["unique"] <= 1:
            raise AssertionError(f"candidate {name} is degenerate")

    report = {
        "schema_version": "hr-batted-ball-candidate-full-certificate-v1",
        "status": "FULL_ARTIFACTS_VALID_RESEARCH_ONLY",
        "betting_authorized": False,
        "may_opened": False,
        "dates": dates,
        "model_rows": len(frozen),
        "outcome_rows": len(outcomes_frozen),
        "feature_rows": feature_rows,
        "changed_probabilities": int((probability_drift > 1e-12).sum()),
        "mean_abs_probability_drift": float(probability_drift.mean()),
        "max_abs_probability_drift": float(probability_drift.max()),
        "effective_config_differences": differences,
        "simulation_random_seed": frozen_manifest["simulation_random_seed"],
        "source_tree_sha256": frozen_manifest["source_snapshot"]["source_tree_sha256"],
        "pa_distribution_sha256": frozen_manifest["pa_distribution_sha256"],
        "frozen_rates": frozen_rates,
        "candidate_rates": candidate_rates,
        "artifacts": {
            name: {"path": str(path.resolve()), "sha256": _sha256(path)}
            for name, path in paths.items()
        },
        "source_manifest": {
            "path": str(source_manifest_path.resolve()),
            "sha256": _sha256(source_manifest_path),
        },
        "candidate_protocol": {
            "path": str(protocol_path.resolve()),
            "sha256": _sha256(protocol_path),
        },
        "verdict": "PASS_FULL_ARTIFACT_VALIDATION_ECONOMIC_GATE_REQUIRED",
    }
    destination = _resolve(args.report)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(destination)

    print("HR BATTED-BALL CANDIDATE FULL ARTIFACTS VALID")
    print(f"  dates: {len(dates)}")
    print(f"  model_rows: {len(frozen)}")
    print(f"  feature_rows: {feature_rows}")
    print(f"  changed_probabilities: {report['changed_probabilities']}")
    print(f"  mean_abs_probability_drift: {report['mean_abs_probability_drift']:.8f}")
    print(f"  candidate_core_barrel_unique: {candidate_rates['core_barrel_rate']['unique']}")
    print(f"  candidate_core_hard_hit_unique: {candidate_rates['core_hard_hit_rate']['unique']}")
    print("  betting authorized: NO; May opened: NO")
    print("  verdict: PASS_FULL_ARTIFACT_VALIDATION_ECONOMIC_GATE_REQUIRED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
