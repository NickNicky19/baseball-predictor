# Rolling-source contract repair v1

Status: predeclared integrity repair; research only; no model promotion and no
betting authorization.

## Measured defects

1. `StatcastRoller._fetch_player_season` catches provider exceptions and returns
   an empty DataFrame, which is serialized as ordinary blank rolling features.
2. A corrupt rolling cache is deleted and silently refetched, destroying the
   original evidence and changing the source behind an existing feature build.
3. `FeatureFactory` catches every configured rolling-provider exception and
   continues with no rolling payload, making source failure indistinguishable
   from a deliberately absent optional block.
4. Rolling outputs persist no source kind, strict target cutoff, maximum source
   date, row count, or content hash.

## Success condition

- Provider exceptions and malformed responses fail closed.
- Corrupt caches are preserved and quarantined; they are never deleted or
  replaced implicitly.
- A configured rolling provider failure stops bundle construction.
- Successful observed and successful empty-history responses remain distinct,
  with exact source/cutoff/row-count/content-hash lineage.
- Any nonempty rolling feature reaching a source-bound rich bundle carries that
  lineage through serialization.

## Failure condition

The repair fails if an exception still produces blank features, if a corrupt
cache is removed or refetched, if feature construction continues after a
configured provider failure, or if observed rolling values can be serialized
without their strict-prior source contract.

## Protected invariants

No historical source data, outcomes, prices, May 2026 artifacts, prospective
receipts, model coefficients, thresholds, frozen baselines, rejection records,
or operational collectors are opened or changed.

## Required mutations

- Provider exception must not return an empty feature dictionary.
- Corrupt cache must remain byte-identical after the failure.
- A rolling-provider exception inside `FeatureFactory` must propagate.
- A target-date row must not enter the lineage row count/hash.
- Deleting or changing rolling lineage on a source-bound rich bundle must fail.
