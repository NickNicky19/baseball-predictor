import json
import unittest
from pathlib import Path


WORKFLOW_PATH = Path(__file__).parents[1] / ".github" / "workflows" / "shadow-evidence-verifier.yml"
READINESS_AUDIT_PATH = Path(__file__).parents[1] / "scripts" / "audit_forward_shadow_readiness.py"
PREFLIGHT_WORKFLOW_PATH = Path(__file__).parents[1] / ".github" / "workflows" / "forward-shadow-preflight.yml"
ACTIVATION_PATH = Path(__file__).parents[1] / "config" / "forward_shadow_verifier_activation.json"
READ_ONLY_PROOF = '"${SSH[@]}" "$PRIMARY_USER@$PRIMARY_HOST" "test -r \'$PRIMARY_ROOT\' && ! test -w \'$PRIMARY_ROOT\'"'


def assert_read_only_proof(workflow: str) -> None:
    assert "Configure read-only primary identity" in workflow
    assert "Prove primary identity cannot write evidence" in workflow
    assert READ_ONLY_PROOF in workflow
    assert "StrictHostKeyChecking=yes" in workflow
    assert workflow.index("Prove primary identity cannot write evidence") < workflow.index(
        "Fetch the synchronized evidence copy"
    )


def assert_activation_boundary(workflow: str, activation: dict[str, object]) -> None:
    assert activation["schema_version"] == "forward-shadow-verifier-activation-v1"
    assert activation["lane"] == "legacy-forward-shadow"
    assert activation["producer_state"] == "NOT_DEPLOYED"
    assert activation["unattended_schedule_allowed"] is False
    assert activation["evidence_root"] == "/srv/baseball-shadow"
    assert activation["protocol_entrypoint"] == "scripts/verify_shadow_lifecycle_tree.py"
    assert "workflow_dispatch:" in workflow
    assert "schedule:" not in workflow


class ShadowVerifierWorkflowTests(unittest.TestCase):
    def test_undeployed_lane_is_manual_only(self) -> None:
        assert_activation_boundary(
            WORKFLOW_PATH.read_text(encoding="utf-8"),
            json.loads(ACTIVATION_PATH.read_text(encoding="utf-8")),
        )

    def test_mutation_reintroducing_hourly_schedule_fails(self) -> None:
        workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
        mutated = workflow.replace(
            "  workflow_dispatch:",
            '  schedule:\n    - cron: "17 * * * *"\n  workflow_dispatch:',
            1,
        )
        with self.assertRaises(AssertionError):
            assert_activation_boundary(
                mutated,
                json.loads(ACTIVATION_PATH.read_text(encoding="utf-8")),
            )

    def test_mutation_claiming_deployed_producer_fails(self) -> None:
        activation = json.loads(ACTIVATION_PATH.read_text(encoding="utf-8"))
        activation["producer_state"] = "DEPLOYED"
        with self.assertRaises(AssertionError):
            assert_activation_boundary(WORKFLOW_PATH.read_text(encoding="utf-8"), activation)

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
        self.assertIn("undeployed forward-shadow lane cannot have an unattended schedule", audit)
        self.assertIn("forward_shadow_verifier_activation.json", audit)
        self.assertIn("GitHub verifier does not prove that its primary identity is read-only", audit)
        self.assertIn("test -r '$PRIMARY_ROOT' && ! test -w '$PRIMARY_ROOT'", audit)
        self.assertIn("live_configuration_verified_by_offline_audit", audit)
        self.assertIn("require a separate live deployment receipt", audit)
        self.assertIn("GitHub pull-request preflight does not run the full forward-shadow readiness boundary", audit)

    def test_pull_request_preflight_runs_the_full_readiness_audit(self) -> None:
        workflow = PREFLIGHT_WORKFLOW_PATH.read_text(encoding="utf-8")
        self.assertIn('"on":', workflow)
        self.assertIn("pull_request:", workflow)
        self.assertIn('export PYBASEBALL_CACHE="$RUNNER_TEMP/pybaseball-cache"', workflow)
        self.assertNotIn("${{ runner.temp }}", workflow)
        self.assertIn("tests.test_shadow_verifier_workflow", workflow)
        self.assertIn("scripts/audit_forward_shadow_readiness.py", workflow)
