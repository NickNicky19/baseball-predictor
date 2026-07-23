# Statcast profile content-hash repair v1

Status: predeclared integrity repair; no model promotion or betting
authorization.

## Measured incident

Source-bound Statcast profiles carry kind, status, cutoff, row count, and
fallback fields, but they do not bind the exact source content that produced
their values. Two payloads with equal row counts and dates can therefore create
different probability inputs under indistinguishable lineage. Feature JSON,
Parquet inspection columns, rich-feature lineage, health reports, and final
probability validation all inherit this omission.

This is a structural provenance defect. It requires no outcome access and no
May/2026 source inspection.

## Locked repair

- Canonical source hashing must be row-order independent, column-order
  independent, duplicate preserving, explicit about missing values, and reject
  unsupported/non-finite cell types.
- Each observed batter profile binds the exact player-specific rows consumed
  after its strict-prior/filter boundary.
- A league-fallback profile binds the exact fallback mapping consumed.
- Every non-legacy source-bound profile requires a lowercase SHA-256 digest.
- The digest must survive feature JSON/Parquet serialization, appear in health
  evidence, and be repeated identically in every effective rich Statcast field.
- Missing or mutated hashes fail again at feature storage and probability
  consumption.
- No numeric feature, coefficient, fallback value, or market rule changes.

## Required proof

- Row and column reordering preserve the hash; changing a value or duplicate
  population changes it.
- Unsupported/non-finite source cells fail closed.
- Missing/malformed profile hashes and rich-lineage hash mutations fail closed.
- Valid profile hashes round-trip through stored feature bundles and health
  evidence.
- The full regression suite passes and frozen artifacts remain untouched.
