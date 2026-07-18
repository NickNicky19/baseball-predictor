#!/usr/bin/env python3
"""Mutation checks for smoke/forward-era separation and frozen provenance."""

from __future__ import annotations

import json
import hashlib
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.forward_evidence_era import (  # noqa: E402
    ForwardEvidenceEraError,
    build_evidence_scope,
    build_runtime_manifest,
    certify_operational_smoke,
    validate_evidence_scope,
    validate_runtime_manifest,
)
from scripts import prepare_forward_evidence_scope as scope_cli  # noqa: E402


PASS = 0
FAIL = 0


def check(condition: bool, label: str) -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [OK] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}")


def rejected(path: Path) -> bool:
    try:
        validate_evidence_scope(path, root=ROOT)
    except ForwardEvidenceEraError:
        return True
    return False


def build(
    mode: str,
    *,
    readiness: Path,
    runtime_manifest: Path,
    smoke_certificate: Path | None = None,
) -> dict:
    return build_evidence_scope(
        mode=mode,
        era_id="offline-test-smoke" if mode == "operational_smoke" else "offline-test-era",
        created_at_utc="2026-07-17T00:00:00+00:00",
        readiness_report=readiness,
        boundary_path=ROOT / "config/forward_shadow_evidence_boundary.json",
        deployment_protocol_path=ROOT / "config/forward_shadow_deployment_protocol.json",
        product_contracts_path=ROOT / "config/hits_execution_product_contracts.json",
        runtime_manifest_path=runtime_manifest,
        source_commit="1" * 40,
        source_tree_clean=mode == "forward_evidence",
        root=ROOT,
        smoke_certificate=smoke_certificate,
    )


