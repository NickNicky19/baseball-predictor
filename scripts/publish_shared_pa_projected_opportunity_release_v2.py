#!/usr/bin/env python3
"""Publish one external, immutable exact-release receipt for the v2 candidate."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.projected_lineup_contract import canonical_bytes
from src.evaluation.shared_pa_projected_opportunity_release_v2 import (
    CANDIDATE_ID,
    PROTOCOL_RELATIVE,
    PUBLISHED_PROTOCOL_STATUS,
    ProjectedOpportunityReleaseV2Error,
    build_runtime_release_receipt_v2,
    load_source_manifest_v2,
    observe_clean_git_release_v2,
    validate_release_claim_v2,
)


def _inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def _external_destination(*, root: Path, output: Path) -> Path:
    requested_parent = output.parent.absolute()
    resolved_parent = requested_parent.resolve(strict=True)
    if requested_parent != resolved_parent:
        raise ProjectedOpportunityReleaseV2Error(
            "external receipt path cannot traverse a symlinked directory"
        )
    destination = requested_parent / output.name
    if _inside(root.resolve(), destination.resolve(strict=False)):
        raise ProjectedOpportunityReleaseV2Error(
            "runtime release receipt must remain outside the source checkout"
        )
    return destination


def _protocol(root: Path) -> tuple[Path, Mapping[str, Any], str]:
    path = (root / PROTOCOL_RELATIVE).resolve()
    raw = path.read_bytes()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProjectedOpportunityReleaseV2Error("v2 protocol is invalid JSON") from exc
    if (
        not isinstance(payload, Mapping)
        or payload.get("candidate_id") != CANDIDATE_ID
        or payload.get("status") != PUBLISHED_PROTOCOL_STATUS
    ):
        raise ProjectedOpportunityReleaseV2Error(
            "external receipt requires the exact published v2 protocol"
        )
    import hashlib

    return path, payload, hashlib.sha256(raw).hexdigest()


def _publish_once(path: Path, payload: bytes) -> bool:
    if not path.parent.is_dir() or path.parent.is_symlink():
        raise ProjectedOpportunityReleaseV2Error(
            "external receipt parent must be an existing non-link directory"
        )
    if path.exists() or path.is_symlink():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != payload:
            raise ProjectedOpportunityReleaseV2Error(
                "existing external release receipt differs or is not a regular file"
            )
        return False
    with tempfile.NamedTemporaryFile(
        dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        temporary = Path(handle.name)
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, 0o640)
    try:
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.is_symlink() or not path.is_file() or path.read_bytes() != payload:
                raise ProjectedOpportunityReleaseV2Error(
                    "concurrent external release receipt differs"
                )
            return False
    finally:
        temporary.unlink(missing_ok=True)
    if path.read_bytes() != payload:
        raise ProjectedOpportunityReleaseV2Error(
            "published external release receipt failed byte replay"
        )
    return True


def publish(*, root: Path, output: Path) -> dict[str, Any]:
    repository = root.resolve()
    destination = _external_destination(root=repository, output=output)
    protocol_path, protocol, protocol_sha = _protocol(repository)
    _, manifest_sha = load_source_manifest_v2(root=repository)
    commit, clean = observe_clean_git_release_v2(root=repository)
    receipt = build_runtime_release_receipt_v2(
        protocol_status=str(protocol["status"]),
        protocol_sha256=protocol_sha,
        protocol_path=PROTOCOL_RELATIVE,
        source_manifest_sha256=manifest_sha,
        observed_commit=commit,
        observed_clean=clean,
        created_at_utc=datetime.now(timezone.utc).isoformat(
            timespec="microseconds"
        ).replace("+00:00", "Z"),
    )
    raw = canonical_bytes(receipt) + b"\n"
    final_commit, final_clean = observe_clean_git_release_v2(root=repository)
    validate_release_claim_v2(
        protocol_status=str(protocol["status"]),
        protocol_sha256=protocol_sha,
        protocol_path=PROTOCOL_RELATIVE,
        source_manifest_sha256=manifest_sha,
        receipt_payload=receipt,
        observed_commit=final_commit,
        observed_clean=final_clean,
    )
    _publish_once(destination, raw)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    receipt = publish(root=args.root, output=args.output)
    print(
        "published exact research-only v2 release receipt: "
        f"{receipt['release_receipt_sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
