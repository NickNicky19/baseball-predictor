# Feature publication transaction repair v1

Status: predeclared integrity repair; no model promotion or betting
authorization.

## Measured incident

`FeatureStore.save` wrote canonical `bundles.json` and `bundles.parquet` paths
before it published the manifest. A first-ever save interrupted after either
artifact therefore left a new canonical file with no manifest; `load` treated
it as a legacy-unverified snapshot and could consume a partial publication.
On replacement saves, artifacts were also overwritten one at a time before the
new manifest became authoritative.

The existing manifest verifier catches many completed-write mutations, but it
cannot make a multi-file publication transactional. This is a structural crash
consistency defect and requires no outcome or May access.

## Locked repair

- New artifacts publish under SHA-256 content-addressed filenames.
- A manifest referencing the complete generation is atomically replaced only
  after all declared artifacts exist with their exact hashes and byte counts.
- Without a manifest, only the old canonical legacy filenames remain eligible;
  content-addressed or temporary orphans are never consumed.
- With a manifest, loading uses only its declared artifact paths and honors the
  requested JSON/Parquet preference within that generation.
- Manifest paths must be local basenames; absolute/traversal paths fail closed.
- Declared byte counts are verified as well as hashes.
- Existing generations and orphans are retained; the repair does not delete or
  rewrite historical feature artifacts.

## Required proof

- A simulated interruption before manifest publication leaves no loadable new
  generation.
- An old valid manifest remains readable while an uncommitted new generation
  is present.
- Path traversal, byte-count, hash, population, and unlisted-sibling mutations
  fail or remain ineligible as appropriate.
- A valid JSON-only or JSON+Parquet generation round-trips exactly.
- The full regression suite passes and probability values remain unchanged.
