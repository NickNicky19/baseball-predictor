# Scaffold Packaging Contract

Schema version: `omega-scaffold-packaging-contract-v1`.

The standalone distribution is `omega-trust-scaffold==0.4.0`. A clean context is generated outside the checkout from one authoritative `dashboard/` implementation, `src/omega_contracts/`, and `packaging/omega_scaffold/pyproject.toml`. It excludes tests, reports, live prediction code, collectors, data, evidence, model artifacts, credentials, and Phase 3 material.

Build dependencies come only from `scaffold-build.lock`; isolation is disabled only after that exact environment is installed. `SOURCE_DATE_EPOCH=315532800` fixes archive timestamps. The complete wheel entry set must equal `WHEEL_CONTENT_ALLOWLIST.txt`. The Docker image installs hash-locked runtime dependencies and the already inspected wheel; it never copies dashboard source from the checkout.

Fresh installed verification moves outside the checkout, clears `PYTHONPATH`, confirms imports resolve under site-packages, verifies package data, closes direct third-party imports against the runtime lock, runs dashboard and portable contract tests, and runs `pip check`.
