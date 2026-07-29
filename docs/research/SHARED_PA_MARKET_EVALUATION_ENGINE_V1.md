# Shared-PA market evaluation engine v1

## State

This component is **research-only, synthetic-test-only, and externally unbound**. It cannot open project data, generate predictions, inspect outcomes, qualify a candidate, promote a model, calculate betting ROI, or authorize betting. A caller must supply in-memory rows plus a separate synthetic structural source-authority directory. Authoritative evaluation is refused until an independent future authority and a real receipt protocol bind the exact candidate, baselines, data manifest, registry checkpoint, evidence window, settlement source, market source, and materiality policy.

## Invariants

- Hits, HR over 0.5, and Total Bases are evaluated independently. No market can rescue another.
- Different Hits or Total Bases lines use canonical decimal strings, remain collision-free distinct products, and are never pooled. Floats, exponent notation, negative values, and trailing-zero aliases fail closed.
- HR is exactly the over-0.5 product.
- May 2026 and any evidence-window alias other than the explicit synthetic fixture authority fail closed.
- A candidate probability is either present with valid evidence or absent with one canonical abstention reason. No clipping or fabricated fallback is permitted.
- League rate, time-safe empirical-Bayes player rate, and the frozen simulator are mandatory and separately scored on same-row common support.
- Synthetic market-pair mechanics require referenced receipt bytes under an explicit synthetic structural source-authority root and manifest. The engine rejects unsafe paths and symlink/junction/reparse traversal, rehashes the referenced regular file, parses an exact typed receipt, independently matches source, sportsbook, product, date, event, player, market, line, timestamp, side order, and prices, then recomputes normalized inverse-decimal no-vig probability.
- Synthetic terminal-unavailability mechanics likewise require referenced typed receipt bytes and independently match source, sportsbook, product, date, event, player, market, line, terminal reason, and pre-horizon observation timestamp. A caller-provided self-hash is not proof, and quote-only fields must remain absent.
- Market prices are reference probabilities only. Historical executability and ROI are not claimed.
- Total Bases requires a contiguous, normalized count distribution for the candidate and every model baseline. The exact over-line tail is derived from that distribution and must equal the supplied tail probability. The full distribution is scored with multiclass Brier, ranked probability score, and log loss in addition to binary market-tail metrics.
- Pushes, voids, and unmatched rows remain visible in coverage and settlement accounting but cannot enter any proper score, calibration, discrimination, or paired delta.
- Comparisons use paired rows only and publish candidate-only, baseline-only, neither, ungradeable, and common-support counts.
- Calibration slope/intercept and AUC report explicit unidentifiable/single-class states; they are never fabricated.
- The exact calibration-bin edges are locked in code and contract; an alternate sorted binning cannot silently change the report.
- Brier and log-loss deltas use deterministic date-cluster and game-cluster bootstrap intervals. Total Bases full-distribution deltas receive the same two cluster treatments.
- The inherited +0.10 capture lower-bound is preserved. This engine does not calculate capture, ROI, qualification, promotion, or authorization.

## Schema/runtime parity boundary

The row schema now fails closed on the same structural states accepted by the runtime: the exact inclusive probability interval `[1e-12, 1 - 1e-12]`; candidate availability versus abstention; binary versus Total Bases distributions; exact model-baseline scales; available versus terminal-unavailable market receipts; the HR 0.5 line; and settlement-status outcome nullability. The report schema conditionally and completely types every market product, candidate metric, comparator, common-support coverage table, uncertainty interval, calibration surface, discrimination surface, HR upper-tail surface, and Total Bases full-distribution surface. `market` is required, so its market conditional always closes the otherwise shared `comparisons` property; an unrecognized or extra comparison key cannot become authority-bearing output.

The release does not declare `jsonschema`. Its tests therefore use a deliberately small standard-library validator for only the JSON Schema keywords shipped by these two schemas and run positive and negative mutation cases against runtime-produced synthetic reports. This avoids silently borrowing a globally installed validation package. It is structural test machinery, not a new production dependency or an alternate evaluation authority.

The following invariants remain runtime-only because Draft 2020-12 structural keywords cannot truthfully prove cross-field arithmetic, cross-record semantics, or time ordering:

- count-distribution key contiguity, total probability mass, and derived-tail equality;
- normalized inverse-decimal no-vig recomputation and receipt-byte semantic binding;
- observation, decision-horizon, scheduled-start, and outcome chronology;
- settlement equality versus the canonical product line (`PUSH` exactly on equality, `GRADED` otherwise);
- product-key/line equality and nested market-name equality;
- coverage and common-support arithmetic;
- exact calibration-bin sequence and exact HR threshold sequence;
- bootstrap interval ordering and deterministic replay identity.

Those checks remain mandatory in the evaluator and retain direct regression/mutation coverage. The schemas supplement those runtime gates; they do not replace or weaken them.

## Authority boundary

The checked-in contract status is `UNBOUND_EXTERNAL_AUTHORITIES_REQUIRED`. The repository does **not** contain a qualified real source authority, real receipt protocol, real candidate/baseline/settlement authority, or eligible evidence window. Passing the synthetic tests proves only that the structural parser, rehash, semantic checks, scoring, and refusal boundaries behave on synthetic fixtures. It does not prove source availability, receipt truth, source authorization, candidate quality, live chronology, settlement correctness, predictive superiority, economic value, or production readiness. Real evaluation remains impossible in this version.

## Verification

Run the isolated synthetic suite:

```text
python -B -m pytest tests/test_shared_pa_market_evaluator.py -q -p no:cacheprovider
```

The offline manifest checker validates the exact nine-file component set, contained regular-file paths, schema JSON, and recorded component bytes without opening project evidence:

```text
python -B scripts/check_shared_pa_market_evaluator_offline.py
```
