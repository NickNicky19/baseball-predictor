from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import verify_direct_batter_pa_source_runtime_authority as runtime


ROOT = Path(__file__).resolve().parents[1]


def policy() -> dict:
    return runtime.load_policy()


def test_policy_is_research_only_and_network_blocked() -> None:
    value = policy()
    assert value["research_only"] is True
    assert value["betting_authorized"] is False
    assert value["model_fitting_permitted"] is False
    assert value["network_fetch_authorized"] is False
    assert value["runtime_requirements"]["external_expected_attestation_digest_required_before_network"] is True


def test_policy_mutations_cannot_weaken_runtime_or_network_boundary(tmp_path: Path) -> None:
    value = policy()
    value["runtime_requirements"]["loaded_shared_library_bytes_hashed"] = False
    changed = tmp_path / "policy.json"
    changed.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(runtime.RuntimeAuthorityError, match="changed or weakened"):
        runtime.load_policy(changed)
    value = policy()
    value["network_fetch_authorized"] = True
    changed.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(runtime.RuntimeAuthorityError, match="cannot authorize"):
        runtime.load_policy(changed)


def test_exact_hashed_lock_and_import_closure_pass() -> None:
    result = runtime.verify_static_authority(policy())
    assert result["lock_sha256"] == "0847ea7c8fe05649a94293b2adbba0164f10f420ca984ecf35f105f1aabc2a1f"
    assert result["third_party_import_roots"] == ["pandas", "pytest"]
    assert result["undeclared_import_roots"] == []
    assert result["network_fetch_authorized"] is False
    assert result["audited_python_sha256"] == policy()["audited_python_sha256"]


def test_static_check_rejects_audited_source_byte_mutation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = "scripts/build_direct_batter_pa_source_release.py"
    changed = tmp_path / "changed.py"
    changed.write_bytes((ROOT / target).read_bytes() + b"# mutation\n")
    original = runtime._safe_repo_file
    monkeypatch.setattr(runtime, "_safe_repo_file", lambda relative: changed if relative == target else original(relative))
    with pytest.raises(runtime.RuntimeAuthorityError, match="audited Python source differs"):
        runtime.verify_static_authority(policy())


def test_lock_parser_rejects_unhashed_and_unpinned_requirements(tmp_path: Path) -> None:
    unhashed = tmp_path / "unhashed.lock"
    unhashed.write_text("pandas==3.0.5\n", encoding="utf-8")
    with pytest.raises(runtime.RuntimeAuthorityError, match="at least one SHA-256"):
        runtime.parse_hashed_lock(unhashed)
    unpinned = tmp_path / "unpinned.lock"
    unpinned.write_text("pandas>=3\n", encoding="utf-8")
    with pytest.raises(runtime.RuntimeAuthorityError, match="unpinned or unsupported"):
        runtime.parse_hashed_lock(unpinned)


def test_static_check_rejects_lock_byte_mutation(tmp_path: Path) -> None:
    source = ROOT / "requirements-direct-batter-pa-source-authority.lock"
    changed = tmp_path / source.name
    changed.write_bytes(source.read_bytes() + b"# mutation\n")
    value = policy()
    value["dependency_lock"] = dict(value["dependency_lock"])
    value["dependency_lock"]["path"] = changed.name
    original = runtime._safe_repo_file
    runtime._safe_repo_file = lambda relative: changed if relative == changed.name else original(relative)
    try:
        with pytest.raises(runtime.RuntimeAuthorityError, match="fixed digest"):
            runtime.verify_static_authority(value)
    finally:
        runtime._safe_repo_file = original


def test_static_check_rejects_new_undeclared_import(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime, "_import_roots", lambda paths: {"json", "pandas", "pytest", "requests"})
    with pytest.raises(runtime.RuntimeAuthorityError, match="undeclared import roots"):
        runtime.verify_static_authority(policy())


