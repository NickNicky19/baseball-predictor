"""Manage the research-only shared-PA experiment registry.

This command handles metadata governance only.  It cannot fit, score, select,
promote, deploy, or authorize betting.  The checked-in policy intentionally
keeps the already-spent 2024 selection token unissued and unconsumed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.shared_pa_experiment_registry import (  # noqa: E402
    SharedPAExperimentRegistryError,
    append_event,
    initialize_registry,
    verify_registry,
    write_trusted_checkpoint_once,
)


def _json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SharedPAExperimentRegistryError("event payload must be a JSON object")
    return value


def _print_state(state) -> None:
    print(json.dumps(state.trusted_checkpoint(), sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    initialize = subparsers.add_parser("initialize")
    initialize.add_argument("--registry-root", required=True, type=Path)
    initialize.add_argument(
        "--policy",
        type=Path,
        default=ROOT / "config/shared_pa_experiment_registry_v1.json",
    )
    initialize.add_argument("--artifact-root", type=Path, default=ROOT)
    initialize.add_argument("--trusted-checkpoint-out", required=True, type=Path)
    initialize.add_argument("--external-release-expectation", type=Path)
    initialize.add_argument("--expected-release-expectation-sha256")

    verify = subparsers.add_parser("verify")
    verify.add_argument("--registry-root", required=True, type=Path)
    verify.add_argument("--artifact-root", type=Path, default=ROOT)
    verify.add_argument("--external-release-expectation", required=True, type=Path)
    verify.add_argument("--expected-release-expectation-sha256", required=True)
    verify.add_argument("--trusted-checkpoint", required=True, type=Path)
    verify.add_argument("--expected-trusted-checkpoint-sha256", required=True)

    append = subparsers.add_parser("append")
    append.add_argument("--registry-root", required=True, type=Path)
    append.add_argument("--artifact-root", type=Path, default=ROOT)
    append.add_argument("--trusted-checkpoint", required=True, type=Path)
    append.add_argument("--expected-trusted-checkpoint-sha256", required=True)
    append.add_argument("--external-release-expectation", required=True, type=Path)
    append.add_argument("--expected-release-expectation-sha256", required=True)
    append.add_argument("--append-receipt", required=True, type=Path)
    append.add_argument("--expected-append-receipt-sha256", required=True)
    append.add_argument("--entry-type", required=True)
    append.add_argument("--payload", required=True, type=Path)
    append.add_argument("--next-trusted-checkpoint-out", required=True, type=Path)

    args = parser.parse_args()
    if args.command == "initialize":
        state = initialize_registry(
            root=args.registry_root,
            policy_path=args.policy,
            artifact_root=args.artifact_root,
            external_release_expectation_path=args.external_release_expectation,
            expected_release_expectation_sha256=args.expected_release_expectation_sha256,
        )
        write_trusted_checkpoint_once(args.trusted_checkpoint_out, state)
    elif args.command == "verify":
        state = verify_registry(
            root=args.registry_root,
            artifact_root=args.artifact_root,
            external_release_expectation_path=args.external_release_expectation,
            expected_release_expectation_sha256=args.expected_release_expectation_sha256,
            trusted_checkpoint_path=args.trusted_checkpoint,
            expected_trusted_checkpoint_sha256=args.expected_trusted_checkpoint_sha256,
        )
    else:
        state = append_event(
            root=args.registry_root,
            artifact_root=args.artifact_root,
            entry_type=args.entry_type,
            payload=_json(args.payload),
            external_release_expectation_path=args.external_release_expectation,
            expected_release_expectation_sha256=args.expected_release_expectation_sha256,
            trusted_checkpoint_path=args.trusted_checkpoint,
            expected_trusted_checkpoint_sha256=args.expected_trusted_checkpoint_sha256,
            append_receipt_path=args.append_receipt,
            expected_append_receipt_sha256=args.expected_append_receipt_sha256,
        )
        write_trusted_checkpoint_once(args.next_trusted_checkpoint_out, state)
    _print_state(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
