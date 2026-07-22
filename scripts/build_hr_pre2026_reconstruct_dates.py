#!/usr/bin/env python3
"""Build an outcome-blind, evenly spaced 2025 HR reconstruction universe."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.utils.provenance import sha256_file


SCHEMA = "hr-pre2026-reconstruct-dates-v3"


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def select_evenly_spaced_done_dates(
    manifest: dict, count: int, *, builder_schema: str = "a3.1"
) -> list[str]:
    if manifest.get("season") != 2025 or manifest.get("builder_schema") != builder_schema:
        raise ValueError(f"date source must be the 2025 {builder_schema} training manifest")
    records = manifest.get("dates")
    if not isinstance(records, dict):
        raise ValueError("training manifest dates must be a mapping")
    dates = sorted(
        date for date, row in records.items()
        if (
            isinstance(row, dict)
            and row.get("status") == "done"
            and int(row.get("hitter_rows", 0)) > 0
        )
    )
    if len(dates) < count or count < 4 or count % 2:
        raise ValueError("need an even count >=4 no larger than the done-date universe")
    positions = [round(index * (len(dates) - 1) / (count - 1)) for index in range(count)]
    selected = [dates[position] for position in positions]
    if len(set(selected)) != count:
        raise ValueError("even-spacing algorithm produced duplicate dates")
    return selected


def select_evenly_spaced_reconstructible_dates(
    manifest: dict, count: int, *, builder_schema: str = "a3.1"
) -> list[str]:
    """Select dates after the first completed regular-season source date.

    The preserved model's canonical reconstruction requires at least one usable
    pre-date current-season Statcast profile. On the first regular-season date
    that history is structurally impossible, so the expensive runner correctly
    refuses its all-fallback slate. This is an outcome-blind input boundary, not
    a performance filter.
    """
    records = manifest.get("dates") if isinstance(manifest, dict) else None
    if manifest.get("season") != 2025 or manifest.get("builder_schema") != builder_schema:
        raise ValueError(f"date source must be the 2025 {builder_schema} training manifest")
    if not isinstance(records, dict):
        raise ValueError("training manifest dates must be a mapping")
    done = sorted(
        date
        for date, row in records.items()
        if (
            isinstance(row, dict)
            and row.get("status") == "done"
            and int(row.get("hitter_rows", 0)) > 0
        )
    )
    if len(done) < 2:
        raise ValueError("need an earlier completed date before any reconstructible date")
    eligible = done[1:]
    shadow_manifest = dict(manifest)
    shadow_manifest["dates"] = {date: records[date] for date in eligible}
    return select_evenly_spaced_done_dates(
        shadow_manifest, count, builder_schema=builder_schema
    )


def select_evenly_spaced_available_dates(
    manifest: dict,
    availability: dict,
    count: int,
    *,
    builder_schema: str = "a3.1",
) -> list[str]:
    """Select only dates proven compatible with the preserved runner gate."""
    if availability.get("schema_version") != "hr-pre2026-input-availability-v1":
        raise ValueError("unrecognized input-availability schema")
    if availability.get("status") != "OUTCOME_BLIND_MODEL_INPUT_AVAILABILITY":
        raise ValueError("input-availability status is not certified")
    if availability.get("uses_outcomes_for_availability") is not False:
        raise ValueError("outcomes cannot select reconstruction dates")
    if availability.get("may_2026_opened") is not False:
        raise ValueError("May 2026 must remain sealed")
    records = manifest.get("dates") if isinstance(manifest, dict) else None
    if manifest.get("season") != 2025 or manifest.get("builder_schema") != builder_schema:
        raise ValueError(f"date source must be the 2025 {builder_schema} training manifest")
    if availability.get("builder_schema", "a3.1") != builder_schema:
        raise ValueError("availability builder schema differs from the training manifest")
    if not isinstance(records, dict):
        raise ValueError("training manifest dates must be a mapping")
    available_records = availability.get("dates")
    if not isinstance(available_records, dict):
        raise ValueError("availability dates must be a mapping")
    eligible = sorted(
        date
        for date, row in records.items()
        if (
            isinstance(row, dict)
            and row.get("status") == "done"
            and int(row.get("hitter_rows", 0)) > 0
            and isinstance(available_records.get(date), dict)
            and available_records[date].get("runner_requirement_satisfied") is True
            and available_records[date].get("all_fallback") is False
            and int(available_records[date].get("active_player_rows", -1))
            == int(row.get("hitter_rows", 0))
        )
    )
    if eligible != availability.get("available_dates"):
        raise ValueError("availability summary differs from its per-date evidence")
    shadow_manifest = dict(manifest)
    shadow_manifest["dates"] = {date: records[date] for date in eligible}
    return select_evenly_spaced_done_dates(
        shadow_manifest, count, builder_schema=builder_schema
    )


def build(
    manifest_path: Path,
    pa_path: Path,
    availability_path: Path,
    count: int,
    *,
    builder_schema: str = "a3.1",
) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    availability = json.loads(availability_path.read_text(encoding="utf-8"))
    dates = select_evenly_spaced_available_dates(
        manifest, availability, count, builder_schema=builder_schema
    )
    split = count // 2
    return {
        "schema_version": SCHEMA,
        "status": "LOCKED_OUTCOME_BLIND_DATE_UNIVERSE",
        "builder_schema": builder_schema,
        "dates": dates,
        "calibration_dates": dates[:split],
        "confirmation_dates": dates[split:],
        "selection_rule": (
            "Sort manifest dates whose status is exactly done and hitter_rows is positive; "
            "retain only dates whose hash-bound outcome-blind availability audit proves at "
            "least one active hitter meets the preserved Statcast gate and whose active-row "
            "count equals the manifest; select round(i*(N-1)/(K-1)) for i=0..K-1; "
            "first K/2 are calibration and last K/2 are confirmation."
        ),
        "source_manifest": {"path": str(manifest_path), "sha256": sha256_file(manifest_path)},
        "pa_distribution": {"path": str(pa_path), "sha256": sha256_file(pa_path)},
        "input_availability": {
            "path": str(availability_path),
            "sha256": sha256_file(availability_path),
            "required_schema": "hr-pre2026-input-availability-v1",
            "required_status": "OUTCOME_BLIND_MODEL_INPUT_AVAILABILITY",
        },
        "baseline_history_rule": (
            "at least one active hitter has >=8 non-null Statcast event rows inside the "
            "preserved inclusive 45-day fetch window"
        ),
        "uses_outcomes_for_date_selection": False,
        "may_2026_opened": False,
        "betting_authorized": False,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", type=Path, default=Path("data/training/manifest_2025.json"))
    ap.add_argument("--builder-schema", choices=("a3.1", "a3.2"), default="a3.1")
    ap.add_argument(
        "--pa-distribution", type=Path,
        default=Path("data/analysis/hr_over_contract_v1/pre2026_fitted_batted_ball_mapping_v1/pa_distribution_fit_2023_2024.json"),
    )
    ap.add_argument("--count", type=int, default=24)
    ap.add_argument(
        "--input-availability",
        type=Path,
        default=Path(
            "data/analysis/hr_over_contract_v1/pre2026_fitted_batted_ball_mapping_v1/"
            "input_availability_2025_v1.json"
        ),
    )
    ap.add_argument(
        "--out", type=Path,
        default=Path("data/analysis/hr_over_contract_v1/pre2026_fitted_batted_ball_mapping_v1/reconstruct_dates_2025_v3.json"),
    )
    args = ap.parse_args(argv)
    payload = build(
        args.manifest.resolve(),
        args.pa_distribution.resolve(),
        args.input_availability.resolve(),
        args.count,
        builder_schema=args.builder_schema,
    )
    _atomic_json(args.out, payload)
    print(f"locked {len(payload['dates'])} outcome-blind dates")
    print(f"calibration {payload['calibration_dates'][0]} .. {payload['calibration_dates'][-1]}")
    print(f"confirmation {payload['confirmation_dates'][0]} .. {payload['confirmation_dates'][-1]}")
    print(f"wrote {args.out}")
    print(f"sha256 {sha256_file(args.out)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
