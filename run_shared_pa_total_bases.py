#!/usr/bin/env python3
"""Generate the separate, unpromoted shared-PA Total Bases comparator archive.

The normal ``run_slate.py`` path remains the frozen production baseline.  This
entry point adds only the structural Total Bases output permission locked by
the shared-PA comparator contract and writes to a separate required directory.
It never applies learned corrections, computes edges, or authorizes betting.
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import date
from pathlib import Path

from src.evaluation.shared_pa_comparator_capture import load_comparator_contract
from src.learning.prediction_archive import PredictionArchive
from src.models.total_bases_contract import shared_pa_forward_candidate_config
from src.prediction import DailyPredictor
from src.utils.errors import ConfigError, DataFetchError, PredictorError
from src.utils.model_version import model_version
from src.utils.provenance import sha256_json


ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "config/config.kbb.json"
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


def _config(loaded_contract: dict) -> dict:
    try:
        frozen = json.loads(DEFAULT_CONFIG.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Cannot load frozen config: {DEFAULT_CONFIG}") from exc
    candidate = shared_pa_forward_candidate_config(frozen)
    binding = loaded_contract["contract"]["frozen_formula"]
    if (
        sha256_json(candidate) != binding["total_bases_extension_effective_config_sha256"]
        or model_version(candidate) != binding["total_bases_extension_model_version"]
    ):
        raise ConfigError("Total Bases extension config differs from its locked contract")
    return candidate


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
        predictor = DailyPredictor(config=_config(loaded))
        archive_dir = Path(args.archive_dir).resolve()
        predictor.outcome_recorder.archive = PredictionArchive(
            archive_dir=str(archive_dir), project_root=ROOT
        )
        if args.refresh:
            predictor.mlb_api.clear_cache(args.date)
        result = predictor.predict(
            args.date,
            hitter_categories=("total_bases",),
            include_pitchers=False,
            persist_features=False,
            archive_predictions=True,
            capture_prediction_provenance=True,
            apply_corrections=False,
            include_edges=False,
            use_projected_lineups=True,
        )
        return EXIT_OK if result.hitter_projections else EXIT_NO_DATA
    except ConfigError:
        logging.exception("Total Bases comparator configuration failed")
        return EXIT_CONFIG
    except (DataFetchError, PredictorError):
        logging.exception("Total Bases comparator generation failed")
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
