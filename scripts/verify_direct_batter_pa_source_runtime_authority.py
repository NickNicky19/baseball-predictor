#!/usr/bin/env python3
"""Fail-closed dependency and Linux TLS runtime attestation.

This verifier never performs a network request and cannot authorize one.  It
only proves the exact local dependency/runtime bytes used by the offline source
authority audit.  A separately supplied expected attestation digest is still
required before any future transport implementation may be considered.
"""
from __future__ import annotations

import argparse
import ast
import base64
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import platform
import re
import site
import ssl
import stat
import sys
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "config/direct_batter_pa_source_runtime_authority_v1.json"
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
REQ_RE = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s\\]+)\s*\\?$")
HASH_RE = re.compile(r"--hash=sha256:([0-9a-f]{64})")
FORBIDDEN_ENV = (
    "PYTHONHOME", "PYTHONPATH", "SSL_CERT_FILE", "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "LD_PRELOAD", "LD_LIBRARY_PATH",
    "LD_AUDIT", "OPENSSL_CONF", "OPENSSL_CONF_INCLUDE", "OPENSSL_MODULES",
    "OPENSSL_ENGINES", "PYTHONPLATLIBDIR", "PYTHONCASEOK",
)
REQUIRED_TLS_LIBRARIES = ("libssl", "libcrypto")
TARGET = {
    "os_id": "ubuntu", "os_version_id": "24.04", "machine": "x86_64",
    "python_implementation": "CPython", "python_version": "3.12.3",
}
RUNTIME_REQUIREMENTS = {
    "tls_context_initialization_required", "default_ca_file_or_directory_required",
    "ca_bytes_hashed", "loaded_libssl_and_libcrypto_required",
    "loaded_shared_library_bytes_hashed", "path_injection_environment_forbidden",
    "executable_and_extension_module_bytes_hashed",
    "deterministic_double_observation_required",
    "external_expected_attestation_digest_required_before_network",
}
REQUIRED_MANIFEST_PATHS = {
    ".github/workflows/direct-batter-pa-source-authority-linux.yml",
    "config/direct_batter_pa_source_binding_v1.json",
    "config/direct_batter_pa_source_release_v1.json",
    "config/direct_batter_pa_source_runtime_authority_v1.json",
    "reports/direct_batter_pa_source_runtime_authority_v1.md",
    "requirements-direct-batter-pa-source-authority.in",
    "requirements-direct-batter-pa-source-authority.lock",
    "scripts/build_direct_batter_pa_source_release.py",
    "scripts/check_tracked_secrets.py",
    "scripts/validate_direct_batter_pa_source_binding.py",
    "scripts/verify_direct_batter_pa_source_runtime_authority.py",
    "tests/test_direct_batter_pa_source_binding.py",
    "tests/test_direct_batter_pa_source_release.py",
    "tests/test_direct_batter_pa_source_runtime_authority.py",
}
DEFAULT_MANIFEST = ROOT / "reports/direct_batter_pa_source_runtime_authority_v1_hash_manifest.json"


class RuntimeAuthorityError(ValueError):
    """Runtime or dependency identity is incomplete or contradictory."""


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_link_or_reparse(path: Path) -> bool:
    info = path.lstat()
    attrs = getattr(info, "st_file_attributes", 0)
    return path.is_symlink() or bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _assert_no_link_or_reparse_chain(path: Path, *, floor: Path | None = None) -> None:
    """Reject every existing symlink/reparse point from ``floor`` through ``path``."""
    lexical = path.absolute()
    boundary = floor.absolute() if floor is not None else Path(lexical.anchor)
    try:
        relative = lexical.relative_to(boundary)
    except ValueError as exc:
        raise RuntimeAuthorityError(f"path is outside its required boundary: {path}") from exc
    current = boundary
    if current.exists() and _is_link_or_reparse(current):
        raise RuntimeAuthorityError(f"unsafe symlink/reparse ancestor: {current}")
    for part in relative.parts:
        current = current / part
        if current.exists() and _is_link_or_reparse(current):
            raise RuntimeAuthorityError(f"unsafe symlink/reparse ancestor: {current}")


def _safe_repo_file(relative: str) -> Path:
    rel = Path(relative)
    if rel.is_absolute() or ".." in rel.parts:
        raise RuntimeAuthorityError(f"unsafe repository path: {relative}")
    path = ROOT / rel
    _assert_no_link_or_reparse_chain(path, floor=ROOT)
    if not path.is_file():
        raise RuntimeAuthorityError(f"missing or unsafe repository file: {relative}")
    if ROOT.resolve(strict=True) not in path.resolve(strict=True).parents:
        raise RuntimeAuthorityError(f"repository file escapes root: {relative}")
    return path


