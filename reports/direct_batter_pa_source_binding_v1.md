# Direct batter PA 2023 source-binding audit v1

## Decision

`BLOCKED_INCOMPLETE_SOURCE_AND_DEPENDENCY_AUTHORITY`

The delivered 2023 panel has strong local byte and semantic consistency, but it
cannot yet be certified as an independently source-authoritative,
dependency-reproducible release. It is not eligible for fitting, protocol
locking, model consumption, shadow use, promotion, activation, or betting.

This is an authority finding, not a finding that the 2023 values are false.

## Predeclared adjudication

- Success: independently reproduce every fixed upstream artifact hash; rehash
  all permitted 2023 raw inputs; prove a 2023-only official source projection
  equals the panel; verify every zero-PA receipt; and permit downstream use only
  if transport, dependency, and external release authority are complete.
- Failure: accept a missing, changed, extra, cross-year, path-escaped, or
  semantically inconsistent source; silently promote an unavailable authority;
  or permit fitting from an incomplete result.
- Protected invariants: no May 2026 read/fetch/write; no 2024 selection or 2025
  spent HR-confirmation outcomes; no prices or prospective evidence; no fitting,
  scoring, selection, collector access, production change, or betting
  authorization.
- Required mutations: contract plus runtime authority promotion, unsafe path,
  non-2023 official row, duplicate official identity, and attempted fitting from
  an incomplete authority result must all fail.

## Reproduced local integrity

- Upstream registry SHA-256:
  `f62983689106e37e57eb6ae2374c688f00c4764f75ee1a412b0f7542648e8096`
- Upstream panel: 43,740 rows; 14,364,814 bytes; SHA-256
  `643a4c6533dbe59fc4e6b5e5932683c0877ae0a5d982945c799976d36af1ebf6`.
- Upstream manifest SHA-256:
  `083fe961b00299f0561130e204e5264dfa05f0b234b72ce2af214ac5f681e9a4`.
- Upstream certificate SHA-256:
  `aa3fd225f946b91ab1e1249eb8b7ce415f0fccabb18a041e3960552b2aeaf8ae`.
- All 643 manifest-listed `2023/` batter Statcast CSVs rehashed exactly.
- The 205 season-only 2023 daily official files contain exactly 43,740 unique
  identities. Their typed official projection exactly equals the panel after
  canonical identity sorting. Fixed tree SHA-256:
  `b1deaed7c16eb35ec75351a37c5d7549d8f47932ba9fbd69ab4bd309eb698408`.
- All 14 zero-PA raw MLB response receipts rehashed exactly, and the 14 evidence
  rows bind one-to-one to those receipt hashes.
- The focused regression/mutation suite passed 11/11.
- Machine-readable report: 2,533 bytes; SHA-256
  `42a1697a2b98446751eeebbbc3cb5b62a3d1ffb463e12f626a8bfcac4793c3b0`.

## Material authority defects

1. `LEGACY_OFFICIAL_SOURCE_HASH_SPANS_2023_2025`

   The legacy builder reads a 43,740-row 2023 prefix but hashes the entire mixed
   `training_hitters_2023_2025_statcast.csv.gz`. A clean 2023-only rebuild under
   the present boundary must not read or hash that mixed file. The 205 daily
   2023 files prove a season-only local value-equivalent source exists, but they
   do not by themselves prove independent transport provenance.

2. `RAW_STATCAST_TRANSPORT_RECEIPTS_NOT_DELIVERED`

   The 643 raw CSV hashes establish byte identity only. No complete retained
   request/query, response bytes, observation time, parser identity, source
   protocol, and source-to-file binding was found for those CSVs.

3. `INDEPENDENT_OFFICIAL_TRANSPORT_RECEIPTS_NOT_DELIVERED`

   The 205 daily files are season-only and value-equivalent to the panel, but
   their upstream MLB response receipts are not delivered. Their local bytes
   therefore cannot serve as independent source authority.

4. `EXACT_REPRODUCIBLE_DEPENDENCY_LOCK_NOT_DELIVERED`

   `requirements.txt` and `pyproject.toml` are hash-bound declarations. They are
   not a complete exact lock. The observed Python/pandas environment is an
   observation, not a portable reconstruction guarantee.

5. `EXTERNAL_EXPECTED_RELEASE_DIGEST_NOT_DELIVERED`

   The registry and certificates establish internal consistency. No independent
   trusted digest anchors the complete release against wholesale replacement or
   rollback.

## Downstream feature-view consequence

The clean-main feature-view test repair changes the tracked test hash, so the
previous derived feature manifest, certificate, registry implementation map,
and their cascading hashes cannot truthfully be copied forward or edited in
place. The only correct next release is either:

1. regenerate the derived features, targets, manifest, certificate, and registry
   from an exact delivered and authority-qualified source package under a locked
   environment; or
2. publish only inert boundary code with all derived artifact identities absent
   or explicitly `null` and a non-certifiable/unbound status.

Stale generated hashes must not be delivered as current claims.

## Smallest correct next repair

Create a receipt-complete, immutable 2023-only source release containing:

- raw Statcast request/query identity, response bytes, timestamps, parser
  identity, and source-to-CSV binding;
- independent official game/source receipts for every 2023 target identity;
- the existing zero-PA receipt chain;
- a complete exact dependency lock with installation hashes;
- a trusted external expected release digest.

Then rebuild the 2023 panel and feature view from that release, compare their
typed contents against the preserved artifacts, and rerun every fail-closed
mutation. Until then, the explicit blocker is the truthful result.
