import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_candidate_ci_protects_deletions_as_well_as_writes() -> None:
    workflow = (ROOT / ".github/workflows/omega-candidate-ci.yml").read_text(
        encoding="utf-8"
    )
    assert "--diff-filter=ACMRTD" in workflow
    assert '--diff-filter=ACMRT"' not in workflow
    assert "os: ubuntu-24.04\n            python-version: '3.12.11'" in workflow
    assert "os: windows-2025\n            python-version: '3.12.10'" in workflow
    assert "python-version: ${{ matrix.python-version }}" in workflow
    mutated = workflow.replace("python-version: '3.12.10'", "python-version: '3.12.11'", 1)
    assert "os: windows-2025\n            python-version: '3.12.10'" not in mutated
    assert "context_a=" in workflow and "context_b=" in workflow
    assert "--network none" in workflow
    assert "--read-only" in workflow


def test_staging_rollback_reuses_only_a_verified_immutable_image() -> None:
    workflow = (ROOT / ".github/workflows/omega-staging-deploy.yml").read_text(
        encoding="utf-8"
    )
    required_fragments = (
        'docker pull "$REPOSITORY_URI@$digest"',
        "org.opencontainers.image.revision",
        'test "$revision" = "$SOURCE_COMMIT"',
        'image_identifier="$REPOSITORY_URI@$digest"',
    )
    for fragment in required_fragments:
        assert fragment in workflow
    assert "refusing a second build or overwrite" not in workflow


def test_ci_and_deploy_build_only_the_inspected_standalone_wheel() -> None:
    candidate = (ROOT / ".github/workflows/omega-candidate-ci.yml").read_text(
        encoding="utf-8"
    )
    deploy = (ROOT / ".github/workflows/omega-staging-deploy.yml").read_text(
        encoding="utf-8"
    )
    for workflow in (candidate, deploy):
        assert "scaffold-build.lock" in workflow
        assert "prepare_omega_scaffold_build_context.py" in workflow
        assert "verify_omega_scaffold_package.py wheel" in workflow
        assert "SCAFFOLD_WHEEL_SHA256" in workflow
        assert "dashboard.lock" not in workflow
        assert "requirements/profiles/test.lock" not in workflow


def test_staging_workflow_binds_the_dedicated_vpc_connector() -> None:
    workflow = (ROOT / ".github/workflows/omega-staging-deploy.yml").read_text(
        encoding="utf-8"
    )
    for fragment in (
        "AppRunnerVpcConnectorArn",
        'VpcConnectorArn="$VPC_CONNECTOR"',
        "egress.get('EgressType') != 'VPC'",
    ):
        assert fragment in workflow


def test_deploy_policy_can_verify_foundation_and_pull_for_rollback_only_in_staging() -> (
    None
):
    policy = json.loads(
        (ROOT / "infra/omega_staging/github-deploy-policy.template.json").read_text(
            encoding="utf-8"
        )
    )
    serialized = json.dumps(policy, sort_keys=True)
    assert "ecr:BatchGetImage" in serialized
    assert "baseball-predictor-omega-staging-foundation" in serialized
    assert "baseball-predictor-omega-staging" in serialized
    assert "lightsail" not in serialized.casefold()
