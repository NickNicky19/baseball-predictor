# Omega staging infrastructure

These files describe an isolated, research-only AWS App Runner dashboard. No
AWS resources were created while authoring them because the AWS account,
region, identity, budget, and authorization boundary were not independently
verified in this environment.

## Fixed boundary

- Exact resource prefix: `baseball-predictor-omega-staging`.
- The App Runner service is private and has no VPC ingress connection in this
  candidate. It is therefore intentionally unreachable until private access is
  separately designed and authorized.
- The container has no access to Lightsail, collectors, production workflows,
  production predictions, S3, databases, or model execution.
- The only instance permission is reading one generated dashboard bearer
  secret. The service starts with an empty, read-only snapshot directory.
- ECR tags are immutable. Deployments use an exact image digest and exact
  40-character source commit. Auto-deployment is disabled.
- The ECR repository, dashboard secret, and App Runner service are retained on
  stack deletion to prevent accidental evidence loss. Removal is a separate,
  explicit operation.

## Prerequisites and stop conditions

Stop before deployment if any item is absent or contradictory:

1. An authenticated AWS identity in the intended account and region.
2. A confirmed AWS Budget whose actual spend has not exceeded its limit.
3. A dedicated GitHub OIDC role with the rendered least-privilege policy from
   `github-deploy-policy.template.json` and a trust policy restricted to this
   repository and the protected `main` environment.
4. The manually reviewed `foundation.yaml` stack in `CREATE_COMPLETE` or
   `UPDATE_COMPLETE` state.
5. Committed CPython 3.12 dashboard and test hash locks that have passed the
   candidate's Ubuntu 24.04 CI installation and test jobs.
6. A tested exact commit already reachable from `origin/main`.

The foundation is deliberately not deployed by a pull request. A human with an
approved bootstrap role must first review and deploy it, then configure these
GitHub environment variables:

- `OMEGA_STAGING_AWS_ACCOUNT_ID`
- `OMEGA_STAGING_AWS_REGION`
- `OMEGA_STAGING_BUDGET_NAME`
- `OMEGA_STAGING_DEPLOY_ROLE_ARN`
- `OMEGA_STAGING_FOUNDATION_STACK` (must equal
  `baseball-predictor-omega-staging-foundation`)
- `OMEGA_STAGING_SERVICE_STACK` (must equal
  `baseball-predictor-omega-staging-service`)

The `omega-staging` GitHub environment must require review. Pull-request CI has
no OIDC permission and no access to AWS secrets. The manual deploy workflow
checks the exact account, region, budget, stack outputs, commit, image digest,
private ingress state, and disabled auto-deployment before declaring success.

## Authentication and data

The foundation creates a random bearer secret in Secrets Manager; it never
prints the secret. App Runner injects it at runtime. Private ingress is the
primary perimeter. Cognito is not added because a secure public ingress path is
not established in this candidate. The service remains private as required.

There is intentionally no snapshot publisher. App Runner cannot read the local
Lightsail filesystem, and connecting it would couple staging to production
collection. A later, separately approved design must provide immutable,
content-addressed display snapshots without outcomes, May 2026 data, or write
access from the dashboard. Until then `/healthz` truthfully reports that the
service is healthy while snapshot state is unavailable.

## Numeric classifications

- Exact account IDs, role/resource names, commit length, SHA-256 length, and
  private-ingress requirements are **STRUCTURAL**.
- Port 8080, 1 vCPU, 2 GB memory, health intervals, and retention choices are
  **OPERATIONAL**. They have zero numerical model effect.
- No **FITTED**, **FROZEN_COMPARATOR_ONLY**, **SAFE_FALLBACK**, or
  **MANUAL_UNEXPLAINED** prediction-affecting constants are introduced here.

## Rollback

Rollback means manually dispatching the workflow with a previous commit that
is still on `main`. If that commit's immutable image already exists, the
workflow pulls it by digest, verifies its source-revision label, and reuses the
same bytes; it never overwrites or retags the image. Verify `/version`, the stack
outputs, private ingress, and App Runner status after rollback. A rollback does
not authorize model promotion or betting.
