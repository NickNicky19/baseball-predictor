# Read-only prediction snapshot dashboard

This package is a display boundary, not a prediction runner. It imports no
model, simulation, fitting, evaluation, feature, scraper, or collector module.
It reads one exact configured manifest, verifies the referenced snapshot bytes
and positive schema, and either displays those values unchanged or shows a
truthful no-data state.

## Runtime

```text
uvicorn dashboard.app:create_app --factory --host 0.0.0.0 --port 8080
```

Required environment:

- `DASHBOARD_SNAPSHOT_ROOT`: allowlisted read-only root.
- `DASHBOARD_MANIFEST_PATH`: one exact manifest beneath that root. If omitted,
  the UI displays no data. It never selects an older file.
- `DASHBOARD_ALLOWED_PRODUCERS`: comma-separated exact producer IDs.
- `DASHBOARD_AUTH_SECRET`: bearer token or private-session value. If absent,
  every non-health route fails closed.
- `DASHBOARD_SOURCE_COMMIT`: exact deployed source commit shown by `/version`.

The only unauthenticated endpoint is `/healthz`. Static assets, version,
predictions, and exports require authentication. The service performs no writes
and does not initiate network access.

## Numeric classifications

- Schema/version identifiers, SHA-256 length, Git commit length, MLB identities,
  and the requirement that probabilities sum to one are **STRUCTURAL**.
- The 1 MiB manifest bound, 10 MiB snapshot bound, HTTP port, identifier length,
  display text lengths, and `1e-12` serialization tolerance are **OPERATIONAL**
  resource/compatibility limits. They have zero model effect.
- Displayed predictions and uncertainty are **FITTED** or
  **FROZEN_COMPARATOR_ONLY**, as bound by the manifest. The dashboard cannot
  create or change them.

No `MANUAL_UNEXPLAINED` prediction-affecting numeric is present.
