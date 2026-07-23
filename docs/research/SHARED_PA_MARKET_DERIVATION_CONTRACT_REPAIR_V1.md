# Shared PA market-derivation contract repair v1

Status: verified integrity repair; no market promotion.

The common PA model derives full-game Hits, HR-over-0.5, and Total Bases tails
by compounding one mutually exclusive PA distribution over a PA-count
distribution. That exact derivation previously checked only the probability
matrix width. Nonfinite, negative, above-one, or non-normalized PA rows could be
consumed; lineup values were truncated with `int`; PA states were also
truncated; distribution mass was neither validated nor required to sum to one;
and suspicious final values within a tolerance were clipped into `[0,1]`.

The repair validates every PA probability and row sum, exact lineup-slot and PA
support identity, complete slots 1–9, finite nonnegative weights, and unit mass
before computing a tail. It rejects any derived value outside probability
support and performs no output clipping or league-average substitution. Valid
probabilities and distributions are numerically unchanged.

The focused shared-PA harness passes 20 tests and the full suite passes 332.
No historical outcome, price, May 2026, source, feature, prediction, AWS,
collector, or frozen-production artifact was opened or changed. Because no
valid probability changed and no fitted candidate was created, this repair is
not new performance evidence for Hits, HR, or Total Bases.
