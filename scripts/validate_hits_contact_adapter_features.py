#!/usr/bin/env python3
"""Validate paired feature snapshots for the Hits contact-adapter candidate."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


CANDIDATE_ID = "hits_point_in_time_hitter_contact_adapter_v1"


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fail(message: str) -> None:
    raise ValueError(message)


def load_json(path: Path) -> Any:
    if not path.is_file():
        fail(f"missing required artifact: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(f"{path.suffix}.tmp")
    temp.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temp.replace(path)


def bundle_key(bundle: dict[str, Any]) -> tuple[int, int]:
    hitter = bundle.get("hitter") or {}
    player = hitter.get("player") or {}
    game = hitter.get("game") or {}
    return int(game["game_pk"]), int(player["mlb_id"])


def verify_snapshot(root: Path, game_date: str) -> tuple[list[dict], dict]:
    directory = root / game_date
    manifest_path = directory / "manifest.json"
    manifest = load_json(manifest_path)
    if manifest.get("schema_version") != "feature-bundle-manifest-v1":
        fail(f"{manifest_path}: unexpected schema")
    if manifest.get("game_date") != game_date:
        fail(f"{manifest_path}: game_date mismatch")
    artifact = (manifest.get("artifacts") or {}).get("json") or {}
    bundle_path = directory / str(artifact.get("path") or "")
    expected_hash = str(artifact.get("sha256") or "")
    if sha256_path(bundle_path) != expected_hash:
        fail(f"{bundle_path}: bundle hash mismatch")
    bundles = load_json(bundle_path)
    if not isinstance(bundles, list) or len(bundles) != int(
        manifest.get("bundle_count", -1)
    ):
        fail(f"{bundle_path}: bundle count mismatch")
    keys = [bundle_key(bundle) for bundle in bundles]
    if len(keys) != len(set(keys)):
        fail(f"{bundle_path}: duplicate game/player feature key")
    return bundles, {
        "manifest_path": str(manifest_path),
        "manifest_sha256": sha256_path(manifest_path),
        "bundle_path": str(bundle_path),
        "bundle_sha256": expected_hash,
        "bundle_count": len(bundles),
        "source_tree_sha256": (manifest.get("provenance") or {}).get(
            "source_tree_sha256"
        ),
        "config_sha256": (manifest.get("provenance") or {}).get("config_sha256"),
    }


def validate(
    *,
    source_manifest: Path,
    baseline_root: Path,
    candidate_root: Path,
) -> dict[str, Any]:
    source = load_json(source_manifest)
    dates = source.get("official_date_universe")
    if not isinstance(dates, list) or not dates:
        fail("source manifest lacks an official_date_universe")
    dates = [str(value) for value in dates]
    if any(value.startswith("2026-05-") for value in dates):
        fail("sealed May date appeared in feature validation source")
    if sorted(path.name for path in baseline_root.iterdir() if path.is_dir()) != dates:
        fail("baseline feature date universe differs from source manifest")
    if sorted(path.name for path in candidate_root.iterdir() if path.is_dir()) != dates:
        fail("candidate feature date universe differs from source manifest")

    status_counts: Counter[str] = Counter()
    fallback_reasons: Counter[str] = Counter()
    evidence_rows = 0
    applied_rows = 0
    date_evidence: dict[str, Any] = {}
    baseline_source_trees: set[str] = set()
    candidate_source_trees: set[str] = set()

    for game_date in dates:
        baseline, baseline_evidence = verify_snapshot(baseline_root, game_date)
        candidate, candidate_evidence = verify_snapshot(candidate_root, game_date)
        baseline_source_trees.add(str(baseline_evidence["source_tree_sha256"]))
        candidate_source_trees.add(str(candidate_evidence["source_tree_sha256"]))
        if baseline_evidence["source_tree_sha256"] != candidate_evidence[
            "source_tree_sha256"
        ]:
            fail(f"{game_date}: source-tree mismatch between arms")
        baseline_by_key = {bundle_key(row): row for row in baseline}
        candidate_by_key = {bundle_key(row): row for row in candidate}
        if set(baseline_by_key) != set(candidate_by_key):
            fail(f"{game_date}: feature identity differs between arms")

        for key, baseline_bundle in baseline_by_key.items():
            baseline_metadata = baseline_bundle.get("metadata") or {}
            baseline_rich = baseline_metadata.get("rich_features") or {}
            if (
                "hits_contact_adapter" in baseline_metadata
                or "contact_xba_fitted" in baseline_rich
            ):
                fail(f"{game_date} {key}: candidate evidence entered baseline")

            candidate_bundle = candidate_by_key[key]
            metadata = candidate_bundle.get("metadata") or {}
            adapter = metadata.get("hits_contact_adapter")
            rich = metadata.get("rich_features") or {}
            if not isinstance(adapter, dict):
                fail(f"{game_date} {key}: missing candidate adapter evidence")
            if adapter.get("candidate_id") != CANDIDATE_ID:
                fail(f"{game_date} {key}: candidate identity mismatch")
            status = str(adapter.get("status"))
            status_counts[status] += 1
            evidence_rows += 1
            if status == "adapter_applied":
                applied_rows += 1
                if adapter.get("reason") is not None:
                    fail(f"{game_date} {key}: applied row has fallback reason")
                if int(adapter.get("player_bip", 0)) < 1:
                    fail(f"{game_date} {key}: applied row lacks BIP evidence")
                if int(adapter.get("window_games", -1)) != 30:
                    fail(f"{game_date} {key}: wrong recency window")
                if float(adapter.get("prior_strength_bip", -1)) != 135.0:
                    fail(f"{game_date} {key}: wrong fitted prior")
                first = str(adapter.get("first_evidence_date"))
                last = str(adapter.get("last_evidence_date"))
                if not first < game_date or not last < game_date or first > last:
                    fail(f"{game_date} {key}: evidence chronology failed")
                bip = float(adapter["player_bip"])
                raw = float(adapter["raw_player_xba"])
                anchor = float(adapter["daily_anchor_xba"])
                fitted = float(adapter["fitted_contact_xba"])
                expected = (bip * raw + 135.0 * anchor) / (bip + 135.0)
                if not all(math.isfinite(value) for value in (raw, anchor, fitted)):
                    fail(f"{game_date} {key}: non-finite adapter value")
                if abs(fitted - expected) > 1e-12:
                    fail(f"{game_date} {key}: shrinkage arithmetic mismatch")
                if rich.get("contact_xba_fitted") != fitted:
                    fail(f"{game_date} {key}: simulator handoff mismatch")
                if float((candidate_bundle.get("statcast") or {}).get("xba")) != fitted:
                    fail(f"{game_date} {key}: profile handoff mismatch")
            elif status == "baseline_fallback":
                reason = str(adapter.get("reason") or "")
                if not reason:
                    fail(f"{game_date} {key}: fallback lacks a reason label")
                fallback_reasons[reason] += 1
                if "contact_xba_fitted" in rich:
                    fail(f"{game_date} {key}: fallback emitted a fitted value")
            else:
                fail(f"{game_date} {key}: unexpected adapter status {status!r}")

        date_evidence[game_date] = {
            "baseline": baseline_evidence,
            "candidate": candidate_evidence,
        }

    if evidence_rows == 0 or applied_rows == 0:
        fail("candidate produced no applied feature evidence")
    if baseline_source_trees != candidate_source_trees or len(baseline_source_trees) != 1:
        fail("paired features do not share one exact source tree")
    return {
        "schema_version": "hits-contact-adapter-feature-certificate-v1",
        "status": "valid",
        "market": "hits",
        "betting_authorized": False,
        "may_2026_read": False,
        "source_manifest": {
            "path": str(source_manifest),
            "sha256": sha256_path(source_manifest),
        },
        "dates": dates,
        "date_count": len(dates),
        "feature_rows": evidence_rows,
        "status_counts": dict(sorted(status_counts.items())),
        "fallback_reasons": dict(sorted(fallback_reasons.items())),
        "source_tree_sha256": next(iter(baseline_source_trees)),
        "date_evidence": date_evidence,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-manifest", required=True, type=Path)
    parser.add_argument("--baseline-features", required=True, type=Path)
    parser.add_argument("--candidate-features", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = validate(
        source_manifest=args.source_manifest,
        baseline_root=args.baseline_features,
        candidate_root=args.candidate_features,
    )
    write_json_atomic(report, args.out)
    print("HITS CONTACT ADAPTER FEATURES VALID")
    print(f"  dates: {report['date_count']}")
    print(f"  feature_rows: {report['feature_rows']}")
    print(f"  status_counts: {report['status_counts']}")
    print(f"  fallback_reasons: {report['fallback_reasons']}")
    print(f"  wrote: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
