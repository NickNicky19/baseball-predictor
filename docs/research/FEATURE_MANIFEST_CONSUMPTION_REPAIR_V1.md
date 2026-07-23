# Feature manifest consumption repair v1

Status: predeclared integrity repair; no model promotion or betting authorization.

## Incident

`FeatureStore.save` emitted content hashes and `verify_manifest` could check
them, but `FeatureStore.load` never called that verifier.  Any consumer could
therefore load a mutated JSON/Parquet bundle even when a contradictory manifest
was present.  The manifest's declared bundle count was also not checked against
the deserialized population.

## Locked repair

- A present manifest must verify before any feature artifact is read.
- Only an artifact explicitly listed by that manifest may be selected.
- The deserialized bundle count must equal the manifest count.
- A missing manifest remains an explicitly logged legacy-unverified state so
  frozen historical artifacts stay readable; it may never be relabeled as
  verified evidence.
- No feature value, probability coefficient, fallback, or archive is changed.

## Required proof

- Mutating a manifested bundle must make `load` fail before deserialization.
- Mutating only `bundle_count` must make `load` fail after deserialization.
- An unlisted sibling artifact must not override a listed artifact.
- Valid JSON and Parquet round trips must remain unchanged.

