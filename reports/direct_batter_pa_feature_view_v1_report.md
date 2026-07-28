# Direct Batter PA Feature View v1 — Pre-Fit Integrity Report

## Decision

**PARTIAL: local label/feature separation and integrity validation passed, but
the release is `UNBOUND_SOURCE_PANEL_NOT_DELIVERED` and is not certifiable or
eligible for model fitting.**

This is not a fitted candidate, a game-level market model, a production
promotion, or betting authorization. It does not authorize opening 2024, the
spent 2025 HR confirmation, May 2026, economic evidence, or any new outcome.

## Predeclared repair contract

- Success: emit a deterministic feature artifact containing only the exact
  ordered positive allowlist, emit labels in a physically separate artifact,
  bind both to the repaired v4.1 panel and implementation bytes, and make every
  named leakage/integrity mutation fail closed.
- Failure: any unlisted/renamed/reordered field is accepted; a label or actual
  lineup field enters the feature artifact; feature bytes depend on labels;
  chronology, identity, denominator, missingness, or barrel/hard-hit mutations
  pass; or the artifacts cannot be hash-bound.
- Protected invariants: research-only; 2023 only; no fitting; 2024 and 2025
  protected; May 2026 skipped; no economic evidence; no AWS or collector work;
  frozen baselines and rejection records unchanged.
- Distinguishing mutations: label renamed to `history_out_hr`; target-label
  permutation; add/delete/rename/reorder a feature; count/rate/denominator and
  missingness contradictions; barrel greater than hard-hit; same-day history;
  doubleheader history divergence; actual `lineup_slot` consumption; missing
  feature/target identity counterpart.

The content and mutation conditions passed locally. Release certification did
not pass because the immutable source panel is not delivered inside this
worktree/release and no external expected release digest exists.

## Fixed expected upstream identity

- Repository base: `7ad2b847e8422169955114c01dea80bcf3751661`
- Repaired panel source commit: `c394dea5a0028b8f78e8ab78daed0d44cde5d771`
- Repaired panel SHA-256:
  `643a4c6533dbe59fc4e6b5e5932683c0877ae0a5d982945c799976d36af1ebf6`
- Panel manifest SHA-256:
  `083fe961b00299f0561130e204e5264dfa05f0b234b72ce2af214ac5f681e9a4`
- Panel certificate SHA-256:
  `aa3fd225f946b91ab1e1249eb8b7ce415f0fccabb18a041e3960552b2aeaf8ae`
- Upstream v4.1 artifact registry SHA-256:
  `f62983689106e37e57eb6ae2374c688f00c4764f75ee1a412b0f7542648e8096`

The coherent v4.1 panel was not rebuilt. Local artifacts were derived from
those exact bytes in a separate read-only worktree. Because those source bytes
are absent from this release, this is not a portable source binding and no
deterministic-release-rebuild claim is made.

## Output identity

| Artifact | Rows | Bytes | SHA-256 |
|---|---:|---:|---|
| `features_2023.csv.gz` | 43,740 | 14,246,866 | `662ce3b0305e477dd7905a726fcaa3031498260fc5079ae28cf3f36069dca57f` |
| `targets_2023.csv.gz` | 43,740 | 260,522 | `c51be0f96ef6b15b2b0f4bea3aa7f2219ddf570928e0f66db6e47eedf598d316` |
| `manifest.json` | — | 8,030 | `08e57f8355edea345562e6a9f00f005a89507fb686f3c73fdb3eff786f29c72a` |
| `certificate.json` | — | 3,669 | `f56c9c8deb0fd33ac161061ec868a72434a6d6143c402998424ede4d2adc59d1` |
| artifact registry | — | 3,659 | `bd2b9d013149775f8bca323fefbe2442fb63ccfaf81a47853c1e8bfc867c782f` |

