# Direct Batter PA 2023 Source-Release Boundary v1

## Decision

`OFFLINE_BOUNDARY_PASSED_NETWORK_FETCH_TERMINALLY_BLOCKED`

The offline source-release and external-verification boundary is implemented and
mutation-tested. No historical network fetch was run. A full fetch is not yet
authorized or executable through this code because two independent authorities
remain unavailable:

1. the real externally digest-anchored 2023 schedule-receipt manifest, exact
   outcome-free identity index, and separately anchored existing zero-PA
   identity map; and
2. proof of the transitive TLS/shared-library runtime. The runtime lock records
   the observed TLS and injection state but truthfully marks this authority
   incomplete, so both the CLI and direct build boundary reject live transport.

This milestone does not certify the legacy panel, authorize fitting, change any
probability, create prospective evidence, or authorize betting.

## Predeclared adjudication

Success required all of the following with synthetic/local response fixtures:

- strict positive schemas and unknown-field rejection for the contract, request
  plan, three external authority documents, runtime lock, receipts, complete
  release manifest, manifest bindings, and manifest file entries;
- full lexical/resolved-path checks before any caller-supplied file is read,
  rejecting explicit standalone four-digit or compact non-2023 dates/years
  (including pre-2000 and post-2099 values), sealed-May paths, parent traversal,
  filesystem-root targets, root escape, symlinks, junctions, and reparse points
  on ancestors and children, while leaving year-like substrings embedded in
  hashes and opaque IDs uninterpreted;
- separately supplied expected SHA-256 values for the official schedule-receipt
  manifest, exact identity index, existing zero-PA map, runtime lock, and final
  release identity;
- exact equality between the identity index and the allowlisted projection of
  retained official schedule receipt bytes;
- an identity index containing only `game_pk`, `official_date`, `game_type`,
  `away_team_id`, and `home_team_id`;
- a zero-PA map containing only `game_pk`, `official_date`, `player_id`, and its
  prior official response SHA-256, with no panel/outcome-field interface;
- unique request IDs, logical requests, and full URLs; non-overlapping Statcast
  intervals; exact official-feed coverage; and exact Statcast date coverage;
- every returned Statcast `game_pk`/date pair belonging to the identity index,
  with the complete observed game set equal to the complete index game set;
- retained raw bytes plus transport, parser, source, entity, chronology, and
  zero-PA semantic receipts;
- a complete manifest whose entire safety/authority identity—not only its file
  list—is hashed into the externally expected release digest; and
- independent semantic revalidation after byte/hash validation.

Failure was predeclared for any unknown field, weakened safety state, source or
parser drift, missing/wrong external digest, non-2023 or unsafe path, authority
index disagreement, duplicate/overlapping/gapped request, response membership
drift, semantic forgery, runtime/platform/TLS/site-injection drift, or attempt to
enable network capture.

Protected invariants remained unchanged: May 2026, 2024 selection, spent 2025 HR
confirmation, prices, prospective evidence, collectors, AWS, model fitting,
model scoring, and the five inherited dirty user CSVs were not touched.

## External authority interface

The offline builder requires all three inputs before it can even parse supplied
source-response fixtures:

1. `official-mlb-2023-schedule-receipt-manifest-v1`
   - separately supplied expected digest;
   - exact official schedule HTTPS source, query, parser, response metadata,
     response-body path, byte count, and response SHA-256;
   - retained raw schedule response bytes.
2. `direct-batter-pa-2023-identity-index-v1`
   - separately supplied expected digest;
   - binds the approved schedule-manifest digest;
   - sorted exact allowlisted projection of every schedule game identity.
3. `direct-batter-pa-existing-zero-pa-identity-map-v1`
   - separately supplied expected digest;
   - binds the already retained zero-PA bundle-manifest digest;
   - sorted unique identity rows only, with each target required to exist in the
     schedule-derived game index.

The schedule receipt may contain other historical response fields, but only the
five identity fields above can enter the identity index. A mutation that adds
`out_pa`, `out_hr`, or any other outcome-like field to the index or zero-PA map
is rejected as an unknown field. The existing zero-PA target set therefore must
be copied from the already retained receipt bundle; this interface cannot
discover targets from outcome panel columns.

The real authority artifacts were not safely available in this isolated lane.
No replacement, guessed index, fabricated target map, or outcome-derived index
was created.

## Request and response completeness

