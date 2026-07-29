# Verified component selection for the projected-opportunity shadow candidate

Date: 2026-07-27
Status: research only; no deployment, promotion, economic claim, or betting authorization

## Decision rule

`baseball_predictor` remains the sole canonical source tree. External packages
are immutable review inputs, not import roots. A component may enter this
candidate only when its exact behavior is independently reproduced, its
chronology and identity contract matches the target experiment, and its use is
covered by local regression and mutation tests. Package age and reported test
counts are not selection criteria.

## External packet identity and independent findings

| Input | SHA-256 | Result |
|---|---|---|
| `props_component_repaired.zip` | `05c03296cc96d1068f30a0fdcdf423f8688a330eff00798c035e0f848bc475f0` | `CERTIFICATION_HOLD_REMEDIABLE_DEFECTS`; individual price and unavailable-state primitives are usable only after native integration tests |
| `rotowire_component_repaired.zip` | `c89eb2cdeb3b856fa159a15b2d4134ad3da8d77d80641c39e2de1967b53b5771` | `CERTIFICATION_HOLD_REMEDIABLE_DEFECTS`; not eligible for the current lineup path |

The props runtime lock installed successfully from its delivered wheels. Its
exact price conversion, proportional no-vig calculation, and terminal
unavailable/quarantined records passed isolated behavioral checks. The complete
test suite was not reproducible from the delivered ZIP because the shipped
wheel directory omits `pytest`, `hypothesis`, `iniconfig`, `pluggy`, `pygments`,
and `sortedcontainers`. `PLAYER_PROPS_FINAL_QUALIFICATION.md` also retains the
obsolete `160 tests green` statement while the packet claims 347 tests.

The RotoWire chronology guard passed direct ISO, compact, path, May-seal, and
horizon-equality boundary checks. The package is not self-contained: runtime
and test wheels are absent, and the official crosswalk required for its claimed
identity tests is an external environment input. Independent recomputation of
`REPAIRED_FILE_MANIFEST.csv` found seven stale rows:

- `DEFECT_DISPOSITION_ROTOWIRE.csv`
- `PROGRESS_NOTE.md`
- `REPAIRED_BASELINE_TO_CANDIDATE_DIFF.csv`
- `REPAIRED_FINAL_DECISION.md`
- `ROTOWIRE_PACKAGE_SECURITY.md`
- `tools/build_candidate_zip.py`
- `tools/generate_file_manifest.py`

Its official identity coverage is limited to 2026-03-23 through 2026-04-30;
the shipped 2026-07-25 lineup fixture correctly cannot be officially resolved.
Live RotoWire access and automation authorization are also unproven. Therefore
RotoWire cannot supply current T-4 projected lineups.

## Component disposition

| Component | Authority | Disposition | Reason |
|---|---|---|---|
| Locked batter per-PA empirical-Bayes control | `src/evaluation/shared_pa_forward_evidence.py` (`9b8f79ce7b12d8ead102a4c2cbfedb227a18c6e1ebb47e70b19f24910fea59f1`) | `USE_AS_LOCKED` | Chronology-bound 2023 control; no hand-tuned matchup effect |
| Locked PA-volume artifact | `pa_distribution_fit_2023.json` (`7ffd6a8fecb1c4f8aed1966c234a61499050346f87f884f731c440daca793c90`) | `USE_AS_LOCKED` | Fitted on 2023 and hash-bound with slot and pooled distributions |
| Empirical joint projected lineup | `src/evaluation/projected_lineup_empirical_joint.py` (`2c30a02db03b2b58db115df36cf80c136ea2cfef7d9d8c4ba84a98dfff86f195`) | `USE_AS_LOCKED_INPUT_CANDIDATE` | Equal-weight strictly-prior complete lineups, roster receipt required, no baseball coefficients |
| Hits/HR/TB probability derivation | `derive_market_distributions` | `USE_AS_LOCKED` | One coherent PA distribution derives all three markets; markets remain separately scored |
| Kimi exact price parser | external `prices.py` (`a45e7ad721e42a25db69ffb182d94dbaac52e2ba7c297d529cf588bb77d2d52c`) | `BEHAVIOR_SELECTIVELY_INTEGRATED` | Exposed a real float-truncation defect; behavior reimplemented at the canonical source boundary |
| Kimi two-sided pairing guard | external `pairing.py` (`034c30b6e7e8a8df77e538a4cea48f81ff54ba8fdbbb052dede2602e9ea36a32`) | `DEFER_UNTIL_PRICE_LANE` | Strong design, but does not improve probabilities and requires integration with existing quote identities |
| Kimi terminal unavailable records | external `unavailable.py` (`abfbdd42223b44a21b5e5b6fe9e8b204ad5f1836457ed447d55296e97697637b`) | `USE_PATTERN_NOT_PACKAGE` | Correctly avoids fabricated metadata; native experiment output will use its existing terminal-abstention contract |
| Kimi RotoWire chronology guard | external (`926a1fd50dd445f0de1325fefabf76a3cbbdfdb8976b1e6277ecf03fae5cbeed`) | `TEST_IDEAS_ONLY` | Useful adversarial cases; duplicating chronology authority would create conflict |
| Kimi RotoWire parser/crosswalk | external | `DO_NOT_USE_CURRENTLY` | Stale manifest, external partial crosswalk, no current identity coverage, no live authorization |
| Kimi SmartStake historical data path | external | `DO_NOT_USE_CURRENTLY` | No 2023-2025 coverage, June/July quarantined, May sealed, no prospective/executable-price proof |
| Kimi settlement/evaluation package | external | `DEFER` | Synthetic/offline contract only; no verified official settlement feed or live price availability |
| Omega/Kimi model formulas and fitted artifacts | external | `DO_NOT_USE` | No independently proven predictive improvement over the frozen project baselines |

