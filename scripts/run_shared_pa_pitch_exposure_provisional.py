"""Run the finite 2023 provisional pitch-exposure tournament."""

from __future__ import annotations

import argparse
import gzip
import json
import os
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.shared_pa_pitch_exposure_tournament import (  # noqa: E402
    DEVELOPMENT_SEASON,
    run_provisional_pitch_exposure_tournament,
    synthetic_signal_control,
    validate_pitch_exposure_matrix,
)
from src.evaluation.shared_pa_plate_discipline_tournament import (  # noqa: E402
    canonical_sha256,
    sha256_file,
)


EXPECTED_PANEL_SHA256 = "643a4c6533dbe59fc4e6b5e5932683c0877ae0a5d982945c799976d36af1ebf6"
EXPECTED_PANEL_ROWS = 43_740
MATRIX_COLUMNS = (
    "game_date",
    "game_pk",
    "player_id",
    "max_source_date",
    "history_pitch_count",
    "history_release_speed_count",
    "history_release_speed_missing_count",
    "history_release_speed_mean",
    "history_release_speed_sd",
    "history_pfx_x_count",
    "history_pfx_x_missing_count",
    "history_pfx_x_mean",
    "history_pfx_x_sd",
    "history_pfx_z_count",
    "history_pfx_z_missing_count",
    "history_pfx_z_mean",
    "history_pfx_z_sd",
    "history_plate_x_count",
    "history_plate_x_missing_count",
    "history_plate_x_mean",
    "history_plate_x_sd",
    "history_plate_z_count",
    "history_plate_z_missing_count",
    "history_plate_z_mean",
    "history_plate_z_sd",
    "history_pitch_type_denominator",
    "history_pitch_type_missing_count",
    "history_distinct_pitch_types",
    "history_pitch_type_entropy",
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _create_only(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise


def run(
    *,
    panel_path: Path,
    panel_manifest_path: Path,
    matrix_output: Path,
    result_output: Path,
) -> dict[str, object]:
    _require(not matrix_output.exists(), "refusing to overwrite pitch-exposure feature matrix")
    _require(not result_output.exists(), "refusing to overwrite pitch-exposure tournament result")
    _require(sha256_file(panel_path) == EXPECTED_PANEL_SHA256, "corrected 2023 panel identity differs")
    manifest = json.loads(panel_manifest_path.read_text(encoding="utf-8"))
    _require(manifest.get("schema_version") == "direct-batter-pa-panel-manifest-v4", "panel manifest schema differs")
    _require(manifest.get("output", {}).get("sha256") == EXPECTED_PANEL_SHA256, "panel manifest does not bind panel")
    _require(manifest.get("population", {}).get("selection_rows_2024") == 0, "2024 selection rows entered panel")
    _require(manifest.get("confirmation_2025_opened") is False, "2025 confirmation was opened")
    _require(manifest.get("may_2026_opened") is False, "May 2026 was opened")
    panel = pd.read_csv(panel_path)
    _require(len(panel) == EXPECTED_PANEL_ROWS, "corrected 2023 panel row count differs")
    _require(set(pd.to_numeric(panel["season"], errors="raise").astype(int)) == {DEVELOPMENT_SEASON}, "panel season differs")
    validate_pitch_exposure_matrix(panel)
    matrix = panel.loc[:, MATRIX_COLUMNS].copy()
    matrix_bytes = gzip.compress(matrix.to_csv(index=False, lineterminator="\n").encode("utf-8"), compresslevel=9, mtime=0)
    _create_only(matrix_output, matrix_bytes)
    _require(synthetic_signal_control(), "pitch-exposure synthetic-signal control failed")
    result = run_provisional_pitch_exposure_tournament(panel)
    result["input_identity"] = {
        "panel_path": str(panel_path),
        "panel_sha256": EXPECTED_PANEL_SHA256,
        "panel_manifest_path": str(panel_manifest_path),
        "panel_manifest_sha256": sha256_file(panel_manifest_path),
        "feature_order_sha256": canonical_sha256(list(MATRIX_COLUMNS)),
    }
    result["feature_matrix"] = {
        "path": str(matrix_output),
        "rows": int(len(matrix)),
        "bytes": matrix_output.stat().st_size,
        "sha256": sha256_file(matrix_output),
    }
    payload = (json.dumps(result, sort_keys=True, indent=2) + "\n").encode("utf-8")
    _create_only(result_output, payload)
    return {
        "status": result["status"],
        "result_path": str(result_output),
        "result_sha256": sha256_file(result_output),
        "matrix_path": str(matrix_output),
        "matrix_sha256": sha256_file(matrix_output),
        "primary_market_verdicts": {
            market: value["provisional_market_verdict"]
            for market, value in result["primary_markets"].items()
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel-path", required=True, type=Path)
    parser.add_argument("--panel-manifest-path", required=True, type=Path)
    parser.add_argument("--matrix-output", required=True, type=Path)
    parser.add_argument("--result-output", required=True, type=Path)
    print(json.dumps(run(**vars(parser.parse_args())), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
