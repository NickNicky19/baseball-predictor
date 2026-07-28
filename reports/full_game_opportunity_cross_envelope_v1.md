# Full-game opportunity cross-envelope v1

Status: **inert research-only trust boundary; external authority unbound**

This component is a blocked audit envelope for two independent probability
objects for one exact T-4 game state:

- the PR #32 batter-side projected-lineup PA-count PMF;
- the PR #35 starter-side joint batters-faced/outs removal PMF, whose source
  truth foundation is PR #33.

The structural validator requires the same official game, date, start time, exact T-4
horizon, opposing sides, teams, immutable plan, and exact source releases. It
also requires receipt-proven probable-starter identity, strictly-prior workload
lineage, explicit exclusion of May 2026, and an explicit bullpen boundary.

The old implementation could accept caller-authored hash labels and expose the
two PMFs from a public validation function. That was not evidence authority.
The repaired boundary requires safe root-relative retained-file references,
rejects any symlink/junction/reparse or root escape (including ancestors),
checks actual bytes against their declared raw-file hashes, rejects May 2026 in
retained data, and checks the semantic target/chronology fields that are
defined by locally shipped formats. Every retained reference is additionally
bound to the exact envelope-declared identity: direct source/configuration
files by raw-byte SHA-256, content-addressed objects by canonical JSON SHA-256,
and shipped self-hashed records by independently recomputing their self-hash.
The ordered batter-stats receipt list must match the ordered canonical receipt
digests exactly; a rehashed but substituted or reordered receipt cannot pass.

May rejection precedes ordinary date parsing and covers canonical ISO values,
supported separator aliases such as `2026.05.01` and `05.31.2026`, month-name
aliases, retained paths, and recursively nested retained payload values. A May
alias therefore cannot degrade into a generic parse failure or pass through a
string-only blind spot.

PR #32's complete raw schedule, roster, lineup-history, and dated-stats replay
types are not present in this source tree. The component therefore stops with
`external PR32 retained-evidence replay contract is unavailable; authority
remains unbound` after the byte and limited semantic checks. It does not invent
a replacement contract and cannot return either PMF.

The bullpen boundary represents only the transition state implied by starter
removal. It carries no bullpen-quality probability and no actual reliever
identity. No league-average or fabricated fallback is permitted.

## Exact component inputs

- Projected-lineup candidate PR #32:
  `1ebeb255bf3ecf17adb82f644f9dc2ff11c7491c`
- Pitcher source-truth PR #33:
  `506ebb40203582d25ff01c0a8c2d1dec4c44ed7d`
- Joint pitcher scaffold PR #35:
  `0e6a40d5cd1b00d3b1f1128f97a12e7b897aaf55`

These hashes identify reviewed inputs; they do not authorize deployment or
probability consumption.

## Public behavior

The checked-in authority contract is `UNBOUND_EXTERNAL_TRUST_REQUIRED`. The
public consumer therefore returns one terminal abstention before inspecting or
exposing either PMF. The audit validator also has no PMF-returning success path.
A future bound consumer requires the missing retained-evidence replay types, a
new reviewed implementation, and an independently fixed authority receipt.

## Explicit non-capabilities

This release does not:

- fit, calibrate, score, select, or promote a model;
- change any probability;
- multiply batter and pitcher distributions;
- model bullpen quality, reliever identity, or times through order;
- accept an actual postgame starter or reliever;
- backfill a missed receipt;
- inspect outcomes, May 2026, prices, execution, or settlement;
- modify collectors, schedules, AWS, or production.

Regression and mutation coverage includes hash-only/nonexistent evidence,
fabricated retained bytes, semantic target swaps, direct/raw/canonical digest
substitution, ordered receipt-set substitution, ledger context-lineage swaps,
ISO and alternate-format May-bearing envelope/path/payload data, direct PMF
exposure, exact T-4 chronology, and ancestor reparse points.

The minimum later evidence requirement remains a future-only externally
authorized archive binding both source bundles and every strictly-prior
workload receipt to the same immutable target.
