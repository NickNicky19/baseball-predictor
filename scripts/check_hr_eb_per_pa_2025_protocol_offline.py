#!/usr/bin/env python3
"""Mutation checks for the locked untouched-2025 per-PA HR protocol."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.hr_eb_per_pa_confirmation import validate_protocol


PROTOCOL = ROOT / "config/hr_eb_per_pa_2025_confirmation_protocol.json"
EVIDENCE = ROOT.parent


def rejected(payload: dict) -> bool:
    try:
        validate_protocol(payload, evidence_root=EVIDENCE)
    except (ValueError, KeyError, TypeError):
        return True
    return False


def main() -> int:
    base = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    validate_protocol(base, evidence_root=EVIDENCE)
    mutations = []
    bad = copy.deepcopy(base); bad["inputs"]["training"]["sha256"] = "0" * 64; mutations.append(("input hash", bad))
    bad = copy.deepcopy(base); bad["confirmation_dates"][0] = "2025-06-28"; mutations.append(("date", bad))
    bad = copy.deepcopy(base); bad["candidate"]["prior_strength_pa"] = 199; mutations.append(("prior", bad))
    bad = copy.deepcopy(base); bad["candidate"]["same_date_updates_forbidden"] = False; mutations.append(("chronology", bad))
    bad = copy.deepcopy(base); bad["eligibility"]["lineup_slot_forbidden"] = False; mutations.append(("lineup", bad))
    bad = copy.deepcopy(base); bad["eligibility"]["market_price_and_settlement_forbidden"] = False; mutations.append(("market", bad))
    bad = copy.deepcopy(base); bad["metrics"]["bootstrap_draws"] = 1000; mutations.append(("draws", bad))
    bad = copy.deepcopy(base); bad["material_improvement_gate"]["candidate_auc_not_below_either_comparator"] = False; mutations.append(("decision", bad))
    bad = copy.deepcopy(base); bad["protected_invariants"]["May_2026_remains_sealed"] = False; mutations.append(("May", bad))
    bad = copy.deepcopy(base); bad["betting_authorized"] = True; mutations.append(("betting", bad))
    for name, payload in mutations:
        if not rejected(payload):
            raise AssertionError(f"protocol mutation survived: {name}")
    print(f"HR EB PER-PA 2025 PROTOCOL VALID: {len(mutations)}/{len(mutations)} mutations rejected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
