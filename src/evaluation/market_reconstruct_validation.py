"""Fail-closed validation for a strict-market reconstruction smoke artifact."""
from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any

import pandas as pd

from src.evaluation.hits_policy_source import (
    DEFERRED_FRESHNESS_RULE,
    POLICY_SOURCE_KIND,
    validate_policy_source,
)
from src.evaluation.identity_keys import MODEL_KEY, OUTCOME_KEY, require_unique
from src.evaluation.strict_market_artifact import (
    HR_RECONSTRUCTION_ARTIFACT_KIND,
    price_freshness_rule_for_manifest,
    selection_rule_for_manifest,
    validate_manifest_purpose,
)


def sha256(path: str | Path) -> str:
    """Return the full content hash used by reconstruction provenance."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _normalise_dates(frame: pd.DataFrame, column: str, label: str) -> pd.DataFrame:
    if column not in frame.columns:
        raise ValueError(f"{label}: missing {column!r}")
    out = frame.copy()
    out[column] = pd.to_datetime(out[column], errors="coerce").dt.strftime("%Y-%m-%d")
    if out[column].isna().any():
        raise ValueError(f"{label}: invalid {column!r}")
    return out


def _load_model(path: str | Path, label: str, dates: list[str]) -> pd.DataFrame:
    frame = pd.read_csv(path)
    needed = [*MODEL_KEY, "game_date", "sim_p_over"]
    missing = [column for column in needed if column not in frame.columns]
    if missing:
        raise ValueError(f"{label}: missing {missing}")
    frame = _normalise_dates(frame, "game_date", label)
    if set(frame.game_date) != set(dates):
        raise ValueError(
            f"{label}: contains dates {sorted(set(frame.game_date))}; expected {dates}"
        )
    if frame[MODEL_KEY].isna().any().any():
        raise ValueError(f"{label}: NULL MODEL_KEY")
    require_unique(frame, MODEL_KEY, label)
    frame["sim_p_over"] = pd.to_numeric(frame.sim_p_over, errors="coerce")
    if frame.sim_p_over.isna().any() or not frame.sim_p_over.between(0, 1).all():
        raise ValueError(f"{label}: sim_p_over is NULL or outside [0,1]")
    return frame


def _load_outcomes(path: str | Path, label: str, dates: list[str]) -> pd.DataFrame:
    frame = pd.read_csv(path)
    needed = [*OUTCOME_KEY, "game_date", "actual_value"]
    missing = [column for column in needed if column not in frame.columns]
    if missing:
        raise ValueError(f"{label}: missing {missing}")
    frame = _normalise_dates(frame, "game_date", label)
    if set(frame.game_date) != set(dates):
        raise ValueError(
            f"{label}: contains dates {sorted(set(frame.game_date))}; expected {dates}"
        )
    if frame[OUTCOME_KEY].isna().any().any():
        raise ValueError(f"{label}: NULL OUTCOME_KEY")
    require_unique(frame, OUTCOME_KEY, label)
    frame["actual_value"] = pd.to_numeric(frame.actual_value, errors="coerce")
    if frame.actual_value.isna().any():
        raise ValueError(f"{label}: NULL official actual")
    return frame.sort_values(OUTCOME_KEY).reset_index(drop=True)


def _validate_provenance(
    path: str | Path, market_manifest: str | Path, artifact_hash: str,
    dates: list[str], label: str, full_universe: bool,
    source_kind: str,
    source_artifact_hash_field: str,
    chronological_protocol: str | Path | None = None,
    chronological_role: str | None = None,
) -> None:
    payload: dict[str, Any] = json.loads(Path(path).read_text(encoding="utf-8"))
    _validate_release_evidence(
        payload,
        dates=dates,
        label=label,
        chronological_protocol=chronological_protocol,
        chronological_role=chronological_role,
    )
    if payload.get("dates") != dates:
        raise ValueError(f"{label}: provenance dates {payload.get('dates')!r} != {dates!r}")
    source = payload.get("date_source")
    if not isinstance(source, dict) or source.get("kind") != source_kind:
        raise ValueError(
            f"{label}: provenance is not bound to the expected {source_kind}"
        )
    if Path(str(source.get("path", ""))).resolve() != Path(market_manifest).resolve():
        raise ValueError(f"{label}: provenance market manifest path differs from this smoke")
    if source.get("sha256") != sha256(market_manifest):
        raise ValueError(f"{label}: provenance market manifest hash differs from this smoke")
    if source.get(source_artifact_hash_field) != artifact_hash:
        raise ValueError(f"{label}: provenance market artifact hash differs from this smoke")
    if full_universe and source.get("smoke_date") is not None:
        raise ValueError(f"{label}: full reconstruction provenance unexpectedly carries a smoke date")
    if (chronological_protocol is None) != (chronological_role is None):
        raise ValueError("chronological protocol and role must be validated together")
    if chronological_protocol is not None:
        block = payload.get("chronological_protocol")
        if not isinstance(block, dict):
            raise ValueError(f"{label}: provenance lacks chronological protocol binding")
        expected_path = Path(chronological_protocol).resolve()
        if Path(str(block.get("protocol_path", ""))).resolve() != expected_path:
            raise ValueError(f"{label}: chronological protocol path differs")
        if block.get("protocol_sha256") != sha256(expected_path):
            raise ValueError(f"{label}: chronological protocol hash differs")
        if block.get("role") != chronological_role:
            raise ValueError(f"{label}: chronological role differs")
        if block.get("market_manifest_sha256") != sha256(market_manifest):
            raise ValueError(f"{label}: chronological market manifest hash differs")
        if block.get("market_artifact_sha256") != artifact_hash:
            raise ValueError(f"{label}: chronological market artifact hash differs")


def _validate_release_evidence(
    payload: dict[str, Any],
    *,
    dates: list[str],
    label: str,
    chronological_protocol: str | Path | None,
    chronological_role: str | None,
) -> None:
    """Verify schema-v2 content snapshots required by canonical releases."""
    schema = int(payload.get("schema_version", 1))
    if schema < 2:
        return

    config = payload.get("effective_config")
    if not isinstance(config, dict):
        raise ValueError(f"{label}: release lacks its effective config content")
    config_bytes = json.dumps(config, sort_keys=True, separators=(",", ":")).encode("utf-8")
    config_hash = hashlib.sha256(config_bytes).hexdigest()
    if config_hash != payload.get("config_sha256"):
        raise ValueError(f"{label}: effective config content/hash mismatch")

    source = payload.get("source_snapshot")
    if not isinstance(source, dict):
        raise ValueError(f"{label}: release lacks a source snapshot")
    source_path = Path(str(source.get("path") or ""))
    if not source_path.is_file() or sha256(source_path) != source.get("sha256"):
        raise ValueError(f"{label}: source snapshot is missing or tampered")
    try:
        with zipfile.ZipFile(source_path) as archive:
            names = set(archive.namelist())
            required = {
                "effective_config.json",
                "environment.json",
                "source/run_gate_reconstruct.py",
                "source/run_reconstruct_date.py",
                "source/src/features/legacy_statcast_features.py",
                "source/src/data/savant.py",
            }
            missing = sorted(required - names)
            if missing:
                raise ValueError(f"{label}: source snapshot omits {missing}")
            if archive.read("effective_config.json") != config_bytes:
                raise ValueError(f"{label}: source snapshot config differs from manifest")
            digest = hashlib.sha256()
            source_names = sorted(name for name in names if name.startswith("source/"))
            for name in source_names:
                relative = name[len("source/"):].encode("utf-8")
                digest.update(relative + b"\0" + archive.read(name) + b"\0")
            if digest.hexdigest() != source.get("source_tree_sha256"):
                raise ValueError(f"{label}: source tree content/hash mismatch")
    except zipfile.BadZipFile as exc:
        raise ValueError(f"{label}: source snapshot is not a valid zip") from exc

    snapshots = payload.get("feature_snapshots")
    if not isinstance(snapshots, dict) or sorted(snapshots) != sorted(dates):
        raise ValueError(f"{label}: feature snapshot dates differ from reconstruction dates")
    protocol_hash = sha256(chronological_protocol) if chronological_protocol else None
    for game_date in dates:
        evidence = snapshots.get(game_date)
        if not isinstance(evidence, dict):
            raise ValueError(f"{label}: malformed feature evidence for {game_date}")
        manifest_path = Path(str(evidence.get("manifest_path") or ""))
        bundle_path = Path(str(evidence.get("bundle_path") or ""))
        if (
            not manifest_path.is_file()
            or sha256(manifest_path) != evidence.get("manifest_sha256")
            or not bundle_path.is_file()
            or sha256(bundle_path) != evidence.get("bundle_sha256")
        ):
            raise ValueError(f"{label}: feature snapshot is missing or tampered for {game_date}")
        feature_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if feature_manifest.get("game_date") != game_date:
            raise ValueError(f"{label}: feature manifest date mismatch for {game_date}")
        if int(feature_manifest.get("bundle_count", -1)) != int(evidence.get("bundle_count", -2)):
            raise ValueError(f"{label}: feature bundle count evidence differs for {game_date}")
        artifact = (feature_manifest.get("artifacts") or {}).get("json") or {}
        if artifact.get("sha256") != evidence.get("bundle_sha256"):
            raise ValueError(f"{label}: feature manifest bundle hash differs for {game_date}")
        bundle_payload = json.loads(bundle_path.read_text(encoding="utf-8"))
        if not isinstance(bundle_payload, list) or len(bundle_payload) != int(
            evidence.get("bundle_count", -1)
        ):
            raise ValueError(f"{label}: feature bundle content/count differs for {game_date}")
        feature_provenance = feature_manifest.get("provenance") or {}
        if feature_provenance.get("config_sha256") != config_hash:
            raise ValueError(f"{label}: feature/config provenance differs for {game_date}")
        if feature_provenance.get("source_tree_sha256") != source.get("source_tree_sha256"):
            raise ValueError(f"{label}: feature/source provenance differs for {game_date}")
        if chronological_protocol is not None:
            if feature_provenance.get("chronological_protocol_sha256") != protocol_hash:
                raise ValueError(f"{label}: feature/protocol provenance differs for {game_date}")
            if feature_provenance.get("chronological_role") != chronological_role:
                raise ValueError(f"{label}: feature chronological role differs for {game_date}")


def validate_reconstruction(
    frozen_path: str | Path,
    candidate_path: str | Path,
    frozen_outcomes_path: str | Path,
    candidate_outcomes_path: str | Path,
    frozen_manifest_path: str | Path,
    candidate_manifest_path: str | Path,
    strict_artifact_path: str | Path,
    strict_manifest_path: str | Path,
    dates: list[str],
    *,
    full_universe: bool,
    chronological_protocol: str | Path | None = None,
    chronological_role: str | None = None,
) -> dict[str, float | int]:
    """Validate a smoke or the exact full strict-market reconstruction."""
    dates = sorted(dates)
    if not dates:
        raise ValueError("reconstruction validation has no expected dates")
    strict_manifest = json.loads(Path(strict_manifest_path).read_text(encoding="utf-8"))
    selection_rule_for_manifest(strict_manifest)
    manifest_purpose = validate_manifest_purpose(strict_manifest)
    is_reconstruction_only = manifest_purpose == HR_RECONSTRUCTION_ARTIFACT_KIND
    is_policy_source = strict_manifest.get("artifact_kind") == POLICY_SOURCE_KIND
    if is_policy_source:
        if strict_manifest.get("price_freshness_rule") != DEFERRED_FRESHNESS_RULE:
            raise ValueError("policy-source manifest did not defer freshness")
        if strict_manifest.get("max_quote_age") is not None:
            raise ValueError("policy-source manifest is already freshness-censored")
    else:
        price_freshness_rule_for_manifest(strict_manifest)
    artifact_hash = sha256(strict_artifact_path)
    expected_artifact_hash = strict_manifest.get("hashes", {}).get("artifact")
    if not isinstance(expected_artifact_hash, str) or not artifact_hash.startswith(expected_artifact_hash):
        raise ValueError("strict artifact hash disagrees with its manifest")
    strict = pd.read_csv(strict_artifact_path)
    if is_policy_source:
        validate_policy_source(strict)
    strict = _normalise_dates(strict, "official_game_date", "strict market artifact")
    market = strict[strict.official_game_date.isin(dates)].copy()
    if market.empty:
        raise ValueError(f"strict market artifact has no rows for requested dates {dates}")
    if full_universe and sorted(strict.official_game_date.unique().tolist()) != dates:
        raise ValueError("full reconstruction dates differ from strict artifact date universe")
    if market[MODEL_KEY].isna().any().any():
        raise ValueError("market artifact has a null MODEL_KEY")
    if not is_policy_source and market.duplicated(MODEL_KEY).any():
        raise ValueError("strict market artifact has duplicate MODEL_KEY")

    frozen = _load_model(frozen_path, "frozen model", dates)
    candidate = _load_model(candidate_path, "candidate model", dates)
    frozen_keys = set(map(tuple, frozen[MODEL_KEY].to_numpy()))
    candidate_keys = set(map(tuple, candidate[MODEL_KEY].to_numpy()))
    if frozen_keys != candidate_keys:
        raise ValueError("frozen/candidate MODEL_KEY sets differ")
    aligned = frozen[[*MODEL_KEY, "sim_p_over"]].merge(
        candidate[[*MODEL_KEY, "sim_p_over"]], on=MODEL_KEY, validate="one_to_one",
        suffixes=("_frozen", "_candidate"),
    )
    drift = (aligned.sim_p_over_frozen - aligned.sim_p_over_candidate).abs()
    if not (drift > 1e-9).any():
        raise ValueError("candidate is inert: no probability differs from frozen")

    market_keys = set(map(tuple, market[MODEL_KEY].to_numpy()))
    missing_model = market_keys - frozen_keys
    covered_market_keys = market_keys.intersection(frozen_keys)
    if missing_model and not is_reconstruction_only:
        raise ValueError(
            f"strict market has {len(missing_model)} MODEL_KEYs absent from the smoke; "
            "do not launch the full reconstruction"
        )
    if is_reconstruction_only and not covered_market_keys:
        raise ValueError("market reconstruction source has zero model-covered keys")

    frozen_outcomes = _load_outcomes(frozen_outcomes_path, "frozen outcomes", dates)
    candidate_outcomes = _load_outcomes(candidate_outcomes_path, "candidate outcomes", dates)
    if not frozen_outcomes.equals(candidate_outcomes):
        raise ValueError("official outcome artifacts differ between frozen and candidate")
    outcome_keys = set(map(tuple, frozen_outcomes[OUTCOME_KEY].to_numpy()))
    outcome_source_keys = covered_market_keys if is_reconstruction_only else market_keys
    needed_outcomes = {
        (game, player, category)
        for game, player, category, _ in outcome_source_keys
    }
    missing_outcomes = needed_outcomes - outcome_keys
    if missing_outcomes:
        raise ValueError(
            f"strict market has {len(missing_outcomes)} rows without an official outcome target"
        )

    _validate_provenance(
        frozen_manifest_path, strict_manifest_path, artifact_hash, dates, "frozen",
        full_universe,
        "policy_source_manifest" if is_policy_source else "strict_market_manifest",
        (
            "policy_source_artifact_sha256"
            if is_policy_source else "strict_market_artifact_sha256"
        ),
        chronological_protocol, chronological_role,
    )
    _validate_provenance(
        candidate_manifest_path, strict_manifest_path, artifact_hash, dates, "candidate",
        full_universe,
        "policy_source_manifest" if is_policy_source else "strict_market_manifest",
        (
            "policy_source_artifact_sha256"
            if is_policy_source else "strict_market_artifact_sha256"
        ),
        chronological_protocol, chronological_role,
    )
    summary: dict[str, float | int] = {
        "model_rows": len(frozen),
        "outcome_rows": len(frozen_outcomes),
        "changed_probabilities": int((drift > 1e-9).sum()),
        "mean_abs_probability_drift": float(drift.mean()),
        "max_abs_probability_drift": float(drift.max()),
    }
    if is_policy_source:
        summary["policy_source_rows"] = int(len(market))
        summary["policy_source_model_keys"] = int(len(market_keys))
    elif is_reconstruction_only:
        covered = pd.DataFrame(list(covered_market_keys), columns=MODEL_KEY)
        covered_drift = covered.merge(
            aligned, on=MODEL_KEY, how="left", validate="one_to_one"
        )
        market_drift = (
            covered_drift.sim_p_over_frozen - covered_drift.sim_p_over_candidate
        ).abs()
        if not (market_drift > 1e-9).any():
            raise ValueError(
                "candidate is inert on every model-covered HR reconstruction key"
            )
        summary["reconstruction_source_keys"] = int(len(market_keys))
        summary["model_covered_market_keys"] = int(len(covered_market_keys))
        summary["model_unavailable_market_keys"] = int(len(missing_model))
        summary["model_market_coverage"] = float(
            len(covered_market_keys) / len(market_keys)
        )
        summary["changed_market_probabilities"] = int((market_drift > 1e-9).sum())
        summary["mean_abs_market_probability_drift"] = float(market_drift.mean())
        summary["max_abs_market_probability_drift"] = float(market_drift.max())
    else:
        summary["strict_market_keys"] = int(len(market_keys))
    return summary


def validate_smoke(
    frozen_path: str | Path, candidate_path: str | Path,
    frozen_outcomes_path: str | Path, candidate_outcomes_path: str | Path,
    frozen_manifest_path: str | Path, candidate_manifest_path: str | Path,
    strict_artifact_path: str | Path, strict_manifest_path: str | Path, date: str,
    *, chronological_protocol: str | Path | None = None,
    chronological_role: str | None = None,
) -> dict[str, float | int]:
    """Validate one certified smoke date before a full reconstruction."""
    return validate_reconstruction(
        frozen_path, candidate_path, frozen_outcomes_path, candidate_outcomes_path,
        frozen_manifest_path, candidate_manifest_path, strict_artifact_path,
        strict_manifest_path, [date], full_universe=False,
        chronological_protocol=chronological_protocol,
        chronological_role=chronological_role,
    )


def validate_full(
    frozen_path: str | Path, candidate_path: str | Path,
    frozen_outcomes_path: str | Path, candidate_outcomes_path: str | Path,
    frozen_manifest_path: str | Path, candidate_manifest_path: str | Path,
    strict_artifact_path: str | Path, strict_manifest_path: str | Path,
    *, chronological_protocol: str | Path | None = None,
    chronological_role: str | None = None,
) -> dict[str, float | int]:
    """Validate exactly every official date in the strict-market manifest."""
    payload = json.loads(Path(strict_manifest_path).read_text(encoding="utf-8"))
    dates = payload.get("official_date_universe")
    if not isinstance(dates, list) or not all(isinstance(date, str) for date in dates):
        raise ValueError("strict manifest lacks an explicit official date universe")
    return validate_reconstruction(
        frozen_path, candidate_path, frozen_outcomes_path, candidate_outcomes_path,
        frozen_manifest_path, candidate_manifest_path, strict_artifact_path,
        strict_manifest_path, dates, full_universe=True,
        chronological_protocol=chronological_protocol,
        chronological_role=chronological_role,
    )
