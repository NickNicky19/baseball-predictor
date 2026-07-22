import hashlib
import json

import pytest

from scripts.prepare_forward_evidence_scope import (
    validate_config_bound_runtime_dependencies,
)
from src.evaluation.forward_evidence_era import ForwardEvidenceEraError


def _write_config(root, kbb, pa):
    config = {
        "pa_simulator": {
            "kbb_artifact_path": "data/kbb.json",
            "kbb_artifact_sha256": hashlib.sha256(kbb).hexdigest(),
        },
        "base_running": {
            "pa_distribution_path": "data/pa.json",
            "pa_distribution_sha256": hashlib.sha256(pa).hexdigest(),
        },
    }
    path = root / "config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    settlement_dir = root / "config"
    settlement_dir.mkdir(exist_ok=True)
    rules = root / "data" / "rules.json"
    rules.write_bytes(b"rules")
    (settlement_dir / "shadow_draftkings_hits_reference_settlement.json").write_text(
        json.dumps(
            {
                "rule_evidence_path": "data/rules.json",
                "rule_evidence_sha256": hashlib.sha256(b"rules").hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    return path


def test_release_preflight_requires_every_hash_bound_runtime_dependency(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    kbb, pa = b"kbb", b"pa"
    (data / "kbb.json").write_bytes(kbb)
    (data / "pa.json").write_bytes(pa)
    config = _write_config(tmp_path, kbb, pa)

    verified = validate_config_bound_runtime_dependencies(config, root=tmp_path)
    assert set(verified) == {"data/kbb.json", "data/pa.json", "data/rules.json"}

    (data / "kbb.json").unlink()
    with pytest.raises(ForwardEvidenceEraError, match="dependency is missing"):
        validate_config_bound_runtime_dependencies(config, root=tmp_path)


def test_release_preflight_rejects_artifact_drift(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    kbb, pa = b"kbb", b"pa"
    (data / "kbb.json").write_bytes(kbb)
    (data / "pa.json").write_bytes(pa)
    config = _write_config(tmp_path, kbb, pa)
    (data / "pa.json").write_bytes(b"mutated")

    with pytest.raises(ForwardEvidenceEraError, match="hash mismatch"):
        validate_config_bound_runtime_dependencies(config, root=tmp_path)


def test_release_preflight_requires_reference_rule_evidence(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    kbb, pa = b"kbb", b"pa"
    (data / "kbb.json").write_bytes(kbb)
    (data / "pa.json").write_bytes(pa)
    config = _write_config(tmp_path, kbb, pa)
    (data / "rules.json").unlink()

    with pytest.raises(ForwardEvidenceEraError, match="dependency is missing"):
        validate_config_bound_runtime_dependencies(config, root=tmp_path)