The request plan must exactly match the three external authorities:

- one unique official feed request for every index game and no other game;
- exact date/team identity for every feed request;
- exact existing zero-PA players attached to their indexed games;
- unique Statcast URLs and non-overlapping intervals whose calendar-date union
  equals the index-date set exactly.

Every Statcast response is parsed as canonical 2023 regular-season CSV. Pitch
identity must be unique. Every row's game/date pair must match the index, and
the final set of observed Statcast games must equal the index game set. Every
official feed is reparsed for finality, regular-season type, game/date/team
identity, and declared zero batting counts. Both the newly retained response
hash and the prior receipt-bundle response hash remain in zero-PA evidence.

## External release identity

The release manifest has an exact full key set and exact nested binding/file
schemas. Its external digest is calculated over the entire manifest identity,
including:

- status and terminal authority state;
- research-only, no-betting, no-fitting, no-network, no-prospective flags;
- all protected-data flags;
- source counts and full coverage counts;
- contract, parser, plan, runtime, schedule, identity-index, and zero-PA-map
  hashes; and
- every retained file path, byte count, and SHA-256.

Only `observed_release_digest` and the required-null
`external_expected_release_digest` fields are excluded to avoid recursion. A
valid-looking safety or coverage mutation changes the external identity; a
weakened safety state or unknown field cannot be hashed at all. Verification
still requires the expected digest from an independent channel and writes its
result outside the release.

## Runtime authority and terminal network block

The standard-library-only runtime lock now records:

- CPython version, build, compiler, and executable SHA-256;
- operating-system release/version, machine, and architecture;
- every used standard-library module identity and file hash;
- exact `sys.path`, system/user site roots, `.pth` file hashes, user-site state,
  and loaded `sitecustomize`/`usercustomize` identities;
- OpenSSL version, `_ssl` and `_hashlib` module hashes, default CA file/path
  identities, and relevant environment overrides.

The lock explicitly states
`BLOCKED_UNPROVEN_TRANSITIVE_TLS_SHARED_LIBRARIES` because it cannot prove the
complete transitive shared-library and CA-directory resolution across supported
platforms. `network_fetch_authorized` is therefore false. There is no network
transport implementation in the builder. The only accepted transport is an
exact built-in `OfflineFixtureTransport` containing a plain dictionary whose
URL key set exactly equals the approved request plan. The CLI network gate
always raises, even when `--allow-network` is supplied.

This is a truthful downgrade, not a workaround. A future network collector must
be separately implemented and certified only after its platform-specific TLS,
shared-library, CA, environment, and injection authority is complete.

## Mutation proof

The offline tests cover the prior mutations plus the independent review's new
negative cases:

- every build input path, verification path, and runtime-lock output path;
- May/cross-year paths before attempted open, including standalone 1999, 2022,
  2024, and 2100 tokens and their compact-date equivalents, plus negative
  controls for year-like substrings embedded in hashes and opaque IDs;
- ancestor/child reparse detection and relative root escape;
- duplicate full URLs/logical requests and overlapping Statcast intervals;
- unknown schedule-manifest, identity-index, zero-PA-map, manifest, binding, and
  file-entry fields;
- outcome-like fields in authority inputs;
- schedule receipt body/hash drift and index projection drift;
- missing feed/zero-PA/date coverage and non-member Statcast game IDs;
- platform, `sys.path`, TLS-authority, and network-authority mutations;
- missing/extra offline fixture URLs and callable/live-transport substitution;
- manifest safety weakening and re-anchored false coverage;
- raw byte, parser, entity, timestamp, redirect, content type, zero-PA, and
  rehashed semantic-receipt mutations.

## Exact prerequisite and next action

The exact next action is not a network fetch. It is external construction and
review of the three required real authority artifacts:

1. retain and independently anchor official MLB 2023 regular-season schedule
   request/response receipts;
2. project only the five approved identity fields and independently anchor the
   exact sorted index;
3. copy the existing zero-PA identity tuples and prior response hashes from the
   already hash-verified receipt bundle—without opening panel outcome columns—
   and independently anchor that map.

After those exist, the remaining blocker is a separately reviewed
platform-specific network collector/runtime boundary with complete transitive
TLS/shared-library and CA authority. Until both blockers are resolved, there is
no lawful full-fetch command. Feature regeneration, model fitting, and scoring
remain prohibited later stages.