def load_policy(path: Path = DEFAULT_POLICY) -> dict[str, Any]:
    if path == DEFAULT_POLICY:
        path = _safe_repo_file("config/direct_batter_pa_source_runtime_authority_v1.json")
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema_version") != "direct-batter-pa-source-runtime-authority-v1":
        raise RuntimeAuthorityError("runtime policy schema changed")
    if value.get("status") != "LOCKED_OFFLINE_RUNTIME_ATTESTATION_NETWORK_BLOCKED":
        raise RuntimeAuthorityError("runtime policy status changed")
    if value.get("research_only") is not True or value.get("betting_authorized") is not False:
        raise RuntimeAuthorityError("runtime policy safety state changed")
    if value.get("model_fitting_permitted") is not False or value.get("network_fetch_authorized") is not False:
        raise RuntimeAuthorityError("runtime policy cannot authorize fitting or network fetch")
    if value.get("target") != TARGET:
        raise RuntimeAuthorityError("runtime target policy changed")
    requirements = value.get("runtime_requirements", {})
    if set(requirements) != RUNTIME_REQUIREMENTS or not all(item is True for item in requirements.values()):
        raise RuntimeAuthorityError("runtime attestation requirements changed or weakened")
    lock = value.get("dependency_lock", {})
    if lock.get("path") != "requirements-direct-batter-pa-source-authority.lock":
        raise RuntimeAuthorityError("dependency lock path changed")
    if not isinstance(lock.get("sha256"), str) or SHA256_RE.fullmatch(lock["sha256"]) is None:
        raise RuntimeAuthorityError("dependency lock digest is invalid")
    audited = value.get("audited_python_files")
    audited_hashes = value.get("audited_python_sha256")
    if not isinstance(audited, list) or len(audited) != len(set(audited)):
        raise RuntimeAuthorityError("audited Python file list is invalid")
    if not isinstance(audited_hashes, dict) or set(audited_hashes) != set(audited):
        raise RuntimeAuthorityError("audited Python hashes do not exactly cover audited files")
    if any(not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None for digest in audited_hashes.values()):
        raise RuntimeAuthorityError("audited Python file digest is invalid")
    local_roots = value.get("local_import_roots")
    if not isinstance(local_roots, list) or local_roots != sorted(set(local_roots)):
        raise RuntimeAuthorityError("local import-root authority is invalid")
    return value


def verify_candidate_manifest(path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    if path == DEFAULT_MANIFEST:
        path = _safe_repo_file("reports/direct_batter_pa_source_runtime_authority_v1_hash_manifest.json")
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema_version") != "direct-batter-pa-source-runtime-authority-hash-manifest-v1":
        raise RuntimeAuthorityError("candidate hash manifest schema changed")
    if value.get("status") != "UNCOMMITTED_CANDIDATE_BYTES_NETWORK_BLOCKED":
        raise RuntimeAuthorityError("candidate hash manifest status changed")
    if value.get("research_only") is not True or value.get("betting_authorized") is not False:
        raise RuntimeAuthorityError("candidate hash manifest safety state changed")
    if value.get("network_fetch_authorized") is not False:
        raise RuntimeAuthorityError("candidate hash manifest cannot authorize network fetch")
    files = value.get("files")
    if not isinstance(files, dict) or set(files) != REQUIRED_MANIFEST_PATHS:
        raise RuntimeAuthorityError("candidate hash manifest does not exactly cover bound files")
    verified: dict[str, dict[str, Any]] = {}
    for relative in sorted(files):
        row = files[relative]
        if not isinstance(row, dict) or set(row) != {"bytes", "sha256"}:
            raise RuntimeAuthorityError(f"invalid candidate hash row: {relative}")
        source = _safe_repo_file(relative)
        observed = {"bytes": source.stat().st_size, "sha256": sha256_file(source)}
        if row != observed:
            raise RuntimeAuthorityError(f"candidate file differs from hash manifest: {relative}")
        verified[relative] = observed
    return {"manifest_path": str(path), "verified_files": verified, "network_fetch_authorized": False}


def parse_hashed_lock(path: Path) -> dict[str, dict[str, Any]]:
    packages: dict[str, dict[str, Any]] = {}
    current: str | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line and not line[0].isspace() and not line.startswith("#"):
            match = REQ_RE.fullmatch(line)
            if match is None:
                raise RuntimeAuthorityError(f"lock contains an unpinned or unsupported requirement: {line}")
            name = re.sub(r"[-_.]+", "-", match.group(1)).lower()
            if name in packages:
                raise RuntimeAuthorityError(f"duplicate locked package: {name}")
            packages[name] = {"version": match.group(2), "hashes": []}
            current = name
        for digest in HASH_RE.findall(line):
            if current is None:
                raise RuntimeAuthorityError("lock hash is not attached to a package")
            packages[current]["hashes"].append(digest)
    if not packages or any(not row["hashes"] for row in packages.values()):
        raise RuntimeAuthorityError("every locked package must have at least one SHA-256")
    return packages


def _import_roots(paths: Iterable[str]) -> set[str]:
    roots: set[str] = set()
    for relative in paths:
        tree = ast.parse(_safe_repo_file(relative).read_text(encoding="utf-8"), filename=relative)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".", 1)[0])
    return roots