def main() -> int:
    original_git = scope_cli._git
    try:
        scope_cli._git = lambda *args: ""
        check(scope_cli._source_tree_clean(), "a clean Git worktree is accepted")
        scope_cli._git = lambda *args: "?? untracked_runtime_override.py"
        check(
            not scope_cli._source_tree_clean(),
            "MUTATION nonignored untracked source makes the release dirty",
        )
    finally:
        scope_cli._git = original_git

    with tempfile.TemporaryDirectory(prefix="forward_era_") as temporary:
        temp = Path(temporary)
        readiness_path = temp / "readiness.json"
        readiness_path.write_text(json.dumps({
            "status": "FULL_LOCAL_LIFECYCLE_GUARDS_VALID_PRIMARY_COLLECTOR_NOT_DEPLOYED_NO_FORWARD_EVIDENCE",
            "betting_authorized": False,
            "guard_checks_passed": 170,
            "bound_files": {
                relative: hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
                for relative in (
                    "config/forward_shadow_evidence_boundary.json",
                    "config/forward_shadow_deployment_protocol.json",
                    "config/hits_execution_product_contracts.json",
                    "src/evaluation/forward_evidence_era.py",
                )
            },
        }), encoding="utf-8")
        runtime_path = temp / "runtime_manifest.json"
        runtime_payload = build_runtime_manifest(created_at_utc="2026-07-17T00:00:00+00:00")
        runtime_path.write_text(json.dumps(runtime_payload), encoding="utf-8")
        checked_runtime = validate_runtime_manifest(runtime_path)
        check(
            checked_runtime["contains_secrets"] is False,
            "runtime manifest validates and is explicitly secret-free",
        )
        smoke_scope_path = temp / "smoke_scope.json"
        smoke_scope = build(
            "operational_smoke",
            readiness=readiness_path,
            runtime_manifest=runtime_path,
        )
        smoke_scope_path.write_text(json.dumps(smoke_scope), encoding="utf-8")
        check(
            smoke_scope["runtime_manifest"]["sha256"]
            == hashlib.sha256(runtime_path.read_bytes()).hexdigest(),
            "evidence scope binds the exact runtime-manifest bytes",
        )
        check(
            validate_evidence_scope(smoke_scope_path, root=ROOT)["economic_evidence_eligible"] is False,
            "operational smoke is valid and permanently excluded from economic evidence",
        )

        changed = json.loads(json.dumps(smoke_scope))
        changed["economic_evidence_eligible"] = True
        smoke_scope_path.write_text(json.dumps(changed), encoding="utf-8")
        check(rejected(smoke_scope_path), "MUTATION smoke cannot become economic evidence")
        smoke_scope_path.write_text(json.dumps(smoke_scope), encoding="utf-8")

        changed = json.loads(json.dumps(smoke_scope))
        first = next(iter(changed["bound_files"]))
        changed["bound_files"][first] = "0" * 64
        changed.pop("scope_sha256")
        encoded = json.dumps(changed, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        changed["scope_sha256"] = hashlib.sha256(encoded).hexdigest()
        drift_path = temp / "drift.json"
        drift_path.write_text(json.dumps(changed), encoding="utf-8")
        check(rejected(drift_path), "MUTATION a bound-file drift cannot reuse the scope")

        lifecycle = temp / "lifecycle.json"
        lifecycle.write_text(json.dumps({
            "schema_version": "shadow-lifecycle-tree-verification-v1",
            "official_game_date": "2026-07-20",
            "evidence_scope_sha256": hashlib.sha256(smoke_scope_path.read_bytes()).hexdigest(),
            "evidence_scope_mode": "operational_smoke",
            "economic_evidence_eligible": False,
            "complete_due_entry_and_prestart_phases": True,
            "settlement_complete": True,
            "replacement_odds_fetched": False,
            "betting_authorized": False,
        }), encoding="utf-8")
        certificate = certify_operational_smoke(
            evidence_scope_path=smoke_scope_path,
            lifecycle_verification_path=lifecycle,
            official_game_date="2026-07-20",
            root=ROOT,
        )
        cert_path = temp / "smoke_certificate.json"
        cert_path.write_text(json.dumps(certificate), encoding="utf-8")
        check(
            certificate["complete_lifecycle"] is True
            and certificate["economic_evidence_eligible"] is False,
            "complete smoke certificate remains explicitly non-economic",
        )

        incomplete = json.loads(lifecycle.read_text(encoding="utf-8"))
        incomplete["settlement_complete"] = False
        lifecycle.write_text(json.dumps(incomplete), encoding="utf-8")
        try:
            certify_operational_smoke(
                evidence_scope_path=smoke_scope_path,
                lifecycle_verification_path=lifecycle,
                official_game_date="2026-07-20",
                root=ROOT,
            )
        except ForwardEvidenceEraError:
            incomplete_rejected = True
        else:
            incomplete_rejected = False
        check(incomplete_rejected, "MUTATION incomplete lifecycle cannot certify the smoke")

        valid_lifecycle = {
            "schema_version": "shadow-lifecycle-tree-verification-v1",
            "official_game_date": "2026-07-20",
            "evidence_scope_sha256": hashlib.sha256(smoke_scope_path.read_bytes()).hexdigest(),
            "evidence_scope_mode": "operational_smoke",
            "economic_evidence_eligible": False,
            "complete_due_entry_and_prestart_phases": True,
            "settlement_complete": True,
            "replacement_odds_fetched": False,
            "betting_authorized": False,
        }
        lifecycle.write_text(json.dumps(valid_lifecycle), encoding="utf-8")

        wrong_scope = dict(valid_lifecycle)
        wrong_scope["evidence_scope_sha256"] = "0" * 64
        lifecycle.write_text(json.dumps(wrong_scope), encoding="utf-8")
        try:
            certify_operational_smoke(
                evidence_scope_path=smoke_scope_path,
                lifecycle_verification_path=lifecycle,
                official_game_date="2026-07-20",
                root=ROOT,
            )
        except ForwardEvidenceEraError:
            wrong_scope_rejected = True
        else:
            wrong_scope_rejected = False
        check(wrong_scope_rejected, "MUTATION lifecycle from another scope cannot certify the smoke")

        wrong_date = dict(valid_lifecycle)
        wrong_date["official_game_date"] = "2026-07-21"
        lifecycle.write_text(json.dumps(wrong_date), encoding="utf-8")
        try:
            certify_operational_smoke(
                evidence_scope_path=smoke_scope_path,
                lifecycle_verification_path=lifecycle,
                official_game_date="2026-07-20",
                root=ROOT,
            )
        except ForwardEvidenceEraError:
            wrong_date_rejected = True
        else:
            wrong_date_rejected = False
        check(wrong_date_rejected, "MUTATION wrong lifecycle date cannot certify the smoke")

        wrong_mode = dict(valid_lifecycle)
        wrong_mode["evidence_scope_mode"] = "forward_evidence"
        wrong_mode["economic_evidence_eligible"] = True
        lifecycle.write_text(json.dumps(wrong_mode), encoding="utf-8")
        try:
            certify_operational_smoke(
                evidence_scope_path=smoke_scope_path,
                lifecycle_verification_path=lifecycle,
                official_game_date="2026-07-20",
                root=ROOT,
            )
        except ForwardEvidenceEraError:
            wrong_mode_rejected = True
        else:
            wrong_mode_rejected = False
        check(wrong_mode_rejected, "MUTATION economic lifecycle cannot certify excluded smoke")

        try:
            build_evidence_scope(
                mode="forward_evidence",
                era_id="missing-smoke",
                created_at_utc="2026-07-17T00:00:00+00:00",
                readiness_report=readiness_path,
                boundary_path=ROOT / "config/forward_shadow_evidence_boundary.json",
                deployment_protocol_path=ROOT / "config/forward_shadow_deployment_protocol.json",
                product_contracts_path=ROOT / "config/hits_execution_product_contracts.json",
                runtime_manifest_path=runtime_path,
                source_commit="1" * 40,
                source_tree_clean=True,
                root=ROOT,
            )
        except ForwardEvidenceEraError:
            no_smoke_rejected = True
        else:
            no_smoke_rejected = False
        check(no_smoke_rejected, "MUTATION economic era cannot start without a smoke certificate")

        try:
            build_evidence_scope(
                mode="forward_evidence",
                era_id="dirty-release",
                created_at_utc="2026-07-17T00:00:00+00:00",
                readiness_report=readiness_path,
                boundary_path=ROOT / "config/forward_shadow_evidence_boundary.json",
                deployment_protocol_path=ROOT / "config/forward_shadow_deployment_protocol.json",
                product_contracts_path=ROOT / "config/hits_execution_product_contracts.json",
                runtime_manifest_path=runtime_path,
                source_commit="1" * 40,
                source_tree_clean=False,
                root=ROOT,
                smoke_certificate=cert_path,
            )
        except ForwardEvidenceEraError:
            dirty_rejected = True
        else:
            dirty_rejected = False
        check(dirty_rejected, "MUTATION dirty release cannot begin economic evidence")

        lifecycle.write_text(json.dumps({
            "schema_version": "shadow-lifecycle-tree-verification-v1",
            "official_game_date": "2026-07-20",
            "evidence_scope_sha256": hashlib.sha256(smoke_scope_path.read_bytes()).hexdigest(),
            "evidence_scope_mode": "operational_smoke",
            "economic_evidence_eligible": False,
            "complete_due_entry_and_prestart_phases": True,
            "settlement_complete": True,
            "replacement_odds_fetched": False,
            "betting_authorized": False,
        }), encoding="utf-8")
        era_scope = build(
            "forward_evidence",
            readiness=readiness_path,
            runtime_manifest=runtime_path,
            smoke_certificate=cert_path,
        )
        era_path = temp / "era_scope.json"
        era_path.write_text(json.dumps(era_scope), encoding="utf-8")
        with patch(
            "src.evaluation.forward_evidence_era._current_git_release",
            return_value=("1" * 40, True),
        ):
            clean_era_valid = (
                validate_evidence_scope(era_path, root=ROOT)["economic_evidence_eligible"] is True
            )
        check(clean_era_valid, "clean forward era with a completed excluded smoke validates")

        with patch(
            "src.evaluation.forward_evidence_era._current_git_release",
            return_value=("2" * 40, True),
        ):
            check(rejected(era_path), "MUTATION a different running commit invalidates the era")

        with patch(
            "src.evaluation.forward_evidence_era._current_git_release",
            return_value=("1" * 40, False),
        ):
            check(rejected(era_path), "MUTATION runtime worktree drift invalidates the era")

        cert_path.write_text("{}", encoding="utf-8")
        with patch(
            "src.evaluation.forward_evidence_era._current_git_release",
            return_value=("1" * 40, True),
        ):
            check(rejected(era_path), "MUTATION tampered smoke certificate invalidates the forward era")

        runtime_path.write_text("{}", encoding="utf-8")
        check(rejected(smoke_scope_path), "MUTATION tampered runtime-manifest bytes invalidate the scope")

        changed_runtime = json.loads(json.dumps(runtime_payload))
        changed_runtime["fingerprint"]["installed_distributions"].append(
            {"name": "mutated-runtime", "version": "1.0"}
        )
        changed_runtime["fingerprint"]["installed_distributions"].sort(
            key=lambda item: item["name"]
        )
        changed_runtime["fingerprint_sha256"] = hashlib.sha256(
            json.dumps(
                changed_runtime["fingerprint"],
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode()
        ).hexdigest()
        runtime_path.write_text(json.dumps(changed_runtime), encoding="utf-8")
        try:
            validate_runtime_manifest(runtime_path)
        except ForwardEvidenceEraError:
            runtime_drift_rejected = True
        else:
            runtime_drift_rejected = False
        check(runtime_drift_rejected, "MUTATION installed-package drift invalidates the runtime")

        secret_runtime = json.loads(json.dumps(runtime_payload))
        secret_runtime["api_key"] = "forbidden"
        runtime_path.write_text(json.dumps(secret_runtime), encoding="utf-8")
        try:
            validate_runtime_manifest(runtime_path)
        except ForwardEvidenceEraError:
            secret_field_rejected = True
        else:
            secret_field_rejected = False
        check(secret_field_rejected, "MUTATION an extra secret-bearing field is rejected")

        runtime_path.write_text(json.dumps(runtime_payload), encoding="utf-8")
        missing_runtime_scope = json.loads(json.dumps(smoke_scope))
        missing_runtime_scope.pop("runtime_manifest")
        missing_runtime_scope.pop("scope_sha256")
        missing_runtime_scope["scope_sha256"] = hashlib.sha256(
            json.dumps(
                missing_runtime_scope,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode()
        ).hexdigest()
        missing_runtime_path = temp / "missing_runtime_scope.json"
        missing_runtime_path.write_text(json.dumps(missing_runtime_scope), encoding="utf-8")
        check(rejected(missing_runtime_path), "MUTATION a scope without runtime binding is rejected")

    print(f"\n{PASS}/{PASS + FAIL} checks passed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
