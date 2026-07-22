#!/usr/bin/env python3
"""Mutation checks for the locked HR level-mapping implementation contract."""
from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from adjudicate_hr_pre2026_level_mapping import load_contract  # noqa: E402


CONTRACT = ROOT / "data/analysis/hr_over_contract_v1/pre2026_a3_2_migration_v2/level_mapping_implementation_v1.json"


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    path.with_suffix(path.suffix + ".sha256").write_text(digest + "\n", encoding="utf-8")


def main() -> int:
    original = json.loads(CONTRACT.read_text(encoding="utf-8"))
    load_contract(CONTRACT, verify_files=True)
    print("[OK] locked implementation contract passes")
    mutations = []
    bad = copy.deepcopy(original); bad["inputs"]["raw_model"]["sha256"] = "0" * 64
    mutations.append((bad, "tampered input hash"))
    bad = copy.deepcopy(original); bad["chronology"]["confirmation_dates_open_once"] = bad["chronology"]["calibration_dates"]
    mutations.append((bad, "overlapping chronology"))
    bad = copy.deepcopy(original); bad["success_condition"]["candidate_Brier_lower_than_raw_and_control"] = False
    mutations.append((bad, "weakened success condition"))
    bad = copy.deepcopy(original); bad["algorithm"]["mapping_pipeline"]["max_iter"] = 100
    mutations.append((bad, "mapping pipeline drift"))
    bad = copy.deepcopy(original); bad["betting_authorized"] = True
    mutations.append((bad, "betting authorization"))
    bad = copy.deepcopy(original); bad["may_2026_opened"] = True
    mutations.append((bad, "May 2026 access"))
    bad = copy.deepcopy(original); bad["inputs"]["evaluator"]["sha256"] = "f" * 64
    mutations.append((bad, "evaluator drift"))
    bad = copy.deepcopy(original); bad["outputs"]["confirmation_report"] = bad["outputs"]["calibration_lock"]
    mutations.append((bad, "output collision"))
    with tempfile.TemporaryDirectory() as directory:
        for index, (payload, label) in enumerate(mutations):
            path = Path(directory) / f"contract_{index}.json"
            _write(path, payload)
            try:
                load_contract(path, verify_files=True)
            except ValueError:
                print(f"[OK] MUTATION {label} fails")
            else:
                raise AssertionError(f"mutation unexpectedly passed: {label}")
        path = Path(directory) / "sidecar.json"
        _write(path, original)
        path.with_suffix(".json.sha256").write_text("0" * 64 + "\n", encoding="utf-8")
        try:
            load_contract(path, verify_files=False)
        except ValueError:
            print("[OK] MUTATION sidecar tamper fails")
        else:
            raise AssertionError("sidecar mutation unexpectedly passed")
    print("10/10")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
