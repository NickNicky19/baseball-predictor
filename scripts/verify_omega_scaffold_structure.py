"""Structural Python, workflow, IAM, and CloudFormation policy gates."""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml


class StructuralGateError(RuntimeError):
    pass


class TaggedBaseLoader(yaml.BaseLoader):
    pass


def _construct_tag(loader: TaggedBaseLoader, suffix: str, node: yaml.Node) -> Any:
    if isinstance(node, yaml.ScalarNode):
        value: Any = loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node)
    else:
        value = loader.construct_mapping(node)
    return {f"!{suffix}": value}


TaggedBaseLoader.add_multi_constructor("!", _construct_tag)


def load_yaml(path: Path) -> dict[str, Any]:
    parsed = yaml.load(path.read_text(encoding="utf-8"), Loader=TaggedBaseLoader)
    if not isinstance(parsed, dict):
        raise StructuralGateError(f"YAML root is not a mapping: {path}")
    return parsed


def _module_path(root: Path, module: str) -> Path:
    package = root.joinpath(*module.split("."), "__init__.py")
    plain = root.joinpath(*module.split(".")).with_suffix(".py")
    if plain.is_file():
        return plain
    if package.is_file():
        return package
    raise StructuralGateError(f"local module does not exist: {module}")


def _resolve_relative(module: str, imported: str | None, level: int) -> str:
    package = module.split(".")[:-1]
    if level > len(package) + 1:
        raise StructuralGateError(f"relative import escapes package: {module}")
    base = package[: len(package) - level + 1]
    if imported:
        base.extend(imported.split("."))
    return ".".join(base)


