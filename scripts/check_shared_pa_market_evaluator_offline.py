"""Verify the inert shared-PA market evaluator component without project evidence."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.evaluation.shared_pa_market_evaluator import (  # noqa: E402
    UNBOUND,
    load_contract,
    safe_regular_file,
    sha256_file,
)


REQUIRED_FILES = {
    "config/shared_pa_market_evaluation_contract_v1.json",
    "config/schemas/shared_pa_market_evaluation_contract_v1.schema.json",
    "config/schemas/shared_pa_market_evaluation_row_v1.schema.json",
    "config/schemas/shared_pa_market_evaluation_authority_v1.schema.json",
    "config/schemas/shared_pa_market_evaluation_report_v1.schema.json",
    "src/evaluation/shared_pa_market_evaluator.py",
    "tests/test_shared_pa_market_evaluator.py",
    "docs/research/SHARED_PA_MARKET_EVALUATION_ENGINE_V1.md",
    "scripts/check_shared_pa_market_evaluator_offline.py",
}


def validate_component_manifest(root: Path) -> int:
    manifest_path = safe_regular_file(
        root,
        "config/shared_pa_market_evaluator_v1_file_manifest.json",
        context="component manifest",
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if set(manifest) != {
        "schema_version", "component_id", "state", "source_base_commit",
        "research_only", "betting_authorized", "files",
    }:
        raise RuntimeError("file manifest surface changed")
    if manifest.get("schema_version") != "shared-pa-market-evaluator-file-manifest-v1":
        raise RuntimeError("file manifest schema changed")
    if (
        manifest.get("component_id") != "shared_pa_market_evaluator_v1"
        or manifest.get("state") != UNBOUND
        or manifest.get("source_base_commit") != "9f86f6a34fc71151b76debf48059829aa4fa91da"
        or manifest.get("research_only") is not True
        or manifest.get("betting_authorized") is not False
    ):
        raise RuntimeError("file manifest authority changed")
    paths = manifest.get("files")
    if not isinstance(paths, list) or not paths:
        raise RuntimeError("file manifest is empty")
    observed = set()
    for item in paths:
        if set(item) != {"path", "size", "sha256", "purpose"}:
            raise RuntimeError("file manifest row surface changed")
        relative = item["path"]
        if not isinstance(relative, str) or relative in observed:
            raise RuntimeError(f"missing or duplicate manifest path: {relative}")
        path = safe_regular_file(root, relative, context="component manifest entry")
        observed.add(relative)
        if (
            isinstance(item["size"], bool)
            or not isinstance(item["size"], int)
            or item["size"] < 0
            or not isinstance(item["purpose"], str)
            or not item["purpose"].strip()
        ):
            raise RuntimeError(f"invalid manifest metadata: {relative}")
        if path.stat().st_size != item["size"] or sha256_file(path) != item["sha256"]:
            raise RuntimeError(f"byte identity mismatch: {relative}")
        if relative.endswith(".json"):
            json.loads(path.read_text(encoding="utf-8"))
    if observed != REQUIRED_FILES:
        raise RuntimeError("component manifest file set differs from exact required set")
    load_contract(root / "config/shared_pa_market_evaluation_contract_v1.json")
    return len(paths)


def main() -> int:
    count = validate_component_manifest(ROOT)
    print(f"OK: {count} exact files; evaluator remains externally unbound")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
