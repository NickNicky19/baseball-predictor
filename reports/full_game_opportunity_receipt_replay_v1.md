# Full-game opportunity retained-receipt replay boundary v1

Decision: `LOCAL_VALIDATOR_REPAIRED_LINUX_GATE_DECLARED_REAL_RECEIPT_REPLAY_TERMINALLY_BLOCKED`

This is a research-only engineering boundary. It does not fit, score, generate,
expose, or combine any batter PA or starter-removal probability. It authorizes
neither model consumption nor betting.

## Source identity and limitations

The full-game envelope is based at commit
`b95f3589a99d4c0ca6055462d492a4e3f0593c09`. Its pitcher source-truth and
joint-opportunity ancestors are `506ebb40203582d25ff01c0a8c2d1dec4c44ed7d`
and `0e6a40d5cd1b00d3b1f1128f97a12e7b897aaf55`.

The projected-opportunity PR #32 commit
`1ebeb255bf3ecf17adb82f644f9dc2ff11c7491c` is present in the local Git object
database but is not an ancestor of this branch. Their merge base is
`9f9d839187550f7a7a6da3b4bfdb25b2dff3e794`. The replay boundary binds the exact
PR #32 protocol and source-manifest bytes by independently computed SHA-256 and
uses the exact projected-lineup and history validators present on this branch.
That does not make PR #32's full runner or probability implementation part of
this branch, and it does not authorize any PR #32 probability.

The candidate files are not yet a clean committed release. The checked-in
release manifest is an internal byte manifest, not an independent authorization
or a substitute for a clean Git commit and green Linux run.

## Exact required evidence surfaces

The checked-in authority enumerates 31 exact surfaces. They cover:

- PR #32 protocol, source manifest, and externally observed runtime release;
- the shared T-4 plan, batter side bundle, candidate record, and projected
  lineup;
- official active-roster receipt plus retained raw bytes;
- strictly-prior lineup-history coverage, schedule receipts/raw bytes, and
  final-game history receipts/raw bytes;
- date-bounded batter-stat transport receipts/raw bytes and explicit failed
  attempts;
- PR #35 protocol, evidence authority, runtime release, and model authorization;
- probable-starter plan receipt/raw schedule bytes, ledger manifest, terminal
  ledger entry, v2 context, and retained raw context bytes; and
- pitcher workload transport receipts/raw feeds, their receipt-manifest
  identity, and the derived point-in-time workload feature artifact.

The source pitcher implementation defines its workload receipt manifest as the
canonical ordered list of transport-receipt hashes. This boundary names the
retained manifest wrapper explicitly; it does not claim that the wrapper or an
externally authorized inventory digest has been delivered.

## Repaired source boundary

The initial audit found four source-level integrity defects: alternate May 2026
date spellings bypassed the raw-string screen, legitimate next-UTC-day game
starts were rejected, noncanonical timestamp spellings were accepted, and a
schema label plus a few optional identifiers could pass without source-specific
semantic replay.

The source boundary now:

- parses and rejects sealed May 2026 in canonical, US, basic, ISO-week,
  ordinal-date, and month-name forms before any evidence can be consumed;
- accepts the legitimate UTC rollover day for an official MLB game date while
  rejecting starts that drift beyond that bounded relationship;
- requires exact canonical UTC spellings and rejects spaces, minute-only
  values, offsets, redundant zero fractions, and short fractions;
- reconstructs the target ID from immutable game/start/horizon fields;
- rejects duplicate JSON keys in semantic receipts and the inventory itself;
- enforces exact typed field sets for fixed receipt types and mandatory typed
  fields for variable source documents;
- verifies immutable PR #32 and PR #35 protocol/authority bytes against exact
  source-commit digests;
- replays `ShadowCapturePlan`, projected-lineup, active-roster, strictly-prior
  schedule/history, starter-context, and pitcher-workload validators rather
  than trusting schema labels;
- binds transport request identity, byte length, payload hash, and raw companion
  bytes;
- reconstructs official schedule target/date/team identity from retained raw
  schedule bytes;
- reconstructs history-coverage denominators from retained schedule receipts
  and raw bytes and binds captured games to retained history receipts;
- validates the receipt-proven probable pitcher before a workload feature can
  be replayed; and
- preserves explicit terminal missingness without substitutes, backfill, or
  fabricated league-average values.

Exact source hashes do not by themselves prove entity, chronology, or protocol
meaning. Those claims are separately checked by the typed and raw replay paths
above. Full semantic coverage still cannot be demonstrated until a legitimate
inventory containing every required retained surface is independently bound and
replayed.

## Verification

The following local Windows commands are the declared verification surfaces
(all use `PYTHONDONTWRITEBYTECODE` behavior through `-B` and disable pytest's
cache provider):

1. Focused boundary:
   `python -B -m pytest -q -p no:cacheprovider tests/test_full_game_opportunity_receipt_replay_v1.py`
2. Combined boundary:
   `python -B -m pytest -q -p no:cacheprovider tests/test_full_game_opportunity_receipt_replay_v1.py tests/test_full_game_opportunity_cross_envelope_v1.py tests/test_full_game_opportunity_cross_envelope_release.py`
3. Inherited non-regression: the exact command in
   `.github/workflows/full-game-opportunity-receipt-replay-v1-linux.yml`, which
   runs the lineup, history, roster, opportunity, source-truth, and joint-pitcher
   suites and deselects only two branch-exact release-delta assertions.

The repaired focused suite has 48 tests. The combined suite has 106 tests; on
this Windows host 106 passed and two platform-specific symlink tests skipped.
The inherited suite passed 179 tests with two branch-exact delta assertions
deselected. Those assertions require, respectively, the exact PR #33 and PR #35
file delta and cannot pass on a later descendant integration branch containing
the cross-envelope and replay-boundary files. Their semantic and mutation tests
remain included.

The new Linux workflow is pinned to Ubuntu 24.04, CPython 3.12.3, pinned action
commits, and a hash-locked test-only dependency set. It disables third-party
pytest plugin autoload, runs focused, combined, and inherited suites, scans for
likely tracked credentials, and requires a mutation-free checkout. Linux has
not yet run, so this report does not claim a green Linux result.

All receipt fixtures used by the focused suite are synthetic. Passing them is
regression proof for the validator, not evidence that a genuine source receipt,
lineup projection, pitcher PMF, or market prediction exists.

## Terminal blocker

`config/full_game_opportunity_receipt_replay_v1.json` truthfully records:

- zero legitimate receipt types delivered to this audit lane;
- no externally authorized retained-inventory digest;
- semantic replay unauthorized;
- network fetch and backfill unauthorized;
- fitting and probability consumption unauthorized; and
- May 2026 access and betting unauthorized.

Consequently, even a structurally valid synthetic inventory returns
`BLOCKED_UNBOUND_EXTERNAL_RECEIPT_AUTHORITY`. The existing envelope remains
inert and its PMFs remain non-consumable.

## Future evidence that cannot be reconstructed here

The missing evidence must be captured legitimately at the original decision
horizon. It includes the PR #32 runtime receipt, raw active-roster, schedule,
prior-lineup and dated-stat responses, explicit source-error/missed-attempt
receipts, pitcher plan/context ledgers and raw schedule responses, and all
strictly-prior workload transport receipts. Missed prospective receipts cannot
be backfilled. No outcome, price, actual postgame starter, actual reliever, or
later lineup can replace them.

No real receipt, May artifact, outcome, price, market, AWS resource, collector,
model artifact, or probability was opened or changed during this work.
