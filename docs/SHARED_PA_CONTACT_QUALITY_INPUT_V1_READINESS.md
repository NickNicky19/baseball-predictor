# Shared PA contact-quality input v1 readiness

Decision: **CERTIFIED AS A 2023 RESEARCH INPUT ONLY.** This is not a fitted
model, a predictive improvement, a promotion, a production change, or betting
authorization.

The accepted panel was built from the already certified direct-batter PA target
identity and all 643 hash-bound 2023 raw Statcast batter receipts. Every source
receipt was rehashed before use. Each feature row uses only regular-season raw
contact observations with `game_date` strictly before the target game date.

## What is now available

The block supplies 44 count-bearing and missingness-bearing features for all
43,740 target player-games:

- joint exit velocity and launch-angle populations;
- hard-hit, launch-angle sweet-spot, and their intersection;
- Statcast speed-angle barrel and classified hard-hit populations;
- launch-angle and batted-ball-type partitions;
- hard-hit counts by batted-ball type;
- EV50 support and mean;
- exact denominators, missing counts, and maximum prior source date.

Rates are absent when their denominator is zero; no league-average or player
deletion fallback is used. The validator independently enforces nested
populations, partitions, count/rate equality, missingness equality, integral
counts, canonical dates, and the original integrity boundary that a barrel
count cannot exceed its classified hard-hit count.

## Deliberate exclusions

The block does not consume historical `estimated_*` outcome probabilities,
because the raw archive does not bind those values to a point-in-time algorithm
version. It also excludes spray/pull fields because the available field
coordinates do not yet have a certified park-coordinate and switch-hitter
transform. Maximum exit velocity, target-game lineup, pitcher context,
same-day rows, and future rows are excluded.

## Failures found and repaired before acceptance

1. The first full build exposed a validator defect: it compared hard-hit counts
   from the EV-plus-batted-ball-type population with hard-hit counts from the
   stricter joint-EV-and-launch-angle population. Missing launch angles make
   those legitimate populations different. The repair removed the false
   cross-population claim and added a regression fixture that preserves valid
   EV/type evidence with a missing launch angle.
2. The next build rejected the feature dictionary because its insertion order
   differed from the locked feature order. The constructor now emits the exact
   predeclared order, and the test suite asserts it.

Neither failed build wrote an accepted artifact. The final build and a second
independent rebuild produced byte-identical panel, manifest, and certificate
files.

## Evidence

- Build commit: `1cc5949b2dddc7ca5693dd78dcaddeba06c39ad4`
- Panel: `a7882dccda3d36b37957261903a8a35644b9d9118c5f80ecb7085ef1cb5bbb89`
- Manifest: `a5ec5637ae181838f09429b5846cc8b575561c6202d83ee165cfb215e008abd1`
- Certificate: `5324e890023acd5ea84aa9c1c9b2cdee5008c3c51f4f244911dd43d4d601fe4f`
- Focused regression/mutation suite: 16 passed
- Actual artifact validation: passed
- Deterministic rebuild: byte-identical
- Identity duplicates: 0
- Chronology violations: 0
- Outcome columns: 0
- 2024 rows opened: 0
- 2025 rows opened: 0
- May 2026 artifacts opened: 0

## Honest model implication

No prediction is better merely because this panel exists. The prior
true-empirical-Bayes-offset challenger remains rejected for Hits, HR over 0.5,
and Total Bases. This input block addresses its measured missing contact-shape
limiter without changing the rejected candidate, its coefficients, its gates,
or any frozen baseline.

The next experiment, if authorized, should be exactly one predeclared,
regularized 2023-only challenger that adds this hash-bound contact block to the
fitted empirical-Bayes baseline. Feature consumption and separate Hits, HR,
and Total Bases gates must be frozen before scoring. The parent lineage's 2024
selection and 2025 HR confirmation remain unavailable; only genuinely future
untouched evidence can later support confirmation.