## Source-level repair selected from the comparison

The old `baseball_predictor` price boundary accepted `105.9` as `105` through
`int(value)`. The canonical parser now accepts only integer values or canonical
integer strings, rejects booleans/floats/decimals, and rejects American prices
whose absolute value is below 100. The same function now governs market
economics, resolved live quotes, and the forward shadow ledger. Raw file odds
parsing can no longer silently reintroduce the truncation. Valid integer prices
retain the same probability and payout behavior.

This is an evaluation-integrity repair. It is not a predictive improvement and
does not authorize use of any historical price as executable.

## Prospective experiment boundary

Before the first prediction, bind one candidate version to the exact files
above plus its protocol, tests, data receipts, and release identity. The first
eligible observation is a genuine non-May target whose T-4 horizon occurs after
that publication. Missing lineup, player, identity, chronology, or source
evidence produces an explicit abstention. No earlier slate, missed receipt, or
existing reserved forward record may be retrofitted.

Hits, HR over 0.5, and Total Bases will be evaluated independently. The first
candidate changes only PA opportunity through the projected start/slot mixture;
the batter per-PA control stays fixed. This isolates the measured opportunity
limiter and prevents a market or simultaneous model change from rescuing it.

The predeclared protocol is
`config/shared_pa_projected_opportunity_forward_v1.json` (SHA-256
`43f2d79cd440750dad65c57126f5077d3d0558093b2cbaa8bc65254aadf6b966`).
The candidate implementation is
`src/evaluation/shared_pa_projected_opportunity_candidate.py` (SHA-256
`95c9a8296325689d265dafe929629f63375728c546c0b39875c8333e7e837a8c`).
The protocol binds the candidate, lineup model, lineup contract, shared-PA
probability code and contracts, empirical-Bayes control, and 2023 PA-volume
artifact. The loader refuses any altered bound byte.

A second source-path defect was found while proving executability: the prior
batter snapshot builder required an official target-game projected lineup
before it could calculate the player's unchanged per-PA skill. That would have
made this candidate unusable precisely when the official T-4 lineup was absent.
`src/evaluation/shared_pa_batter_skill.py` (SHA-256
`3cf6e0ea724ce8af2e7b738c246eba414139d8b3b3d4c19012e46e3abdf48776`)
now constructs a lineup-independent, receipt-timed batter skill snapshot only
for players supported by the validated projected-lineup distribution. It
reuses the locked empirical-Bayes control, rejects post-horizon stats, and
independently replays counts, denominators, probabilities, and lineage. It
does not consume an actual target lineup or opposing pitcher.

`src/evaluation/shared_pa_projected_opportunity_runner.py` (SHA-256
`d17c6fef04c7e90ec546c8b794001bb4fcfe62f2b09671d80489a2e816c2cdfb`)
requires complete accounting of the projected player support. Every supported
player is either assigned a hash-bound candidate record or an explicit
no-substitution abstention. A missing player, post-horizon generation time, or
candidate-wide chronology/identity/probability failure cannot be silently
converted into a successful side.

The versioned code/config/data/test manifest is
`reports/shared_pa_projected_opportunity_candidate_v1_hash_manifest.json`
(SHA-256
`2918133ef6562b0a9d8dfe03e961efce21855cada1bd9034330c08e241b4bdb8`).
It explicitly retains the missing exact dependency lock, clean-Ubuntu run,
append-only runtime persistence, and AWS schedule as release blockers rather
than claiming those gates are already complete.

Because the prior shared-PA experiment already reserved 2026-07-23 through
2026-09-16, every new-candidate output in that interval is permanently
nonqualifying operational smoke and cannot be scored by opening the reserved
outcomes. The first fresh candidate window is 2026-09-17 through the official
2026 regular-season end on 2026-09-27. Its short duration is a declared
limitation, not grounds to include postseason games, extend backward, lower a
gate, or skip independent forward replication. The season-end date is sourced
to MLB's official 2026 schedule announcement:
<https://www.mlb.com/press-release/press-release-mlb-announces-2026-regular-season-schedule>.
