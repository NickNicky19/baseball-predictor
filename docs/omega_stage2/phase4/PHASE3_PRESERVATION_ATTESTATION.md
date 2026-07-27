# Phase 3 Preservation Attestation

- Schema version: `phase3-preservation-attestation-v1`
- Original candidate commit: `1ff7a2b9d064a53d1e26d961c7e511651ea2f0d7`
- Canonical base commit: `67efa1427987517d2c283cd502c1898b97c6bb2b`
- Preserved output count: `20`
- Preserved output bytes: `90167`
- Manifest SHA-256: `b1ca3411fb0c8c164a47343bcf314bb31f03ae3812091f4dd7897164c9afcc19`

Before Phase 4 edits, every Phase 3 output was hashed, copied byte-for-byte to the separate content-addressed directory `C:\Projects\baseball_predictor_phase3_audit_b1ca3411fb0c8c164a47343bcf314bb31f03ae3812091f4dd7897164c9afcc19\evidence`, rehashed, and marked read-only. These two preservation records are selected for the Phase 4 commit; the underlying Phase 3 reports are deliberately excluded.

The read-only attribute is an operational guard, not cryptographic authorization. The manifest SHA-256 is the byte-identity anchor. No preserved report was edited.
