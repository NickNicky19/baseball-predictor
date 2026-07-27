# Staging Deployment Prohibition

Schema version: `omega-staging-deployment-prohibition-v1`.

Phase 4 authorizes design and local validation only. No AWS API, CloudFormation create/update, IAM mutation, App Runner action, VPC creation, image push, deployment, or runtime probe is permitted. The deployment workflow remains manual, exact-commit constrained, main-only, immutable-image constrained, private-ingress constrained, and disabled until a later approval supplies and verifies the AWS account, region, budget, OIDC role, foundation, network, authentication, and runtime identities.

Current state is exactly `DESIGNED_NOT_DEPLOYMENT_VERIFIED`. QA-005 through QA-008 remain open.
