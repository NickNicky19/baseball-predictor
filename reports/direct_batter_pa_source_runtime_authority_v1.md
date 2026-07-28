# Direct batter PA source runtime authority v1

## Decision

`DEPENDENCY_AUTHORITY_CLOSED_LINUX_RUNTIME_GATE_AUTHORED_NOT_EXECUTED_NETWORK_BLOCKED`

This repair closes the locally provable dependency and import-authority defect for the direct 2023 batter-PA source audit. It does **not** authorize a source fetch, model fitting, prediction generation, promotion, production use, or betting.

## What is now bound

- `requirements-direct-batter-pa-source-authority.lock` is an exact, SHA-256-hashed lock generated from the two direct requirements in `requirements-direct-batter-pa-source-authority.in`.
- The fixed lock SHA-256 is `0847ea7c8fe05649a94293b2adbba0164f10f420ca984ecf35f105f1aabc2a1f`.
- The import scanner covers the source-release builder, legacy source-binding validator, runtime-authority verifier, and all three focused test modules. Every audited Python file is SHA-256-bound and the scanner fails closed on an undeclared third-party or undeclared local import root.
- The only direct third-party import roots are `pandas` and `pytest`; both map to exact locked distributions. Their transitive distributions are also exact and hashed.
- The installed-environment check rejects missing, extra, duplicate, or version-drifted distributions, verifies each installed file against SHA-256 RECORD metadata where available, hashes every installed distribution file, and requires each imported package origin to belong to its declared distribution.
- The Linux workflow creates a no-pip virtual environment before installing only the hash-locked closure. Pytest runs in isolated mode with third-party plugin autoload disabled.
- The runtime policy fixes Ubuntu 24.04 x86_64 and CPython 3.12.3.
- The Linux attestation initializes the default TLS context without contacting a remote host, hashes the Python executable, `_ssl`, `_hashlib`, default CA bytes, and every loaded shared-library byte, and requires loaded `libssl` and `libcrypto`.
- The Linux gate generates two independent canonical attestations and requires byte-for-byte equality.
- Repository inputs reject every symlink/reparse ancestor. Python, loader, OpenSSL, TLS, and CA path-injection variables, user-site loading, `.pth`, `sitecustomize.py`, and `usercustomize.py` are rejected.
- The candidate hash manifest exactly covers every policy, source, test, report, lock, workflow, and secret-scanner input used by this gate and is verified inside CI. It remains candidate-local rather than an independent trust anchor.

## Fail-closed boundary preserved

`network_fetch_authorized` remains false in policy, static-check output, and Linux-attestation output. The existing source-release builder still has no live transport and its terminal network block was not weakened or bypassed. An externally supplied expected runtime-attestation digest remains mandatory before any future transport review.

## Verification performed locally

- Static lock and import closure: passed.
- Exact installed-distribution and Linux runtime checks require the dedicated clean Linux environment and remain unexecuted locally.
- Focused source binding, source release, and runtime authority suite: `115 passed` locally under CPython 3.12.13 with isolated pytest and plugin autoload disabled.
- Local Windows execution cannot satisfy or certify the Linux runtime target; the dedicated GitHub Actions gate is authored but has not been executed in this worktree.

## External blockers that remain

1. Execute and independently review the exact Linux CI bytes and its canonical attestation digest.
2. Supply an external expected runtime-attestation digest; do not let the candidate self-authorize its own runtime.
3. Supply independently anchored 2023 schedule, game/player identity, and zero-PA authority artifacts.
4. Supply receipt-complete request/response provenance for the historical source bytes.
5. Supply an independent external expected release digest.

Until every applicable blocker is closed, source fetching and fitting remain forbidden. The historical source-binding rejection record remains unchanged rather than being rewritten after this repair.
