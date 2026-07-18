#!/usr/bin/env python3
"""Mutation checks for separate, fail-closed execution-product contracts."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.execution_product_contracts import (  # noqa: E402
    product_readiness,
    validate_execution_product_contracts,
)


PASS = 0
FAIL = 0
SOURCE = ROOT / "config/hits_execution_product_contracts.json"


def check(value: bool, label: str) -> None:
    global PASS, FAIL
    if value:
        PASS += 1
        print(f"  [OK] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}")


def raises(payload: dict) -> bool:
    with tempfile.TemporaryDirectory(prefix="product_contract_") as temporary:
        path = Path(temporary) / "contract.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        try:
            validate_execution_product_contracts(path)
        except ValueError:
            return True
        return False


def main() -> int:
    payload, digest = validate_execution_product_contracts(SOURCE)
    readiness = product_readiness(SOURCE)
    check(len(digest) == 64 and len(readiness) == 4, "four independent product contracts load with one artifact hash")
    check(all(not value.ready and value.blockers for value in readiness), "every product remains explicitly blocked")

    changed = json.loads(json.dumps(payload))
    changed["reference_market"]["execution_product"] = True
    check(raises(changed), "MUTATION DraftKings reference quote cannot become execution evidence")
    changed = json.loads(json.dumps(payload))
    changed["cross_product_inheritance_permitted"] = True
    check(raises(changed), "MUTATION cross-product rule inheritance fails")
    changed = json.loads(json.dumps(payload))
    del changed["products"]["novig"]
    check(raises(changed), "MUTATION missing product cannot disappear from readiness")
    changed = json.loads(json.dumps(payload))
    changed["products"]["prizepicks"]["authorization"] = True
    check(raises(changed), "MUTATION one product cannot self-authorize")
    changed = json.loads(json.dumps(payload))
    changed["products"]["onyx"]["contract_id"] = changed["products"]["chalkboard"]["contract_id"]
    check(raises(changed), "MUTATION product contracts cannot share identity")
    changed = json.loads(json.dumps(payload))
    changed["products"]["onyx"]["rules_and_effective_date_hash"] = "0" * 64
    check(raises(changed), "MUTATION public rules hash cannot become decoration")

    print(f"\n{PASS}/{PASS + FAIL} checks passed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
