"""Verification for evidence that upgrades a market-policy parameter.

Changing an origin from ``placeholder`` to ``fitted`` must not be enough to
promote a market run. This module binds each approved policy value to a
content-addressed parameter-evidence artifact *and* to a real,
content-addressed protocol file. A bare 64-character ``protocol_sha256`` is
not evidence that a protocol exists; it is just text that looks like a hash.

The verifier checks provenance, exact value binding, and the minimum temporal
or outcome-blind contract appropriate to the declared origin. It deliberately
does not infer that a method is statistically sufficient. That judgment
belongs in the predeclared authorization protocol and its review.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from pathlib import Path
from typing import Any


PARAMETER_EVIDENCE_SCHEMA = "market-policy-parameter-evidence-v1"
PARAMETER_PROTOCOL_SCHEMA = "market-policy-parameter-protocol-v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _valid_sha256(value: Any) -> bool:
    return bool(_SHA256.fullmatch(str(value).strip().lower()))


def _project_path(project_root: Path, raw_path: Any, label: str) -> Path:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError(f"{label} path is missing")
    path = Path(raw_path)
    if not path.is_absolute():
        path = project_root / path
    try:
        path.resolve().relative_to(project_root.resolve())
    except ValueError as exc:
        raise ValueError(f"{label} must remain inside the project root") from exc
    if not path.is_file():
        raise ValueError(f"{label} does not exist: {path}")
    return path


def _verify_hashed_reference(project_root: Path, label: str, raw: Any) -> None:
    if not isinstance(raw, dict):
        raise ValueError(f"{label} reference is not an object")
    expected = str(raw.get("sha256", "")).strip().lower()
    if not _valid_sha256(expected):
        raise ValueError(f"{label} reference lacks a SHA-256 digest")
    path = _project_path(project_root, raw.get("path"), label)
    actual = sha256_file(path)
    if actual != expected:
        raise ValueError(f"{label} hash mismatch: expected {expected}, got {actual}")


def _load_hashed_json_reference(
    project_root: Path,
    label: str,
    raw: Any,
) -> tuple[Path, dict[str, Any]]:
    """Return one hash-verified JSON reference inside the project root."""

    if not isinstance(raw, dict):
        raise ValueError(f"{label} reference is not an object")
    expected = str(raw.get("sha256", "")).strip().lower()
    if not _valid_sha256(expected):
        raise ValueError(f"{label} reference lacks a SHA-256 digest")
    path = _project_path(project_root, raw.get("path"), label)
    actual = sha256_file(path)
    if actual != expected:
        raise ValueError(f"{label} hash mismatch: expected {expected}, got {actual}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} root is not an object")
    return path, payload


def _date_list(raw: Any, label: str) -> list[date]:
    """Parse an explicit chronological date list without normalising it."""

    if not isinstance(raw, list) or not raw:
        raise ValueError(f"{label} must be a non-empty date list")
    if not all(isinstance(value, str) for value in raw):
        raise ValueError(f"{label} must contain ISO date strings")
    if len(raw) != len(set(raw)):
        raise ValueError(f"{label} repeats a date")
    try:
        values = [date.fromisoformat(value) for value in raw]
    except ValueError as exc:
        raise ValueError(f"{label} contains an invalid ISO date") from exc
    if values != sorted(values):
        raise ValueError(f"{label} must be strictly chronological")
    return values


def _verify_protocol(
    *,
    project_root: Path,
    evidence: dict[str, Any],
    parameter: str,
    origin: str,
    inputs: dict[str, Any],
) -> None:
    """Verify the real protocol behind a future policy promotion.

    ``fitted`` values must document a chronological, disjoint fit/validation
    partition. ``predeclared_structural`` values must explicitly prohibit
    outcome values in their evidence. These checks make a future promotion
    mechanically harder to launder, while leaving substantive statistical
    review to the authorization gate.
    """

    # v1 accepted an unresolvable string named protocol_sha256. Do not retain
    # that escape hatch: a protocol must be a file we can hash and inspect.
    protocol_path, protocol = _load_hashed_json_reference(
        project_root,
        "parameter evidence protocol",
        evidence.get("protocol"),
    )
    if protocol.get("schema_version") != PARAMETER_PROTOCOL_SCHEMA:
        raise ValueError("parameter evidence protocol has an unknown schema")
    if protocol.get("parameter") != parameter:
        raise ValueError("parameter evidence protocol names a different parameter")
    if protocol.get("origin") != origin:
        raise ValueError("parameter evidence protocol origin differs from policy")
    if not isinstance(protocol.get("method"), str) or not protocol["method"].strip():
        raise ValueError("parameter evidence protocol lacks a declared method")

    # Store an explicit digest in the evidence too. This prevents a second,
    # detached reference from becoming an unverified claim later.
    actual_digest = sha256_file(protocol_path)
    if str(evidence.get("protocol_sha256", "")).strip().lower() != actual_digest:
        raise ValueError("parameter evidence protocol_sha256 does not match protocol artifact")

    if origin == "fitted":
        if protocol.get("data_use") != "select_on_fit_evaluate_once_on_validation":
            raise ValueError("fitted protocol must select on fit data and evaluate once on validation")
        temporal = evidence.get("temporal_evaluation")
        if not isinstance(temporal, dict):
            raise ValueError("fitted parameter evidence lacks temporal_evaluation")
        fit_dates = _date_list(temporal.get("fit_dates"), "fitted temporal_evaluation.fit_dates")
        validation_dates = _date_list(
            temporal.get("validation_dates"),
            "fitted temporal_evaluation.validation_dates",
        )
        if set(fit_dates) & set(validation_dates):
            raise ValueError("fitted temporal evaluation overlaps fit and validation dates")
        if max(fit_dates) >= min(validation_dates):
            raise ValueError("fitted temporal evaluation is not strictly chronological")
        for role in ("fit_universe", "validation_universe"):
            if role not in inputs:
                raise ValueError(f"fitted parameter evidence lacks hashed {role!r} input")
    elif origin == "predeclared_structural":
        if protocol.get("data_use") != "outcome_blind_structural":
            raise ValueError("structural protocol must be explicitly outcome blind")
        if evidence.get("outcome_values_used") is not False:
            raise ValueError("structural parameter evidence must declare outcome_values_used=false")
    else:  # Defensive: callers currently invoke only allowlisted origins.
        raise ValueError(f"unknown evidence origin {origin!r}")


def verify_parameter_evidence(
    *,
    project_root: Path,
    parameter: str,
    value: Any,
    origin: str,
    policy_entry: Any,
) -> tuple[bool, str]:
    """Validate one future ``fitted``/structural policy promotion.

    The evidence document must name the exact parameter and value, itself be
    hash-pinned in the policy, and pin each source artifact it claims to use.
    This prevents a policy edit from reusing unrelated evidence or quietly
    changing a fitted threshold after the fact.
    """

    if not isinstance(policy_entry, dict):
        return False, "policy parameter is not an object"
    expected = str(policy_entry.get("evidence_sha256", "")).strip().lower()
    if not _valid_sha256(expected):
        return False, "approved origin lacks evidence_sha256"
    try:
        path = _project_path(project_root, policy_entry.get("evidence_artifact"), "parameter evidence")
        actual = sha256_file(path)
        if actual != expected:
            return False, f"parameter evidence hash mismatch: expected {expected}, got {actual}"
        evidence = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(evidence, dict):
            return False, "parameter evidence root is not an object"
        if evidence.get("schema_version") != PARAMETER_EVIDENCE_SCHEMA:
            return False, "parameter evidence has an unknown schema"
        if evidence.get("parameter") != parameter:
            return False, "parameter evidence names a different parameter"
        if evidence.get("selected_value") != value:
            return False, "parameter evidence selected_value differs from policy"
        if evidence.get("origin") != origin:
            return False, "parameter evidence origin differs from policy"
        if not isinstance(evidence.get("method"), str) or not evidence["method"].strip():
            return False, "parameter evidence lacks a declared method"
        inputs = evidence.get("input_artifacts")
        if not isinstance(inputs, dict) or not inputs:
            return False, "parameter evidence has no hashed input artifacts"
        for label, reference in inputs.items():
            _verify_hashed_reference(project_root, f"parameter evidence input {label!r}", reference)
        _verify_protocol(
            project_root=project_root,
            evidence=evidence,
            parameter=parameter,
            origin=origin,
            inputs=inputs,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return False, str(exc)
    return True, "verified parameter evidence"
