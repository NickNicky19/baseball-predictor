#!/usr/bin/env python3
"""Mutation checks for the mechanical cumulative-selector retry."""
from __future__ import annotations

import copy
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.shared_pa_canonical_selection import sha256  # noqa: E402
from src.evaluation.shared_pa_cumulative_selection import load_retry_authorization  # noqa: E402


def accepted(payload: dict) -> bool:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        failure_source = ROOT / payload["failed_attempt_record"]["path"]
        failure_target = root / payload["failed_attempt_record"]["path"]
        failure_target.parent.mkdir(parents=True, exist_ok=True)
        failure_target.write_bytes(failure_source.read_bytes())
        retry_path = root / "retry.json"
        retry_path.write_text(json.dumps(payload), encoding="utf-8")
        try:
            load_retry_authorization(retry_path, code_root=root)
        except (KeyError, TypeError, ValueError):
            return False
    return True


def mutate(payload: dict, fn: object) -> dict:
    result = copy.deepcopy(payload)
    fn(result)  # type: ignore[operator]
    return result


def main() -> int:
    path = ROOT / "config/shared_pa_cumulative_selection_retry_v2.json"
    retry = json.loads(path.read_text(encoding="utf-8"))
    failure = ROOT / retry["failed_attempt_record"]["path"]
    checks = [
        ("locked retry accepted", accepted(retry)),
        ("failure hash exact", sha256(failure) == retry["failed_attempt_record"]["sha256"]),
        ("status mutation rejected", not accepted(mutate(retry, lambda p: p.update(status="UNLOCKED")))),
        ("authorization mutation rejected", not accepted(mutate(retry, lambda p: p.update(betting_authorized=True)))),
        ("production mutation rejected", not accepted(mutate(retry, lambda p: p.update(production_unchanged=False)))),
        ("2025 release rejected", not accepted(mutate(retry, lambda p: p.update(confirmation_2025_forbidden=False)))),
        ("May release rejected", not accepted(mutate(retry, lambda p: p.update(may_2026_forbidden=False)))),
        ("scope mutation rejected", not accepted(mutate(retry, lambda p: p["allowed_change"].update(id="change_model")))),
        ("failed hash mutation rejected", not accepted(mutate(retry, lambda p: p["failed_attempt_record"].update(sha256="0" * 64)))),
        ("quarantine release rejected", not accepted(mutate(retry, lambda p: p.update(failed_output_must_remain_quarantined=False)))),
    ]
    failed = [name for name, passed in checks if not passed]
    if failed:
        raise SystemExit(f"cumulative retry checks failed: {failed}")
    print(f"CUMULATIVE SELECTION RETRY VALID: {len(checks)}/{len(checks)} checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
