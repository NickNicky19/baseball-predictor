#!/usr/bin/env python3
"""Adjudicate the immutable 2023 batter-PA source chain without fitting.

The boundary distinguishes local byte/semantic consistency from source,
dependency, and release authority.  An internally consistent legacy package
must remain blocked when any required authority is absent.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Any, Iterable

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config/direct_batter_pa_source_binding_v1.json"
CONTRACT_SHA256 = "996bc9014a8c9825c89f83b254f88bcf695254a81d3f7b027eda992edf7caa3f"
OFFICIAL_COLUMNS = [
    "season", "game_date", "game_pk", "player_id", "lineup_slot", "out_pa",
    "out_ab", "out_hits", "out_doubles", "out_triples", "out_hr", "out_bb", "out_k",
]
IDENTITY = ["season", "game_date", "game_pk", "player_id"]
DAILY_RE = re.compile(r"hitters_(2023-\d{2}-\d{2})_statcast\.csv\Z")
EXPECTED_MANIFEST_KEYS = {
    "betting_authorized", "build_runtime_identity", "confirmation_2025_opened",
    "dependency_identity", "feature_policy", "identity_files_sha256", "identity_policy",
    "may_2026_opened", "missing_prior_year_files", "official", "outcome_truth", "output",
    "population", "production_changed", "protocol", "raw_root", "raw_source_sha256",
    "schema_version", "script_sha256", "status", "zero_pa_evidence",
}
IMPLEMENTATION_SOURCE_BASE_COMMIT = "3ae769f172a0550e23ea094af9f6e8fd6618bca4"
IMPLEMENTATION_FILES = (
    "config/direct_batter_pa_source_binding_v1.json",
    "scripts/validate_direct_batter_pa_source_binding.py",
    "tests/test_direct_batter_pa_source_binding.py",
)
DEPENDENCY_DECLARATIONS = ("requirements.txt", "pyproject.toml")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _has_reparse_point(path: Path) -> bool:
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    return path.is_symlink() or bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def safe_existing_file(root: Path, relative: str, *, context: str) -> Path:
    rel = Path(relative)
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError(f"{context} path is unsafe")
    root_resolved = root.resolve(strict=True)
    candidate = root / rel
    if not candidate.is_file():
        raise ValueError(f"{context} is missing or not a regular file")
    resolved = candidate.resolve(strict=True)
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"{context} escapes its declared root") from exc
    current = candidate
    while True:
        if _has_reparse_point(current):
            raise ValueError(f"{context} traverses a symlink, junction, or reparse point")
        if current.resolve(strict=True) == root_resolved:
            break
        current = current.parent
    return candidate


def load_contract(path: Path = CONTRACT_PATH) -> dict[str, Any]:
    if sha256_file(path) != CONTRACT_SHA256:
        raise ValueError("source-binding contract differs from fixed digest")
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema_version") != "direct-batter-pa-source-binding-contract-v1":
        raise ValueError("source-binding contract schema changed")
    if value.get("status") != "LOCKED_2023_SOURCE_BINDING_AUDIT_NO_FIT":
        raise ValueError("source-binding contract status changed")
    if value.get("research_only") is not True or value.get("betting_authorized") is not False:
        raise ValueError("source-binding safety state changed")
    if value.get("model_fitting_permitted") is not False:
        raise ValueError("source-binding contract cannot permit fitting")
    protected = value.get("protected_data", {})
    if set(protected.values()) != {False} or set(protected) != {
        "may_2026_opened", "selection_2024_opened", "spent_hr_confirmation_2025_opened",
        "prices_opened", "prospective_evidence_opened",
    }:
        raise ValueError("protected-data state changed")
    required = value.get("required_authority", {})
    observed = value.get("observed_authority", {})
    if not required or not all(flag is True for flag in required.values()):
        raise ValueError("required authority contract weakened")
    if observed.get("raw_transport_request_and_response_receipts") is not False:
        raise ValueError("unproven raw transport authority was promoted")
    if observed.get("independent_official_source_receipts") is not False:
        raise ValueError("unproven official source authority was promoted")
    if observed.get("exact_reproducible_dependency_lock") is not False:
        raise ValueError("unproven dependency authority was promoted")
    if observed.get("external_expected_release_digest") is not False:
        raise ValueError("unproven external release authority was promoted")
    if value.get("expected_decision") != "BLOCKED_INCOMPLETE_SOURCE_AND_DEPENDENCY_AUTHORITY":
        raise ValueError("source-binding decision changed")
    return value


def _verify_fixed_upstream(upstream_root: Path, contract: dict[str, Any]) -> tuple[dict[str, Any], pd.DataFrame]:
    expected = contract["upstream_release"]
    for label in ("registry", "panel", "manifest", "certificate"):
        path = safe_existing_file(upstream_root, expected[f"{label}_path"], context=f"upstream {label}")
        if sha256_file(path) != expected[f"{label}_sha256"]:
            raise ValueError(f"upstream {label} differs from fixed digest")
        if label == "panel" and path.stat().st_size != expected["panel_bytes"]:
            raise ValueError("upstream panel byte size changed")
    registry_path = upstream_root / expected["registry_path"]
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    if registry.get("source_commit") != expected["source_commit"]:
        raise ValueError("upstream source commit changed")
    manifest_path = upstream_root / expected["manifest_path"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if set(manifest) != EXPECTED_MANIFEST_KEYS:
        raise ValueError("legacy panel manifest schema has missing or unexpected entries")
    if manifest.get("schema_version") != "direct-batter-pa-panel-manifest-v4":
        raise ValueError("legacy panel manifest schema changed")
    if manifest.get("status") != "DIRECT_BATTER_PA_TIMING_CONTRACT_PASSED_RESEARCH_ONLY":
        raise ValueError("legacy panel manifest status changed")
    if manifest.get("may_2026_opened") is not False or manifest.get("confirmation_2025_opened") is not False:
        raise ValueError("legacy panel protected-data state changed")
    if manifest.get("production_changed") is not False or manifest.get("betting_authorized") is not False:
        raise ValueError("legacy panel safety state changed")
    official_path = str(manifest.get("official", {}).get("path", "")).replace("\\", "/")
    if "2023_2025" not in official_path:
        raise ValueError("expected legacy mixed-year official-source defect was not observed")
    dependency = manifest.get("dependency_identity", {})
    if dependency.get("exact_environment_lock_claimed") is not False:
        raise ValueError("legacy dependency state was silently promoted")
    panel_path = upstream_root / expected["panel_path"]
    panel = pd.read_csv(panel_path, usecols=OFFICIAL_COLUMNS, low_memory=False)
    if len(panel) != expected["panel_rows"]:
        raise ValueError("upstream panel row count changed")
    return manifest, panel


def _canonical_official(frame: pd.DataFrame, *, context: str) -> pd.DataFrame:
    if list(frame.columns) != OFFICIAL_COLUMNS:
        frame = frame.loc[:, OFFICIAL_COLUMNS]
    result = frame.copy()
    for column in ("season", "game_pk", "player_id", "lineup_slot", "out_pa", "out_ab", "out_hits", "out_doubles", "out_triples", "out_hr", "out_bb", "out_k"):
        values = pd.to_numeric(result[column], errors="coerce")
        if values.isna().any() or not values.eq(values.round()).all():
            raise ValueError(f"{context} {column} is invalid")
        result[column] = values.astype("int64")
    dates = pd.to_datetime(result["game_date"], format="%Y-%m-%d", errors="coerce")
    if dates.isna().any() or not dates.dt.strftime("%Y-%m-%d").eq(result["game_date"].astype(str)).all():
        raise ValueError(f"{context} game_date is not canonical ISO")
    if not result["season"].eq(2023).all() or not dates.dt.year.eq(2023).all():
        raise ValueError(f"{context} contains a non-2023 row")
    result["game_date"] = dates.dt.strftime("%Y-%m-%d")
    if result.duplicated(IDENTITY).any():
        raise ValueError(f"{context} identity is duplicated")
    return result.sort_values(IDENTITY, kind="stable").reset_index(drop=True)


def _verify_official_2023(official_root: Path, panel: pd.DataFrame, contract: dict[str, Any]) -> dict[str, Any]:
    expected = contract["permitted_2023_sources"]
    root = official_root.resolve(strict=True)
    manifest = safe_existing_file(root, expected["official_manifest_filename"], context="2023 official manifest")
    if sha256_file(manifest) != expected["official_manifest_sha256"]:
        raise ValueError("2023 official manifest differs from fixed digest")
    daily_root = root / expected["official_daily_directory"]
    if not daily_root.is_dir() or _has_reparse_point(daily_root):
        raise ValueError("2023 official daily directory is missing or unsafe")
    entries = sorted(path for path in daily_root.iterdir() if path.is_file() and path.name.endswith("_statcast.csv"))
    if len(entries) != expected["official_daily_files"]:
        raise ValueError("2023 official daily file count changed")
    parts: list[pd.DataFrame] = []
    hashes: dict[str, str] = {}
    for path in entries:
        match = DAILY_RE.fullmatch(path.name)
        if match is None:
            raise ValueError(f"official daily source name is outside 2023 contract: {path.name}")
        safe = safe_existing_file(
            root, f"{expected['official_daily_directory']}/{path.name}",
            context="2023 official daily source",
        )
        hashes[path.name] = sha256_file(safe)
        part = pd.read_csv(safe, usecols=OFFICIAL_COLUMNS, low_memory=False)
        if not part.empty and not part["game_date"].astype(str).eq(match.group(1)).all():
            raise ValueError(f"official daily source date disagrees with filename: {path.name}")
        parts.append(part)
    source = _canonical_official(pd.concat(parts, ignore_index=True), context="2023 official daily source")
    panel_official = _canonical_official(panel, context="upstream panel official projection")
    if len(source) != expected["official_rows"] or not source.equals(panel_official):
        raise ValueError("2023 daily official source does not exactly match panel official projection")
    tree_lines = [f"{name}\0{hashes[name]}\n" for name in sorted(hashes)]
    tree_sha = hashlib.sha256("".join(tree_lines).encode("utf-8")).hexdigest()
    if tree_sha != expected["official_daily_tree_sha256"]:
        raise ValueError("2023 official daily source tree differs from fixed digest")
    return {"files_rehashed": len(hashes), "rows": len(source), "tree_sha256": tree_sha}


def _verify_raw_2023(raw_root: Path, manifest: dict[str, Any], expected_count: int) -> dict[str, Any]:
    hashes = manifest.get("raw_source_sha256")
    if not isinstance(hashes, dict) or len(hashes) != expected_count:
        raise ValueError("legacy raw Statcast hash map count changed")
    for relative, expected in sorted(hashes.items()):
        normalized = str(relative).replace("\\", "/")
        if not normalized.startswith("2023/"):
            raise ValueError("raw Statcast map contains a non-2023 path")
        path = safe_existing_file(raw_root, normalized, context="2023 raw Statcast source")
        if sha256_file(path) != expected:
            raise ValueError(f"2023 raw Statcast source hash mismatch: {normalized}")
    return {"files_rehashed": len(hashes), "local_bytes_match": True, "transport_receipts_complete": False}


def _verify_zero_pa(upstream_root: Path, contract: dict[str, Any]) -> dict[str, Any]:
    expected = contract["permitted_2023_sources"]
    base = upstream_root / "data/analysis/system_integrity_v2/direct_batter_pa_foundation_v4/zero_pa_receipts"
    manifest_path = safe_existing_file(base, "manifest.json", context="zero-PA receipt manifest")
    evidence_path = safe_existing_file(base, "zero_pa_evidence.csv", context="zero-PA evidence")
    if sha256_file(manifest_path) != expected["zero_pa_manifest_sha256"]:
        raise ValueError("zero-PA receipt manifest differs from fixed digest")
    if sha256_file(evidence_path) != expected["zero_pa_evidence_sha256"]:
        raise ValueError("zero-PA evidence differs from fixed digest")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    receipts = manifest.get("receipts", {})
    if not isinstance(receipts, dict) or len(receipts) != expected["zero_pa_receipts"]:
        raise ValueError("zero-PA receipt count changed")
    for relative, digest in sorted(receipts.items()):
        path = safe_existing_file(base, relative, context="zero-PA raw receipt")
        if sha256_file(path) != digest:
            raise ValueError(f"zero-PA raw receipt hash mismatch: {relative}")
    evidence = pd.read_csv(evidence_path, low_memory=False)
    if len(evidence) != expected["zero_pa_receipts"] or not pd.to_numeric(evidence["season"], errors="raise").eq(2023).all():
        raise ValueError("zero-PA evidence population changed")
    if set(evidence["source_sha256"].astype(str)) != set(receipts.values()):
        raise ValueError("zero-PA evidence is not exactly bound to raw receipts")
    return {"receipts_rehashed": len(receipts), "semantic_rows": len(evidence), "official_response_receipts_complete": True}


def require_complete_authority(result: dict[str, Any]) -> None:
    if result.get("decision") != "SOURCE_AND_DEPENDENCY_AUTHORITY_COMPLETE":
        raise ValueError("2023 source panel authority is incomplete; fitting is forbidden")


def audit(*, upstream_root: Path, raw_root: Path, official_2023_root: Path, contract_path: Path = CONTRACT_PATH) -> dict[str, Any]:
    contract = load_contract(contract_path)
    manifest, panel = _verify_fixed_upstream(upstream_root, contract)
    official = _verify_official_2023(official_2023_root, panel, contract)
    raw = _verify_raw_2023(raw_root, manifest, contract["permitted_2023_sources"]["raw_statcast_files"])
    zero = _verify_zero_pa(upstream_root, contract)
    blockers = [
        "LEGACY_OFFICIAL_SOURCE_HASH_SPANS_2023_2025",
        "RAW_STATCAST_TRANSPORT_RECEIPTS_NOT_DELIVERED",
        "INDEPENDENT_OFFICIAL_TRANSPORT_RECEIPTS_NOT_DELIVERED",
        "EXACT_REPRODUCIBLE_DEPENDENCY_LOCK_NOT_DELIVERED",
        "EXTERNAL_EXPECTED_RELEASE_DIGEST_NOT_DELIVERED",
    ]
    return {
        "schema_version": "direct-batter-pa-source-binding-audit-v1",
        "decision": contract["expected_decision"],
        "research_only": True,
        "betting_authorized": False,
        "model_fitting_permitted": False,
        "local_integrity": {
            "upstream_panel_sha256": contract["upstream_release"]["panel_sha256"],
            "upstream_manifest_sha256": contract["upstream_release"]["manifest_sha256"],
            "upstream_certificate_sha256": contract["upstream_release"]["certificate_sha256"],
            "official_2023": official,
            "raw_statcast_2023": raw,
            "zero_pa": zero,
        },
        "implementation_source_base_commit": IMPLEMENTATION_SOURCE_BASE_COMMIT,
        "implementation_sha256": {
            relative: sha256_file(ROOT / relative) for relative in IMPLEMENTATION_FILES
        },
        "dependency_declaration_sha256": {
            relative: sha256_file(ROOT / relative) for relative in DEPENDENCY_DECLARATIONS
        },
        "dependency_state": {
            "exact_reproducible_lock_available": False,
            "observed_python": os.sys.version,
            "observed_pandas": pd.__version__,
        },
        "blockers": blockers,
        "protected_data": contract["protected_data"],
        "eligible_for_feature_fitting": False,
        "eligible_for_protocol_locking": False,
        "eligible_for_model_consumption": False,
    }


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite source-binding audit: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, sort_keys=True, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream-root", required=True, type=Path)
    parser.add_argument("--raw-root", required=True, type=Path)
    parser.add_argument(
        "--official-2023-root", required=True, type=Path,
        help="season-only training root containing manifest_2023.json and 2023/",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    result = audit(
        upstream_root=args.upstream_root,
        raw_root=args.raw_root,
        official_2023_root=args.official_2023_root,
    )
    if args.output is not None:
        atomic_json(args.output, result)
    print(json.dumps({"decision": result["decision"], "blockers": result["blockers"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
