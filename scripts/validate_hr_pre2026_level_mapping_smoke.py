#!/usr/bin/env python3
"""Validate one protocol-bound HR level-mapping reconstruction smoke."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.hr_pre2026_level_mapping import load_protocol, sha256  # noqa: E402


MODEL_KEY = ["mlb_game_pk", "player_id", "category", "line"]
OFFICIAL_KEY = ["mlb_game_pk", "player_id", "category"]
IDENTITY_KEY = ["mlb_game_pk", "player_id"]


def _require_columns(frame: pd.DataFrame, columns: list[str], label: str) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"{label} missing columns: {missing}")


def validate_frames(
    model: pd.DataFrame,
    outcomes: pd.DataFrame,
    training: pd.DataFrame,
    *,
    smoke_date: str,
) -> dict[str, int]:
    _require_columns(model, MODEL_KEY + ["game_date", "sim_p_over"], "model")
    _require_columns(outcomes, OFFICIAL_KEY + ["game_date", "actual_value"], "outcomes")
    _require_columns(training, IDENTITY_KEY + ["game_date"], "training")
    if set(model["game_date"].astype(str)) != {smoke_date}:
        raise ValueError("model contains a date outside the smoke date")
    if set(outcomes["game_date"].astype(str)) != {smoke_date}:
        raise ValueError("outcomes contain a date outside the smoke date")
    if model[MODEL_KEY].isna().any().any() or model.duplicated(MODEL_KEY).any():
        raise ValueError("MODEL_KEY must be unique and non-null")
    if outcomes[OFFICIAL_KEY].isna().any().any() or outcomes.duplicated(OFFICIAL_KEY).any():
        raise ValueError("official key must be unique and non-null")
    probabilities = pd.to_numeric(model["sim_p_over"], errors="coerce")
    if probabilities.isna().any() or not probabilities.between(0.0, 1.0).all():
        raise ValueError("sim_p_over must be finite and inside [0,1]")
    actual = pd.to_numeric(outcomes["actual_value"], errors="coerce")
    if actual.isna().any() or (actual < 0).any():
        raise ValueError("official actuals must be non-null and non-negative")

    hr_model = model[(model["category"] == "home_runs") & (model["line"] == 0.5)]
    if len(hr_model) == 0 or len(hr_model) != int((model["category"] == "home_runs").sum()):
        raise ValueError("every HR model row must be exactly line 0.5")
    hr_outcomes = outcomes[outcomes["category"] == "home_runs"]
    training_date = training[training["game_date"].astype(str) == smoke_date]
    for frame, label in ((hr_model, "HR model"), (hr_outcomes, "HR outcomes"), (training_date, "training")):
        if frame[IDENTITY_KEY].isna().any().any() or frame.duplicated(IDENTITY_KEY).any():
            raise ValueError(f"{label} identity must be unique and non-null")
    model_keys = set(map(tuple, hr_model[IDENTITY_KEY].astype(int).to_numpy()))
    outcome_keys = set(map(tuple, hr_outcomes[IDENTITY_KEY].astype(int).to_numpy()))
    training_keys = set(map(tuple, training_date[IDENTITY_KEY].astype(int).to_numpy()))
    if model_keys != outcome_keys or model_keys != training_keys:
        raise ValueError("HR model, official, and point-in-time feature identities differ")
    return {
        "model_rows": len(model),
        "outcome_rows": len(outcomes),
        "hr_rows": len(hr_model),
        "training_rows": len(training_date),
    }


def _validate_hash(path: Path, expected: str, label: str) -> None:
    if not path.is_file() or sha256(path) != expected:
        raise ValueError(f"{label} is missing or its hash differs")


def validate_artifacts(args: argparse.Namespace) -> dict[str, object]:
    protocol_path = Path(args.protocol).resolve()
    protocol = load_protocol(protocol_path, verify_files=True)
    dates = json.loads(Path(args.dates_file).read_text(encoding="utf-8"))
    if args.date not in dates.get("calibration_dates", []):
        raise ValueError("smoke date must be in the locked calibration arm")
    if args.date in dates.get("confirmation_dates", []):
        raise ValueError("smoke date cannot open the confirmation arm")

    model_path = Path(args.probabilities).resolve()
    outcomes_path = Path(args.outcomes).resolve()
    provenance_path = Path(args.provenance).resolve()
    feature_dir = Path(args.feature_dir).resolve()
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    if provenance.get("schema_version") != 2 or provenance.get("dates") != [args.date]:
        raise ValueError("provenance must be schema v2 and exactly one smoke date")
    if provenance.get("simulation_random_seed") != 17:
        raise ValueError("simulation seed differs from the locked protocol")
    if provenance.get("require_statcast_profiles") is not True or provenance.get("fail_on_flags") is not True:
        raise ValueError("required fail-closed reconstruction flags are absent")
    lock = provenance.get("hr_level_mapping_protocol") or {}
    if lock.get("protocol_sha256") != sha256(protocol_path) or lock.get("run_kind") != "smoke":
        raise ValueError("provenance is not bound to this protocol as a smoke")
    if lock.get("betting_authorized") is not False or lock.get("may_2026_opened") is not False:
        raise ValueError("smoke provenance cannot open May or authorize betting")
    source = provenance.get("date_source") or {}
    if source.get("sha256") != sha256(Path(args.dates_file)) or source.get("smoke_date") != args.date:
        raise ValueError("date-source provenance differs from the locked smoke")
    _validate_hash(model_path, provenance["probability_artifact_sha256"], "probability artifact")
    _validate_hash(outcomes_path, provenance["outcome_artifact_sha256"], "outcome artifact")
    snapshot = provenance.get("source_snapshot") or {}
    _validate_hash(Path(snapshot.get("path", "")), snapshot.get("sha256", ""), "source snapshot")
    if provenance.get("pa_distribution_sha256") != protocol["inputs"]["pa_distribution"]["sha256"]:
        raise ValueError("PA distribution hash differs from the protocol")

    feature_record = (provenance.get("feature_snapshots") or {}).get(args.date) or {}
    manifest_path = feature_dir / "manifest.json"
    bundle_path = feature_dir / "bundles.json"
    _validate_hash(manifest_path, feature_record.get("manifest_sha256", ""), "feature manifest")
    _validate_hash(bundle_path, feature_record.get("bundle_sha256", ""), "feature bundles")
    feature_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    bundles = json.loads(bundle_path.read_text(encoding="utf-8"))
    if feature_manifest.get("bundle_count") != len(bundles) or not bundles:
        raise ValueError("feature bundle count is empty or inconsistent")
    bundle_keys: set[tuple[int, int]] = set()
    advanced = 0
    for bundle in bundles:
        hitter = bundle.get("hitter") or {}
        player = hitter.get("player") or {}
        game = hitter.get("game") or {}
        key = (int(game.get("game_pk")), int(player.get("mlb_id")))
        if key in bundle_keys or str(game.get("game_date")) != args.date:
            raise ValueError("feature identity is duplicated or outside the smoke date")
        bundle_keys.add(key)
        statcast = bundle.get("statcast") or {}
        if int(statcast.get("sample_pa") or 0) > 0 and statcast.get("xwoba") is not None:
            advanced += 1
    if advanced == 0:
        raise ValueError("feature snapshot has no advanced Statcast profile")

    training = pd.read_csv(
        protocol["inputs"]["point_in_time_feature_source"]["path"],
        usecols=["game_date", "game_pk", "player_id"],
    ).rename(columns={"game_pk": "mlb_game_pk"})
    counts = validate_frames(
        pd.read_csv(model_path), pd.read_csv(outcomes_path), training, smoke_date=args.date
    )
    training_keys = set(
        map(
            tuple,
            training[training["game_date"].astype(str) == args.date][IDENTITY_KEY]
            .astype(int)
            .to_numpy(),
        )
    )
    if bundle_keys != training_keys:
        raise ValueError("feature bundles do not exactly cover point-in-time identities")
    return {
        **counts,
        "advanced_statcast_profiles": advanced,
        "protocol_sha256": sha256(protocol_path),
        "probability_sha256": sha256(model_path),
        "outcome_sha256": sha256(outcomes_path),
        "source_snapshot_sha256": snapshot["sha256"],
        "betting_authorized": False,
        "may_2026_opened": False,
    }


def self_test() -> int:
    model = pd.DataFrame(
        [[1, 10, "2025-03-27", "home_runs", 0.5, 0.2]],
        columns=MODEL_KEY[:2] + ["game_date", "category", "line", "sim_p_over"],
    )
    outcomes = pd.DataFrame(
        [[1, 10, "2025-03-27", "home_runs", 0]],
        columns=OFFICIAL_KEY[:2] + ["game_date", "category", "actual_value"],
    )
    training = pd.DataFrame(
        [[1, 10, "2025-03-27"]], columns=IDENTITY_KEY + ["game_date"]
    )
    validate_frames(model, outcomes, training, smoke_date="2025-03-27")
    print("[OK] exact valid HR identity passes")
    mutations = []
    bad = pd.concat([model, model], ignore_index=True)
    mutations.append((bad, outcomes, training, "duplicate model key"))
    bad = model.copy(); bad.loc[0, "sim_p_over"] = 1.1
    mutations.append((bad, outcomes, training, "probability outside [0,1]"))
    bad = model.copy(); bad.loc[0, "line"] = 1.5
    mutations.append((bad, outcomes, training, "wrong HR line"))
    bad = model.copy(); bad.loc[0, "game_date"] = "2025-03-28"
    mutations.append((bad, outcomes, training, "wrong model date"))
    bad_out = outcomes.copy(); bad_out.loc[0, "player_id"] = 11
    mutations.append((model, bad_out, training, "official identity drift"))
    bad_training = training.copy(); bad_training.loc[0, "player_id"] = 11
    mutations.append((model, outcomes, bad_training, "feature identity drift"))
    for candidate, official, features, label in mutations:
        try:
            validate_frames(candidate, official, features, smoke_date="2025-03-27")
        except ValueError:
            print(f"[OK] MUTATION {label} fails")
        else:
            raise AssertionError(f"mutation unexpectedly passed: {label}")
    print("7/7")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--protocol")
    ap.add_argument("--dates-file")
    ap.add_argument("--date")
    ap.add_argument("--probabilities")
    ap.add_argument("--outcomes")
    ap.add_argument("--provenance")
    ap.add_argument("--feature-dir")
    args = ap.parse_args(argv)
    if args.self_test:
        return self_test()
    required = [
        args.protocol, args.dates_file, args.date, args.probabilities,
        args.outcomes, args.provenance, args.feature_dir,
    ]
    if not all(required):
        ap.error("production validation requires every artifact argument")
    result = validate_artifacts(args)
    print("HR LEVEL-MAPPING SMOKE VALID")
    for key, value in result.items():
        print(f"  {key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
