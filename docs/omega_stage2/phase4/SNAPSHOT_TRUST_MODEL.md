# Snapshot Trust Model

Schema version: `omega-snapshot-trust-model-v1`.

The sole authorization root is the out-of-band digest of raw manifest bytes. The manifest then binds the exact snapshot path, SHA-256, byte size, schema version, producer identity, chronology fields, and all identity/source/chronology receipt objects used by rows.

Trust is intentionally narrow:

- SHA-256 establishes exact byte identity, not publisher legitimacy.
- Receipt matching establishes the semantic fields the reader consumes, not source truth beyond those receipts.
- Load-time chronology establishes eligibility under the configured clock and age bound, not prospective qualification.
- local path checks establish a tested reader-side non-writable boundary, not deployed storage immutability.
- authentication controls access to the rendered view, not snapshot authorship.

Any missing, stale, future, writable, ambiguous, mismatched, unresolved, unknown, or partially valid input rejects the entire snapshot. No failed condition triggers a fallback.
