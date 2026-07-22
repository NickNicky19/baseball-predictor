"""Immutable scope contracts for an operational smoke and a forward evidence era.

The operational smoke proves that the deployed lifecycle works.  It is never
economic evidence.  A later forward era can begin only from a clean release
whose bound files still match the certified readiness report and only after a
completed smoke certificate exists.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import re
import ssl
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

from src.evaluation.execution_product_contracts import validate_execution_product_contracts
from src.evaluation.forward_evidence_boundary import load_forward_evidence_boundary
from src.utils.provenance import sha256_file


SCOPE_SCHEMA = "forward-shadow-evidence-scope-v2"
SMOKE_CERTIFICATE_SCHEMA = "forward-operational-smoke-certificate-v2"
RUNTIME_MANIFEST_SCHEMA = "forward-runtime-manifest-v1"
READINESS_STATUS = (
    "FULL_LOCAL_LIFECYCLE_GUARDS_VALID_PRIMARY_COLLECTOR_NOT_DEPLOYED_NO_FORWARD_EVIDENCE"
)


class ForwardEvidenceEraError(ValueError):
    """Raised when smoke and economic evidence could mix or provenance drifts."""


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _scope_digest(payload: Mapping[str, Any]) -> str:
    body = dict(payload)
    body.pop("scope_sha256", None)
    return hashlib.sha256(_canonical(body)).hexdigest()


def _runtime_digest(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _distribution_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _runtime_fingerprint() -> dict[str, Any]:
    """Return a deterministic, secret-free fingerprint of the active runtime."""

    executable = Path(sys.executable).resolve()
    if not executable.is_file():
        raise ForwardEvidenceEraError("Python executable is unavailable")
    distributions: dict[str, str] = {}
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata.get("Name")
        if not name:
            continue
        normalized = _distribution_name(str(name))
        version = str(distribution.version)
        previous = distributions.get(normalized)
        if previous is not None and previous != version:
            raise ForwardEvidenceEraError(
                f"installed distribution has conflicting versions: {normalized}"
            )
        distributions[normalized] = version
    return {
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "python_cache_tag": str(sys.implementation.cache_tag),
        "python_executable_name": executable.name,
        "python_executable_sha256": sha256_file(executable),
        "byteorder": sys.byteorder,
        "platform_system": platform.system(),
        "platform_release": platform.release(),
        "platform_version": platform.version(),
        "platform_machine": platform.machine(),
        "openssl_version": ssl.OPENSSL_VERSION,
        "installed_distributions": [
            {"name": name, "version": distributions[name]}
            for name in sorted(distributions)
        ],
    }


def build_runtime_manifest(*, created_at_utc: str) -> dict[str, Any]:
    fingerprint = _runtime_fingerprint()
    return {
        "schema_version": RUNTIME_MANIFEST_SCHEMA,
        "created_at_utc": str(created_at_utc),
        "contains_secrets": False,
        "fingerprint": fingerprint,
        "fingerprint_sha256": _runtime_digest(fingerprint),
    }


def validate_runtime_manifest(
    path: str | Path,
    *,
    require_current_runtime: bool = True,
) -> dict[str, Any]:
    """Require exact schema/hash and, for an active scope, the running runtime."""

    payload = _json(Path(path), "runtime manifest")
    required = {
        "schema_version",
        "created_at_utc",
        "contains_secrets",
        "fingerprint",
        "fingerprint_sha256",
    }
    if set(payload) != required:
        raise ForwardEvidenceEraError("runtime manifest fields differ from the locked schema")
    if payload.get("schema_version") != RUNTIME_MANIFEST_SCHEMA:
        raise ForwardEvidenceEraError("runtime manifest has an unknown schema")
    if payload.get("contains_secrets") is not False:
        raise ForwardEvidenceEraError("runtime manifest must be explicitly secret-free")
    fingerprint = payload.get("fingerprint")
    if not isinstance(fingerprint, dict):
        raise ForwardEvidenceEraError("runtime fingerprint is absent")
    expected_fingerprint_fields = {
        "python_implementation",
        "python_version",
        "python_cache_tag",
        "python_executable_name",
        "python_executable_sha256",
        "byteorder",
        "platform_system",
        "platform_release",
        "platform_version",
        "platform_machine",
        "openssl_version",
        "installed_distributions",
    }
    if set(fingerprint) != expected_fingerprint_fields:
        raise ForwardEvidenceEraError("runtime fingerprint fields differ from the locked schema")
    _hash(fingerprint.get("python_executable_sha256"), "python_executable_sha256")
    distributions = fingerprint.get("installed_distributions")
    if not isinstance(distributions, list) or any(
        not isinstance(item, dict) or set(item) != {"name", "version"}
        for item in distributions
    ):
        raise ForwardEvidenceEraError("installed distribution inventory is malformed")
    expected = _hash(payload.get("fingerprint_sha256"), "fingerprint_sha256")
    if expected != _runtime_digest(fingerprint):
        raise ForwardEvidenceEraError("runtime fingerprint hash differs from its contents")
    if require_current_runtime and fingerprint != _runtime_fingerprint():
        raise ForwardEvidenceEraError("running Python/runtime differs from the frozen manifest")
    return payload


def _json(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ForwardEvidenceEraError(f"{label} is missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ForwardEvidenceEraError(f"{label} is malformed: {path}") from exc
    if not isinstance(payload, dict):
        raise ForwardEvidenceEraError(f"{label} must be a JSON object")
    return payload


def _hash(value: Any, label: str) -> str:
    out = str(value).strip().lower()
    if len(out) != 64 or any(char not in "0123456789abcdef" for char in out):
        raise ForwardEvidenceEraError(f"{label} must be a SHA-256 digest")
    return out


def _contained_artifact(certificate_path: Path, relative: Any, label: str) -> Path:
    value = Path(str(relative))
    if value.is_absolute():
        raise ForwardEvidenceEraError(f"{label} path must be relative to the smoke certificate")
    root = certificate_path.resolve().parent
    candidate = (root / value).resolve()
    if not candidate.is_relative_to(root):
        raise ForwardEvidenceEraError(f"{label} path escapes the operational-smoke root")
    return candidate


def _relative_binding(path: Path, *, base: Path, path_base: str, label: str) -> dict[str, str]:
    artifact = path.resolve()
    resolved_base = base.resolve()
    if not artifact.is_relative_to(resolved_base):
        raise ForwardEvidenceEraError(f"{label} must remain inside its declared {path_base} root")
    return {
        "path": str(artifact.relative_to(resolved_base)).replace("\\", "/"),
        "path_base": path_base,
        "sha256": sha256_file(artifact),
    }


def _resolve_scope_binding(
    *,
    scope_path: Path,
    release_root: Path,
    item: Mapping[str, Any],
    label: str,
) -> Path:
    path_base = item.get("path_base")
    if path_base == "release":
        base = release_root.resolve()
    elif path_base == "scope":
        base = scope_path.resolve().parent
    else:
        raise ForwardEvidenceEraError(f"{label}.path_base must be release or scope")
    relative = Path(str(item.get("path")))
    if relative.is_absolute():
        raise ForwardEvidenceEraError(f"{label} path must be relative to its declared root")
    candidate = (base / relative).resolve()
    if not candidate.is_relative_to(base):
        raise ForwardEvidenceEraError(f"{label} path escapes its declared root")
    return candidate


def _validate_smoke_lifecycle_binding(
    *,
    evidence_scope_path: Path,
    lifecycle_verification_path: Path,
    official_game_date: str,
    root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    scope = validate_evidence_scope(
        evidence_scope_path,
        root=root,
        require_current_runtime=False,
    )
    if scope["mode"] != "operational_smoke":
        raise ForwardEvidenceEraError("only an operational-smoke scope can be certified")
    verification = _json(lifecycle_verification_path, "lifecycle verification")
    if (
        verification.get("schema_version") != "shadow-lifecycle-tree-verification-v1"
        or verification.get("official_game_date") != str(official_game_date)
        or verification.get("evidence_scope_sha256") != sha256_file(evidence_scope_path)
        or verification.get("evidence_scope_mode") != "operational_smoke"
        or verification.get("economic_evidence_eligible") is not False
        or verification.get("complete_due_entry_and_prestart_phases") is not True
        or verification.get("settlement_complete") is not True
        or verification.get("replacement_odds_fetched") is not False
        or verification.get("betting_authorized") is not False
    ):
        raise ForwardEvidenceEraError(
            "operational smoke lifecycle is incomplete or bound to a different scope/date"
        )
    return scope, verification


def validate_operational_smoke_certificate(
    path: str | Path,
    *,
    root: str | Path,
) -> dict[str, Any]:
    """Reopen and independently verify every artifact named by a smoke certificate."""

    certificate_path = Path(path).resolve()
    payload = _json(certificate_path, "operational smoke certificate")
    required = {
        "schema_version",
        "official_game_date",
        "evidence_scope",
        "lifecycle_verification",
        "verified",
        "complete_lifecycle",
        "operational_smoke",
        "economic_evidence_eligible",
        "betting_authorized",
    }
    if set(payload) != required:
        raise ForwardEvidenceEraError("operational smoke certificate fields differ from v2")
    if (
        payload.get("schema_version") != SMOKE_CERTIFICATE_SCHEMA
        or payload.get("verified") is not True
        or payload.get("complete_lifecycle") is not True
        or payload.get("operational_smoke") is not True
        or payload.get("economic_evidence_eligible") is not False
        or payload.get("betting_authorized") is not False
    ):
        raise ForwardEvidenceEraError("operational smoke certificate is not complete and excluded")
    scope_item = payload.get("evidence_scope")
    lifecycle_item = payload.get("lifecycle_verification")
    if not isinstance(scope_item, dict) or set(scope_item) != {"path", "sha256", "scope_sha256"}:
        raise ForwardEvidenceEraError("smoke certificate scope binding is malformed")
    if not isinstance(lifecycle_item, dict) or set(lifecycle_item) != {"path", "sha256"}:
        raise ForwardEvidenceEraError("smoke certificate lifecycle binding is malformed")
    scope_path = _contained_artifact(certificate_path, scope_item["path"], "evidence scope")
    lifecycle_path = _contained_artifact(
        certificate_path,
        lifecycle_item["path"],
        "lifecycle verification",
    )
    if not scope_path.is_file() or sha256_file(scope_path) != _hash(scope_item["sha256"], "evidence_scope.sha256"):
        raise ForwardEvidenceEraError("smoke certificate evidence scope is missing or changed")
    if not lifecycle_path.is_file() or sha256_file(lifecycle_path) != _hash(
        lifecycle_item["sha256"], "lifecycle_verification.sha256"
    ):
        raise ForwardEvidenceEraError("smoke certificate lifecycle verification is missing or changed")
    scope, _ = _validate_smoke_lifecycle_binding(
        evidence_scope_path=scope_path,
        lifecycle_verification_path=lifecycle_path,
        official_game_date=str(payload.get("official_game_date")),
        root=Path(root).resolve(),
    )
    if scope["scope_sha256"] != _hash(scope_item["scope_sha256"], "evidence_scope.scope_sha256"):
        raise ForwardEvidenceEraError("smoke certificate internal scope digest differs")
    return payload


def _current_git_release(root: Path) -> tuple[str, bool]:
    """Return the actual running commit and full nonignored worktree state.

    A recorded ``source_commit`` or ``source_tree_clean`` flag is not evidence
    unless validation independently observes the same release checkout.
    """

    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise ForwardEvidenceEraError("Git release identity is unavailable") from exc
    if head.returncode != 0 or status.returncode != 0:
        raise ForwardEvidenceEraError("Git release identity is unavailable")
    commit = head.stdout.strip().lower()
    if len(commit) != 40 or any(char not in "0123456789abcdef" for char in commit):
        raise ForwardEvidenceEraError("running Git commit is malformed")
    return commit, not bool(status.stdout.strip())


def _bound_source(readiness: dict[str, Any], root: Path) -> dict[str, str]:
    if readiness.get("status") != READINESS_STATUS or readiness.get("betting_authorized") is not False:
        raise ForwardEvidenceEraError("readiness report is not the fail-closed local lifecycle certificate")
    if int(readiness.get("guard_checks_passed", 0)) < 170:
        raise ForwardEvidenceEraError("readiness report predates the complete era-boundary guards")
    bound = readiness.get("bound_files")
    if not isinstance(bound, dict) or not bound:
        raise ForwardEvidenceEraError("readiness report has no bound-file map")
    normalized: dict[str, str] = {}
    for relative, digest in sorted(bound.items()):
        path = root / str(relative)
        expected = _hash(digest, f"bound_files[{relative}]")
        if not path.is_file() or sha256_file(path) != expected:
            raise ForwardEvidenceEraError(f"bound release file drifted: {relative}")
        normalized[str(relative).replace("\\", "/")] = expected
    return normalized


def build_evidence_scope(
    *,
    mode: str,
    era_id: str,
    created_at_utc: str,
    readiness_report: Path,
    boundary_path: Path,
    deployment_protocol_path: Path,
    product_contracts_path: Path,
    runtime_manifest_path: Path,
    scope_path: Path,
    source_commit: str,
    source_tree_clean: bool,
    root: Path,
    smoke_certificate: Path | None = None,
) -> dict[str, Any]:
    """Build but do not publish one content-addressed scope."""

    if mode not in {"operational_smoke", "forward_evidence"}:
        raise ForwardEvidenceEraError("mode must be operational_smoke or forward_evidence")
    if not str(era_id).strip():
        raise ForwardEvidenceEraError("era_id cannot be blank")
    commit = str(source_commit).strip().lower()
    if len(commit) != 40 or any(char not in "0123456789abcdef" for char in commit):
        raise ForwardEvidenceEraError("source_commit must be a full Git commit hash")
    release_root = root.resolve()
    output_path = scope_path.resolve()
    scope_root = output_path.parent
    readiness = _json(readiness_report, "readiness report")
    bound = _bound_source(readiness, root)
    load_forward_evidence_boundary(boundary_path, root=root)
    validate_execution_product_contracts(product_contracts_path)
    protocol = _json(deployment_protocol_path, "deployment protocol")
    runtime_manifest = validate_runtime_manifest(runtime_manifest_path)
    if (
        protocol.get("schema_version") != "forward-shadow-live-deployment-protocol-v2"
        or protocol.get("status") != "RESEARCH_ONLY"
        or protocol.get("betting_authorized") is not False
    ):
        raise ForwardEvidenceEraError("deployment protocol is not the locked research-only v2 contract")

    smoke_binding: dict[str, str] | None = None
    if mode == "operational_smoke":
        if smoke_certificate is not None:
            raise ForwardEvidenceEraError("an operational smoke cannot consume a prior smoke certificate")
    else:
        if source_tree_clean is not True:
            raise ForwardEvidenceEraError("forward economic evidence requires a clean release checkout")
        if smoke_certificate is None:
            raise ForwardEvidenceEraError("forward economic evidence requires a completed smoke certificate")
        validate_operational_smoke_certificate(smoke_certificate, root=root)
        smoke_binding = _relative_binding(
            smoke_certificate,
            base=scope_root,
            path_base="scope",
            label="operational smoke certificate",
        )

    payload: dict[str, Any] = {
        "schema_version": SCOPE_SCHEMA,
        "era_id": str(era_id).strip(),
        "mode": mode,
        "created_at_utc": str(created_at_utc),
        "operational_smoke": mode == "operational_smoke",
        "economic_evidence_eligible": mode == "forward_evidence",
        "status": "RESEARCH_ONLY",
        "betting_authorized": False,
        "source_commit": commit,
        "source_tree_clean": bool(source_tree_clean),
        "readiness_report": {
            **_relative_binding(
                readiness_report,
                base=release_root,
                path_base="release",
                label="readiness report",
            ),
            "guard_checks_passed": readiness["guard_checks_passed"],
        },
        "forward_evidence_boundary": _relative_binding(
            boundary_path,
            base=release_root,
            path_base="release",
            label="forward evidence boundary",
        ),
        "deployment_protocol": _relative_binding(
            deployment_protocol_path,
            base=release_root,
            path_base="release",
            label="deployment protocol",
        ),
        "execution_product_contracts": _relative_binding(
            product_contracts_path,
            base=release_root,
            path_base="release",
            label="execution product contracts",
        ),
        "runtime_manifest": {
            **_relative_binding(
                runtime_manifest_path,
                base=scope_root,
                path_base="scope",
                label="runtime manifest",
            ),
            "fingerprint_sha256": runtime_manifest["fingerprint_sha256"],
        },
        "operational_smoke_certificate": smoke_binding,
        "bound_files": bound,
    }
    payload["scope_sha256"] = _scope_digest(payload)
    return payload


def validate_evidence_scope(
    path: str | Path,
    *,
    root: str | Path,
    require_current_runtime: bool = True,
) -> dict[str, Any]:
    source = Path(path).resolve()
    release_root = Path(root).resolve()
    payload = _json(source, "evidence scope")
    if payload.get("schema_version") != SCOPE_SCHEMA:
        raise ForwardEvidenceEraError("evidence scope has an unknown schema")
    if _hash(payload.get("scope_sha256"), "scope_sha256") != _scope_digest(payload):
        raise ForwardEvidenceEraError("evidence scope hash differs from its immutable contents")
    mode = payload.get("mode")
    if mode == "operational_smoke":
        if payload.get("operational_smoke") is not True or payload.get("economic_evidence_eligible") is not False:
            raise ForwardEvidenceEraError("operational smoke cannot be economic evidence")
        if payload.get("operational_smoke_certificate") is not None:
            raise ForwardEvidenceEraError("operational smoke unexpectedly consumes a smoke certificate")
    elif mode == "forward_evidence":
        if payload.get("operational_smoke") is not False or payload.get("economic_evidence_eligible") is not True:
            raise ForwardEvidenceEraError("forward evidence scope has contradictory classification")
        certificate = payload.get("operational_smoke_certificate")
        if not isinstance(certificate, dict):
            raise ForwardEvidenceEraError("forward evidence scope lacks its smoke certificate binding")
        certificate_path = _resolve_scope_binding(
            scope_path=source,
            release_root=release_root,
            item=certificate,
            label="operational smoke certificate",
        )
        if (
            not certificate_path.is_file()
            or sha256_file(certificate_path)
            != _hash(certificate.get("sha256"), "operational_smoke_certificate.sha256")
        ):
            raise ForwardEvidenceEraError("operational smoke certificate is missing or changed")
        validate_operational_smoke_certificate(certificate_path, root=release_root)
        if payload.get("source_tree_clean") is not True:
            raise ForwardEvidenceEraError("forward evidence scope did not start from a clean release")
        running_commit, running_clean = _current_git_release(release_root)
        if running_commit != str(payload.get("source_commit", "")).strip().lower():
            raise ForwardEvidenceEraError("running Git commit differs from the frozen evidence scope")
        if running_clean is not True:
            raise ForwardEvidenceEraError("running source tree is not clean")
    else:
        raise ForwardEvidenceEraError("evidence scope mode is unknown")
    if payload.get("status") != "RESEARCH_ONLY" or payload.get("betting_authorized") is not False:
        raise ForwardEvidenceEraError("evidence scope must remain research-only")

    bound = payload.get("bound_files")
    if not isinstance(bound, dict) or not bound:
        raise ForwardEvidenceEraError("evidence scope has no bound files")
    for relative, digest in bound.items():
        expected = _hash(digest, f"bound_files[{relative}]")
        candidate = release_root / str(relative)
        if not candidate.is_file() or sha256_file(candidate) != expected:
            raise ForwardEvidenceEraError(f"active release differs from evidence scope: {relative}")

    for field in ("readiness_report", "forward_evidence_boundary", "deployment_protocol", "execution_product_contracts", "runtime_manifest"):
        item = payload.get(field)
        if not isinstance(item, dict):
            raise ForwardEvidenceEraError(f"{field} binding is absent")
        candidate = _resolve_scope_binding(
            scope_path=source,
            release_root=release_root,
            item=item,
            label=field,
        )
        if not candidate.is_file() or sha256_file(candidate) != _hash(item.get("sha256"), f"{field}.sha256"):
            raise ForwardEvidenceEraError(f"{field} artifact is missing or changed")
        if field == "runtime_manifest":
            runtime = validate_runtime_manifest(
                candidate,
                require_current_runtime=require_current_runtime,
            )
            if runtime["fingerprint_sha256"] != _hash(
                item.get("fingerprint_sha256"), "runtime_manifest.fingerprint_sha256"
            ):
                raise ForwardEvidenceEraError("runtime manifest fingerprint binding changed")
    return payload


def certify_operational_smoke(
    *,
    evidence_scope_path: Path,
    lifecycle_verification_path: Path,
    official_game_date: str,
    certificate_path: Path,
    root: Path,
) -> dict[str, Any]:
    """Create a certificate only after the excluded smoke completed end to end."""

    scope_path = evidence_scope_path.resolve()
    lifecycle_path = lifecycle_verification_path.resolve()
    output_path = certificate_path.resolve()
    scope, _ = _validate_smoke_lifecycle_binding(
        evidence_scope_path=scope_path,
        lifecycle_verification_path=lifecycle_path,
        official_game_date=official_game_date,
        root=root,
    )
    for artifact, label in (
        (scope_path, "evidence scope"),
        (lifecycle_path, "lifecycle verification"),
    ):
        if not artifact.is_relative_to(output_path.parent):
            raise ForwardEvidenceEraError(f"{label} must remain inside the smoke certificate root")
    return {
        "schema_version": SMOKE_CERTIFICATE_SCHEMA,
        "official_game_date": str(official_game_date),
        "evidence_scope": {
            "path": str(scope_path.relative_to(output_path.parent)).replace("\\", "/"),
            "sha256": sha256_file(scope_path),
            "scope_sha256": scope["scope_sha256"],
        },
        "lifecycle_verification": {
            "path": str(lifecycle_path.relative_to(output_path.parent)).replace("\\", "/"),
            "sha256": sha256_file(lifecycle_path),
        },
        "verified": True,
        "complete_lifecycle": True,
        "operational_smoke": True,
        "economic_evidence_eligible": False,
        "betting_authorized": False,
    }
