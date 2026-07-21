"""Mutation guards for output-affecting model identity."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path

from src.utils.model_version import model_version


def test_pa_simulator_change_forks_model_version():
    frozen = {
        "season": 2026,
        "simulation": {"n_sims": 8000},
        "pa_simulator": {"use_fitted_kbb": False},
    }
    candidate = deepcopy(frozen)
    candidate["pa_simulator"] = {
        "use_fitted_kbb": True,
        "kbb_artifact_sha256": "a" * 64,
    }

    assert model_version(candidate) != model_version(frozen)


def test_operational_logging_change_does_not_fork_model_version():
    first = {"season": 2026, "logging": {"level": "INFO"}}
    second = {"season": 2026, "logging": {"level": "DEBUG"}}

    assert model_version(first) == model_version(second)


def test_current_configs_bind_the_exact_pa_distribution_and_have_distinct_identity():
    repo = Path(__file__).resolve().parents[1]
    configs = [
        json.loads((repo / "config" / name).read_text(encoding="utf-8"))
        for name in ("config.json", "config.kbb.json")
    ]
    for config in configs:
        base_running = config["base_running"]
        artifact = repo / Path(base_running["pa_distribution_path"])
        assert artifact.is_file()
        assert hashlib.sha256(artifact.read_bytes()).hexdigest() == base_running[
            "pa_distribution_sha256"
        ]

    assert model_version(configs[0]) != model_version(configs[1])