def test_installed_environment_rejects_version_drift(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    expected = policy()["dependency_lock"]["exact_packages"]
    owned = tmp_path / "owned.py"
    owned.write_text("# owned\n", encoding="utf-8")
    distributions = [
        SimpleNamespace(metadata={"Name": name}, version="0.0.0" if name == "pandas" else version)
        for name, version in expected.items()
    ]
    monkeypatch.setattr(runtime.importlib.metadata, "distributions", lambda: distributions)
    monkeypatch.setattr(runtime, "_runtime_path_injection_files", lambda: [])
    monkeypatch.setattr(runtime, "_distribution_file_identity", lambda distribution: {
        "files": [{"path": str(owned)}], "file_count": 1,
        "record_bound_file_count": 1, "aggregate_sha256": "0" * 64,
    })
    with pytest.raises(runtime.RuntimeAuthorityError, match="installed distribution mismatch"):
        runtime.verify_installed_environment(policy())


def _named_distributions(*names: str):
    return [SimpleNamespace(metadata={"Name": name}) for name in names]


def test_installed_environment_requires_exact_distribution_closure(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = list(policy()["dependency_lock"]["exact_packages"])
    monkeypatch.setattr(runtime.importlib.metadata, "distributions", lambda: _named_distributions(*expected[:-1]))
    with pytest.raises(runtime.RuntimeAuthorityError, match="closure mismatch"):
        runtime.verify_installed_environment(policy())
    monkeypatch.setattr(runtime.importlib.metadata, "distributions", lambda: _named_distributions(*expected, "unexpected"))
    with pytest.raises(runtime.RuntimeAuthorityError, match="closure mismatch"):
        runtime.verify_installed_environment(policy())


def test_installed_environment_rejects_import_shadow_and_path_injection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = policy()
    expected = value["dependency_lock"]["exact_packages"]
    owned = tmp_path / "owned.py"
    shadow = tmp_path / "shadow.py"
    owned.write_text("# owned\n", encoding="utf-8")
    shadow.write_text("# shadow\n", encoding="utf-8")
    distributions = [SimpleNamespace(metadata={"Name": name}, version=version) for name, version in expected.items()]
    monkeypatch.setattr(runtime.importlib.metadata, "distributions", lambda: distributions)
    monkeypatch.setattr(runtime, "_distribution_file_identity", lambda distribution: {
        "files": [{"path": str(owned)}], "file_count": 1,
        "record_bound_file_count": 1, "aggregate_sha256": "0" * 64,
    })
    monkeypatch.setattr(runtime, "_runtime_path_injection_files", lambda: [])
    monkeypatch.setattr(runtime.importlib.util, "find_spec", lambda root: SimpleNamespace(origin=str(shadow)))
    with pytest.raises(runtime.RuntimeAuthorityError, match="origin is outside"):
        runtime.verify_installed_environment(value)
    monkeypatch.setattr(runtime, "_runtime_path_injection_files", lambda: [str(tmp_path / "evil.pth")])
    with pytest.raises(runtime.RuntimeAuthorityError, match="path injection files"):
        runtime.verify_installed_environment(value)


def test_distribution_file_identity_rejects_record_hash_mismatch(tmp_path: Path) -> None:
    payload = tmp_path / "package.py"
    payload.write_text("trusted = True\n", encoding="utf-8")

    class Relative:
        hash = SimpleNamespace(mode="sha256", value="invalid")

        def __str__(self) -> str:
            return "package.py"

    class Distribution:
        metadata = {"Name": "example"}
        files = [Relative()]

        @staticmethod
        def locate_file(relative) -> Path:
            return payload

    with pytest.raises(runtime.RuntimeAuthorityError, match="differs from RECORD"):
        runtime._distribution_file_identity(Distribution())


def test_linux_attestation_rejects_non_target_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime, "_os_release", lambda: {"ID": "ubuntu", "VERSION_ID": "22.04"})
    with pytest.raises(runtime.RuntimeAuthorityError, match="runtime target mismatch"):
        runtime.linux_runtime_attestation(policy())


def test_linux_attestation_rejects_path_injection(monkeypatch: pytest.MonkeyPatch) -> None:
    value = policy()
    value["target"] = {
        "os_id": "ubuntu",
        "os_version_id": "24.04",
        "machine": runtime.platform.machine(),
        "python_implementation": runtime.platform.python_implementation(),
        "python_version": runtime.platform.python_version(),
    }
    monkeypatch.setattr(runtime, "_os_release", lambda: {"ID": "ubuntu", "VERSION_ID": "24.04"})
    monkeypatch.setenv("PYTHONPATH", "/untrusted")
    with pytest.raises(runtime.RuntimeAuthorityError, match="environment injection"):
        runtime.linux_runtime_attestation(value)


@pytest.mark.parametrize(
    "name",
    [
        "LD_PRELOAD", "LD_LIBRARY_PATH", "LD_AUDIT", "OPENSSL_CONF",
        "OPENSSL_CONF_INCLUDE", "OPENSSL_MODULES", "OPENSSL_ENGINES",
        "PYTHONPLATLIBDIR", "PYTHONCASEOK", "SSL_CERT_FILE", "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
    ],
)
def test_linux_attestation_rejects_every_loader_tls_and_ca_injection(
    name: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = policy()
    value["target"] = {
        "os_id": "ubuntu", "os_version_id": "24.04",
        "machine": runtime.platform.machine(),
        "python_implementation": runtime.platform.python_implementation(),
        "python_version": runtime.platform.python_version(),
    }
    monkeypatch.setattr(runtime, "_os_release", lambda: {"ID": "ubuntu", "VERSION_ID": "24.04"})
    monkeypatch.setenv(name, "/untrusted")
    with pytest.raises(runtime.RuntimeAuthorityError, match="environment injection"):
        runtime.linux_runtime_attestation(value)


def test_linux_attestation_rejects_enabled_user_site(monkeypatch: pytest.MonkeyPatch) -> None:
    value = policy()
    value["target"] = {
        "os_id": "ubuntu", "os_version_id": "24.04",
        "machine": runtime.platform.machine(),
        "python_implementation": runtime.platform.python_implementation(),
        "python_version": runtime.platform.python_version(),
    }
    monkeypatch.setattr(runtime, "_os_release", lambda: {"ID": "ubuntu", "VERSION_ID": "24.04"})
    for name in runtime.FORBIDDEN_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(runtime.site, "ENABLE_USER_SITE", True)
    with pytest.raises(runtime.RuntimeAuthorityError, match="user-site"):
        runtime.linux_runtime_attestation(value)


def test_loaded_library_check_requires_tls_libraries(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_maps = tmp_path / "maps"
    fake_maps.write_text("", encoding="utf-8")
    original_path = runtime.Path

    def path_factory(value: str):
        return fake_maps if value == "/proc/self/maps" else original_path(value)

    monkeypatch.setattr(runtime, "Path", path_factory)
    with pytest.raises(runtime.RuntimeAuthorityError, match="required TLS shared library"):
        runtime._loaded_shared_libraries()


def test_attestation_schema_cannot_authorize_network() -> None:
    source = (ROOT / "scripts/verify_direct_batter_pa_source_runtime_authority.py").read_text(encoding="utf-8")
    assert "network_fetch_authorized\": False" in source
    assert "external_expected_attestation_digest_supplied\": False" in source
    assert "urllib.request" not in source
    assert "requests" not in source


def test_policy_json_is_canonical_parseable() -> None:
    parsed = json.loads((ROOT / "config/direct_batter_pa_source_runtime_authority_v1.json").read_text(encoding="utf-8"))
    assert parsed == policy()


def test_repo_file_rejects_reparse_ancestor(monkeypatch: pytest.MonkeyPatch) -> None:
    original = runtime._is_link_or_reparse
    unsafe = runtime.ROOT / "config"
    monkeypatch.setattr(runtime, "_is_link_or_reparse", lambda path: path == unsafe or original(path))
    with pytest.raises(runtime.RuntimeAuthorityError, match="symlink/reparse ancestor"):
        runtime._safe_repo_file("config/direct_batter_pa_source_runtime_authority_v1.json")


def test_manifest_exactly_binds_every_runtime_gate_input(tmp_path: Path) -> None:
    result = runtime.verify_candidate_manifest()
    assert set(result["verified_files"]) == runtime.REQUIRED_MANIFEST_PATHS
    changed = json.loads((ROOT / "reports/direct_batter_pa_source_runtime_authority_v1_hash_manifest.json").read_text(encoding="utf-8"))
    changed["files"].pop("scripts/check_tracked_secrets.py")
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(runtime.RuntimeAuthorityError, match="exactly cover"):
        runtime.verify_candidate_manifest(path)


def test_ci_uses_clean_no_pip_environment_and_isolated_pytest() -> None:
    workflow = (ROOT / ".github/workflows/direct-batter-pa-source-authority-linux.yml").read_text(encoding="utf-8")
    assert "venv --without-pip" in workflow
    assert "PYTEST_DISABLE_PLUGIN_AUTOLOAD" in workflow
    assert workflow.count("unset LD_LIBRARY_PATH") == 5
    assert " -I -B -m pytest" in workflow
    assert "--check-manifest" in workflow
    assert '"reports/direct_batter_pa_source_runtime_authority_v1_hash_manifest.json"' in workflow
    assert '"scripts/check_tracked_secrets.py"' in workflow
