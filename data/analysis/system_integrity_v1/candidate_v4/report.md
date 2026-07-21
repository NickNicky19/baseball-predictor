# Statcast integrity candidate report

- Candidate: `statcast-integrity-v3-7db213dfacc6f820`
- Classification: INTEGRITY_REPAIR_AND_RESEARCH_CANDIDATE_NOT_PROMOTED
- Promotion: **NO**
- Betting authorization: **NO**
- May 2026: not fetched, read, parsed, or written
- Prospective evidence: not backfilled

## Verified reconstruction

- Source chunks: 14
- Input-only rows: 322042
- Measured BBE: 55400
- Feature dates: 88
- Strictly-prior feature rows: 38891

## July 21 incident

The preserved archive remains unchanged at SHA-256 `edbfabdc97255515f87dc3ab4683530101753f10f44f47b9b953b4285351064b` and records
barrel rate 0.500000 versus hard-hit rate 0.094000.
The repaired numeric MLB identity 680757 uses sources only through
2026-07-20: 0 barrels and
10 hard-hit BBE over a shared denominator of
98 (0.000000 and
0.102041).

## Adjudication

This establishes an integrity repair, not probability improvement. Calibration,
Brier score, log loss, discrimination, uncertainty, and market behavior have not
yet been rerun. The candidate is not promoted and cannot authorize betting.

Highest-value next action: Bind the repaired count-bearing features into an isolated HR-first probability candidate, audit every active formula/override for duplicate or unsafe consumption, then fit on 2023 and select once on 2024 without opening May or reusing spent HR confirmation.
