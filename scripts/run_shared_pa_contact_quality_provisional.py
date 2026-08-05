"""Run the finite 2023 provisional contact-quality tournament."""

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

from src.evaluation.shared_pa_contact_quality_tournament import (  # noqa: E402
    DEVELOPMENT_SEASON,
    run_provisional_contact_tournament,
    synthetic_signal_control,
    validate_contact_matrix,
)
from src.evaluation.shared_pa_plate_discipline_tournament import (  # noqa: E402
    canonical_sha256,
    sha256_file,
)


EXPECTED_PANEL_SHA256 = "643a4c6533dbe59fc4e6b5e5932683c0877ae0a5d982945c799976d36af1ebf6"
EXPECTED_CONTACT_SHA256 = "a7882dccda3d36b37957261903a8a35644b9d9118c5f80ecb7085ef1cb5bbb89"
EXPECTED_CONTACT_MANIFEST_SHA256 = "a5ec5637ae181838f09429b5846cc8b575561c6202d83ee165cfb215e008abd1"
EXPECTED_ROWS = 43_740


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
    contact_panel_path: Path,
    contact_manifest_path: Path,
    matrix_output: Path,
    result_output: Path,
) -> dict[str, object]:
    _require(not matrix_output.exists(), "refusing to overwrite contact feature matrix")
    _require(not result_output.exists(), "refusing to overwrite contact tournament result")
    _require(sha256_file(panel_path) == EXPECTED_PANEL_SHA256, "corrected 2023 panel identity differs")
    _require(
        sha256_file(contact_panel_path) == EXPECTED_CONTACT_SHA256,
        "strict-prior contact panel identity differs",
    )
    _require(
        sha256_file(contact_manifest_path) == EXPECTED_CONTACT_MANIFEST_SHA256,
        "contact manifest identity differs",
    )
    manifest = json.loads(contact_manifest_path.read_text(encoding="utf-8"))
    _require(
        manifest.get("schema_version") == "shared-pa-contact-quality-panel-manifest-v1",
        "contact manifest schema differs",
    )
    _require(
        manifest.get("status") == "CONTACT_QUALITY_TIMING_CONTRACT_BUILT_RESEARCH_ONLY",
        "contact timing contract is not research-only passed",
    )
    _require(
        manifest.get("output", {}).get("sha256") == EXPECTED_CONTACT_SHA256,
        "contact manifest does not bind its panel",
    )
    boundaries = manifest.get("protected_boundaries", {})
    _require(
        boundaries.get("2024_opened") is False
        and boundaries.get("2025_opened") is False
        and boundaries.get("may_2026_opened") is False,
        "contact manifest opened protected evidence",
    )
    _require(boundaries.get("outcomes_scored") is False, "contact source build scored outcomes")
    panel = pd.read_csv(panel_path)
    contact = pd.read_csv(contact_panel_path)
    _require(len(panel) == len(contact) == EXPECTED_ROWS, "contact input row count differs")
    _require(
        set(pd.to_numeric(panel["season"], errors="raise").astype(int))
        == set(pd.to_numeric(contact["season"], errors="raise").astype(int))
        == {DEVELOPMENT_SEASON},
        "contact input season differs",
    )
    identity = ["game_date", "game_pk", "player_id"]
    _require(
        panel[identity].reset_index(drop=True).equals(contact[identity].reset_index(drop=True)),
        "contact input identity differs from outcome panel",
    )
    validate_contact_matrix(contact)
    matrix_bytes = gzip.compress(
        contact.to_csv(index=False, lineterminator="\n").encode("utf-8"),
        compresslevel=9,
        mtime=0,
    )
    _create_only(matrix_output, matrix_bytes)
    _require(synthetic_signal_control(), "contact synthetic-signal control failed")
    result = run_provisional_contact_tournament(panel, contact)
    result["input_identity"] = {
        "panel_path": str(panel_path),
        "panel_sha256": EXPECTED_PANEL_SHA256,
        "contact_panel_path": str(contact_panel_path),
        "contact_panel_sha256": EXPECTED_CONTACT_SHA256,
        "contact_manifest_path": str(contact_manifest_path),
        "contact_manifest_sha256": EXPECTED_CONTACT_MANIFEST_SHA256,
        "contact_feature_order_sha256": canonical_sha256(list(contact.columns)),
    }
    result["feature_matrix"] = {
        "path": str(matrix_output),
        "rows": int(len(contact)),
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
    parser.add_argument("--contact-panel-path", required=True, type=Path)
    parser.add_argument("--contact-manifest-path", required=True, type=Path)
    parser.add_argument("--matrix-output", required=True, type=Path)
    parser.add_argument("--result-output", required=True, type=Path)
    print(json.dumps(run(**vars(parser.parse_args())), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
