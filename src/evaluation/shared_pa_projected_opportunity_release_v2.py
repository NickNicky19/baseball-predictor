"""Exact-byte and observed-Git release boundary for the v2 PA candidate.

The tracked source manifest proves only the identity of reviewed bytes.  It
cannot truthfully contain the commit that contains itself.  A published
protocol therefore also requires a separate runtime receipt, written outside
the checkout after deployment, that is replayed against the observed clean Git
HEAD, the source-manifest bytes, and the protocol bytes.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from src.evaluation.projected_lineup_contract import sha256_value


class ProjectedOpportunityReleaseV2Error(ValueError):
    """The tracked bytes or runtime release identity are not exact."""


CANDIDATE_ID = "shared_pa_projected_opportunity_eb200_v2"
SOURCE_MANIFEST_RELATIVE = (
    "reports/shared_pa_projected_opportunity_candidate_v2_hash_manifest.json"
)
PROTOCOL_RELATIVE = "config/shared_pa_projected_opportunity_forward_v2.json"
SOURCE_MANIFEST_STATUS = "EXACT_BYTES_VALIDATED_RUNTIME_RELEASE_REQUIRED"
PENDING_PROTOCOL_STATUS = "RESEARCH_ONLY_EXACT_BYTES_RELEASE_PENDING"
PUBLISHED_PROTOCOL_STATUS = "EXACT_RELEASE_PUBLISHED"
RUNTIME_RECEIPT_SCHEMA = "shared-pa-projected-opportunity-runtime-release-v2"

REQUIRED_FILE_ROLES: dict[str, str] = {
    ".github/workflows/shared-pa-projected-opportunity-linux.yml": "exact_head_full_affected_linux_gate",
    "requirements-shared-pa-projected-opportunity-ci.lock": "exact_linux_test_lock",
    "config/shared_pa_projected_opportunity_forward_v1.json": "inherited_evaluation_and_promotion_contract",
    "config/shared_pa_projected_opportunity_forward_v2.json": "v2_protocol",
    "config/projected_lineup_contract_v1.json": "lineup_contract",
    "config/shared_pa_forward_evidence_contract_v1.json": "locked_control_contract",
    "data/analysis/system_integrity_v2/pa_volume_chronology_v1/pa_distribution_fit_2023.json": "locked_pa_volume",
    "scripts/check_shared_pa_projected_opportunity_manifest.py": "manifest_validator",
    "src/data/odds/base.py": "file_odds_boundary",
    "src/data/odds/odds_api_provider.py": "live_odds_source_boundary",
    "src/evaluation/market_economics.py": "canonical_price_parser",
    "src/evaluation/live_market_identity.py": "live_price_consumer",
    "src/evaluation/shadow_ledger.py": "shadow_price_consumer",
    "src/evaluation/projected_lineup_contract.py": "projected_lineup_semantic_contract",
    "src/evaluation/projected_lineup_contract_v2.py": "projected_lineup_strict_wrapper",
    "src/evaluation/projected_lineup_empirical_joint.py": "lineup_projection_replay",
    "src/evaluation/projected_lineup_history.py": "strict_prior_history_replay",
    "src/evaluation/projected_lineup_official_roster.py": "raw_roster_replay",
    "src/evaluation/prospective_batter_opportunity.py": "raw_history_and_schedule_replay",
    "src/evaluation/shadow_capture_plan.py": "immutable_t4_plan",
    "src/evaluation/shared_pa_batter_skill.py": "inherited_batter_skill_runtime",
    "src/evaluation/shared_pa_forward_collector.py": "schedule_and_stats_surface",
    "src/evaluation/shared_pa_forward_evidence.py": "locked_probability_math",
    "src/evaluation/shared_pa_projected_opportunity_candidate.py": "inherited_pa_mixture_runtime",
    "src/evaluation/shared_pa_projected_opportunity_candidate_v2.py": "v2_candidate",
    "src/evaluation/shared_pa_projected_opportunity_evidence_v2.py": "typed_raw_replay_envelope",
    "src/evaluation/shared_pa_projected_opportunity_release_v2.py": "runtime_exact_release_boundary",
    "src/evaluation/shared_pa_projected_opportunity_runner_v2.py": "v2_side_population_consumer",
    "scripts/publish_shared_pa_projected_opportunity_release_v2.py": "external_runtime_release_observer",
    "tests/test_prospective_batter_opportunity.py": "raw_history_replay_regressions",
    "tests/test_shared_pa_projected_opportunity_candidate_v2.py": "v2_integrity_mutations",
    "tests/test_shared_pa_projected_opportunity_runner_v2.py": "v2_consumer_boundary_mutations",
    "tests/test_shared_pa_projected_opportunity_release_observer_v2.py": "external_release_receipt_mutations",
    "tests/test_strict_american_odds_boundary.py": "price_boundary_mutations",
    "tests/test_odds_loader.py": "odds_source_regressions",
    "tests/test_market_economics_properties.py": "price_math_properties",
}

PROTECTED_BOUNDARIES = {
    "may_2026_accessed": False,
    "outcomes_accessed": False,
    "economic_evidence_accessed": False,
    "production_changed": False,
    "aws_changed": False,
    "betting_authorized": False,
}

RELEASE_CONTRACT = {
    "tracked_manifest_is_authorization": False,
    "published_protocol_requires_external_runtime_receipt": True,
    "runtime_receipt_must_bind_observed_clean_git_head": True,
    "runtime_receipt_must_bind_protocol_and_source_manifest": True,
}

_RELEASE_LIMITATION = (
    "A tracked manifest cannot truthfully self-bind its containing Git commit. "
    "Published confirmation eligibility therefore requires a separate runtime "
    "receipt bound to the observed clean exact HEAD, this manifest, and the protocol."
)


def _inside(repository: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(repository)
    except ValueError:
        return False
    return True


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _full_commit(value: Any, label: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise ProjectedOpportunityReleaseV2Error(f"{label} must be a full lowercase Git commit")
    return value


def validate_source_manifest_payload_v2(
    *, root: Path, payload: Mapping[str, Any]
) -> dict[str, Any]:
    """Validate exact required paths, roles, status, and protected values."""
    repository = root.resolve()
    expected_keys = {
        "schema_version", "status", "candidate_id", "supersedes_candidate_id",
        "release_commit", "release_commit_limitation", "release_contract",
        "files", "protected_boundaries",
    }
    if not isinstance(payload, Mapping) or set(payload) != expected_keys:
        raise ProjectedOpportunityReleaseV2Error("v2 source manifest field set changed")
    if (
        payload.get("schema_version")
        != "shared-pa-projected-opportunity-candidate-hash-manifest-v2"
        or payload.get("status") != SOURCE_MANIFEST_STATUS
        or payload.get("candidate_id") != CANDIDATE_ID
        or payload.get("supersedes_candidate_id")
        != "shared_pa_projected_opportunity_eb200_v1"
        or payload.get("release_commit") is not None
        or payload.get("release_commit_limitation") != _RELEASE_LIMITATION
        or payload.get("release_contract") != RELEASE_CONTRACT
        or payload.get("protected_boundaries") != PROTECTED_BOUNDARIES
    ):
        raise ProjectedOpportunityReleaseV2Error(
            "v2 source manifest release identity or protected boundary changed"
        )
    files = payload.get("files")
    if not isinstance(files, list) or len(files) != len(REQUIRED_FILE_ROLES):
        raise ProjectedOpportunityReleaseV2Error("v2 source manifest exact file set changed")
    observed_roles: dict[str, str] = {}
    for row in files:
        if not isinstance(row, Mapping) or set(row) != {"path", "size", "sha256", "role"}:
            raise ProjectedOpportunityReleaseV2Error("v2 source manifest row schema changed")
        relative = row.get("path")
        role = row.get("role")
        size = row.get("size")
        digest = row.get("sha256")
        if (
            not isinstance(relative, str)
            or relative in observed_roles
            or not isinstance(role, str)
            or isinstance(size, bool)
            or not isinstance(size, int)
            or size <= 0
            or not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        ):
            raise ProjectedOpportunityReleaseV2Error("v2 source manifest row identity changed")
        observed_roles[relative] = role
    if observed_roles != REQUIRED_FILE_ROLES:
        raise ProjectedOpportunityReleaseV2Error("v2 source manifest exact path or role set changed")
    for row in files:
        source = (repository / row["path"]).resolve()
        if not _inside(repository, source) or not source.is_file():
            raise ProjectedOpportunityReleaseV2Error(
                f"v2 source manifest path is invalid: {row['path']}"
            )
        raw = source.read_bytes()
        if len(raw) != row["size"] or hashlib.sha256(raw).hexdigest() != row["sha256"]:
            raise ProjectedOpportunityReleaseV2Error(
                f"v2 source manifest bytes differ: {row['path']}"
            )
    return dict(payload)


def load_source_manifest_v2(*, root: Path) -> tuple[dict[str, Any], str]:
    repository = root.resolve()
    path = (repository / SOURCE_MANIFEST_RELATIVE).resolve()
    if not _inside(repository, path) or not path.is_file():
        raise ProjectedOpportunityReleaseV2Error("v2 source manifest is missing")
    raw = path.read_bytes()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProjectedOpportunityReleaseV2Error("v2 source manifest is invalid JSON") from exc
    return validate_source_manifest_payload_v2(root=repository, payload=payload), hashlib.sha256(raw).hexdigest()


def _git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise ProjectedOpportunityReleaseV2Error("unable to observe exact Git release identity")
    return result.stdout.strip()


def observe_clean_git_release_v2(*, root: Path) -> tuple[str, bool]:
    repository = root.resolve()
    observed_root = Path(_git(repository, "rev-parse", "--show-toplevel")).resolve()
    if observed_root != repository:
        raise ProjectedOpportunityReleaseV2Error("release root differs from observed Git root")
    commit = _full_commit(_git(repository, "rev-parse", "HEAD").lower(), "observed HEAD")
    clean = _git(repository, "status", "--porcelain=v1", "--untracked-files=all") == ""
    return commit, clean


def validate_release_claim_v2(
    *,
    protocol_status: str,
    protocol_sha256: str,
    protocol_path: str,
    source_manifest_sha256: str,
    receipt_payload: Mapping[str, Any] | None,
    observed_commit: str | None,
    observed_clean: bool | None,
) -> dict[str, Any]:
    """Bind protocol status to an independently observed runtime release."""
    if protocol_status == PENDING_PROTOCOL_STATUS:
        if receipt_payload is not None or observed_commit is not None or observed_clean is not None:
            raise ProjectedOpportunityReleaseV2Error(
                "pending protocol cannot carry a published runtime release claim"
            )
        return {
            "release_state": "exact_source_bytes_release_pending",
            "confirmation_release_eligible": False,
            "source_manifest_sha256": source_manifest_sha256,
            "source_commit": None,
            "release_created_at_utc": None,
            "runtime_release_receipt_sha256": None,
        }
    if protocol_status != PUBLISHED_PROTOCOL_STATUS:
        raise ProjectedOpportunityReleaseV2Error("unknown v2 protocol release status")
    if receipt_payload is None or observed_commit is None or observed_clean is not True:
        raise ProjectedOpportunityReleaseV2Error(
            "published protocol requires a non-null receipt and observed clean exact HEAD"
        )
    expected_keys = {
        "schema_version", "candidate_id", "source_commit", "source_tree_clean",
        "source_manifest_path", "source_manifest_sha256", "candidate_protocol_path",
        "candidate_protocol_sha256", "candidate_protocol_status", "created_at_utc",
        "research_only", "betting_authorized", "release_receipt_sha256",
    }
    if not isinstance(receipt_payload, Mapping) or set(receipt_payload) != expected_keys:
        raise ProjectedOpportunityReleaseV2Error("runtime release receipt field set changed")
    unsigned = dict(receipt_payload)
    receipt_sha = unsigned.pop("release_receipt_sha256", None)
    try:
        created = datetime.fromisoformat(str(unsigned.get("created_at_utc", "")).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProjectedOpportunityReleaseV2Error(
            "runtime release receipt timestamp is invalid"
        ) from exc
    if created.tzinfo is None or created.utcoffset() is None:
        raise ProjectedOpportunityReleaseV2Error("runtime release receipt timestamp is naive")
    canonical_created = created.astimezone(timezone.utc).isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")
    source_commit = _full_commit(receipt_payload.get("source_commit"), "receipt source_commit")
    if (
        receipt_payload.get("schema_version") != RUNTIME_RECEIPT_SCHEMA
        or receipt_payload.get("candidate_id") != CANDIDATE_ID
        or receipt_payload.get("source_tree_clean") is not True
        or receipt_payload.get("source_manifest_path") != SOURCE_MANIFEST_RELATIVE
        or receipt_payload.get("source_manifest_sha256") != source_manifest_sha256
        or receipt_payload.get("candidate_protocol_path") != protocol_path
        or receipt_payload.get("candidate_protocol_sha256") != protocol_sha256
        or receipt_payload.get("candidate_protocol_status") != PUBLISHED_PROTOCOL_STATUS
        or receipt_payload.get("created_at_utc") != canonical_created
        or receipt_payload.get("research_only") is not True
        or receipt_payload.get("betting_authorized") is not False
        or receipt_sha != sha256_value(unsigned)
        or source_commit != _full_commit(observed_commit, "observed HEAD")
    ):
        raise ProjectedOpportunityReleaseV2Error(
            "runtime release receipt differs from protocol, manifest, or observed Git state"
        )
    return {
        "release_state": "exact_runtime_release_observed",
        "confirmation_release_eligible": True,
        "source_manifest_sha256": source_manifest_sha256,
        "source_commit": source_commit,
        "release_created_at_utc": canonical_created,
        "runtime_release_receipt_sha256": receipt_sha,
    }


def build_runtime_release_receipt_v2(
    *,
    protocol_status: str,
    protocol_sha256: str,
    protocol_path: str,
    source_manifest_sha256: str,
    observed_commit: str,
    observed_clean: bool,
    created_at_utc: str,
) -> dict[str, Any]:
    """Create and self-validate one external exact-release receipt payload."""
    if protocol_status != PUBLISHED_PROTOCOL_STATUS:
        raise ProjectedOpportunityReleaseV2Error(
            "only the published protocol can receive a runtime release receipt"
        )
    if observed_clean is not True:
        raise ProjectedOpportunityReleaseV2Error(
            "runtime release receipt requires an observed clean source tree"
        )
    source_commit = _full_commit(observed_commit, "observed HEAD")
    for value, label in (
        (protocol_sha256, "protocol_sha256"),
        (source_manifest_sha256, "source_manifest_sha256"),
    ):
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise ProjectedOpportunityReleaseV2Error(f"{label} must be a lowercase SHA-256")
    if protocol_path != PROTOCOL_RELATIVE:
        raise ProjectedOpportunityReleaseV2Error("runtime receipt protocol path changed")
    try:
        created = datetime.fromisoformat(str(created_at_utc).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProjectedOpportunityReleaseV2Error(
            "runtime release receipt timestamp is invalid"
        ) from exc
    if created.tzinfo is None or created.utcoffset() is None:
        raise ProjectedOpportunityReleaseV2Error("runtime release receipt timestamp is naive")
    if not math.isfinite(created.timestamp()):
        raise ProjectedOpportunityReleaseV2Error("runtime release receipt timestamp is not finite")
    canonical_created = created.astimezone(timezone.utc).isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")
    unsigned = {
        "schema_version": RUNTIME_RECEIPT_SCHEMA,
        "candidate_id": CANDIDATE_ID,
        "source_commit": source_commit,
        "source_tree_clean": True,
        "source_manifest_path": SOURCE_MANIFEST_RELATIVE,
        "source_manifest_sha256": source_manifest_sha256,
        "candidate_protocol_path": protocol_path,
        "candidate_protocol_sha256": protocol_sha256,
        "candidate_protocol_status": PUBLISHED_PROTOCOL_STATUS,
        "created_at_utc": canonical_created,
        "research_only": True,
        "betting_authorized": False,
    }
    receipt = {**unsigned, "release_receipt_sha256": sha256_value(unsigned)}
    validate_release_claim_v2(
        protocol_status=protocol_status,
        protocol_sha256=protocol_sha256,
        protocol_path=protocol_path,
        source_manifest_sha256=source_manifest_sha256,
        receipt_payload=receipt,
        observed_commit=source_commit,
        observed_clean=True,
    )
    return receipt


def resolve_release_identity_v2(
    *, root: Path, protocol_status: str, protocol_sha256: str,
    protocol_source_path: Path, runtime_release_receipt_path: Path | None,
    decision_time_utc: str | None = None,
) -> dict[str, Any]:
    repository = root.resolve()
    _, manifest_sha = load_source_manifest_v2(root=repository)
    protocol_path = protocol_source_path.resolve()
    if not _inside(repository, protocol_path):
        raise ProjectedOpportunityReleaseV2Error("v2 protocol path is outside release root")
    relative_protocol = protocol_path.relative_to(repository).as_posix()
    if relative_protocol != PROTOCOL_RELATIVE or _sha256(protocol_path) != protocol_sha256:
        raise ProjectedOpportunityReleaseV2Error("v2 protocol bytes differ at release boundary")
    if protocol_status == PENDING_PROTOCOL_STATUS:
        return validate_release_claim_v2(
            protocol_status=protocol_status,
            protocol_sha256=protocol_sha256,
            protocol_path=relative_protocol,
            source_manifest_sha256=manifest_sha,
            receipt_payload=None,
            observed_commit=None,
            observed_clean=None,
        )
    if runtime_release_receipt_path is None:
        raise ProjectedOpportunityReleaseV2Error(
            "published protocol has no runtime release receipt"
        )
    receipt_path = runtime_release_receipt_path.resolve()
    if _inside(repository, receipt_path) or not receipt_path.is_file():
        raise ProjectedOpportunityReleaseV2Error(
            "runtime release receipt must be a retained file outside the source checkout"
        )
    try:
        receipt = json.loads(receipt_path.read_bytes())
    except json.JSONDecodeError as exc:
        raise ProjectedOpportunityReleaseV2Error("runtime release receipt is invalid JSON") from exc
    observed_commit, observed_clean = observe_clean_git_release_v2(root=repository)
    identity = validate_release_claim_v2(
        protocol_status=protocol_status,
        protocol_sha256=protocol_sha256,
        protocol_path=relative_protocol,
        source_manifest_sha256=manifest_sha,
        receipt_payload=receipt,
        observed_commit=observed_commit,
        observed_clean=observed_clean,
    )
    if decision_time_utc is not None:
        try:
            decision = datetime.fromisoformat(
                str(decision_time_utc).replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise ProjectedOpportunityReleaseV2Error(
                "release decision timestamp is invalid"
            ) from exc
        if decision.tzinfo is None or decision.utcoffset() is None:
            raise ProjectedOpportunityReleaseV2Error(
                "release decision timestamp is naive"
            )
        created = datetime.fromisoformat(
            str(identity["release_created_at_utc"]).replace("Z", "+00:00")
        )
        if created > decision:
            raise ProjectedOpportunityReleaseV2Error(
                "runtime release receipt was created after the prediction decision time"
            )
    return identity