def verify_static_authority(policy: dict[str, Any]) -> dict[str, Any]:
    lock_cfg = policy["dependency_lock"]
    lock = _safe_repo_file(lock_cfg["path"])
    if sha256_file(lock) != lock_cfg["sha256"]:
        raise RuntimeAuthorityError("dependency lock differs from fixed digest")
    packages = parse_hashed_lock(lock)
    expected = lock_cfg["exact_packages"]
    observed_versions = {name: row["version"] for name, row in packages.items()}
    if observed_versions != expected:
        raise RuntimeAuthorityError("dependency lock package closure differs from policy")

    audited_hashes: dict[str, str] = {}
    for relative in policy["audited_python_files"]:
        observed_digest = sha256_file(_safe_repo_file(relative))
        if observed_digest != policy["audited_python_sha256"][relative]:
            raise RuntimeAuthorityError(f"audited Python source differs from fixed digest: {relative}")
        audited_hashes[relative] = observed_digest

    roots = _import_roots(policy["audited_python_files"])
    declared_roots = policy["import_roots"]
    local_roots = set(policy["local_import_roots"])
    third_party: set[str] = set()
    unresolved: set[str] = set()
    for root in roots:
        if root in sys.stdlib_module_names:
            continue
        if root in local_roots:
            local = ROOT / root
            _assert_no_link_or_reparse_chain(local, floor=ROOT)
            if not local.exists():
                unresolved.add(root)
            continue
        if root in declared_roots:
            third_party.add(root)
        else:
            unresolved.add(root)
    if unresolved:
        raise RuntimeAuthorityError(f"undeclared import roots: {sorted(unresolved)}")
    if third_party != set(declared_roots):
        raise RuntimeAuthorityError("declared third-party import roots do not match audited files")
    if set(declared_roots.values()) - set(packages):
        raise RuntimeAuthorityError("an imported distribution is absent from the exact lock")
    return {
        "lock_path": lock_cfg["path"],
        "lock_sha256": lock_cfg["sha256"],
        "locked_packages": observed_versions,
        "audited_python_files": policy["audited_python_files"],
        "audited_python_sha256": audited_hashes,
        "observed_import_roots": sorted(roots),
        "third_party_import_roots": sorted(third_party),
        "undeclared_import_roots": [],
        "network_fetch_authorized": False,
    }


def _normalized_distribution_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _distribution_file_identity(distribution: importlib.metadata.Distribution) -> dict[str, Any]:
    files = distribution.files
    if not files:
        raise RuntimeAuthorityError(f"installed distribution has no file manifest: {distribution.metadata['Name']}")
    rows: list[dict[str, Any]] = []
    record_bound = 0
    for relative in sorted(files, key=lambda item: str(item)):
        located = Path(distribution.locate_file(relative))
        if not located.exists():
            raise RuntimeAuthorityError(f"installed distribution file is missing: {relative}")
        _assert_no_link_or_reparse_chain(located)
        if not located.is_file():
            raise RuntimeAuthorityError(f"installed distribution entry is not a file: {relative}")
        digest = sha256_file(located)
        declared_hash = getattr(relative, "hash", None)
        declared = None
        if declared_hash is not None:
            if declared_hash.mode != "sha256":
                raise RuntimeAuthorityError(f"installed distribution uses a non-SHA256 RECORD entry: {relative}")
            declared = declared_hash.value
            encoded = base64.urlsafe_b64encode(bytes.fromhex(digest)).decode("ascii").rstrip("=")
            if encoded != declared:
                raise RuntimeAuthorityError(f"installed distribution file differs from RECORD: {relative}")
            record_bound += 1
        rows.append({
            "path": str(located.resolve(strict=True)),
            "relative_path": str(relative).replace("\\", "/"),
            "bytes": located.stat().st_size,
            "sha256": digest,
            "record_sha256": declared,
        })
    if record_bound == 0:
        raise RuntimeAuthorityError(f"installed distribution has no SHA256-bound files: {distribution.metadata['Name']}")
    return {
        "files": rows,
        "file_count": len(rows),
        "record_bound_file_count": record_bound,
        "aggregate_sha256": hashlib.sha256(canonical_json_bytes(rows)).hexdigest(),
    }


