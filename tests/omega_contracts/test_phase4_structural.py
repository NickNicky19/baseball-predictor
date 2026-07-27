from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from scripts.verify_omega_scaffold_structure import (
    StructuralGateError,
    verify_dashboard_capabilities,
    verify_iac_and_workflows,
)

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "docs/omega_stage2/phase4/DASHBOARD_CAPABILITY_POLICY.json"


def _copy_dashboard_boundary(tmp_path: Path) -> Path:
    target = tmp_path / "candidate"
    (target / "dashboard").mkdir(parents=True)
    (target / "src/omega_contracts").mkdir(parents=True)
    for source in (ROOT / "dashboard").glob("*.py"):
        shutil.copyfile(source, target / "dashboard" / source.name)
    for source in (ROOT / "src/omega_contracts").glob("*.py"):
        shutil.copyfile(source, target / "src/omega_contracts" / source.name)
    return target


def test_dashboard_transitive_graph_is_exact_and_allowlisted() -> None:
    graph = verify_dashboard_capabilities(ROOT, POLICY)
    assert set(graph) == {
        "dashboard.app",
        "dashboard.contracts",
        "dashboard.snapshot_store",
        "src.omega_contracts.canonical",
        "src.omega_contracts.chronology",
        "src.omega_contracts.errors",
    }
    assert "src.omega_contracts.chronology" in graph["dashboard.contracts"]
    assert "src.omega_contracts.chronology" in graph["dashboard.snapshot_store"]


@pytest.mark.parametrize(
    "mutation",
    [
        "\nimport requests as harmless\nharmless.get('https://invalid.example')\n",
        "\nimport httpx as harmless\nharmless.get('https://invalid.example')\n",
        "\nfrom urllib import request as harmless\nharmless.urlopen('https://invalid.example')\n",
        "\nimport socket as harmless\nharmless.socket()\n",
        "\nimport subprocess as harmless\nharmless.run(['true'])\n",
        "\nimport os as harmless\ngetattr(harmless, 'system')('true')\n",
        "\nfrom pathlib import Path as QuietPath\nQuietPath('x').write_text('x')\n",
        "\nimport builtins as quiet\nquiet.open('x', 'wb')\n",
        "\nimport importlib as quiet\nquiet.import_module('requests')\n",
        "\n__import__('requests')\n",
        "\nfrom dashboard import renamed_collector_helper\nrenamed_collector_helper.start()\n",
        "\nfrom dashboard import harmless_prediction_wrapper\nharmless_prediction_wrapper.execute()\n",
        "\nfrom dashboard import intermediate_calibration_helper\nintermediate_calibration_helper.apply()\n",
        "\nPROTECTED = '/srv/' + 'baseball-shadow'\n",
        "\nimport os\nPROTECTED = os.environ['PRODUCTION_ROOT']\n",
        "\nimport boto3 as helper\nhelper.client('s3')\n",
    ],
)
def test_transitive_and_capability_mutations_are_detected(
    tmp_path: Path, mutation: str
) -> None:
    candidate = _copy_dashboard_boundary(tmp_path)
    app = candidate / "dashboard/app.py"
    app.write_text(app.read_text(encoding="utf-8") + mutation, encoding="utf-8")
    with pytest.raises(StructuralGateError):
        verify_dashboard_capabilities(candidate, POLICY)


def _copy_iac_boundary(tmp_path: Path) -> Path:
    target = tmp_path / "candidate"
    (target / "infra/omega_staging").mkdir(parents=True)
    (target / ".github/workflows").mkdir(parents=True)
    for relative in (
        "infra/omega_staging/foundation.yaml",
        "infra/omega_staging/service.yaml",
        "infra/omega_staging/github-deploy-policy.template.json",
        ".github/workflows/omega-candidate-ci.yml",
        ".github/workflows/omega-staging-deploy.yml",
    ):
        source = ROOT / relative
        output = target / relative
        shutil.copyfile(source, output)
    return target


