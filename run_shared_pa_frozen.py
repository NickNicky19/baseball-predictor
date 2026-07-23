#!/usr/bin/env python3
"""Generate the unchanged frozen Hits/HR archive for shared-PA capture.

This is deliberately separate from ``run_slate.py``.  It writes no feature
cache into the clean release checkout, requests no odds or outcomes, applies
no corrections, and includes projected lineups so the locked T-4 comparator
contract can validate the resulting decision-time archive.
"""

from __future__ import annotations

import argparse
import logging
from datetime import date
from pathlib import Path

from src.evaluation.shared_pa_comparator_capture import load_comparator_contract
from src.learning.prediction_archive import PredictionArchive
from src.prediction import DailyPredictor
from src.utils.errors import ConfigError, DataFetchError, PredictorError
from src.utils.model_version import model_version
from src.utils.provenance import sha256_json


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config/config.kbb.json"
CONTRACT_PATH = ROOT / "config/shared_pa_comparator_capture_v1.json"
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NO_DATA = 2
EXIT_CONFIG = 3


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, metavar="YYYY-MM-DD")
    parser.add_argument("--archive-dir", required=True, metavar="PATH")
    parser.add_argument("--refresh", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        slate = date.fromisoformat(args.date)
    except ValueError:
        return EXIT_CONFIG
    if slate.year == 2026 and slate.month == 5:
        return EXIT_CONFIG
    try:
        loaded = load_comparator_contract(root=ROOT, path=CONTRACT_PATH)
        binding = loaded["contract"]["frozen_formula"]
        predictor = DailyPredictor(config_path=CONFIG_PATH)
        if (
            model_version(predictor.config) != binding["model_version"]
            or sha256_json(predictor.config) != binding["effective_config_sha256"]
        ):
            raise ConfigError("frozen predictor differs from comparator contract")
        archive_dir = Path(args.archive_dir).resolve()
        predictor.outcome_recorder.archive = PredictionArchive(
            archive_dir=str(archive_dir), project_root=ROOT
        )
        if args.refresh:
            predictor.mlb_api.clear_cache(args.date)
        result = predictor.predict(
            args.date,
            hitter_categories=("hits", "hrr", "home_runs"),
            include_pitchers=True,
            persist_features=False,
            archive_predictions=True,
            capture_prediction_provenance=True,
            apply_corrections=False,
            include_edges=False,
            use_projected_lineups=True,
        )
        return EXIT_OK if result.hitter_projections else EXIT_NO_DATA
    except ConfigError:
        logging.exception("Frozen shared-PA comparator configuration failed")
        return EXIT_CONFIG
    except (DataFetchError, PredictorError):
        logging.exception("Frozen shared-PA comparator generation failed")
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
