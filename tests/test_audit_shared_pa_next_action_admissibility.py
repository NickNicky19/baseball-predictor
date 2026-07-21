from __future__ import annotations

from datetime import datetime, timezone

import pytest

from scripts.audit_shared_pa_next_action_admissibility import audit_attempt


def fixtures():
    target = {"target_id": "a", "entry_target_at_utc": "2026-07-20T20:00:00Z"}
    plan = {"targets": [target]}
    manifest = {"target_ids": ["a"], "records": 0, "entry_chain_order": []}
    terminal = {"targets": {}}
    return plan, manifest, terminal


def test_empty_past_ledger_is_permanently_missed_not_receipt_complete() -> None:
    result = audit_attempt(*fixtures(), as_of=datetime(2026, 7, 21, tzinfo=timezone.utc))
    assert result == {"targets": 1, "past_targets": 1, "records": 0, "terminal_targets": 0, "receipt_complete": False, "permanently_missed_targets": 1}


def test_record_count_chain_mutation_fails_closed() -> None:
    plan, manifest, terminal = fixtures(); manifest["records"] = 1
    with pytest.raises(ValueError, match="chain length"):
        audit_attempt(plan, manifest, terminal, as_of=datetime(2026, 7, 21, tzinfo=timezone.utc))


def test_unknown_terminal_identity_mutation_fails_closed() -> None:
    plan, manifest, terminal = fixtures(); terminal["targets"] = {"wrong": {}}
    with pytest.raises(ValueError, match="unknown target"):
        audit_attempt(plan, manifest, terminal, as_of=datetime(2026, 7, 21, tzinfo=timezone.utc))