The feature artifact has 4 identity columns, one chronology-lineage column,
and exactly 90 allowlisted batter-history features. The target artifact has the
same four identities plus only the eight PA outcome counts. The only permitted
join is one-to-one on `season`, `game_date`, `game_pk`, `player_id`.

The feature artifact physically excludes:

- actual `lineup_slot`;
- every `out_*` and `target_*` value;
- duplicate `target_date`;
- target-day pitcher, team, lineup, park, weather, umpire, market, and outcome
  context.

`game_date` remains only because it is part of the required identity and
chronology boundary. `max_source_date` is lineage-only and the contract forbids
model consumption of identity or lineage columns.

## Integrity results

- Exact positive feature schema and order: PASS.
- Identity duplicates: 0.
- Feature/target identity parity: exact for all 43,740 rows.
- Positive-PA fit-eligible rows: 43,726.
- Explicit receipt-proven zero-PA rows: 14.
- Zero-history rows retained without imputation: 545.
- Rows with explicit missing historical measurements: 26,660; none were
  silently dropped or filled.
- Same-day/future history violations: 0.
- Doubleheader rows: 792 across 396 player/date groups; both games retain the
  same conservative prior-calendar-date feature values.
- Barrel rate greater than hard-hit rate: 0.
- Count/rate/denominator and measured-BBE composition validation: PASS.
- Release rebuild claim: **NOT MADE** because the source panel is absent from
  the release.
- Focused feature-view suite: 50 passed.
- Combined feature-view, upstream panel/history, and Statcast integrity suite:
  87 passed.

## Bound implementation identity

The implementation fixes the exact ordered 90-name feature tuple, its digest,
the exact contract digest, upstream registry/source commit/artifact digests,
safe logical paths, complete implementation/dependency maps, and immutable
safety values independently of the JSON contract. Canonical root-contained
paths are required; symlinks, junctions, reparse points, or unexpected artifact
entries fail closed. The manifest binds the exact hashes of the contract,
builder, validator, feature-boundary module, mutation tests,
`requirements.txt`, and `pyproject.toml`. The observed build environment was
CPython 3.12.13 with NumPy 2.3.5 and pandas 3.0.1; the Python executable is also
hash-bound in the manifest.

## Explicit limitations and blockers

1. **Release source binding is incomplete.** The exact source panel is absent
   from this release and no externally trusted expected release digest exists.
   The current artifact is explicitly unbound, non-certifiable, and must not be
   consumed by fitting.
2. **Raw transport provenance remains incomplete.** The upstream panel binds
   643 derived per-player Statcast CSV hashes and official zero-PA receipts,
   but does not contain receipt-complete raw transport bytes, request/query,
   observation time, parser identity, and source-to-derived bindings for the
   entire Statcast/official-starter source universe.
3. **There is no exact dependency lock.** `requirements.txt` uses minimum
   versions. This build records the observed environment but does not pretend
   that observation is a portable exact lock.
4. **Full-game opportunity is not qualified.** This PA feature view cannot by
   itself produce valid game-level Hits, HR over 0.5, or Total Bases
   probabilities. Those require a separately receipt-bound, point-in-time
   projected-lineup, batting-slot, substitution, and PA-opportunity
   distribution. Missing, late, ambiguous, or contradictory opportunity
   evidence must produce an abstention.

## Highest-value next source repair

First create an immutable, independently identified release that safely
delivers the exact source panel, its manifest and certificate, plus an external
expected release digest. Then create receipt-complete historical source
manifests for the exact 2023 Statcast requests and official original-starter
target universe: retain raw response bytes, observation time, endpoint/query,
parser identity, source entity, and every source-to-derived hash. Independently
add an exact dependency lock. None of these repairs requires reopening outcomes.

Only after those release/source/reproducibility boundaries pass should one separately
predeclared, regularized 2023-only shared-PA challenger consume this view.
Game-level evaluation must still wait for the qualified opportunity layer, and
every market must be adjudicated separately.
