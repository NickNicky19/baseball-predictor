#!/usr/bin/env python3
"""Offline, mutation-oriented checks for fitted PA and K/BB provenance."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from run_gate_reconstruct import _validate_fitted_artifact_chronology  # noqa: E402
from src.models.dataclasses import LeagueBaselines  # noqa: E402
from src.prediction.prop_engine import PropEngine  # noqa: E402
from src.simulation.pa_simulator import PASimulatorConfig  # noqa: E402


class Failure(Exception):
    pass


def check(label: str, condition: bool) -> None:
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        raise Failure(label)


def must_fail(label: str, fn) -> None:
    try:
        fn()
    except (ValueError, FileNotFoundError, json.JSONDecodeError):
        check(label, True)
        return
    check(label, False)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def build_pa_config(block: dict) -> PASimulatorConfig:
    fake = type("FakeEngine", (), {})()
    fake.league = LeagueBaselines()
    fake.config = {"pa_simulator": block}
    return PropEngine._build_pa_config(fake)


def main() -> int:
    print("FITTED-ARTIFACT CONTRACT")
    try:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            kbb = root / "kbb.json"
            pa = root / "pa.json"
            kbb_payload = {
                "provenance": {"fit_universe_date_max": "2025-09-28"},
                "fits": {
                    "K": {
                        "intercept": -2.7, "hitter_season": 3.9,
                        "hitter_recent": 0.3, "pitcher": 0.02,
                    },
                    "BB": {
                        "intercept": -3.2, "hitter_season": 5.5,
                        "hitter_recent": 1.3, "pitcher": 0.03,
                    },
                },
            }
            write(kbb, kbb_payload)
            write(pa, {
                "provenance": {"date_max": "2025-09-28"},
                "by_lineup_slot": {"1": {"4": 1.0}},
            })

            block = {
                "use_fitted_kbb": True,
                "kbb_artifact_path": str(kbb),
                "kbb_artifact_sha256": digest(kbb),
            }
            cfg = build_pa_config(block)
            check("runtime consumes the artifact coefficient", cfg.kbb_bb_intercept == -3.2)

            kbb_payload["fits"]["BB"]["intercept"] = -4.4
            write(kbb, kbb_payload)
            changed = build_pa_config({**block, "kbb_artifact_sha256": digest(kbb)})
            check(
                "mutation: changing the artifact changes the served coefficient",
                changed.kbb_bb_intercept == -4.4,
            )
            must_fail(
                "mutation: changing the artifact without its bound hash fails",
                lambda: build_pa_config(block),
            )
            must_fail(
                "fitted mode without an artifact fails closed",
                lambda: build_pa_config({"use_fitted_kbb": True}),
            )
            must_fail(
                "direct config coefficients cannot become a second truth source",
                lambda: build_pa_config({
                    **block,
                    "kbb_artifact_sha256": digest(kbb),
                    "kbb_k_intercept": -99.0,
                }),
            )
            legacy = build_pa_config({})
            check(
                "artifact repair is inert when fitted K/BB is disabled",
                asdict(legacy) == asdict(PASimulatorConfig.from_league(LeagueBaselines())),
            )

            chronology = {
                "base_running": {"pa_distribution_path": str(pa)},
                "pa_simulator": {
                    "use_fitted_kbb": True,
                    "kbb_artifact_path": str(kbb),
                    "kbb_artifact_sha256": digest(kbb),
                },
            }
            _validate_fitted_artifact_chronology(chronology, ["2026-03-25"])
            check("pre-target PA and K/BB artifacts pass chronology", True)

            write(pa, {
                "provenance": {"date_max": "2026-03-25"},
                "by_lineup_slot": {"1": {"4": 1.0}},
            })
            must_fail(
                "mutation: PA artifact containing the target date fails",
                lambda: _validate_fitted_artifact_chronology(
                    chronology, ["2026-03-25"]
                ),
            )

            write(pa, {
                "provenance": {"date_max": "2025-09-28"},
                "by_lineup_slot": {"1": {"4": 1.0}},
            })
            kbb_payload["provenance"]["fit_universe_date_max"] = "2026-03-25"
            write(kbb, kbb_payload)
            chronology["pa_simulator"]["kbb_artifact_sha256"] = digest(kbb)
            must_fail(
                "mutation: K/BB artifact containing the target date fails",
                lambda: _validate_fitted_artifact_chronology(
                    chronology, ["2026-03-25"]
                ),
            )

        repo = Path(__file__).resolve().parents[1]
        actual_config = json.loads(
            (repo / "config" / "config.kbb.json").read_text(encoding="utf-8")
        )
        actual_cfg = build_pa_config(actual_config["pa_simulator"])
        actual_artifact = json.loads(
            (repo / actual_config["pa_simulator"]["kbb_artifact_path"])
            .read_text(encoding="utf-8")
        )
        check(
            "repository candidate serves the hash-bound BB intercept",
            actual_cfg.kbb_bb_intercept
            == actual_artifact["fits"]["BB"]["intercept"],
        )
        actual_config["base_running"]["pa_distribution_path"] = str(
            repo / "data" / "learning" / "pa_distribution_pre2026.json"
        )
        _validate_fitted_artifact_chronology(actual_config, ["2026-03-25"])
        check("repository repair is chronology-safe for March 2026", True)
    except Failure:
        return 1

    print("\n11/11")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
