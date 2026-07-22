from pathlib import Path
import unittest


WORKFLOW_PATH = Path(__file__).parents[1] / ".github" / "workflows" / "shadow-evidence-verifier.yml"
READ_ONLY_PROOF = '"${SSH[@]}" "$PRIMARY_USER@$PRIMARY_HOST" "test -r \'$PRIMARY_ROOT\' && ! test -w \'$PRIMARY_ROOT\'"'


def assert_read_only_proof(workflow: str) -> None:
    assert "Configure read-only primary identity" in workflow
    assert "Prove primary identity cannot write evidence" in workflow
    assert READ_ONLY_PROOF in workflow
    assert "StrictHostKeyChecking=yes" in workflow
    assert workflow.index("Prove primary identity cannot write evidence") < workflow.index(
        "Fetch the synchronized evidence copy"
    )


class ShadowVerifierWorkflowTests(unittest.TestCase):
    def test_read_only_identity_is_proven_before_fetch(self) -> None:
        assert_read_only_proof(WORKFLOW_PATH.read_text(encoding="utf-8"))

    def test_mutation_removing_non_write_check_fails(self) -> None:
        mutated = WORKFLOW_PATH.read_text(encoding="utf-8").replace(
            " && ! test -w '$PRIMARY_ROOT'", "", 1
        )
        with self.assertRaises(AssertionError):
            assert_read_only_proof(mutated)