def _runtime_path_injection_files() -> list[str]:
    candidates: set[Path] = set()
    for raw in site.getsitepackages():
        base = Path(raw)
        if not base.is_dir():
            continue
        candidates.update(base.glob("*.pth"))
        candidates.update(base / name for name in ("sitecustomize.py", "usercustomize.py") if (base / name).exists())
    user = Path(site.getusersitepackages())
    if user.is_dir():
        candidates.update(user.glob("*.pth"))
        candidates.update(user / name for name in ("sitecustomize.py", "usercustomize.py") if (user / name).exists())
    return sorted(str(path.resolve(strict=True)) for path in candidates)


def verify_installed_environment(policy: dict[str, Any]) -> dict[str, Any]:
    expected = policy["dependency_lock"]["exact_packages"]
    observed_distributions: dict[str, importlib.metadata.Distribution] = {}
    duplicates: set[str] = set()
    for distribution in importlib.metadata.distributions():
        raw_name = distribution.metadata.get("Name")
        if not raw_name:
            raise RuntimeAuthorityError("installed distribution has no canonical name")
        name = _normalized_distribution_name(raw_name)
        if name in observed_distributions:
            duplicates.add(name)
        observed_distributions[name] = distribution
    if duplicates:
        raise RuntimeAuthorityError(f"duplicate installed distributions: {sorted(duplicates)}")
    if set(observed_distributions) != set(expected):
        missing = sorted(set(expected) - set(observed_distributions))
        extra = sorted(set(observed_distributions) - set(expected))
        raise RuntimeAuthorityError(f"installed distribution closure mismatch: missing={missing}, extra={extra}")
    injected_files = _runtime_path_injection_files()
    if injected_files:
        raise RuntimeAuthorityError(f"runtime path injection files are present: {injected_files}")

    installed: dict[str, Any] = {}
    distribution_paths: dict[str, set[Path]] = {}
    for name, version in expected.items():
        distribution = observed_distributions[name]
        observed = distribution.version
        if observed != version:
            raise RuntimeAuthorityError(f"installed distribution mismatch: {name} {observed} != {version}")
        identity = _distribution_file_identity(distribution)
        installed[name] = {"version": observed, **identity}
        distribution_paths[name] = {Path(row["path"]) for row in identity["files"]}
    for root, distribution in policy["import_roots"].items():
        spec = importlib.util.find_spec(root)
        if spec is None or not spec.origin:
            raise RuntimeAuthorityError(f"required import cannot resolve: {root}")
        origin = Path(spec.origin).resolve(strict=True)
        if origin not in distribution_paths[distribution]:
            raise RuntimeAuthorityError(f"resolved import origin is outside its locked distribution: {root}")
        installed[distribution]["import_root"] = root
        installed[distribution]["import_origin"] = str(origin)
    return {
        "distributions": dict(sorted(installed.items())),
        "distribution_count": len(installed),
        "path_injection_files": [],
    }


def _os_release() -> dict[str, str]:
    path = Path("/etc/os-release")
    if not path.is_file():
        raise RuntimeAuthorityError("Linux OS release identity is unavailable")
    rows: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.startswith("#"):
            continue
        key, value = line.split("=", 1)
        rows[key] = value.strip().strip('"')
    return rows


def _file_identity(path: Path) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    if not resolved.is_file():
        raise RuntimeAuthorityError(f"runtime identity path is not a file: {resolved}")
    return {"path": str(resolved), "bytes": resolved.stat().st_size, "sha256": sha256_file(resolved)}


def _ca_identity() -> dict[str, Any]:
    defaults = ssl.get_default_verify_paths()
    files: list[dict[str, Any]] = []
    if defaults.cafile and Path(defaults.cafile).is_file():
        files.append(_file_identity(Path(defaults.cafile)))
    if defaults.capath and Path(defaults.capath).is_dir():
        capath = Path(defaults.capath).resolve(strict=True)
        seen: set[str] = set()
        for entry in sorted(capath.iterdir(), key=lambda item: item.name):
            if not (entry.is_file() or entry.is_symlink()):
                continue
            resolved = entry.resolve(strict=True)
            key = str(resolved)
            if key in seen:
                continue
            seen.add(key)
            files.append(_file_identity(resolved))
    if not files:
        raise RuntimeAuthorityError("no default CA bytes were available to hash")
    aggregate = hashlib.sha256(canonical_json_bytes(files)).hexdigest()
    return {"cafile": defaults.cafile, "capath": defaults.capath, "files": files, "aggregate_sha256": aggregate}


