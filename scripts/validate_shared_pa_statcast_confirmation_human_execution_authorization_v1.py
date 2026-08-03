"""Fail-closed offline validator for a future one-time confirmation authorization.

This module performs no networking. GitHub actor, run, and artifact snapshots must
be obtained by the workflow before invoking this validator.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class AuthorizationError(RuntimeError):
    pass


FUTURE_RECORD_PATH = "config/shared_pa_statcast_confirmation_attempt_01_human_execution_authorization_v1.json"
WORKFLOW_PATH = ".github/workflows/shared-pa-statcast-v2-confirmation-execution-v1.yml"
EXPECTED_REPOSITORY = "NickNicky19/baseball-predictor"
EXPECTED_ACTOR_LOGIN = "NickNicky19"
EXPECTED_ACTOR_ID = 208912933
MAX_VALIDITY_SECONDS = 1800
GAME_PKS = [717575, 717576, 717577, 717578, 717579, 717580, 717581, 717582, 717583, 717584, 717585, 717586, 717587, 717588, 717589]
HASH_BINDINGS = {
    "authority_package_sha256": "31181de53446951a4d48c5740e4687c36fb042005e1466ff09a9ac9e1cf3ef0d",
    "execution_contract_sha256": "851e02beab8732b8f6d1a242ea4609d30ab1f3b462804134ae8194b3d42dc510",
    "attempt_ledger_sha256": "4f7aea30afdbff76fedd695ef3bd83bb23e5395f4db9496405552433695d9c56",
    "runtime_authority_sha256": "f348f1baf664fa12c821eb488b4ff4c8fb293b3e243d713600efcd0da96fbf2a",
    "source_bundle_sha256": "81861595f8eb4b83772220bdad2fb556fc06ec3640a1a4f2084255e8f5183033",
    "source_contract_v1_sha256": "7078857cc1815e5c8dcecd667a9b6d69079ef31c1fc68057473d1fb1dc9ceebd",
    "source_contract_v2_proposal_sha256": "166c49a5ea66bf4491c9d04c7f233c7bc51c032683b9883b695236dabc637328",
    "request_plan_sha256": "0bcc3c9482ea67189e414502b9b58e6445cd66efeb27c309c5bcff4864108402",
    "full_request_url_sha256": "0efc234b3ef8e8490550bc11a28896bbc1e85ca39b31bf503484c65ed9d96aa9",
    "query_sha256": "16920ee504c363fb27b19b7100d8fc54c95649054a9357af887e4ab51ef58e23",
}
FILE_BINDINGS = {
    "authority_package_sha256": "config/shared_pa_statcast_confirmation_attempt_01_authority_package_20260802_v1.json",
    "execution_contract_sha256": "config/shared_pa_statcast_confirmation_execution_contract_v1.json",
    "attempt_ledger_sha256": "config/shared_pa_statcast_confirmation_sample_2023-06-28_attempt_history_v1.json",
    "runtime_authority_sha256": "config/shared_pa_statcast_confirmation_runtime_authority_v1.json",
    "source_contract_v1_sha256": "config/shared_pa_statcast_source_contract_v1.json",
    "source_contract_v2_proposal_sha256": "config/shared_pa_statcast_source_contract_v2_proposal.json",
    "request_plan_sha256": "config/shared_pa_statcast_confirmation_sample_2023-06-28_request_plan_v1.json",
}
TOP_LEVEL_KEYS = {
    "schema_version", "status", "authorization_id", "repository", "authorized_actor",
    "bindings", "scope", "validity", "canonical_human_authorization_text",
    "canonical_human_authorization_text_sha256", "preparation_is_execution_authorization",
    "dispatch_authorized", "single_use",
}
GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def _object_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise AuthorizationError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_object_no_duplicates)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AuthorizationError(f"cannot load JSON: {path}") from exc
    if not isinstance(value, dict):
        raise AuthorizationError("JSON root must be an object")
    return value


def _require_exact_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise AuthorizationError(f"{label} keys differ")


def _reject_placeholders(value: Any, path: str = "record") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            _reject_placeholders(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_placeholders(item, f"{path}[{index}]")
    elif isinstance(value, str):
        stripped = value.strip()
        if not stripped or re.search(r"(?i)(placeholder|tbd|todo|changeme|replace[_ -]?me)", stripped):
            raise AuthorizationError(f"placeholder value: {path}")
        if re.fullmatch(r"0{40}|0{64}", stripped):
            raise AuthorizationError(f"zero identity: {path}")


def _parse_utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", value):
        raise AuthorizationError(f"{label} is not strict UTC")
    try:
        result = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise AuthorizationError(f"{label} is invalid") from exc
    if result.tzinfo != timezone.utc:
        raise AuthorizationError(f"{label} is not UTC")
    return result


def deterministic_artifact_name(authorization_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", authorization_id):
        raise AuthorizationError("authorization ID is unsafe")
    return f"shared-pa-statcast-v2-confirmation-2023-06-28-attempt-01-auth-{authorization_id}"


def verify_single_use_snapshot(
    runs: dict[str, Any], artifacts: dict[str, Any], current_run_id: int,
    authorization_id: str, authorization_sha256: str,
) -> None:
    if not isinstance(runs.get("workflow_runs", []), list) or not isinstance(artifacts.get("artifacts", []), list):
        raise AuthorizationError("GitHub single-use snapshot is malformed")
    id_marker = f"authorization={authorization_id}"
    hash_marker = f"record={authorization_sha256}"
    for run in runs.get("workflow_runs", []):
        if not isinstance(run, dict):
            raise AuthorizationError("workflow run snapshot entry is malformed")
        if int(run.get("id", -1)) == current_run_id:
            continue
        title = str(run.get("display_title", ""))
        if id_marker in title:
            raise AuthorizationError("authorization ID was already used")
        if hash_marker in title:
            raise AuthorizationError("authorization record hash was already used")
        if run.get("event") in (None, "workflow_dispatch"):
            raise AuthorizationError("a prior confirmation dispatch already exists")
    expected_artifact = deterministic_artifact_name(authorization_id)
    for artifact in artifacts.get("artifacts", []):
        if not isinstance(artifact, dict):
            raise AuthorizationError("artifact snapshot entry is malformed")
        name = str(artifact.get("name", ""))
        if name == expected_artifact or name.startswith("shared-pa-statcast-v2-confirmation-2023-06-28-attempt-01-"):
            raise AuthorizationError("confirmation attempt terminal artifact already exists")


def _git(repository: Path, *args: str, text: bool = True) -> str | bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(repository), *args], check=True,
            capture_output=True, text=text,
        )
    except subprocess.CalledProcessError as exc:
        raise AuthorizationError(f"Git evidence command failed: {' '.join(args)}") from exc
    return result.stdout


def _record_contains_forbidden_commit(record: Any, forbidden: set[str]) -> bool:
    if isinstance(record, dict):
        return any(_record_contains_forbidden_commit(value, forbidden) for value in record.values())
    if isinstance(record, list):
        return any(_record_contains_forbidden_commit(value, forbidden) for value in record)
    return isinstance(record, str) and record in forbidden


def verify_non_circular_merge(
    repository: Path,
    record_path: Path,
    record: dict[str, Any],
    expected_record_sha256: str,
    expected_dispatch_commit: str,
    observed_dispatch_commit: str,
    observed_first_parent: str,
    observed_default_branch_head: str,
) -> dict[str, str]:
    """Verify B -> (B,H) -> M without requiring M inside the record."""
    for value, label in (
        (expected_dispatch_commit, "expected dispatch commit"),
        (observed_dispatch_commit, "observed dispatch commit"),
        (observed_first_parent, "observed first parent"),
        (observed_default_branch_head, "observed default-branch head"),
    ):
        if not isinstance(value, str) or not GIT_SHA_RE.fullmatch(value):
            raise AuthorizationError(f"{label} is missing or malformed")
    if expected_dispatch_commit != observed_dispatch_commit:
        raise AuthorizationError("expected dispatch commit differs from observed dispatch commit")
    if observed_default_branch_head != observed_dispatch_commit:
        raise AuthorizationError("current default-branch head is stale or differs")
    bindings = record.get("bindings")
    if not isinstance(bindings, dict):
        raise AuthorizationError("bindings are malformed")
    authorization_base = bindings.get("authorization_base_main_commit")
    if not isinstance(authorization_base, str) or not GIT_SHA_RE.fullmatch(authorization_base):
        raise AuthorizationError("authorization base-main commit is malformed")
    if authorization_base != observed_first_parent:
        raise AuthorizationError("authorization base-main commit differs from observed first parent")
    parent_line = str(_git(repository, "show", "-s", "--format=%P", observed_dispatch_commit)).strip()
    parents = parent_line.split()
    if len(parents) != 2:
        raise AuthorizationError("dispatch commit is not an exact two-parent merge commit")
    if parents[0] != observed_first_parent:
        raise AuthorizationError("independently observed first parent differs from Git commit evidence")
    second_parent = parents[1]
    if _record_contains_forbidden_commit(record, {observed_dispatch_commit, second_parent}):
        raise AuthorizationError("authorization record embeds its PR head or eventual merge commit")
    raw = str(_git(
        repository, "diff-tree", "--no-commit-id", "--raw", "-r", "--no-renames",
        observed_first_parent, observed_dispatch_commit,
    )).splitlines()
    raw = [line for line in raw if line.strip()]
    if len(raw) != 1:
        raise AuthorizationError("first-parent delta does not contain exactly one path")
    match = re.fullmatch(
        r":([0-7]{6}) ([0-7]{6}) ([0-9a-f]+) ([0-9a-f]+) ([A-Z][0-9]*)\t(.+)",
        raw[0],
    )
    if match is None:
        raise AuthorizationError("first-parent raw delta is malformed")
    old_mode, new_mode, old_oid, _new_oid, status, changed_path = match.groups()
    if changed_path != FUTURE_RECORD_PATH or status != "A":
        raise AuthorizationError("first-parent delta is not the exact authorization-record addition")
    if old_mode != "000000" or set(old_oid) != {"0"}:
        raise AuthorizationError("authorization-record path existed in the authorization base")
    if new_mode != "100644":
        raise AuthorizationError("authorization record is not a regular non-executable file")
    expected_path = (repository / FUTURE_RECORD_PATH).resolve()
    if record_path.resolve() != expected_path or not record_path.is_file() or record_path.is_symlink():
        raise AuthorizationError("authorization record path or working-tree file type differs")
    merged_bytes = _git(repository, "show", f"{observed_dispatch_commit}:{FUTURE_RECORD_PATH}", text=False)
    if not isinstance(merged_bytes, bytes):
        raise AuthorizationError("authorization record blob could not be read")
    if sha256_bytes(merged_bytes) != expected_record_sha256 or merged_bytes != record_path.read_bytes():
        raise AuthorizationError("authorization record blob or SHA-256 mismatch")
    return {
        "authorization_base_main_commit": authorization_base,
        "dispatch_commit": observed_dispatch_commit,
        "first_parent": parents[0],
        "second_parent": second_parent,
        "first_parent_delta": FUTURE_RECORD_PATH,
    }


def validate_record(
    record_path: Path, schema_path: Path, repository_root: Path,
    expected_record_sha256: str, expected_text_sha256: str, expected_authorization_id: str,
    observed_repository: str, observed_actor_login: str, observed_actor_id: int,
    expected_dispatch_commit: str, observed_dispatch_commit: str,
    observed_first_parent: str, observed_default_branch_head: str,
    observed_workflow_sha256: str, run_attempt: int,
    now: datetime, runs: dict[str, Any], artifacts: dict[str, Any], current_run_id: int,
    output_path_exists: bool | None = None,
) -> dict[str, Any]:
    if not schema_path.is_file() or schema_path.is_symlink():
        raise AuthorizationError("authorization schema is absent or unsafe")
    if not record_path.is_file() or record_path.is_symlink():
        raise AuthorizationError("authorization record is absent or unsafe")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_record_sha256) or sha256_file(record_path) != expected_record_sha256:
        raise AuthorizationError("authorization record SHA-256 mismatch")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_text_sha256):
        raise AuthorizationError("human authorization text SHA-256 is malformed")
    if expected_record_sha256 in HASH_BINDINGS.values() or expected_record_sha256 == expected_text_sha256:
        raise AuthorizationError("authorization record cannot authorize itself or reuse another authority identity")
    if run_attempt != 1:
        raise AuthorizationError("workflow reruns are forbidden")
    record = load_json(record_path)
    _require_exact_keys(record, TOP_LEVEL_KEYS, "record")
    _reject_placeholders(record)
    if record.get("schema_version") != "shared-pa-statcast-confirmation-human-execution-authorization-v1" or record.get("status") != "ACTIVE_ONE_TIME":
        raise AuthorizationError("authorization record is not active")
    if record.get("preparation_is_execution_authorization") is not False or record.get("dispatch_authorized") is not True or record.get("single_use") is not True:
        raise AuthorizationError("authorization flags differ")
    authorization_id = record.get("authorization_id")
    if authorization_id != expected_authorization_id or not isinstance(authorization_id, str):
        raise AuthorizationError("authorization ID mismatch")
    deterministic_artifact_name(authorization_id)
    repository = record.get("repository")
    actor = record.get("authorized_actor")
    if not isinstance(repository, dict) or not isinstance(actor, dict):
        raise AuthorizationError("repository or actor binding is malformed")
    _require_exact_keys(repository, {"owner", "name", "full_name"}, "repository")
    _require_exact_keys(actor, {"login", "numeric_user_id"}, "authorized_actor")
    if repository != {"owner": "NickNicky19", "name": "baseball-predictor", "full_name": EXPECTED_REPOSITORY} or observed_repository != EXPECTED_REPOSITORY:
        raise AuthorizationError("repository identity mismatch")
    if actor != {"login": EXPECTED_ACTOR_LOGIN, "numeric_user_id": EXPECTED_ACTOR_ID}:
        raise AuthorizationError("authorized actor identity mismatch")
    if observed_actor_login != EXPECTED_ACTOR_LOGIN or observed_actor_id != EXPECTED_ACTOR_ID:
        raise AuthorizationError("dispatching actor identity mismatch")
    bindings = record.get("bindings")
    if not isinstance(bindings, dict):
        raise AuthorizationError("bindings are malformed")
    expected_binding_keys = {"authorization_base_main_commit", "workflow_path", "workflow_sha256", "carrier_commit", *HASH_BINDINGS}
    _require_exact_keys(bindings, expected_binding_keys, "bindings")
    authorization_base = bindings.get("authorization_base_main_commit")
    if not isinstance(authorization_base, str) or not GIT_SHA_RE.fullmatch(authorization_base):
        raise AuthorizationError("authorization base-main commit mismatch")
    if bindings.get("workflow_path") != WORKFLOW_PATH or bindings.get("workflow_sha256") != observed_workflow_sha256:
        raise AuthorizationError("workflow identity mismatch")
    if bindings.get("carrier_commit") != "a8ebc64eddf8ace3a48e25af57ee0d6658d0663a":
        raise AuthorizationError("carrier identity mismatch")
    graph = verify_non_circular_merge(
        repository_root, record_path, record, expected_record_sha256,
        expected_dispatch_commit, observed_dispatch_commit, observed_first_parent,
        observed_default_branch_head,
    )
    for key, expected in HASH_BINDINGS.items():
        if bindings.get(key) != expected:
            raise AuthorizationError(f"package identity mismatch: {key}")
    for key, relative in FILE_BINDINGS.items():
        path = repository_root / relative
        if not path.is_file() or path.is_symlink() or sha256_file(path) != bindings[key]:
            raise AuthorizationError(f"repository file identity mismatch: {key}")
    scope = record.get("scope")
    expected_scope = {
        "official_date": "2023-06-28", "certified_game_pks": GAME_PKS,
        "output_path": "data/source/shared_pa_statcast_v2_confirmation_2023-06-28_v1",
        "confirmation_attempt_number": 1, "maximum_request_count": 1,
        "automatic_http_retry_maximum": 0, "replacement_request_maximum": 0,
        "automatic_workflow_rerun_allowed": False, "redirects_allowed": False, "no_overwrite": True,
    }
    if scope != expected_scope:
        raise AuthorizationError("confirmation scope mismatch")
    ledger = load_json(repository_root / FILE_BINDINGS["attempt_ledger_sha256"])
    if ledger.get("attempts") != [] or ledger.get("next_attempt") != {"attempt_number": 1, "status": "UNUSED_UNAUTHORIZED", "reserved": False, "consumed": False}:
        raise AuthorizationError("confirmation attempt 1 is not unused and unreserved")
    if ledger.get("global_real_external_statcast_request_count") != 3:
        raise AuthorizationError("global request accounting mismatch")
    old_ledger = load_json(repository_root / "config/shared_pa_statcast_sample_attempt_history_20260802_v3.json")
    if old_ledger.get("remaining_attempts") != [{"attempt_number": 4, "status": "UNUSED_UNAUTHORIZED"}]:
        raise AuthorizationError("old July attempt 4 state mismatch")
    output_path = repository_root / expected_scope["output_path"]
    observed_output_exists = output_path.exists() or Path(str(output_path) + ".work").exists()
    if output_path_exists is not None:
        observed_output_exists = output_path_exists
    if observed_output_exists:
        raise AuthorizationError("no-overwrite output path already exists")
    tracked = subprocess.run(["git", "-C", str(repository_root), "ls-files"], check=True, capture_output=True, text=True).stdout.splitlines()
    if any("shared_pa_statcast_v2_confirmation_2023-06-28_v1" in item or "response-attempt-01" in item for item in tracked):
        raise AuthorizationError("June 28 response is no longer unseen")
    validity = record.get("validity")
    if not isinstance(validity, dict):
        raise AuthorizationError("validity is malformed")
    _require_exact_keys(validity, {"authorization_issued_utc", "authorization_valid_from_utc", "authorization_expires_utc", "maximum_validity_seconds"}, "validity")
    issued = _parse_utc(validity.get("authorization_issued_utc"), "authorization issued")
    valid_from = _parse_utc(validity.get("authorization_valid_from_utc"), "authorization valid from")
    expires = _parse_utc(validity.get("authorization_expires_utc"), "authorization expires")
    if validity.get("maximum_validity_seconds") != MAX_VALIDITY_SECONDS or not issued <= valid_from <= now < expires:
        raise AuthorizationError("authorization is stale, future-dated, or expired")
    if (expires - valid_from).total_seconds() > MAX_VALIDITY_SECONDS or (expires - valid_from).total_seconds() <= 0:
        raise AuthorizationError("authorization validity interval is too long or empty")
    text = record.get("canonical_human_authorization_text")
    if not isinstance(text, str) or not text.endswith("\n") or text.endswith("\n\n") or "\r" in text or text.startswith("\ufeff"):
        raise AuthorizationError("canonical human authorization text encoding differs")
    if any(line.endswith((" ", "\t")) for line in text.splitlines()):
        raise AuthorizationError("canonical human authorization text has trailing whitespace")
    observed_text_sha = sha256_bytes(text.encode("utf-8"))
    if record.get("canonical_human_authorization_text_sha256") != observed_text_sha or expected_text_sha256 != observed_text_sha:
        raise AuthorizationError("canonical human authorization text SHA-256 mismatch")
    verify_single_use_snapshot(runs, artifacts, current_run_id, authorization_id, expected_record_sha256)
    return {
        "schema_version": "shared-pa-statcast-confirmation-human-execution-authorization-validation-v1",
        "status": "PASS",
        "authorization_id": authorization_id,
        "authorization_record_sha256": expected_record_sha256,
        "canonical_human_authorization_text_sha256": observed_text_sha,
        "authorized_actor_login": observed_actor_login,
        "authorized_actor_numeric_user_id": observed_actor_id,
        "authorization_base_main_commit": graph["authorization_base_main_commit"],
        "dispatch_commit": graph["dispatch_commit"],
        "observed_first_parent": graph["first_parent"],
        "observed_second_parent": graph["second_parent"],
        "first_parent_delta": graph["first_parent_delta"],
        "workflow_sha256": observed_workflow_sha256,
        "valid_at_utc": now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "terminal_artifact_name": deterministic_artifact_name(authorization_id),
        "single_use_snapshot_passed": True,
        "transport_authorized_by_validator": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", type=Path, required=True)
    parser.add_argument("--schema", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--expected-record-sha256", required=True)
    parser.add_argument("--expected-text-sha256", required=True)
    parser.add_argument("--expected-authorization-id", required=True)
    parser.add_argument("--observed-repository", required=True)
    parser.add_argument("--observed-actor-json", type=Path, required=True)
    parser.add_argument("--expected-dispatch-commit", required=True)
    parser.add_argument("--observed-dispatch-commit", required=True)
    parser.add_argument("--observed-first-parent", required=True)
    parser.add_argument("--observed-default-branch-head", required=True)
    parser.add_argument("--observed-workflow-sha256", required=True)
    parser.add_argument("--run-attempt", type=int, required=True)
    parser.add_argument("--current-run-id", type=int, required=True)
    parser.add_argument("--runs-json", type=Path, required=True)
    parser.add_argument("--artifacts-json", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args(argv)
    actor = load_json(args.observed_actor_json)
    receipt = validate_record(
        args.record.resolve(), args.schema.resolve(), args.repository_root.resolve(),
        args.expected_record_sha256, args.expected_text_sha256, args.expected_authorization_id,
        args.observed_repository, str(actor.get("login", "")), int(actor.get("id", -1)),
        args.expected_dispatch_commit, args.observed_dispatch_commit,
        args.observed_first_parent, args.observed_default_branch_head,
        args.observed_workflow_sha256, args.run_attempt,
        datetime.now(timezone.utc), load_json(args.runs_json), load_json(args.artifacts_json), args.current_run_id,
    )
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AuthorizationError as exc:
        print(f"AUTHORIZATION_GATE=FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
