# MLB data scraper v6 audit - 2026-07-17

## Scope

- ZIP SHA-256: `83016956e65f81667463d31165620f01977fc8f458480b9415f00329e2061272`
- PDF SHA-256: `2ec032fbeaee76f03e0b1f2c884363900c743aad130a9018795bc05eb935171d`
- Supplied tests: **24/24 passed** with offline `unittest` discovery.
- Review standard: the project's MLB player-prop evidence and execution contract, including exact prices and lines, hard identity, exact decision timing, immutable lifecycle, official-final truth, settlement, model provenance, product separation, and executable evidence.

## Disposition

**Substantially improved, but not admissible as the project's complete evidence collector.**

The prior stale-odds corruption is fixed and guarded by a regression test. Several other parser and correction defects were also repaired. The build is now a more credible **source-adapter/code-donor candidate**. It still cannot replace or bypass the project's existing lifecycle, identity, settlement, ledger, evidence-era, or execution-product contracts.

Do not deploy it wholesale and do not use its output for betting authorization.

## Improvements independently verified

1. The historical odds writer now reads each current row's price. The supplied regression preserves `-119`, `-125`, `-105`, and `+150` independently rather than repeating a stale value.
2. Raw payloads are content-addressed with SHA-256 and tampering is detected on read.
3. Vendor `result` and `won` fields remain quarantined from official grading.
4. Approved-API observations now reach the shared persistence function instead of being fetched and discarded.
5. PrizePicks pagination and per-page raw-artifact attribution have explicit tests.
6. Correction handling now rewrites changed official-stat values instead of merely applying a `CORRECTED` label.
7. Transport failures are explicitly logged for the keyless, ESPN, and approved-API clients covered by the new tests.
8. One-sided projections are not converted into fabricated two-sided prices.
9. Team aliases and exact-name player resolution are materially more complete than the prior build.

## Material blockers and new verified defects

### 1. The exact line and provider market timestamp are lost by the approved-API persistence path

The parser emits `point` and `market_last_update`, but `persist_board_obs()` writes `o.get("line")` and never writes `market_ts`.

An independent mutation produced:

```text
persist (1, 0)
stored_line None stored_market_ts None phase first_observed
```

This is disqualifying for market evidence. A Hits 0.5 observation cannot become a row with a null line, and a provider update timestamp cannot be silently discarded.

### 2. Provider-schema handling remains fail-open in normal operation

`KeylessHttpClient.strict_unknowns` defaults to `False`, and no CLI option or deployment contract enables it. The approved-API client inventories unknown fields but does not halt. More seriously, changing DraftKings' market name from the hard-coded string made the parser return zero observations with an empty unknown inventory:

```text
observations_after_market_name_change 0
unknown_inventory []
```

That mutation proves a plausible provider schema/name change can silently erase the Hits board.

### 3. No exact T-4h-to-close lifecycle exists

The supplied PDF correctly acknowledges this remains out of scope. There is no per-game target scheduler, locked pre-T-4h capture window, permanent missed-target state, restart recovery, no-backfill guard, exact prestart/closing capture, or complete-date lifecycle verifier. Every stored observation is labeled `first_observed`.

The project already has these contracts; any salvaged adapter must run behind them.

### 4. No final hard-keyed market identity is enforced

The database stores source selection keys plus later MLB game/player fields, but it never constructs and uniqueness-checks the final hard `MARKET_KEY` (`mlb_game_pk`, `player_id`, category, line, side/book contract). Duplicate/conflicting final market keys are named as funnel statuses but are not detected or enforced by the collector.

### 5. Event identity is not artifact-bound

The official schedule request is hashed, but `schedule()` discards the digest and `resolve_event()` does not return it. `_resolve_all()` updates the MLB game fields without populating `events.identity_artifact`. Therefore the final event mapping cannot be proven from a bound official identity artifact.

### 6. Official outcomes are not restricted to final games

`mlb-outcomes` reads the official game status but inserts outcome rows without requiring a final state. An in-progress box score can therefore enter the official-outcome table. A later correction command does not make the earlier non-final admission acceptable for an immutable grading contract.