def _attribute_name(node: ast.AST, aliases: dict[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        left = _attribute_name(node.value, aliases)
        return f"{left}.{node.attr}" if left else node.attr
    return None


def _constant_string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _constant_string(node.left)
        right = _constant_string(node.right)
        return left + right if left is not None and right is not None else None
    return None


def verify_dashboard_capabilities(
    repository_root: Path, policy_path: Path
) -> dict[str, list[str]]:
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    allowed_local = set(policy["allowed_local_modules"])
    allowed_external = set(policy["allowed_external_import_roots"])
    forbidden_imports = set(policy["forbidden_import_roots"])
    forbidden_modules = set(policy["forbidden_import_modules"])
    forbidden_calls = set(policy["forbidden_call_suffixes"])
    exceptions = {
        key: set(value) for key, value in policy["narrow_call_exceptions"].items()
    }
    queue = list(policy["entrypoints"])
    visited: set[str] = set()
    graph: dict[str, list[str]] = {}
    while queue:
        module = queue.pop(0)
        if module in visited:
            continue
        if module not in allowed_local:
            raise StructuralGateError(
                f"dashboard reached non-allowlisted local module: {module}"
            )
        visited.add(module)
        path = _module_path(repository_root, module)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        aliases: dict[str, str] = {}
        local_edges: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    aliases[alias.asname or alias.name.split(".", 1)[0]] = alias.name
                    root = alias.name.split(".", 1)[0]
                    if root in forbidden_imports:
                        raise StructuralGateError(
                            f"forbidden import in {module}: {alias.name}"
                        )
                    if alias.name in forbidden_modules:
                        raise StructuralGateError(
                            f"forbidden import in {module}: {alias.name}"
                        )
                    if root in {"dashboard", "src"}:
                        local_edges.add(alias.name)
                    elif (
                        root not in sys.stdlib_module_names
                        and root not in allowed_external
                    ):
                        raise StructuralGateError(
                            f"undeclared external import in {module}: {alias.name}"
                        )
            elif isinstance(node, ast.ImportFrom):
                imported = (
                    _resolve_relative(module, node.module, node.level)
                    if node.level
                    else (node.module or "")
                )
                root = imported.split(".", 1)[0] if imported else ""
                for alias in node.names:
                    target = f"{imported}.{alias.name}" if imported else alias.name
                    aliases[alias.asname or alias.name] = target
                    if target in forbidden_modules:
                        raise StructuralGateError(
                            f"forbidden from-import in {module}: {target}"
                        )
                if root in forbidden_imports:
                    raise StructuralGateError(
                        f"forbidden from-import in {module}: {imported}"
                    )
                if imported in forbidden_modules:
                    raise StructuralGateError(
                        f"forbidden from-import in {module}: {imported}"
                    )
                if root in {"dashboard", "src"}:
                    local_edges.add(imported)
                elif (
                    root
                    and root not in sys.stdlib_module_names
                    and root not in allowed_external
                ):
                    raise StructuralGateError(
                        f"undeclared external from-import in {module}: {imported}"
                    )
        normalized_edges: set[str] = set()
        for edge in local_edges:
            candidate = edge
            while candidate and candidate not in allowed_local:
                candidate = candidate.rsplit(".", 1)[0] if "." in candidate else ""
            if not candidate:
                raise StructuralGateError(
                    f"local import has no allowlisted target: {module} -> {edge}"
                )
            normalized_edges.add(candidate)
            queue.append(candidate)
        graph[module] = sorted(normalized_edges)

        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                value_node = node.value
                literal = (
                    _constant_string(value_node) if value_node is not None else None
                )
                if literal is not None:
                    normalized = literal.replace("\\", "/").casefold()
                    if any(
                        token in normalized
                        for token in (
                            "/srv/baseball-shadow",
                            "/production/",
                            "/collectors/",
                            "/evidence/",
                            "/model_artifacts/",
                        )
                    ):
                        raise StructuralGateError(
                            f"production/collector path capability in {module}"
                        )
            if isinstance(node, ast.Subscript):
                owner = _attribute_name(node.value, aliases)
                key = _constant_string(node.slice)
                if owner in {"os.environ", "environ"} and key is not None:
                    if any(
                        token in key.casefold()
                        for token in (
                            "production",
                            "collector",
                            "evidence",
                            "model_artifact",
                        )
                    ):
                        raise StructuralGateError(
                            f"production-capable environment lookup in {module}: {key}"
                        )
            if not isinstance(node, ast.Call):
                continue
            call = _attribute_name(node.func, aliases)
            if call is None:
                raise StructuralGateError(
                    f"indirect callable expression is prohibited in {module}"
                )
            suffix = call.rsplit(".", 1)[-1]
            if suffix in forbidden_calls and call not in exceptions.get(module, set()):
                raise StructuralGateError(
                    f"forbidden capability call in {module}: {call}"
                )
            if call == "os.open":
                if module != "dashboard.snapshot_store":
                    raise StructuralGateError(
                        "os.open is allowed only in the immutable reader"
                    )
                rendered = ast.dump(node)
                if any(
                    token in rendered
                    for token in (
                        "O_WRONLY",
                        "O_RDWR",
                        "O_CREAT",
                        "O_TRUNC",
                        "O_APPEND",
                    )
                ):
                    raise StructuralGateError(
                        "immutable reader attempted write-capable os.open flags"
                    )
            if (call.endswith(".open") or call == "open") and call != "os.open":
                if call == "open":
                    mode_node = node.args[1] if len(node.args) > 1 else None
                else:
                    mode_node = node.args[0] if node.args else None
                for keyword in node.keywords:
                    if keyword.arg == "mode":
                        mode_node = keyword.value
                if (
                    mode_node is None
                    or not isinstance(mode_node, ast.Constant)
                    or not isinstance(mode_node.value, str)
                ):
                    raise StructuralGateError(
                        f"filesystem open mode is not statically read-only in {module}"
                    )
                if any(token in mode_node.value for token in "wax+"):
                    raise StructuralGateError(
                        f"filesystem write capability in {module}"
                    )
    if visited != allowed_local:
        raise StructuralGateError(
            f"capability allowlist has unreachable modules: {sorted(allowed_local - visited)}"
        )
    return {key: graph[key] for key in sorted(graph)}


def _walk(value: Any):
    yield value
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def _as_bool(value: Any) -> bool:
    return str(value).casefold() == "true"


def _actions(statement: dict[str, Any]) -> set[str]:
    value = statement.get("Action", [])
    return {value} if isinstance(value, str) else set(value)


def verify_iam_documents(documents: list[dict[str, Any]]) -> None:
    globally_scoped_exceptions = {"sts:GetCallerIdentity", "ecr:GetAuthorizationToken"}
    forbidden_write_terms = (
        "snapshot",
        "evidence",
        "collector",
        "model-artifact",
        "production",
    )
    for document in documents:
        for node in _walk(document):
            if not isinstance(node, dict) or "Action" not in node:
                continue
            actions = _actions(node)
            if not actions or any("*" in action for action in actions):
                raise StructuralGateError("IAM wildcard or empty action is prohibited")
            resources = node.get("Resource")
            resource_values = (
                [resources] if isinstance(resources, str) else list(resources or [])
            )
            for resource in resource_values:
                if resource == "*" and not actions.issubset(globally_scoped_exceptions):
                    raise StructuralGateError(
                        f"IAM wildcard resource is prohibited for {sorted(actions)}"
                    )
                text = str(resource).casefold()
                if any(term in text for term in forbidden_write_terms):
                    raise StructuralGateError(
                        f"IAM resource crosses a prohibited boundary: {resource}"
                    )
            for action in actions:
                lowered = action.casefold()
                if any(
                    term in lowered
                    for term in (
                        "lightsail",
                        "s3:put",
                        "dynamodb:put",
                        "ssm:sendcommand",
                    )
                ):
                    raise StructuralGateError(f"prohibited IAM capability: {action}")


def verify_iac_and_workflows(repository_root: Path) -> dict[str, str]:
    foundation = load_yaml(repository_root / "infra/omega_staging/foundation.yaml")
    service = load_yaml(repository_root / "infra/omega_staging/service.yaml")
    candidate_workflow = load_yaml(
        repository_root / ".github/workflows/omega-candidate-ci.yml"
    )
    deploy_workflow = load_yaml(
        repository_root / ".github/workflows/omega-staging-deploy.yml"
    )
    deploy_policy = json.loads(
        (
            repository_root / "infra/omega_staging/github-deploy-policy.template.json"
        ).read_text(encoding="utf-8")
    )
    resources = foundation.get("Resources", {})
    forbidden_types = {
        "AWS::EC2::EIP",
        "AWS::EC2::InternetGateway",
        "AWS::EC2::NatGateway",
        "AWS::EC2::Route",
        "AWS::EC2::TransitGateway",
        "AWS::EC2::VPCGatewayAttachment",
        "AWS::EC2::VPCEndpoint",
        "AWS::EC2::VPCPeeringConnection",
        "AWS::EC2::VPNConnection",
        "AWS::EC2::VPNGateway",
    }
    observed_types = {
        item.get("Type") for item in resources.values() if isinstance(item, dict)
    }
    if observed_types.intersection(forbidden_types):
        raise StructuralGateError(
            "foundation contains an outbound or cross-network resource"
        )
    if (
        "AWS::AppRunner::VpcConnector" not in observed_types
        or "AWS::EC2::VPC" not in observed_types
    ):
        raise StructuralGateError(
            "dedicated isolated VPC connector foundation is incomplete"
        )
    subnets = [
        item
        for item in resources.values()
        if isinstance(item, dict) and item.get("Type") == "AWS::EC2::Subnet"
    ]
    if len(subnets) < 2 or any(
        _as_bool(item.get("Properties", {}).get("MapPublicIpOnLaunch"))
        for item in subnets
    ):
        raise StructuralGateError(
            "at least two private no-public-IP subnets are required"
        )
    if any(
        isinstance(item, dict) and item.get("Type") == "AWS::EC2::NetworkAclEntry"
        for item in resources.values()
    ):
        raise StructuralGateError(
            "deny-all network ACL must not contain an allow entry"
        )
    connector_group = resources.get("DenyAllConnectorSecurityGroup", {})
    sink_group = resources.get("UnattachedIsolationSinkSecurityGroup", {})
    if (
        connector_group.get("Type") != "AWS::EC2::SecurityGroup"
        or sink_group.get("Type") != "AWS::EC2::SecurityGroup"
    ):
        raise StructuralGateError(
            "connector and unattached isolation-sink groups are required"
        )
    connector_properties = connector_group.get("Properties", {})
    expected_egress = [
        {
            "IpProtocol": "tcp",
            "FromPort": "9",
            "ToPort": "9",
            "DestinationSecurityGroupId": {
                "!GetAtt": "UnattachedIsolationSinkSecurityGroup.GroupId"
            },
        }
    ]
    if (
        connector_properties.get("SecurityGroupIngress") != []
        or connector_properties.get("SecurityGroupEgress") != expected_egress
    ):
        raise StructuralGateError(
            "connector group must suppress implicit egress with only the unreachable sink rule"
        )
    connector = resources.get("DashboardVpcConnector", {}).get("Properties", {})
    if connector.get("SecurityGroups") != [
        {"!GetAtt": "DenyAllConnectorSecurityGroup.GroupId"}
    ]:
        raise StructuralGateError(
            "VPC connector must use only the deny-all connector group"
        )
    serialized_foundation = json.dumps(foundation, sort_keys=True).casefold()
    for prohibited in ("0.0.0.0/0", "::/0", "assignipv6addressoncreation"):
        if prohibited in serialized_foundation:
            raise StructuralGateError(
                f"foundation contains prohibited network/production token: {prohibited}"
            )
    if re.search(r"(?:vpc|subnet|sg)-[^\"\s]*production", serialized_foundation):
        raise StructuralGateError(
            "foundation references a production network identifier"
        )
    if re.search(r"arn:[^\"\s]+:[0-9]{12}:", serialized_foundation):
        raise StructuralGateError("foundation embeds a concrete AWS account identifier")

    service_resource = service.get("Resources", {}).get("DashboardService", {})
    properties = service_resource.get("Properties", {})
    network = properties.get("NetworkConfiguration", {})
    ingress = network.get("IngressConfiguration", {})
    egress = network.get("EgressConfiguration", {})
    source = properties.get("SourceConfiguration", {})
    if _as_bool(ingress.get("IsPubliclyAccessible")):
        raise StructuralGateError("App Runner ingress must remain private")
    if str(egress.get("EgressType")) != "VPC" or "VpcConnectorArn" not in egress:
        raise StructuralGateError(
            "App Runner egress must use the dedicated VPC connector"
        )
    if _as_bool(source.get("AutoDeploymentsEnabled")):
        raise StructuralGateError("automatic App Runner deployment is prohibited")
    image_pattern = (
        service.get("Parameters", {})
        .get("ImageIdentifier", {})
        .get("AllowedPattern", "")
    )
    if "@sha256:" not in str(image_pattern):
        raise StructuralGateError("service image reference must be digest constrained")

    deploy_triggers = deploy_workflow.get("on", {})
    if set(deploy_triggers) != {"workflow_dispatch"}:
        raise StructuralGateError("staging deploy workflow must be manual only")
    inputs = deploy_triggers.get("workflow_dispatch", {}).get("inputs", {})
    source_input = inputs.get("source_commit", {})
    if not _as_bool(source_input.get("required")):
        raise StructuralGateError("manual deploy requires an exact source_commit input")
    candidate_triggers = set(candidate_workflow.get("on", {}))
    if not candidate_triggers.issubset({"pull_request", "workflow_dispatch"}):
        raise StructuralGateError("candidate validation has an unauthorized trigger")
    for workflow in (candidate_workflow, deploy_workflow):
        for node in _walk(workflow):
            if isinstance(node, dict) and isinstance(node.get("uses"), str):
                reference = node["uses"]
                if re.fullmatch(r"[^@]+@[0-9a-f]{40}", reference) is None:
                    raise StructuralGateError(
                        f"GitHub Action is not immutable-SHA pinned: {reference}"
                    )
        serialized_workflow = json.dumps(workflow, sort_keys=True)
        if re.search(
            r"git\s+push.*(?:origin\s+main|refs/heads/main)",
            serialized_workflow,
            re.IGNORECASE,
        ):
            raise StructuralGateError("workflow may mutate the default branch")
        if re.search(
            r"AKIA[0-9A-Z]{16}|aws_secret_access_key|BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY",
            serialized_workflow,
        ):
            raise StructuralGateError("workflow contains an embedded credential")
    verify_iam_documents([foundation, deploy_policy])
    return {
        "network": "DESIGNED_NOT_DEPLOYMENT_VERIFIED",
        "ingress": "PRIVATE_TEMPLATE_ONLY",
        "egress": "DEDICATED_VPC_NO_ROUTE_TEMPLATE",
        "workflow": "MANUAL_EXACT_COMMIT",
    }
