# Statcast zone-denominator repair v1

Status: predeclared integrity repair after an outcome-blind input audit; no
model promotion or betting authorization.

## Measured incident

The locked audit read only the `zone` field from permissible 2023/2024 raw
Statcast caches. Across 1,572,200 nonempty rows, 53,661 had no classified zone;
none of the nonmissing values was nonnumeric or outside Statcast zones 1–14.
There were 107 zero-byte player cache files, recorded separately and not
invented as empty successful responses. No outcome field and no 2026/May file
was read.

`SavantClient._compute_zone_rate` divided in-zone pitches by every source row,
including rows for which zone was unavailable. The certified direct batter
history builder already uses the truthful classified-zone denominator, so the
generic live profile path disagreed with the research feature contract.

## Locked repair

- Zone rate is in-zone classified pitches divided by all classified pitches
  (zones 1–14), never by rows with missing zone.
- Chase rate and zone rate share one parser that fails on a nonnumeric
  nonmissing zone or a numeric value outside 1–14.
- A genuinely absent zone column/rate remains explicit missing data for the
  existing lineage-aware fallback boundary.
- No coefficient, probability cap, player filter, or league value changes.

## Required proof

- A three-row fixture with one missing zone must use two classified pitches as
  its denominator, not all three source rows.
- Missing-zone mutation must not alter PA or source-row counts.
- Nonnumeric and out-of-domain zone mutations must fail closed.
- The full regression suite and outcome-blind audit must remain reproducible.

