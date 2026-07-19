#!/usr/bin/env python3
"""Mutation and execution checks for cumulative selector retry v3."""
from __future__ import annotations

import ast
import copy
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.select_shared_pa_cumulative_challenger import (  # noqa: E402
    PREFIT_MUTATION_CONTRACT,
    atomic_publish_directory,
)
from src.evaluation.shared_pa_canonical_selection import sha256  # noqa: E402
from src.evaluation.shared_pa_cumulative_selection import load_retry_v3_authorization  # noqa: E402


def accepted(payload: dict) -> bool:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        for name in ("previous_retry", "failed_attempt_record"):
            source = ROOT / payload[name]["path"]
            target = root / payload[name]["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
        retry_path = root / "retry.json"
        retry_path.write_text(json.dumps(payload), encoding="utf-8")
        try:
            load_retry_v3_authorization(retry_path, code_root=root)
        except (KeyError, TypeError, ValueError):
            return False
    return True


def mutate(payload: dict, fn: object) -> dict:
    result = copy.deepcopy(payload)
    fn(result)  # type: ignore[operator]
    return result


def atomic_publish_works() -> bool:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        staging, final = root / ".staging", root / "final"
        staging.mkdir()
        (staging / "complete.json").write_text("{}", encoding="utf-8")
        atomic_publish_directory(staging, final)
        return final.is_dir() and (final / "complete.json").is_file() and not staging.exists()


def atomic_publish_refuses_overwrite() -> bool:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        staging, final = root / ".staging", root / "final"
        staging.mkdir()
        final.mkdir()
        try:
            atomic_publish_directory(staging, final)
        except ValueError:
            return True
    return False


def main() -> int:
    path = ROOT / "config/shared_pa_cumulative_selection_retry_v3.json"
    retry = json.loads(path.read_text(encoding="utf-8"))
    selector_source = (ROOT / "scripts/select_shared_pa_cumulative_challenger.py").read_text(encoding="utf-8")
    names = {node.id for node in ast.walk(ast.parse(selector_source)) if isinstance(node, ast.Name)}
    checks = [
        ("locked retry accepted", accepted(retry)),
        ("bindings exact", all(sha256(ROOT / retry[name]["path"]) == retry[name]["sha256"] for name in ("previous_retry", "failed_attempt_record"))),
        ("status mutation rejected", not accepted(mutate(retry, lambda p: p.update(status="UNLOCKED")))),
        ("authorization mutation rejected", not accepted(mutate(retry, lambda p: p.update(betting_authorized=True)))),
        ("production mutation rejected", not accepted(mutate(retry, lambda p: p.update(production_unchanged=False)))),
        ("2025 release rejected", not accepted(mutate(retry, lambda p: p.update(confirmation_2025_forbidden=False)))),
        ("May release rejected", not accepted(mutate(retry, lambda p: p.update(may_2026_forbidden=False)))),
        ("scope mutation rejected", not accepted(mutate(retry, lambda p: p["allowed_changes"].append("change_model")))),
        ("previous retry hash mutation rejected", not accepted(mutate(retry, lambda p: p["previous_retry"].update(sha256="0" * 64)))),
        ("failure hash mutation rejected", not accepted(mutate(retry, lambda p: p["failed_attempt_record"].update(sha256="0" * 64)))),
        ("quarantine release rejected", not accepted(mutate(retry, lambda p: p.update(all_failed_outputs_must_remain_quarantined=False)))),
        ("JSON names absent from Python AST", not ({"true", "false", "null"} & names) and PREFIT_MUTATION_CONTRACT["validator_mutations_required_after_publication"] is True),
        ("atomic directory publication works", atomic_publish_works()),
        ("atomic directory overwrite rejected", atomic_publish_refuses_overwrite()),
    ]
    failed = [name for name, passed in checks if not passed]
    if failed:
        raise SystemExit(f"cumulative retry v3 checks failed: {failed}")
    print(f"CUMULATIVE SELECTION RETRY V3 VALID: {len(checks)}/{len(checks)} checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
