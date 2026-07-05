# Automation + 3-Week Data Collection Plan

This folder adds cloud automation so your model predicts and grades itself
daily without your laptop, and you can read results from your phone.

## What these workflows do

| Workflow | When (UTC) | What it does | Changes model? |
|---|---|---|---|
| `record-outcomes.yml` | 14:00 daily | Grades yesterday's predictions → appends pairs to `prediction_outcomes.csv` | No |
| `daily-predictions.yml` | 15:00 daily | Predicts today's slate, archives it | No |
| `weekly-validation.yml` | Mon 15:30 | Writes a readable report to `reports/latest.md` | No |

Grading runs an hour before predicting so each day's collection closes out
cleanly before new predictions are made. All three are read-only with respect
to the model — **corrections stay OFF during collection by design** (see below).

## One-time setup (10 minutes)

1. **Clear old corrections first** (they were fit against the pre-fix model):
   ```
   git rm -f data/learning/bias_corrections.json   # if it exists
   ```
   Confirm in `config/config.json`: `"apply_corrections": false` and
   `"auto_enable_corrections": false`. (Yours are already set this way.)

2. **Copy the `.github/` folder** from this package into the root of your
   `baseball_predictor` repo, preserving the path
   `.github/workflows/*.yml`.

3. **Allow Actions to push commits.** In your repo on github.com:
   Settings → Actions → General → Workflow permissions →
   select **Read and write permissions** → Save.
   (The workflows already request `contents: write`, but this repo-level
   toggle must also be on.)

4. **Commit and push:**
   ```
   git add .github
   git commit -m "Add daily prediction + grading automation"
   git push
   ```

5. **Test it by hand before trusting the schedule.** In the repo's **Actions**
   tab, open "Daily Predictions" → **Run workflow** → optionally enter a date →
   Run. Watch it go green. Then do the same for "Record Outcomes" with a date
   that already has archived predictions. If both commit results back, you're
   done — the schedules will now run on their own.

## Reading results from your phone

- Install the **GitHub mobile app**, open your repo.
- Predictions: `data/predictions/preds_YYYY-MM-DD.csv` — tap to view, or open
  in a phone spreadsheet app.
- Weekly report: `reports/latest.md` — renders as formatted text right in the
  app. Bookmark it.
- The pairs dataset: `data/learning/prediction_outcomes.csv` — this growing
  file IS the goal.

## The 3-week plan

**The discipline that matters most: leave corrections OFF the whole time.**
You are measuring the *raw* model's honest error. Turning corrections on mid-
collection makes the data uninterpretable.

### Week 1 — pure collection
Let the two daily workflows run. Don't analyze, don't retrain. One week isn't
enough signal to judge anything. At the end, glance at `reports/latest.md`
(or trigger Weekly Validation by hand with lookback 7) just to confirm pairs
are landing sanely — not to act on.

### Week 2 — first honest read
Keep collecting. Around day 10–12 you cross ~25 pairs/category (the retrain
gate). The Monday report's **retrain dry-run** section now shows real
per-category mean error. This is the empirical answer to "what does the model
predict well." Expectation from the model's structure: HR and strikeouts
tightest, HRR loosest. If the data disagrees, believe the data.

### Week 3 — validate, then cautiously enable
Keep collecting. With ~3 weeks of pairs, check whether per-category errors are
*stable* week to week (not swinging wildly). If stable, generate a real
correction state locally:
```
python run_retrain.py --lookback 21
```
Then compare one day both ways before trusting it:
```
python run_daily.py --date TODAY
python run_daily.py --date TODAY --apply-corrections
```
If corrections move projections toward reality, set
`"apply_corrections": true` and keep collecting. If they make things weirder,
leave them off — 3 weeks may not be enough pairs yet. Either outcome is fine;
the pairs keep accruing regardless.

## Guardrails

- **Don't retrain daily.** Weekly at most. Retraining on tiny increments
  chases noise — that's what the `min_pairs` gate protects against.
- **The corrections layer is not the prize.** The clean, corrections-off pairs
  file is. Every day of it is a training row for the eventual Phase 2 learned
  model that replaces the hand-set coefficients — where the model gets
  genuinely good.
- **Watch the calibration log line** in the daily run
  (`Calibrated contact baselines from N batted balls...`). Stable numbers day
  to day = healthy. Sudden lurches = something upstream changed.

## Cost / limits

Private repos get 2,000 free Actions minutes/month. Each run here is a few
minutes, so ~3 runs/day ≈ well under the limit. No servers, no keys, nothing
to maintain.

## Troubleshooting

- **"Record Outcomes" commits nothing:** no archived predictions existed for
  that date (the prediction workflow must have run that day first), or games
  weren't final when it ran. Re-run it by hand with the specific date once
  games are final.
- **Push rejected / permission error:** the repo-level "Read and write
  permissions" toggle in step 3 isn't set.
- **A schedule didn't fire:** GitHub sometimes delays scheduled Actions under
  load, and disables schedules on repos with no activity for 60 days. A manual
  run or any push re-activates them.
- **MLB/Statcast fetch failed:** transient upstream outage; the next day's run
  picks up. To backfill a missed grading day, trigger "Record Outcomes" by
  hand with that date.
