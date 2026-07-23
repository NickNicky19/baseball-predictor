from pathlib import Path


WORKFLOW = (
    Path(__file__).parents[1] / ".github" / "workflows" / "record-outcomes.yml"
)


def assert_forward_only(workflow: str) -> None:
    assert "TARGET_DATE=\"$(TZ=America/New_York date -d 'yesterday' +%F)\"" in workflow
    assert "--date \"${TARGET_DATE}\"" in workflow
    assert "--backfill-days" not in workflow
    assert "backfill_days:" not in workflow
    assert "May 2026 is sealed" in workflow
    assert "never re-opened on a later day" in workflow


def test_scheduled_settlement_is_forward_only() -> None:
    assert_forward_only(WORKFLOW.read_text(encoding="utf-8"))


def test_mutation_reintroducing_backfill_fails() -> None:
    mutated = WORKFLOW.read_text(encoding="utf-8") + "\n--backfill-days 7\n"
    try:
        assert_forward_only(mutated)
    except AssertionError:
        return
    raise AssertionError("backfill mutation unexpectedly passed")
