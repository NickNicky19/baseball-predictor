#!/usr/bin/env python3
"""Validate the emitted original-starter PA probability artifact."""
from __future__ import annotations

import argparse
import copy
import json
import math
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.hr_pre2026_pa_refit import load_protocol, sha256  # noqa: E402


def validate_artifact(payload: dict[str, Any], protocol: dict[str, Any]) -> dict[str, Any]:
    provenance = payload.get("provenance") or {}
    training = protocol["inputs"]["certified_training"]
    if provenance.get("source_sha256") != training["sha256"]:
        raise ValueError("PA artifact source hash mismatch")
    if Path(str(provenance.get("source"))).as_posix() != Path(training["path"]).as_posix():
        raise ValueError("PA artifact source path mismatch")
    if provenance.get("fit_before_exclusive") != protocol["fit_before_exclusive"]:
        raise ValueError("PA artifact fit boundary mismatch")
    if provenance.get("sanity_profile") != protocol["sanity_profile"]:
        raise ValueError("PA artifact sanity profile mismatch")
    if str(provenance.get("date_max")) >= protocol["fit_before_exclusive"]:
        raise ValueError("PA artifact includes post-fit chronology")
    season_sanity = provenance.get("season_sanity") or {}
    if set(season_sanity) != {"2023", "2024"}:
        raise ValueError("PA artifact fit seasons mismatch")
    n_by_slot = provenance.get("n_rows_by_slot") or {}
    if set(n_by_slot) != {str(i) for i in range(1, 10)}:
        raise ValueError("PA artifact slot counts incomplete")
    if sum(int(v) for v in n_by_slot.values()) != int(provenance.get("n_rows", -1)):
        raise ValueError("PA artifact fitted row count mismatch")
    if min(int(v) for v in n_by_slot.values()) < 5000:
        raise ValueError("PA artifact slot evidence too thin")

    slots = payload.get("by_lineup_slot") or {}
    if set(slots) != {str(i) for i in range(1, 10)}:
        raise ValueError("PA artifact distributions incomplete")

    def check_dist(dist: dict[str, Any], label: str) -> float:
        if not dist:
            raise ValueError(f"empty PA distribution: {label}")
        total = 0.0
        mean = 0.0
        for key, value in dist.items():
            pa = int(key)
            prob = float(value)
            if pa < 0 or not math.isfinite(prob) or prob < 0:
                raise ValueError(f"invalid PA probability: {label}")
            total += prob
            mean += pa * prob
        if not math.isclose(total, 1.0, rel_tol=0, abs_tol=1e-12):
            raise ValueError(f"PA probabilities do not sum to one: {label}")
        return mean

    means = [check_dist(slots[str(slot)], f"slot {slot}") for slot in range(1, 10)]
    check_dist(payload.get("pooled") or {}, "pooled")
    if not all(means[i] >= means[i + 1] for i in range(8)):
        raise ValueError("PA slot means are not monotone")
    return payload


def validate(protocol_path: Path, report_out: Path | None) -> dict[str, Any]:
    protocol = load_protocol(protocol_path, verify_files=True)
    artifact_path = ROOT / protocol["output"]["path"]
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    validate_artifact(payload, protocol)
    report = {
        "status": "VALID_PA_REFIT_ORIGINAL_STARTERS_A3_2",
        "artifact_path": protocol["output"]["path"],
        "artifact_sha256": sha256(artifact_path),
        "protocol_sha256": sha256(protocol_path),
        "n_rows": payload["provenance"]["n_rows"],
        "date_min": payload["provenance"]["date_min"],
        "date_max": payload["provenance"]["date_max"],
        "sanity_profile": payload["provenance"]["sanity_profile"],
        "may_2026_opened": False,
        "betting_authorized": False,
    }
    if report_out is not None:
        tmp = report_out.with_suffix(report_out.suffix + ".tmp")
        tmp.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        tmp.replace(report_out)
    return report


def self_test(protocol_path: Path, artifact_path: Path) -> int:
    protocol = load_protocol(protocol_path, verify_files=True)
    original = json.loads(artifact_path.read_text(encoding="utf-8"))
    validate_artifact(original, protocol)
    print("[OK] production PA artifact passes")
    changes = [
        ("source hash", lambda p: p["provenance"].update(source_sha256="0" * 64)),
        ("fit chronology", lambda p: p["provenance"].update(date_max="2025-01-01")),
        ("population profile", lambda p: p["provenance"].update(sanity_profile="legacy_final_occupants")),
        ("missing slot", lambda p: p["by_lineup_slot"].pop("9")),
        ("probability sum", lambda p: p["by_lineup_slot"]["1"].update({"4": 0.9})),
    ]
    for label, mutate in changes:
        candidate = copy.deepcopy(original)
        mutate(candidate)
        try:
            validate_artifact(candidate, protocol)
        except ValueError:
            print(f"[OK] MUTATION {label} fails")
        else:
            raise AssertionError(f"mutation passed: {label}")
    print("6/6")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--report-out", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    protocol = args.protocol.resolve()
    if args.self_test:
        loaded = load_protocol(protocol, verify_files=True)
        return self_test(protocol, ROOT / loaded["output"]["path"])
    report = validate(protocol, args.report_out.resolve() if args.report_out else None)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
