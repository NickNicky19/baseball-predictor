#!/usr/bin/env python3
"""Fail-closed validation for the full a3.2 HR level-mapping reconstruction.

This validates artifacts and identities only. It does not fit a mapping, score
confirmation outcomes, open May 2026, promote a model, or authorize betting.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.hr_pre2026_batted_ball_mapping import (  # noqa: E402
    validate_historical_input,
)
from src.evaluation.hr_pre2026_level_mapping import load_protocol, sha256  # noqa: E402


MODEL_KEY = ["mlb_game_pk", "player_id", "category", "line"]
OUTCOME_KEY = ["mlb_game_pk", "player_id", "category"]
IDENTITY = ["game_date", "mlb_game_pk", "player_id"]
STATUS = "VALID_FULL_A3_2_HR_LEVEL_MAPPING_RESEARCH_ONLY"


def _resolve(value: str | Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _require_file(path: Path, expected: str, label: str) -> None:
    if not path.is_file() or sha256(path) != expected:
        raise ValueError(f"{label} is missing or its hash differs: {path}")


def _require_columns(frame: pd.DataFrame, columns: list[str], label: str) -> None:
    missing = sorted(set(columns) - set(frame.columns))
    if missing:
        raise ValueError(f"{label} missing columns: {missing}")


def _identity_set(frame: pd.DataFrame) -> set[tuple[str, int, int]]:
    return set(
        zip(
            frame["game_date"].astype(str),
            pd.to_numeric(frame["mlb_game_pk"]).astype(int),
            pd.to_numeric(frame["player_id"]).astype(int),
        )
    )


def validate_frames(
    model: pd.DataFrame,
    outcomes: pd.DataFrame,
    training: pd.DataFrame,
    dates: list[str],
) -> dict[str, int]:
    """Require exact full-universe identity without a permissive intersection."""
    _require_columns(model, MODEL_KEY + ["game_date", "sim_p_over"], "model")
    _require_columns(outcomes, OUTCOME_KEY + ["game_date", "actual_value"], "outcomes")
    _require_columns(training, ["game_date", "game_pk", "player_id", "builder_schema"], "training")
    if len(dates) != 24 or dates != sorted(dates) or len(dates) != len(set(dates)):
        raise ValueError("full reconstruction requires exactly 24 sorted unique dates")
    if any(date.startswith("2026-05") for date in dates):
        raise ValueError("May 2026 is forbidden")
    if set(model.game_date.astype(str)) != set(dates):
        raise ValueError("model date universe differs from the locked 24 dates")
    if set(outcomes.game_date.astype(str)) != set(dates):
        raise ValueError("official date universe differs from the locked 24 dates")
    if model[MODEL_KEY].isna().any().any() or model.duplicated(MODEL_KEY).any():
        raise ValueError("MODEL_KEY must be unique and non-null")
    if outcomes[OUTCOME_KEY].isna().any().any() or outcomes.duplicated(OUTCOME_KEY).any():
        raise ValueError("official key must be unique and non-null")
    probability = pd.to_numeric(model.sim_p_over, errors="coerce")
    if probability.isna().any() or not probability.between(0.0, 1.0).all():
        raise ValueError("sim_p_over must be finite and inside [0,1]")
    actual = pd.to_numeric(outcomes.actual_value, errors="coerce")
    if actual.isna().any() or (actual < 0).any():
        raise ValueError("official actuals must be non-null and non-negative")

    hr_model = model[model.category.eq("home_runs")].copy()
    hr_outcomes = outcomes[outcomes.category.eq("home_runs")].copy()
    if hr_model.empty or not pd.to_numeric(hr_model.line).eq(0.5).all():
        raise ValueError("every HR model row must be exactly line 0.5")
    selected_training = training[training.game_date.astype(str).isin(dates)].copy()
    selected_training = selected_training.rename(columns={"game_pk": "mlb_game_pk"})
    if set(selected_training.builder_schema.dropna().astype(str)) != {"a3.2"}:
        raise ValueError("selected feature rows must be builder_schema a3.2")
    for frame, label in (
        (hr_model, "HR model"),
        (hr_outcomes, "HR outcomes"),
        (selected_training, "training"),
    ):
        if frame[IDENTITY].isna().any().any() or frame.duplicated(IDENTITY).any():
            raise ValueError(f"{label} identity must be unique and non-null")
    model_keys = _identity_set(hr_model)
    outcome_keys = _identity_set(hr_outcomes)
    training_keys = _identity_set(selected_training)
    if model_keys != outcome_keys or model_keys != training_keys:
        raise ValueError("HR model, official, and a3.2 feature identities differ")
    return {
        "model_rows": int(len(model)),
        "outcome_rows": int(len(outcomes)),
        "hr_rows": int(len(hr_model)),
        "training_rows": int(len(selected_training)),
    }


def _validate_source_snapshot(path: Path, manifest: dict[str, Any]) -> None:
    snapshot = manifest.get("source_snapshot") or {}
    _require_file(path, str(snapshot.get("sha256", "")), "source snapshot")
    recorded = manifest.get("source_sha256") or {}
    digest = hashlib.sha256()
    source_count = 0
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        source_names = sorted(name for name in names if name.startswith("source/"))
        source_count = len(source_names)
        for name in source_names:
            relative = name.removeprefix("source/")
            payload = archive.read(name)
            digest.update(relative.encode("utf-8") + b"\0" + payload + b"\0")
        for relative, expected in recorded.items():
            name = f"source/{relative}"
            if name not in names:
                raise ValueError(f"source snapshot lacks recorded source: {relative}")
            actual = hashlib.sha256(archive.read(name)).hexdigest()
            if actual != expected:
                raise ValueError(f"source snapshot content hash differs: {relative}")
        effective = json.loads(archive.read("effective_config.json"))
    if source_count != int(snapshot.get("files", -1)):
        raise ValueError("source snapshot file count differs")
    if digest.hexdigest() != snapshot.get("source_tree_sha256"):
        raise ValueError("source snapshot tree hash differs")
    canonical = json.dumps(effective, sort_keys=True, separators=(",", ":")).encode()
    if hashlib.sha256(canonical).hexdigest() != manifest.get("config_sha256"):
        raise ValueError("effective config hash differs")
    if effective != manifest.get("effective_config"):
        raise ValueError("effective config differs from its source snapshot")


def validate_artifacts(args: argparse.Namespace) -> dict[str, Any]:
    root = _resolve(args.root)
    protocol_path = _resolve(args.protocol)
    dates_path = _resolve(args.dates_file)
    protocol = load_protocol(protocol_path, verify_files=True)
    date_payload = _read_json(dates_path)
    dates = list(protocol["chronology"]["calibration_dates"]) + list(
        protocol["chronology"]["confirmation_dates_open_once"]
    )
    if date_payload.get("dates") != dates:
        raise ValueError("date artifact differs from the protocol chronology")

    paths = {
        "model": root / "raw_model.csv",
        "outcomes": root / "official_outcomes.csv",
        "manifest": root / "raw_model.manifest.json",
    }
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise ValueError(f"full reconstruction is incomplete: {missing}")
    manifest = _read_json(paths["manifest"])
    if manifest.get("schema_version") != 2 or manifest.get("dates") != dates:
        raise ValueError("provenance must be schema v2 and exactly the locked 24 dates")
    if manifest.get("simulation_random_seed") != 17:
        raise ValueError("simulation seed differs from the protocol")
    if manifest.get("require_statcast_profiles") is not True or manifest.get("fail_on_flags") is not True:
        raise ValueError("required reconstruction flags are absent")
    if manifest.get("total_bases_candidate") is not False:
        raise ValueError("unrelated total-bases candidate entered the run")
    lock = manifest.get("hr_level_mapping_protocol") or {}
    if lock.get("protocol_sha256") != sha256(protocol_path) or lock.get("run_kind") != "full":
        raise ValueError("full provenance is not bound to this protocol")
    if lock.get("betting_authorized") is not False or lock.get("may_2026_opened") is not False:
        raise ValueError("full provenance opened May or authorized betting")
    source = manifest.get("date_source") or {}
    if source.get("sha256") != sha256(dates_path) or source.get("smoke_date") is not None:
        raise ValueError("date-source provenance differs from the locked full run")
    if source.get("kind") != "dates_file":
        raise ValueError("full reconstruction did not use the locked date file")
    if _resolve(manifest.get("probability_artifact", "")) != paths["model"].resolve():
        raise ValueError("probability artifact path was substituted")
    if _resolve(manifest.get("outcome_artifact", "")) != paths["outcomes"].resolve():
        raise ValueError("outcome artifact path was substituted")
    _require_file(paths["model"], manifest.get("probability_artifact_sha256", ""), "model")
    _require_file(paths["outcomes"], manifest.get("outcome_artifact_sha256", ""), "outcomes")
    pa_path = _resolve(manifest.get("pa_distribution_path", ""))
    if manifest.get("pa_distribution_sha256") != protocol["inputs"]["pa_distribution"]["sha256"]:
        raise ValueError("PA artifact differs from the protocol")
    _require_file(pa_path, manifest["pa_distribution_sha256"], "PA artifact")
    config_path = _resolve(manifest.get("config_argument", ""))
    if config_path != _resolve(protocol["inputs"]["preserved_model_config"]["path"]):
        raise ValueError("preserved config path differs from the protocol")
    _require_file(config_path, protocol["inputs"]["preserved_model_config"]["sha256"], "preserved config")
    snapshot_path = _resolve((manifest.get("source_snapshot") or {}).get("path", ""))
    if snapshot_path.parent != root.resolve():
        raise ValueError("source snapshot was substituted outside the release root")
    _validate_source_snapshot(snapshot_path, manifest)

    training_path = _resolve(protocol["inputs"]["point_in_time_feature_source"]["path"])
    training = pd.read_csv(training_path)
    validate_historical_input(training, expected_builder_schema="a3.2")
    model = pd.read_csv(paths["model"])
    outcomes = pd.read_csv(paths["outcomes"])
    counts = validate_frames(model, outcomes, training, dates)

    feature_records = manifest.get("feature_snapshots") or {}
    if list(feature_records) != dates:
        raise ValueError("feature snapshot dates differ from the locked universe")
    selected_training = training[training.game_date.astype(str).isin(dates)].rename(
        columns={"game_pk": "mlb_game_pk"}
    )
    advanced_by_date: dict[str, int] = {}
    bundle_rows = 0
    for date in dates:
        record = feature_records[date]
        expected_dir = (root / "features" / date).resolve()
        bundle_path = _resolve(record.get("bundle_path", ""))
        feature_manifest_path = _resolve(record.get("manifest_path", ""))
        if bundle_path.parent != expected_dir or feature_manifest_path.parent != expected_dir:
            raise ValueError(f"{date} feature artifact was substituted")
        _require_file(bundle_path, record.get("bundle_sha256", ""), f"{date} bundles")
        _require_file(
            feature_manifest_path,
            record.get("manifest_sha256", ""),
            f"{date} feature manifest",
        )
        feature_manifest = _read_json(feature_manifest_path)
        bundles = json.loads(bundle_path.read_text(encoding="utf-8"))
        if not isinstance(bundles, list) or not bundles:
            raise ValueError(f"{date} bundles are empty or malformed")
        if feature_manifest.get("schema_version") != "feature-bundle-manifest-v1":
            raise ValueError(f"{date} feature manifest schema differs")
        if feature_manifest.get("game_date") != date:
            raise ValueError(f"{date} feature manifest date differs")
        if len(bundles) != record.get("bundle_count") or len(bundles) != feature_manifest.get("bundle_count"):
            raise ValueError(f"{date} feature bundle count differs")
        nested = ((feature_manifest.get("artifacts") or {}).get("json") or {})
        if nested.get("sha256") != record.get("bundle_sha256"):
            raise ValueError(f"{date} nested feature hash differs")
        provenance = feature_manifest.get("provenance") or {}
        if provenance.get("config_sha256") != manifest.get("config_sha256"):
            raise ValueError(f"{date} feature config hash differs")
        if provenance.get("source_tree_sha256") != manifest["source_snapshot"]["source_tree_sha256"]:
            raise ValueError(f"{date} feature source-tree hash differs")
        keys: list[tuple[str, int, int]] = []
        advanced = 0
        for bundle in bundles:
            hitter = bundle.get("hitter") or {}
            player = hitter.get("player") or {}
            game = hitter.get("game") or {}
            key = (str(game.get("game_date")), int(game.get("game_pk")), int(player.get("mlb_id")))
            if key[0] != date:
                raise ValueError(f"{date} bundle contains another date")
            keys.append(key)
            statcast = bundle.get("statcast") or {}
            if int(statcast.get("sample_pa") or 0) > 0 and statcast.get("xwoba") is not None:
                advanced += 1
        if len(keys) != len(set(keys)):
            raise ValueError(f"{date} feature identities are duplicated")
        training_date = selected_training[selected_training.game_date.astype(str).eq(date)]
        if set(keys) != _identity_set(training_date):
            raise ValueError(f"{date} feature identities differ from the a3.2 source")
        if advanced == 0:
            raise ValueError(f"{date} has no advanced Statcast profile")
        advanced_by_date[date] = advanced
        bundle_rows += len(bundles)

    return {
        "schema_version": "hr-pre2026-level-mapping-full-validation-v1",
        "status": STATUS,
        "betting_authorized": False,
        "may_2026_opened": False,
        "performance_inspected": False,
        "dates": dates,
        **counts,
        "feature_rows": bundle_rows,
        "advanced_statcast_by_date": advanced_by_date,
        "protocol": {"path": str(protocol_path), "sha256": sha256(protocol_path)},
        "artifacts": {
            name: {"path": str(path.resolve()), "sha256": sha256(path)}
            for name, path in paths.items()
        },
        "source_snapshot": {
            "path": str(snapshot_path),
            "sha256": sha256(snapshot_path),
            "source_tree_sha256": manifest["source_snapshot"]["source_tree_sha256"],
        },
        "pa_distribution_sha256": manifest["pa_distribution_sha256"],
        "config_sha256": manifest["config_sha256"],
        "verdict": "PASS_FULL_ARTIFACT_VALIDATION_CALIBRATION_LOCK_REQUIRED",
    }


def self_test() -> int:
    dates = [f"2025-01-{day:02d}" for day in range(1, 25)]
    model = pd.DataFrame(
        [[index, 10 + index, date, "home_runs", 0.5, 0.2] for index, date in enumerate(dates, 1)],
        columns=["mlb_game_pk", "player_id", "game_date", "category", "line", "sim_p_over"],
    )
    outcomes = model[["mlb_game_pk", "player_id", "game_date", "category"]].copy()
    outcomes["actual_value"] = 0
    training = model[["game_date", "mlb_game_pk", "player_id"]].rename(
        columns={"mlb_game_pk": "game_pk"}
    )
    training["builder_schema"] = "a3.2"
    validate_frames(model, outcomes, training, dates)
    print("[OK] exact 24-date identity passes")
    mutations: list[tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, list[str], str]] = []
    mutations.append((pd.concat([model, model.iloc[[0]]]), outcomes, training, dates, "duplicate model key"))
    bad = model.copy(); bad.loc[0, "sim_p_over"] = 1.1
    mutations.append((bad, outcomes, training, dates, "probability outside [0,1]"))
    bad = model.copy(); bad.loc[0, "line"] = 1.5
    mutations.append((bad, outcomes, training, dates, "wrong HR line"))
    bad_out = outcomes.copy(); bad_out.loc[0, "player_id"] = 999
    mutations.append((model, bad_out, training, dates, "official identity drift"))
    bad_training = training.copy(); bad_training.loc[0, "player_id"] = 999
    mutations.append((model, outcomes, bad_training, dates, "feature identity drift"))
    bad_training = training.copy(); bad_training.loc[0, "builder_schema"] = "a3.1"
    mutations.append((model, outcomes, bad_training, dates, "retired builder schema"))
    mutations.append((model.iloc[:-1], outcomes, training, dates, "partial date universe"))
    mutations.append((model, outcomes, training, dates[:-1], "23-date declaration"))
    for candidate, official, features, declared, label in mutations:
        try:
            validate_frames(candidate, official, features, declared)
        except ValueError:
            print(f"[OK] MUTATION {label} fails")
        else:
            raise AssertionError(f"mutation unexpectedly passed: {label}")
    print("9/9")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--root")
    parser.add_argument("--protocol")
    parser.add_argument("--dates-file")
    parser.add_argument("--report")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    if not all((args.root, args.protocol, args.dates_file, args.report)):
        parser.error("production validation requires root, protocol, dates-file, and report")
    destination = _resolve(args.report)
    if destination.exists():
        raise FileExistsError(f"validation report already exists: {destination}")
    result = validate_artifacts(args)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(destination)
    print("HR A3.2 LEVEL-MAPPING FULL ARTIFACTS VALID")
    print(f"  dates: {len(result['dates'])}")
    print(f"  HR rows: {result['hr_rows']}")
    print(f"  feature rows: {result['feature_rows']}")
    print("  performance inspected: NO")
    print("  betting authorized: NO; May 2026 opened: NO")
    print(f"  report: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
