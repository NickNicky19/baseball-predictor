# Manifest Authorization Contract

Schema version: `omega-manifest-authorization-contract-v1`.

The dashboard may display a snapshot only when an independently configured lowercase SHA-256 equals the digest of the exact raw manifest bytes read from the verified descriptor. The digest is not accepted from the manifest, snapshot, filename, query, request body, or snapshot directory. Missing or malformed trust configuration yields no data.

Authorization order is: validate safe relative manifest path; prove the root and path chain non-writable; read the manifest from one verified descriptor within the configured size bound; compare its raw-byte SHA-256 with the out-of-band digest; strictly parse duplicate-free UTF-8 JSON; validate manifest schema v2; read and hash the bound snapshot through the same file boundary; then validate every row and receipt binding atomically.

Equivalent JSON serialization changes bytes and therefore requires a different externally approved digest. No signature scheme, private key, fallback snapshot, demo row, previous file, or network source exists. This reader contract does not qualify or authorize a publisher.

Executable enforcement: `dashboard/snapshot_store.py`; configuration schema: `MANIFEST_TRUST_SCHEMA.json`; negative controls: `tests/omega_dashboard/test_snapshot_contract.py`.
