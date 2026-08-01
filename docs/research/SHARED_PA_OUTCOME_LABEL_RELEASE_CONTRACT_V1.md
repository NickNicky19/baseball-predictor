# Shared-PA 2023 outcome-label release contract v1

This contract governs a future offline label builder. It does not authorize
network capture, feature construction, fitting, scoring, predictions, or
betting. The original certified PA-opportunity release remains immutable.

## Input authority

The only accepted input is a separately certified raw capture containing the
exact 2,430 official MLB 2023 regular-season boxscore response byte strings,
their receipts, the locked request plan, capture context, attempt journals,
runtime attestation, source-access authorization, and immutable manifest.

Every input must bind to the existing certified schedule-index SHA-256
`0f3d68d002f5fa87b0d6cb9daa4d51606f37547bbca0a28bce066e6e7c09c96f`.
No postgame batting order may be represented as a pregame projection.

## Row identity and required values

One row represents one official player-game-team-side batting line. Its hard
identity is:

`gamePk + team_side + team_id + MLB player_id`

Rows must retain:

- official date, game type, starter flag, batting-order slot when present, and
  substitution/appearance status;
- plate appearances, at-bats, hits, doubles, triples, home runs, walks,
  intentional walks, strikeouts, hit by pitch, sacrifice flies, sacrifice
  bunts, total bases, runs, and RBI;
- catcher interference and every other available official PA residual field;
- singles derived as `H - 2B - 3B - HR`;
- non-hit non-strikeout at-bats derived as `AB - H - K`;
- non-intentional walks derived as `BB - IBB`;
- an explicit `other_official_pa` residual derived only after the above
  official terms are accounted for.

All quantities must be nonnegative integers. The label builder must fail closed
instead of clipping, imputing, or substituting a league average.

## Exact PA partition

Every plate appearance must map into exactly one mutually exclusive category:

1. strikeout;
2. non-intentional walk;
3. intentional walk;
4. hit by pitch;
5. home run;
6. single;
7. double;
8. triple;
9. non-hit non-strikeout at-bat;
10. sacrifice fly;
11. sacrifice bunt;
12. catcher interference;
13. explicit other official PA residual.

The release must retain both the source statistics and these derived categories
so the partition can be recomputed independently. It must not describe the
aggregate non-hit/non-strikeout-at-bat category as exclusively an out when the
official boxscore cannot distinguish reached-on-error or another event.

## Reconciliation gates

For every player row:

- `H = 1B + 2B + 3B + HR`;
- `TB = 1B + 2*2B + 3*3B + 4*HR`;
- `AB = H + K + non_hit_non_strikeout_at_bats`;
- `BB = IBB + non_intentional_walks`;
- the 13 outcome categories sum exactly to official PA.

For every game-team side, summed player totals must reconcile to every
available official team batting total for PA, AB, H, 2B, 3B, HR, BB, IBB, K,
HBP, SF, SH, TB, runs, RBI, catcher interference, and residuals. Team runs must
also reconcile to the official final game score. Contradiction or absence of a
required field is terminal missingness, never silent repair.

All original starters must be retained, including zero-PA starters. Every
substitute with an official batting appearance or batting line must retain a
separate MLB-player-ID row. Doubleheaders remain distinct by gamePk.

## Immutable release identity

The outcome-label release must use a new directory and schema identity:

`shared-pa-official-2023-outcome-label-release-v1`

It must contain canonical rows, a raw-input manifest, parser and schema hashes,
row and coverage summaries, reconciliation findings, an immutable release
manifest, and deterministic reproduction evidence. Building this release is a
separate offline authorization after the raw capture is certified.

