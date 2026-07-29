#!/usr/bin/env python3
"""Validate the exact PR32 batter evidence collection source manifest."""

from __future__ import annotations

import hashlib
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "reports/shared_pa_batter_evidence_collection_v1_hash_manifest.json"

REQUIRED = {
    ".github/workflows/shared-pa-batter-evidence-collection-linux.yml",
    "config/shared_pa_batter_evidence_collection_v1.json",
    "deploy/pr32_batter_evidence/README.md",
    "deploy/pr32_batter_evidence/baseball-pr32-batter-evidence-inventory.service",
    "deploy/pr32_batter_evidence/baseball-pr32-batter-evidence-inventory.timer",
    "requirements-shared-pa-batter-evidence-collection-ci.lock",
    "scripts/check_shared_pa_batter_evidence_collection_manifest.py",
    "scripts/check_tracked_secrets.py",
    "scripts/run_shared_pa_batter_evidence_inventory.py",
    "src/__init__.py",
    "src/evaluation/__init__.py",
    "src/evaluation/projected_lineup_roster_ledger.py",
    "src/evaluation/projected_lineup_contract.py",
    "src/evaluation/projected_lineup_official_roster.py",
    "src/evaluation/prospective_batter_opportunity.py",
    "src/evaluation/prospective_batter_opportunity_history.py",
    "src/evaluation/shared_pa_batter_evidence_inventory.py",
    "src/evaluation/shared_pa_forward_collector.py",
    "src/evaluation/shared_pa_forward_evidence.py",
    "src/evaluation/shadow_capture_plan.py",
    "tests/test_prospective_batter_opportunity_history.py",
    "tests/test_shared_pa_batter_evidence_inventory.py"
}


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key in collection manifest: {key}")
        value[key] = item
    return value


def load_manifest(path: Path) -> dict[str, object]:
    value = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_reject_duplicate_pairs,
    )
    if not isinstance(value, dict):
        raise ValueError("collection manifest root must be an object")
    return value


def local_import_closure(entry: str) -> set[str]:
    pending = [entry]
    closure: set[str] = set()
    while pending:
        relative = pending.pop()
        if relative in closure:
            continue
        closure.add(relative)
        tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"), filename=relative)
        modules: set[str] = set()
        # Only module-level imports execute when this focused runtime imports a
        # module. Lazy imports inside unrelated public API functions (notably
        # evaluation.__getattr__) are not part of this entry point's closure.
        for node in tree.body:
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
        for module in modules:
            if not (module == "src" or module.startswith("src.")):
                continue
            parts = module.split(".")
            for length in range(1, len(parts)):
                initializer = "/".join(parts[:length]) + "/__init__.py"
                if (ROOT / initializer).is_file() and initializer not in closure:
                    pending.append(initializer)
            source = "/".join(parts) + ".py"
            package = "/".join(parts) + "/__init__.py"
            candidate = source if (ROOT / source).is_file() else package
            if (ROOT / candidate).is_file() and candidate not in closure:
                pending.append(candidate)
    return closure


def main() -> int:
    value = load_manifest(MANIFEST)
    if (
        set(value) != {"schema_version", "status", "files", "protected_boundaries"}
        or value["schema_version"] != "shared-pa-batter-evidence-collection-hash-manifest-v1"
        or value["status"] != "EXACT_BYTES_CODE_ONLY_NOT_DEPLOYED"
        or value["protected_boundaries"] != {
            "aws_changed": False,
            "may_2026_accessed": False,
            "outcomes_accessed": False,
            "probabilities_generated": False,
            "process_20872_touched": False,
            "betting_authorized": False,
        }
    ):
        raise ValueError("collection manifest identity or protected boundaries changed")
    rows = value["files"]
    if not isinstance(rows, list) or {row.get("path") for row in rows if isinstance(row, dict)} != REQUIRED:
        raise ValueError("collection manifest exact file set changed")
    if len(rows) != len(REQUIRED):
        raise ValueError("collection manifest contains duplicate paths")
    for row in rows:
        if set(row) != {"path", "size", "sha256"}:
            raise ValueError("collection manifest row schema changed")
        path = ROOT / row["path"]
        raw = path.read_bytes()
        if len(raw) != row["size"] or hashlib.sha256(raw).hexdigest() != row["sha256"]:
            raise ValueError(f"collection manifest bytes differ: {row['path']}")
    closure = local_import_closure("scripts/run_shared_pa_batter_evidence_inventory.py")
    missing_closure = closure - REQUIRED
    if missing_closure:
        raise ValueError(f"runtime local import closure is unbound: {sorted(missing_closure)}")
    print(f"[OK] exact PR32 batter evidence collection bytes: {len(rows)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
