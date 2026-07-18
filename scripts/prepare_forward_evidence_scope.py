#!/usr/bin/env python3
"""Create or verify one immutable smoke/forward evidence scope."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.forward_evidence_era import (  # noqa: E402
    ForwardEvidenceEraError,
    build_evidence_scope,
    build_runtime_manifest,
    validate_evidence_scope,
    validate_runtime_manifest,
)


def _git(*args: str) -> str:
    result = subprocess.run(["git", *args], cwd=ROOT, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise ForwardEvidenceEraError("Git release identity is unavailable")
    return result.stdout.strip()


def _source_tree_clean() -> bool:
    """Require a genuinely clean release, including nonignored untracked files.

    Ignoring untracked files here would let an uncommitted Python module or
    configuration influence a supposedly immutable economic evidence era.
    Git-ignored runtime evidence remains outside this source-tree contract.
    """

    return not bool(_git("status", "--porcelain", "--untracked-files=all"))


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mode", required=True, choices=("operational_smoke", "forward_evidence"))
    ap.add_argument("--era-id", required=True)
    ap.add_argument("--service-root", required=True)
    ap.add_argument("--readiness", required=True, help="Exact current readiness report to bind")
    ap.add_argument("--boundary", default="config/forward_shadow_evidence_boundary.json")
    ap.add_argument("--protocol", default="config/forward_shadow_deployment_protocol.json")
    ap.add_argument("--products", default="config/hits_execution_product_contracts.json")
    ap.add_argument("--smoke-certificate")
    args = ap.parse_args(argv)

    service_root = Path(args.service_root).resolve()
    output = service_root / "evidence_scope.json"
    runtime_manifest_path = service_root / "runtime_manifest.json"
    if output.exists():
        scope = validate_evidence_scope(output, root=ROOT)
        if scope["mode"] != args.mode or scope["era_id"] != args.era_id:
            raise ForwardEvidenceEraError("service root already belongs to a different immutable scope")
        print(f"EVIDENCE SCOPE VERIFIED {scope['scope_sha256']}")
        return 0

    if runtime_manifest_path.exists():
        validate_runtime_manifest(runtime_manifest_path)
    else:
        _write_json_atomic(
            runtime_manifest_path,
            build_runtime_manifest(
                created_at_utc=datetime.now(timezone.utc).isoformat(timespec="seconds")
            ),
        )
        validate_runtime_manifest(runtime_manifest_path)

    payload = build_evidence_scope(
        mode=args.mode,
        era_id=args.era_id,
        created_at_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        readiness_report=Path(args.readiness).resolve(),
        boundary_path=Path(args.boundary).resolve(),
        deployment_protocol_path=Path(args.protocol).resolve(),
        product_contracts_path=Path(args.products).resolve(),
        runtime_manifest_path=runtime_manifest_path,
        source_commit=_git("rev-parse", "HEAD"),
        source_tree_clean=_source_tree_clean(),
        root=ROOT,
        smoke_certificate=Path(args.smoke_certificate).resolve() if args.smoke_certificate else None,
    )
    _write_json_atomic(output, payload)
    validate_evidence_scope(output, root=ROOT)
    print(f"EVIDENCE SCOPE CREATED {payload['scope_sha256']}")
    print(f"  mode: {payload['mode']}; economic evidence eligible: {payload['economic_evidence_eligible']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as exc:
        print(f"FATAL: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2)
