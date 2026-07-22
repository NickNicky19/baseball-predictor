from pathlib import Path
import unittest


WORKFLOW_PATH = Path(__file__).parents[1] / ".github" / "workflows" / "shadow-evidence-verifier.yml"
READINESS_AUDIT_PATH = Path(__file__).parents[1] / "scripts" / "audit_forward_shadow_readiness.py"
PREFLIGHT_WORKFLOW_PATH = Path(__file__).parents[1] / ".github" / "workflows" / "forward-shadow-preflight.yml"
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

    def test_readiness_audit_binds_the_read_only_proof(self) -> None:
        audit = READINESS_AUDIT_PATH.read_text(encoding="utf-8")
        self.assertIn("GitHub verifier does not prove that its primary identity is read-only", audit)
        self.assertIn("test -r '$PRIMARY_ROOT' && ! test -w '$PRIMARY_ROOT'", audit)
        self.assertIn("live_configuration_verified_by_offline_audit", audit)
        self.assertIn("require a separate live deployment receipt", audit)
        self.assertIn("GitHub pull-request preflight does not run the full forward-shadow readiness boundary", audit)

    def test_pull_request_preflight_runs_the_full_readiness_audit(self) -> None:
        workflow = PREFLIGHT_WORKFLOW_PATH.read_text(encoding="utf-8")
        self.assertIn("pull_request:", workflow)
        self.assertIn("PYBASEBALL_CACHE", workflow)
        self.assertIn("tests.test_shadow_verifier_workflow", workflow)
        self.assertIn("scripts/audit_forward_shadow_readiness.py", workflow)