def _loaded_shared_libraries() -> list[dict[str, Any]]:
    maps = Path("/proc/self/maps")
    if not maps.is_file():
        raise RuntimeAuthorityError("loaded shared-library map is unavailable")
    paths: set[str] = set()
    for line in maps.read_text(encoding="utf-8").splitlines():
        parts = line.split(maxsplit=5)
        if len(parts) == 6 and parts[5].startswith("/") and ".so" in Path(parts[5]).name:
            paths.add(str(Path(parts[5]).resolve(strict=True)))
    rows = [_file_identity(Path(path)) for path in sorted(paths)]
    names = [Path(row["path"]).name.lower() for row in rows]
    for required in REQUIRED_TLS_LIBRARIES:
        if not any(required in name for name in names):
            raise RuntimeAuthorityError(f"required TLS shared library is not loaded: {required}")
    return rows


def linux_runtime_attestation(policy: dict[str, Any]) -> dict[str, Any]:
    target = policy["target"]
    release = _os_release()
    observed = {
        "os_id": release.get("ID"), "os_version_id": release.get("VERSION_ID"),
        "machine": platform.machine(), "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
    }
    if observed != target:
        raise RuntimeAuthorityError(f"runtime target mismatch: {observed!r}")
    injected = {name: os.environ[name] for name in FORBIDDEN_ENV if os.environ.get(name)}
    if injected:
        raise RuntimeAuthorityError(f"forbidden runtime path/TLS environment injection: {sorted(injected)}")
    if site.ENABLE_USER_SITE:
        raise RuntimeAuthorityError("user-site package loading is enabled")
    ssl.create_default_context()
    static = verify_static_authority(policy)
    installed = verify_installed_environment(policy)
    extension_modules = {}
    for name in ("_ssl", "_hashlib"):
        spec = importlib.util.find_spec(name)
        if spec is None or not spec.origin:
            raise RuntimeAuthorityError(f"runtime extension module is unavailable: {name}")
        extension_modules[name] = _file_identity(Path(spec.origin))
    return {
        "schema_version": "direct-batter-pa-source-linux-runtime-attestation-v1",
        "status": "EXACT_LOCAL_RUNTIME_ATTESTED_NETWORK_STILL_BLOCKED",
        "research_only": True,
        "betting_authorized": False,
        "model_fitting_permitted": False,
        "network_fetch_authorized": False,
        "external_expected_attestation_digest_supplied": False,
        "target": observed,
        "python_executable": _file_identity(Path(sys.executable)),
        "python_build": list(platform.python_build()),
        "python_compiler": platform.python_compiler(),
        "dependency_authority": static,
        "installed_distributions": installed,
        "extension_modules": extension_modules,
        "openssl_version": ssl.OPENSSL_VERSION,
        "ca_identity": _ca_identity(),
        "loaded_shared_libraries": _loaded_shared_libraries(),
        "site_user_enabled": site.ENABLE_USER_SITE,
        "forbidden_environment_present": [],
        "remaining_external_blocker": "EXTERNAL_EXPECTED_ATTESTATION_DIGEST_AND_SOURCE_AUTHORITIES_REQUIRED",
    }


def write_attestation(path: Path, policy: dict[str, Any]) -> str:
    payload = canonical_json_bytes(linux_runtime_attestation(policy))
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--check-static", action="store_true")
    parser.add_argument("--check-installed", action="store_true")
    parser.add_argument("--check-manifest", action="store_true")
    parser.add_argument("--write-linux-attestation", type=Path)
    args = parser.parse_args()
    policy = load_policy(args.policy)
    if not (args.check_static or args.check_installed or args.check_manifest or args.write_linux_attestation):
        raise RuntimeAuthorityError("an explicit verification mode is required")
    if args.check_static:
        print(json.dumps(verify_static_authority(policy), sort_keys=True))
    if args.check_installed:
        print(json.dumps(verify_installed_environment(policy), sort_keys=True))
    if args.check_manifest:
        print(json.dumps(verify_candidate_manifest(), sort_keys=True))
    if args.write_linux_attestation:
        print(write_attestation(args.write_linux_attestation, policy))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