Additionally, `game_info()` bypasses the shared request logger and has no explicit HTTP/transport failure record despite the module's stated request-provenance guarantee.

### 7. Observation timing is not the exact response-received timestamp

The HTTP layer records a response receipt time, but DraftKings, PrizePicks, and approved-API observations call `now()` again while parsing individual rows. This creates per-row parser timestamps instead of binding every row to the request's exact receipt timestamp. Around T-4h, that distinction can change eligibility.

### 8. Rejected observations do not receive their terminal funnel reason

Validation inserts the event/player first, increments a rejection counter, and continues. It does not call `finalize()` with the specific violation. The audit can reveal an unfinalized selection later, but the required mutually exclusive terminal reason is not written at rejection time.

### 9. Historical timestamp parsing can fail open

Malformed `start_time` or quote timestamps are caught with `except ValueError: pass`, leaving `post_pitch = 0`. That converts an unparseable timing claim into an apparently pregame row instead of quarantining or rejecting it.

### 10. Artifact publication is not crash-atomic

Raw bytes are written directly to their final content-addressed path before the database row is committed. There is no temporary-file write, fsync, atomic rename, or coupled publication certificate. A process or host interruption can leave partial/mismatched filesystem and database state.

### 11. The execution-product contract remains absent

There are no Onyx, Novig, or Chalkboard adapters. PrizePicks capture is a board scraper, not a complete account-visible lineup, multiplier, submit-ability, receipt, settlement, or return record. The code itself labels PrizePicks and DraftKings endpoints unofficial/ToS-gray. This cannot prove legal account access, actual availability, liquidity, fills, accepted amounts, product settlement, or realized payout.

### 12. Model, policy, and evidence-era binding remain absent

There is no locked model probability, model/config/code hash, feature snapshot, decision, prestart reference, settlement disposition, immutable ledger row, smoke exclusion certificate, or evidence-era compatibility enforcement.

## Test-harness assessment

The 24 tests are useful but not sufficient. They prove local parser behaviors, not the load-bearing end-to-end contract. Examples:

- The approved-API persistence test does not assert retained line or provider timestamp.
- The unknown-field test bypasses the request log and only checks that `_record_unknowns` exists.
- The pagination test checks the parser row count, not unique persisted observations and artifact lineage after storage.
- The one-sided test permits `side=None`; it does not prove explicit missing-side inventory for a nominally two-sided market.
- No mutation covers provider market-name drift, exact T-4h timing, restart/no-backfill behavior, final-only outcomes, hard final market-key duplication, model binding, settlement, or ledger immutability.

## PDF/provenance note

The two-page PDF renders legibly. Its v6 self-audit is dated `2026-07-18`, one day after this `2026-07-17` review. That future-dated label should be corrected or explicitly explained before the PDF is treated as provenance evidence.

## Safe reuse recommendation

Retain this build only as a candidate source-adapter library. The most reusable pieces are:

- exact historical price-retention fix and regression;
- raw-byte artifact hashing/tamper check;
- parser fixtures;
- PrizePicks pagination/per-page hash logic;
- official-stat correction comparison;
- team alias inventory.

Before any adapter is admitted, it must be surgically adapted behind the project's existing target scheduler and pass mutations for:

1. exact line and provider timestamp preservation;
2. strict required-field/schema failure by default;
3. hard game/player/final-market identity and duplicate exclusion;
4. exact response receipt time;
5. exact T-4h and prestart capture;
6. restart idempotence, permanent missed targets, and no backfill;
7. final-only official outcomes and correction publication;
8. locked settlement and immutable ledger integration;
9. model/config/feature/evidence-era hash binding;
10. product-specific, user-supplied or approved execution evidence.

## Bottom line

This update is real progress and fixes the prior headline corruption. It is **not yet the master evidence-and-execution system**. Integrating it wholesale would weaken contracts the project already enforces. The correct next action is to salvage only the improved parser components that fill a measured gap, after writing mutations that prove they preserve the existing lifecycle and evidence invariants.
