# Omega staging operations

## Current state

This candidate adds deployable definitions, not a deployment. AWS identity,
account, region, budget, and credentials were unavailable for independent
verification during authoring. Hash-locked dependencies passed a clean local
CPython 3.12 install, but Ubuntu CI and the container build remain fail-closed
prerequisites; see `requirements/profiles/README.md`.

## What the staging service does

The App Runner image serves only `dashboard/`. It cannot import or invoke the
model, feature builders, simulation, fitting, evaluation, collectors, or
production automation. It reads one configured immutable display snapshot,
validates it, and renders or exports values without recalculation. With no
snapshot publisher configured, it reports a truthful no-data state.

The existing Lightsail collection system and every file under `deploy/` remain
outside this architecture. The production-boundary hash manifest makes an
unexpected change to selected collector installers, service definitions, or
production workflows fail candidate CI.

## Release sequence

1. Install and verify the committed dashboard and test hash locks on the clean
   CPython 3.12/Ubuntu CI runner, and review every resolved package and hash.
2. Run candidate CI without AWS credentials or OIDC.
3. Review and manually deploy `infra/omega_staging/foundation.yaml` in the
   confirmed account/region, using an approved bootstrap identity.
4. Configure the reviewed GitHub `omega-staging` environment variables and its
   dedicated OIDC role. Do not use the Lightsail deployment keys.
5. Manually dispatch `omega-staging-deploy.yml` from `main` with one exact
   40-character commit already contained in `origin/main`.
6. Verify the workflow-recorded source commit, image digest, account, region,
   budget, stack status, App Runner status, disabled auto-deployment, and
   private ingress.

## Monitoring

Use App Runner service status and CloudWatch application/service logs. The
health endpoint reports service health and snapshot availability separately;
an unavailable snapshot is not silently converted into an older prediction.
The version endpoint binds the running image to `DASHBOARD_SOURCE_COMMIT`.

Stop and investigate on an account/region mismatch, budget failure, mutable tag,
missing lock, production-boundary hash mismatch, public ingress, enabled auto
deployment, unexpected snapshot, schema/hash failure, or any model/collector
import in the dashboard image.

## Explicit non-capabilities

This staging service does not collect receipts, run daily predictions, fit or
promote a candidate, access prices, settle outcomes, backfill evidence, inspect
May 2026, or authorize betting. Private access is not yet wired to a VPC or
Cognito; no claim of a user-accessible deployed GUI is made.