def _replace(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise AssertionError(f"mutation anchor missing: {old!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def _mutate_iac(candidate: Path, case: str) -> None:
    foundation = candidate / "infra/omega_staging/foundation.yaml"
    service = candidate / "infra/omega_staging/service.yaml"
    policy = candidate / "infra/omega_staging/github-deploy-policy.template.json"
    deploy = candidate / ".github/workflows/omega-staging-deploy.yml"
    ci = candidate / ".github/workflows/omega-candidate-ci.yml"
    if case == "default_egress":
        _replace(service, "EgressType: VPC", "EgressType: DEFAULT")
    elif case in {"nat", "internet_gateway", "route", "public_ip"}:
        resource_type = {
            "nat": "AWS::EC2::NatGateway",
            "internet_gateway": "AWS::EC2::InternetGateway",
            "route": "AWS::EC2::Route",
            "public_ip": "AWS::EC2::EIP",
        }[case]
        _replace(
            foundation,
            "Resources:\n",
            f"Resources:\n  MutatedResource:\n    Type: {resource_type}\n",
        )
    elif case == "ipv4_default":
        _replace(
            foundation,
            "Description: >-",
            "Metadata:\n  MutatedCidr: 0.0.0.0/0\nDescription: >-",
        )
    elif case == "ipv6_default":
        _replace(
            foundation,
            "Description: >-",
            "Metadata:\n  MutatedCidr: ::/0\nDescription: >-",
        )
    elif case == "connector_unrestricted_egress":
        _replace(
            foundation,
            "DestinationSecurityGroupId: !GetAtt UnattachedIsolationSinkSecurityGroup.GroupId",
            "CidrIp: 0.0.0.0/0",
        )
    elif case == "network_acl_allow":
        _replace(
            foundation,
            "Resources:\n",
            "Resources:\n  MutatedAclAllow:\n    Type: AWS::EC2::NetworkAclEntry\n",
        )
    elif case == "public_subnet":
        _replace(foundation, "MapPublicIpOnLaunch: false", "MapPublicIpOnLaunch: true")
    elif case == "public_ingress":
        _replace(service, "IsPubliclyAccessible: false", "IsPubliclyAccessible: true")
    elif case == "automatic_deploy":
        _replace(
            service, "AutoDeploymentsEnabled: false", "AutoDeploymentsEnabled: true"
        )
    elif case in {
        "wildcard_action",
        "wildcard_resource",
        "production_arn",
        "write_evidence",
    }:
        document = json.loads(policy.read_text(encoding="utf-8"))
        statement = document["Statement"][1]
        if case == "wildcard_action":
            statement["Action"] = "*"
        elif case == "wildcard_resource":
            statement["Resource"] = "*"
        elif case == "production_arn":
            statement["Resource"] = "arn:aws:s3:::production-model-artifacts"
        else:
            statement["Action"] = "s3:PutObject"
            statement["Resource"] = "arn:aws:s3:::omega-staging-evidence/*"
        policy.write_text(json.dumps(document), encoding="utf-8")
    elif case == "production_network":
        _replace(foundation, "!Ref IsolatedStagingVpc", "vpc-production-network")
    elif case == "concrete_account":
        _replace(
            foundation,
            "Description: >-",
            "Metadata:\n  MutatedArn: arn:aws:iam::123456789012:role/example\nDescription: >-",
        )
    elif case == "missing_exact_commit":
        _replace(deploy, "required: true", "required: false")
    elif case == "push_trigger":
        _replace(
            deploy, "on:\n  workflow_dispatch:", "on:\n  push:\n  workflow_dispatch:"
        )
    elif case == "pr_trigger":
        _replace(
            deploy,
            "on:\n  workflow_dispatch:",
            "on:\n  pull_request:\n  workflow_dispatch:",
        )
    elif case == "mutable_image":
        _replace(service, "@sha256:", ":")
    elif case == "unpinned_action":
        _replace(
            ci,
            "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683",
            "actions/checkout@v4",
        )
    elif case == "default_branch_mutation":
        _replace(deploy, "steps:\n", "steps:\n      - run: git push origin main\n")
    elif case == "embedded_credential":
        synthetic_access_key = "AKIA" + "1234567890ABCDEF"
        _replace(
            deploy,
            "env:\n",
            f"env:\n      AWS_ACCESS_KEY_ID: {synthetic_access_key}\n",
        )
    else:
        raise AssertionError(case)


def test_semantic_iac_and_workflow_boundary_passes() -> None:
    result = verify_iac_and_workflows(ROOT)
    assert result["network"] == "DESIGNED_NOT_DEPLOYMENT_VERIFIED"
    assert result["egress"] == "DEDICATED_VPC_NO_ROUTE_TEMPLATE"


@pytest.mark.parametrize(
    "case",
    [
        "default_egress",
        "nat",
        "internet_gateway",
        "route",
        "public_ip",
        "ipv4_default",
        "ipv6_default",
        "connector_unrestricted_egress",
        "network_acl_allow",
        "public_subnet",
        "public_ingress",
        "automatic_deploy",
        "wildcard_action",
        "wildcard_resource",
        "production_arn",
        "write_evidence",
        "production_network",
        "concrete_account",
        "missing_exact_commit",
        "push_trigger",
        "pr_trigger",
        "mutable_image",
        "unpinned_action",
        "default_branch_mutation",
        "embedded_credential",
    ],
)
def test_iac_and_workflow_mutations_are_detected(tmp_path: Path, case: str) -> None:
    candidate = _copy_iac_boundary(tmp_path)
    _mutate_iac(candidate, case)
    with pytest.raises(StructuralGateError):
        verify_iac_and_workflows(candidate)
