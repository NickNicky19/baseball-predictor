from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from scripts.validate_shared_pa_contact_quality_panel import validate
from src.evaluation.shared_pa_contact_quality import FEATURE_COLUMNS, validate_contact_feature_row


def _empty_valid_row() -> dict[str, object]:
    from src.evaluation.shared_pa_contact_quality import contact_quality_features, empty_prepared_source

    return contact_quality_features(
        empty_prepared_source(), player_id=1, target_date="2023-04-01"
    )


def test_empty_history_row_is_explicit_and_valid() -> None:
    row = _empty_valid_row()
    assert row["history_contact_bip"] == 0
    assert row["history_contact_ev50_mean"] is None
    validate_contact_feature_row(row)


def test_validator_rejects_outcome_column_before_row_validation(tmp_path: Path) -> None:
    output = tmp_path / "output"
    output.mkdir()
    row = {"season": 2023, "game_date": "2023-04-01", "game_pk": 1, "player_id": 2, **_empty_valid_row(), "out_hr": 1}
    frame = pd.DataFrame([row])
    panel = output / "contact_quality_panel_2023.csv.gz"
    panel.write_bytes(gzip.compress(frame.to_csv(index=False).encode(), mtime=0))
    manifest = {
        "schema_version": "shared-pa-contact-quality-panel-manifest-v1",
        "status": "CONTACT_QUALITY_TIMING_CONTRACT_BUILT_RESEARCH_ONLY",
        "output": {"rows": 1, "bytes": panel.stat().st_size, "sha256": hashlib.sha256(panel.read_bytes()).hexdigest()},
        "protected_boundaries": {
            "2024_opened": False, "2025_opened": False, "may_2026_opened": False,
            "outcomes_scored": False, "production_changed": False, "betting_authorized": False,
        },
    }
    (output / "contact_quality_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="column surface changed"):
        validate(output_dir=output)


def test_manifest_hash_mutation_fails_closed(tmp_path: Path) -> None:
    output = tmp_path / "output"
    output.mkdir()
    panel = output / "contact_quality_panel_2023.csv.gz"
    panel.write_bytes(b"mutated")
    manifest = {
        "schema_version": "shared-pa-contact-quality-panel-manifest-v1",
        "status": "CONTACT_QUALITY_TIMING_CONTRACT_BUILT_RESEARCH_ONLY",
        "output": {"rows": 43740, "bytes": len(b"mutated"), "sha256": "0" * 64},
        "protected_boundaries": {},
    }
    (output / "contact_quality_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        validate(output_dir=output)
