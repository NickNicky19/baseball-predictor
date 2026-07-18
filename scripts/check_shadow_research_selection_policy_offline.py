#!/usr/bin/env python3
"""Credential-free mutations for the locked shadow research policy."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.shadow_provider_adapter import (  # noqa: E402
    POLICY_SCHEMA_VERSION,
    _selection_side,
    load_research_selection_policy,
)
from src.utils.provenance import sha256_file  # noqa: E402


PASS = 0
FAIL = 0


def check(value: bool, label: str) -> None:
    global PASS, FAIL
    if value:
        PASS += 1
        print(f"  [OK] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}")


def raises(fn) -> bool:
    try:
        fn()
    except ValueError:
        return True
    return False


def write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def dynamic_payload() -> dict[str, object]:
    return {
        "schema_version": POLICY_SCHEMA_VERSION,
        "policy_id": "synthetic-max-positive-posted-ev-v1",
        "status": "RESEARCH_ONLY",
        "betting_authorized": False,
        "sportsbook": "draftkings",
        "category": "hits",
        "entry_hours": 4,
        "selection_mode": "max_positive_posted_ev",
        "selection_side": None,
    }


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="shadow_policy_") as temporary:
        root = Path(temporary)
        policy_path = root / "policy.json"
        write(policy_path, dynamic_payload())
        digest = sha256_file(policy_path)
        policy = load_research_selection_policy(policy_path, expected_sha256=digest)
        check(
            policy.selection_mode == "max_positive_posted_ev"
            and policy.selection_side is None
            and policy.entry_hours == 4,
            "locked dynamic policy loads without a copied edge threshold",
        )
        check(
            _selection_side(
                policy,
                model_p_over=0.70,
                over_odds_american=-115,
                under_odds_american=-105,
            ) == "over",
            "positive posted-price over EV selects over",
        )
        check(
            _selection_side(
                policy,
                model_p_over=0.30,
                over_odds_american=-115,
                under_odds_american=-105,
            ) == "under",
            "positive posted-price under EV selects under",
        )
        check(
            raises(lambda: _selection_side(
                policy,
                model_p_over=0.50,
                over_odds_american=-115,
                under_odds_american=-105,
            )),
            "MUTATION no positive posted-price EV produces no selected entry",
        )

        bad_side = dynamic_payload()
        bad_side["selection_side"] = "over"
        bad_side_path = root / "dynamic_with_side.json"
        write(bad_side_path, bad_side)
        check(
            raises(lambda: load_research_selection_policy(
                bad_side_path,
                expected_sha256=sha256_file(bad_side_path),
            )),
            "MUTATION dynamic policy cannot hide a fixed side",
        )
        check(
            raises(lambda: load_research_selection_policy(
                policy_path,
                expected_sha256="0" * 64,
            )),
            "MUTATION policy hash drift fails",
        )

        fixed = dynamic_payload()
        fixed["policy_id"] = "legacy-fixed-over-v1"
        fixed["selection_mode"] = "fixed_side"
        fixed["selection_side"] = "over"
        fixed_path = root / "fixed.json"
        write(fixed_path, fixed)
        fixed_policy = load_research_selection_policy(
            fixed_path,
            expected_sha256=sha256_file(fixed_path),
        )
        check(
            _selection_side(
                fixed_policy,
                model_p_over=0.01,
                over_odds_american=-115,
                under_odds_american=-105,
            ) == "over",
            "legacy fixed-side contract remains behaviorally unchanged",
        )

    print(f"\n{PASS}/{PASS + FAIL} checks passed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
