# Edge Engine + Shadow Logging + GUI Sorting

Read-only value tooling. **Nothing here changes a single projection** — no model
coefficients touched, no reset of your data-collection clock. Everything operates
on projections you already produce.

## 1. De-vigged, distribution-aware edge engine (`edge_calculator.py`)

Three fixes to the value math, all correctness improvements:

**De-vigging.** The old code compared the model against the raw one-sided implied
probability, which includes the book's margin (vig). A -110/-110 line implies
52.4% on *each* side — 104.8% total; the extra 4.8% is the house edge. Comparing
against that distorts every edge. The engine now reads both sides, removes the
overround, and compares against the true no-vig probability. It also computes edge
on **both** the over and under and keeps whichever side the model favors — so it
catches under value you'd otherwise miss.

**True distribution, not a bell curve.** For HR especially, the count is discrete
and skewed — a normal approximation misprices the "1+ HR" (0.5) line badly. The
engine uses the actual Monte Carlo tail probability (`p_ge_threshold`) whenever the
line matches a simulated threshold, and labels which method it used in the notes.

**Fractional-Kelly staking.** Each play gets a stake as a bankroll fraction, using
quarter-Kelly by default (`kelly_fraction=0.25`) with a hard 5% cap
(`kelly_cap`). This is the value-correct sizing: it accounts for edge *and* odds
*and* win probability together. Full Kelly on single-game props is reckless given
the variance; quarter-Kelly is the standard defensive choice.

A concrete example from testing — Aaron Judge 1+ HR, model 12% for the over, book
+550/-800: the engine removes 4.3% vig, correctly finds the model actually favors
the *under* (88% vs 85.3% fair), but returns **zero stake** because -800 is such a
heavy price that an 88% probability isn't enough value. That's the engine refusing
a bad-value bet the raw edge would have tempted you into.

## 2. Ranking: value first, both columns shown

`find_value_plays(..., sort_by="kelly")` (default) ranks by fractional-Kelly stake —
the value-correct ordering. `sort_by="edge"` ranks by raw model-vs-market gap. Every
result carries both numbers regardless of sort, so the GUI can re-sort freely. Why
Kelly as default: a huge edge on a coin-flip and a modest edge on a near-lock are
not equally good bets, and edge % alone can't tell them apart — Kelly can.

## 3. GUI click-to-sort (`gui.py`)

Every column header is now clickable to sort; click again to toggle direction.
Numeric columns (projected, confidence, rank) sort numerically and default to
biggest-first; text sorts alphabetically. Purely presentational — it re-sorts the
rows already on screen and never re-runs the model.

**One deliberate guardrail:** sorting by *confidence* works, but the status bar
warns that confidence is an unvalidated model self-estimate. Until the calibration
data confirms that high-confidence plays actually hit more often, that column is a
guess — sorting by it is fine for exploration, but don't size bets off it yet.

## 4. Shadow logging (`shadow_logger.py`)

During the collection window the engine runs in **read-only shadow mode**: each day
it appends the value plays it *would* have recommended to
`data/learning/shadow_plays.csv` — date, player, edge side, model vs fair prob,
edge %, Kelly stake, odds, confidence. Nothing is bet. When outcomes are known, these
would-be plays can be graded, giving you an independent check on the betting logic
separate from projection calibration. You accumulate a real track record before
risking a dollar.

## The discipline this encodes

This tooling is built to be used a specific way, and the design enforces it:

1. **It's read-only.** It can't tempt you to change the model mid-collection,
   because it doesn't touch the model.
2. **It shadows, doesn't bet.** For the next ~3 weeks it just logs would-be plays.
3. **It flags unvalidated confidence** rather than pretending that number is
   trustworthy yet.

The point: **a +EV call is only trustworthy if the model is calibrated, and you
won't know that until the pairs data lands.** This engine is instrumentation for the
collection window — it produces *more* validation data (shadow picks to grade), and
it's ready to go live the moment the calibration report confirms which categories to
trust. Don't bet its outputs until then.

## Config (optional — sensible defaults already apply)

Add under `edge` in `config.json` to tune:
```json
"edge": {
  "kelly_fraction": 0.25,
  "kelly_cap": 0.05
}
```

## Files changed (5)

- `src/prediction/edge_calculator.py` — de-vig, both-sides edge, Kelly
- `src/prediction/shadow_logger.py` — NEW, shadow-play recording
- `src/models/dataclasses.py` — EdgeResult gains de-vig/Kelly fields (all defaulted)
- `gui.py` — click-to-sort headers
- `tests/test_edge_engine.py` — NEW, 8 regression tests (de-vig, distribution, edge
  side, Kelly cap/refusal, ranking)

Suite: 63 pass / 0 fail.

## What's still yours to do

1. **Test a free odds API key against the real endpoint** to confirm MLB props come
   through — no marketing page can settle this. That decides the feed.
2. **Let the collection run.** The edge engine shadow-logs in the background.
3. **After ~3 weeks:** the calibration report. Then, and only then, wire the feed to
   live edges, turn on validated categories, and start betting the ones the data
   says are trustworthy.
