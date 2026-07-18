#!/usr/bin/env python3
"""Offline mutations for the runner's HR level-mapping binding."""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from run_gate_reconstruct import (  # noqa: E402
    load_dates_file,
    validate_hr_level_mapping_source,
)


BASE = ROOT / "data/analysis/hr_over_contract_v1/pre2026_a3_2_migration_v2"
PROTOCOL = BASE / "level_mapping_protocol_v4.json"
DATES = BASE / "reconstruct_dates_2025_v4.json"
PA = BASE / "pa_distribution_fit_2023_2024.json"
CONFIG = ROOT / "config/config.json"


def args_for(**changes: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "hr_level_mapping_protocol": str(PROTOCOL),
        "chronological_protocol": None,
        "dates_file": str(DATES),
        "pa_distribution_artifact": str(PA),
        "feature_snapshot_dir": str(BASE / "smoke/features"),
        "outcomes_out": str(BASE / "smoke/outcomes.csv"),
        "fail_on_flags": True,
        "require_statcast_profiles": True,
        "config": str(CONFIG),
        "simulation_seed": 17,
        "smoke_date": "2025-03-27",
    }
    values.update(changes)
    return argparse.Namespace(**values)


def config_for(pa: Path = PA) -> dict:
    payload = json.loads(CONFIG.read_text(encoding="utf-8"))
    payload.setdefault("base_running", {})["pa_distribution_path"] = str(pa)
    payload.setdefault("simulation", {})["random_seed"] = 17
    return payload


def must_fail(args: argparse.Namespace, config: dict, dates: list[str], label: str) -> None:
    try:
        validate_hr_level_mapping_source(args, config=config, dates=dates)
    except ValueError:
        print(f"[OK] MUTATION {label} fails")
        return
    raise AssertionError(f"mutation unexpectedly passed: {label}")


def main() -> int:
    smoke_dates = load_dates_file(DATES, smoke_date="2025-03-27")
    lock = validate_hr_level_mapping_source(
        args_for(), config=config_for(), dates=smoke_dates
    )
    assert lock and lock["run_kind"] == "smoke"
    print("[OK] exact one-date smoke binding validates")

    full_dates = load_dates_file(DATES)
    full_args = args_for(smoke_date=None)
    lock = validate_hr_level_mapping_source(
        full_args, config=config_for(), dates=full_dates
    )
    assert lock and lock["run_kind"] == "full" and len(full_dates) == 24
    print("[OK] exact 24-date full binding validates")

    must_fail(args_for(simulation_seed=18), config_for(), smoke_dates, "wrong seed")
    must_fail(
        args_for(config=str(ROOT / "config/config.hr_batted_ball.json")),
        config_for(),
        smoke_dates,
        "wrong model config",
    )
    must_fail(
        args_for(pa_distribution_artifact=str(ROOT / "data/learning/pa_distribution_pre2026.json")),
        config_for(),
        smoke_dates,
        "wrong PA artifact",
    )
    must_fail(args_for(fail_on_flags=False), config_for(), smoke_dates, "disabled fail-on-flags")
    must_fail(args_for(outcomes_out=None), config_for(), smoke_dates, "missing outcomes")
    must_fail(
        args_for(smoke_date="2025-03-27"),
        config_for(),
        ["2025-04-02"],
        "smoke date/output mismatch",
    )

    bad_config = config_for()
    bad_config["base_running"]["pa_distribution_path"] = "data/learning/pa_distribution_pre2026.json"
    must_fail(args_for(), bad_config, smoke_dates, "effective PA mismatch")

    print("\n9/9")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
