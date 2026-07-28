#!/usr/bin/env python3
"""Independently validate and certify the direct batter PA panel."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path: os.sys.path.insert(0, str(ROOT))
from src.evaluation.shared_pa_training_data import outcome_counts  # noqa: E402
from src.features.direct_batter_pa_history import OUTCOMES  # noqa: E402


IDENTITY = ["season", "game_date", "game_pk", "player_id"]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""): digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    if path.exists(): raise FileExistsError(f"refusing to overwrite certificate: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, sort_keys=True, indent=2, allow_nan=False); handle.write("\n"); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try: os.unlink(temporary)
        except FileNotFoundError: pass
        raise


def validate_batted_ball_lineage(frame: pd.DataFrame) -> None:
    """Prove serialized direct-history counts, denominator, and rates agree."""
    columns = [
        "history_batted_ball_denominator", "history_barrel_count",
        "history_hard_hit_count", "history_barrel_rate", "history_hard_hit_rate",
    ]
    if missing := sorted(set(columns).difference(frame.columns)):
        raise ValueError(f"batted-ball lineage columns missing: {missing}")
    try:
        values = frame[columns].apply(pd.to_numeric, errors="raise")
    except (TypeError, ValueError) as exc:
        raise ValueError("batted-ball lineage contains nonnumeric values") from exc
    partial = values.notna().any(axis=1) & ~values.notna().all(axis=1)
    if partial.any():
        raise ValueError("partial batted-ball count/rate lineage")
    present = values.notna().all(axis=1)
    if not present.any():
        raise ValueError("batted-ball count/rate lineage has no measured rows")
    measured = values.loc[present]
    denominator = measured["history_batted_ball_denominator"]
    barrels = measured["history_barrel_count"]
    hard_hits = measured["history_hard_hit_count"]
    if (denominator <= 0).any() or (barrels < 0).any() or (hard_hits < 0).any():
        raise ValueError("invalid batted-ball counts or denominator")
    if not denominator.mod(1).eq(0).all() or not barrels.mod(1).eq(0).all() or not hard_hits.mod(1).eq(0).all():
        raise ValueError("batted-ball counts and denominator must be integers")
    if (barrels > hard_hits).any() or (hard_hits > denominator).any():
        raise ValueError("impossible batted-ball count ordering")
    expected_barrel = barrels / denominator
    expected_hard_hit = hard_hits / denominator
    if not (measured["history_barrel_rate"] - expected_barrel).abs().le(1e-12).all():
        raise ValueError("barrel count/rate serialization mismatch")
    if not (measured["history_hard_hit_rate"] - expected_hard_hit).abs().le(1e-12).all():
        raise ValueError("hard-hit count/rate serialization mismatch")


def _validate_rate_lineage(
    frame: pd.DataFrame, *, name: str, numerator: str, denominator: str, rate: str,
) -> None:
    columns = [numerator, denominator, rate]
    if missing := sorted(set(columns).difference(frame.columns)):
        raise ValueError(f"{name} lineage columns missing: {missing}")
    values = frame[columns].apply(pd.to_numeric, errors="raise")
    counts = values[numerator]
    denominators = values[denominator]
    if counts.isna().any() or denominators.isna().any():
        raise ValueError(f"{name} count or denominator is missing")
    if (counts < 0).any() or (denominators < 0).any():
        raise ValueError(f"{name} count or denominator is negative")
    if not counts.mod(1).eq(0).all() or not denominators.mod(1).eq(0).all():
        raise ValueError(f"{name} count or denominator is nonintegral")
    if (counts > denominators).any():
        raise ValueError(f"{name} count exceeds denominator")
    positive = denominators.gt(0)
    if values.loc[~positive, rate].notna().any():
        raise ValueError(f"{name} rate exists with zero denominator")
    if values.loc[positive, rate].isna().any():
        raise ValueError(f"{name} rate missing with positive denominator")
    expected = counts.loc[positive] / denominators.loc[positive]
    if not (values.loc[positive, rate] - expected).abs().le(1e-12).all():
        raise ValueError(f"{name} count/rate serialization mismatch")


def _validate_moment_lineage(
    frame: pd.DataFrame, *, prefix: str, population: str,
) -> None:
    columns = [
        f"{prefix}_count", f"{prefix}_missing_count", f"{prefix}_mean", f"{prefix}_sd", population,
    ]
    if missing := sorted(set(columns).difference(frame.columns)):
        raise ValueError(f"{prefix} moment lineage columns missing: {missing}")
    values = frame[columns].apply(pd.to_numeric, errors="raise")
    count = values[f"{prefix}_count"]
    missing_count = values[f"{prefix}_missing_count"]
    population_count = values[population]
    for label, series in (("count", count), ("missing", missing_count), ("population", population_count)):
        if series.isna().any() or (series < 0).any() or not series.mod(1).eq(0).all():
            raise ValueError(f"{prefix} {label} lineage is invalid")
    if not count.add(missing_count).eq(population_count).all():
        raise ValueError(f"{prefix} count and missing count do not equal population")
    present = count.gt(0)
    if values.loc[present, [f"{prefix}_mean", f"{prefix}_sd"]].isna().any().any():
        raise ValueError(f"{prefix} moments missing with positive denominator")
    if values.loc[~present, [f"{prefix}_mean", f"{prefix}_sd"]].notna().any().any():
        raise ValueError(f"{prefix} moments exist with zero denominator")
    if (values.loc[present, f"{prefix}_sd"] < 0).any():
        raise ValueError(f"{prefix} standard deviation is negative")


def validate_feature_denominator_lineage(frame: pd.DataFrame) -> None:
    """Fail closed unless every rate and moment carries truthful support."""
    _validate_rate_lineage(
        frame, name="swing", numerator="history_swing_count",
        denominator="history_swing_denominator", rate="history_swing_rate",
    )
    _validate_rate_lineage(
        frame, name="whiff", numerator="history_whiff_count",
        denominator="history_whiff_denominator", rate="history_whiff_rate",
    )
    _validate_rate_lineage(
        frame, name="chase", numerator="history_chase_count",
        denominator="history_chase_denominator", rate="history_chase_rate",
    )
    _validate_rate_lineage(
        frame, name="zone", numerator="history_zone_count",
        denominator="history_zone_denominator", rate="history_zone_rate",
    )
    lineage_columns = [
        "history_pitch_count", "history_swing_count", "history_swing_denominator",
        "history_whiff_denominator", "history_description_denominator",
        "history_description_missing_count", "history_zone_denominator",
        "history_zone_missing_count", "history_pitch_type_denominator",
        "history_pitch_type_missing_count",
    ]
    numeric = frame.loc[:, lineage_columns].apply(pd.to_numeric, errors="raise")
    if not numeric["history_whiff_denominator"].eq(numeric["history_swing_count"]).all():
        raise ValueError("whiff denominator is not the serialized swing count")
    if not numeric["history_description_denominator"].eq(numeric["history_swing_denominator"]).all():
        raise ValueError("swing denominator is not the valid-description denominator")
    if not numeric["history_description_denominator"].add(
        numeric["history_description_missing_count"]
    ).eq(numeric["history_pitch_count"]).all():
        raise ValueError("description denominator lineage does not equal pitch population")
    if not numeric["history_zone_denominator"].add(numeric["history_zone_missing_count"]).eq(
        numeric["history_pitch_count"]
    ).all():
        raise ValueError("zone denominator lineage does not equal pitch population")
    for prefix, population in (
        ("history_pa_age_days", "history_pa"),
        ("history_exit_velocity", "history_bip"),
        ("history_launch_angle", "history_bip"),
        ("history_release_speed", "history_pitch_count"),
        ("history_pfx_x", "history_pitch_count"),
        ("history_pfx_z", "history_pitch_count"),
        ("history_plate_x", "history_pitch_count"),
        ("history_plate_z", "history_pitch_count"),
    ):
        _validate_moment_lineage(frame, prefix=prefix, population=population)
    if not numeric["history_pitch_type_denominator"].add(
        numeric["history_pitch_type_missing_count"]
    ).eq(numeric["history_pitch_count"]).all():
        raise ValueError("pitch-type denominator lineage does not equal pitch population")
    entropy = pd.to_numeric(frame["history_pitch_type_entropy"], errors="coerce")
    has_types = numeric["history_pitch_type_denominator"].gt(0)
    if entropy.loc[has_types].isna().any() or entropy.loc[~has_types].notna().any():
        raise ValueError("pitch-type entropy does not match its denominator")
    if (entropy.loc[has_types] < 0).any():
        raise ValueError("pitch-type entropy is negative")


def validate(*, panel: Path, manifest: Path, raw_root: Path, certificate: Path) -> dict[str, Any]:
    source = json.loads(manifest.read_text(encoding="utf-8"))
    if source.get("schema_version") != "direct-batter-pa-panel-manifest-v4":
        raise ValueError("panel manifest schema is not v4")
    if source.get("status") != "DIRECT_BATTER_PA_TIMING_CONTRACT_PASSED_RESEARCH_ONLY":
        raise ValueError("panel manifest status is not admissible")
    if source.get("output", {}).get("sha256") != sha256_file(panel):
        raise ValueError("panel content hash mismatch")
    identity_files = source.get("identity_files_sha256")
    if not isinstance(identity_files, dict) or not identity_files:
        raise ValueError("panel transitive identity files are missing")
    for relative, expected in identity_files.items():
        relative_path = Path(str(relative))
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise ValueError(f"panel identity path is unsafe: {relative}")
        path = ROOT / relative_path
        if not path.is_file() or sha256_file(path) != expected:
            raise ValueError(f"panel identity file hash mismatch: {relative}")
    dependency = source.get("dependency_identity")
    if dependency != {
        "declaration": "requirements.txt",
        "declaration_sha256": identity_files.get("requirements.txt"),
        "build_metadata": "pyproject.toml",
        "build_metadata_sha256": identity_files.get("pyproject.toml"),
        "exact_environment_lock_claimed": False,
    }:
        raise ValueError("panel dependency identity changed")
    raw_hashes = source.get("raw_source_sha256")
    if not isinstance(raw_hashes, dict) or not raw_hashes:
        raise ValueError("raw source hashes are missing")
    for relative, expected in raw_hashes.items():
        path = raw_root / relative
        if not path.is_file() or sha256_file(path) != expected:
            raise ValueError(f"raw source hash mismatch: {relative}")
    zero_pa = source.get("zero_pa_evidence", {})
    zero_path = Path(str(zero_pa.get("path", "")))
    if not zero_path.is_file() or zero_pa.get("sha256") != sha256_file(zero_path):
        raise ValueError("zero-PA evidence hash mismatch")
    frame = pd.read_csv(panel, low_memory=False)
    if len(frame) != source["output"]["rows"]:
        raise ValueError("panel row count mismatch")
    if frame.duplicated(IDENTITY).any():
        raise ValueError("panel identity is duplicated")
    years = pd.to_numeric(frame["season"], errors="raise").astype(int)
    if set(years.unique()) != {2023}:
        raise ValueError("panel contains a forbidden year")
    target = pd.to_datetime(frame["game_date"], format="%Y-%m-%d", errors="coerce")
    source_date = pd.to_datetime(frame["max_source_date"], format="%Y-%m-%d", errors="coerce")
    if target.isna().any() or (source_date.notna() & source_date.ge(target)).any():
        raise ValueError("panel chronology is invalid")
    counts = outcome_counts(frame)
    if (counts < 0).any().any() or not counts.sum(axis=1).eq(pd.to_numeric(frame["out_pa"], errors="raise")).all():
        raise ValueError("panel PA outcomes are invalid")
    target_columns = [f"target_{name}" for name in OUTCOMES]
    if missing := sorted(set(target_columns).difference(frame.columns)):
        raise ValueError(f"raw target columns missing: {missing}")
    raw_truth = frame[target_columns].rename(columns={f"target_{name}": name for name in OUTCOMES}).astype(int)
    if not counts.loc[:, OUTCOMES].astype(int).equals(raw_truth):
        raise ValueError("probability consumer outcome columns do not equal raw terminal truth")
    if any(column.startswith(("opp_sp_", "weather_", "park_", "umpire_")) for column in frame.columns):
        raise ValueError("forbidden context entered direct batter panel")
    history_columns = [column for column in frame.columns if column.startswith("history_") or column.startswith("days_since_")]
    if not history_columns:
        raise ValueError("direct batter feature set is empty")
    validate_batted_ball_lineage(frame)
    validate_feature_denominator_lineage(frame)
    result = {
        "schema_version": "direct-batter-pa-panel-certificate-v1",
        "status": "DIRECT_BATTER_PA_PANEL_CERTIFIED_RESEARCH_ONLY",
        "panel": {"path": str(panel), "sha256": sha256_file(panel), "rows": len(frame)},
        "manifest": {"path": str(manifest), "sha256": sha256_file(manifest)},
        "validation": {
            "raw_files_rehashed": len(raw_hashes), "identity_duplicates": 0,
            "chronology_violations": 0, "feature_columns": len(history_columns),
            "physical_target_rows_2023": int(years.eq(2023).sum()),
            "fit_eligible_rows_2023": int(counts.sum(axis=1).gt(0).sum()),
            "selection_rows_2024": 0,
            "zero_pa_rows": int(counts.sum(axis=1).eq(0).sum()),
            "probability_consumer_raw_truth_parity": True,
        },
        "protected_invariants": {
            "confirmation_2025_opened": False, "may_2026_opened": False,
            "pitcher_matchup_features": False, "production_changed": False,
            "betting_authorized": False,
        },
        "validator_sha256": sha256_file(ROOT / "scripts" / "validate_direct_batter_pa_panel.py"),
    }
    atomic_json(certificate, result)
    return result


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel",required=True,type=Path); parser.add_argument("--manifest",required=True,type=Path)
    parser.add_argument("--raw-root",required=True,type=Path); parser.add_argument("--certificate",required=True,type=Path)
    args=parser.parse_args(); result=validate(**vars(args)); print(json.dumps({"status":result["status"],"validation":result["validation"]},sort_keys=True)); return 0


if __name__ == "__main__": raise SystemExit(main())
