# RUNBOOK — install & test A1, A2, A3 (start to finish)

Yes: every command below runs from your repo root. Open one terminal and do
this first, then stay there the whole time:

    cd C:\Projects\baseball_predictor

Do the stages in order. Each one de-risks the next, so don't skip ahead. When
you're done, send me: the merge/push result, the three offline harness lines,
the pytest summary, and the console output of the A2 and A3 live runs + the
audit. That's everything I need to verify your end.

Note on `python`: if `python` doesn't work on your machine, try `py` or
`python3`. Use whichever prints a 3.12+ version from `python --version`.


===============================================================================
STAGE 0 — extract all three bundles
===============================================================================

Extract these over C:\Projects\baseball_predictor, letting them overwrite:

    a1_files.zip
    a2_files.zip
    a3_files.zip

They don't overlap except .gitignore (a3's is the newest and correct one —
let it win). After extracting, this should list six new run_/script files:

    dir run_slate.py run_reconstruct_date.py run_build_training_set.py run_audit_player.py
    dir scripts\check_reconstruct_offline.py scripts\check_training_builder_offline.py

If any are missing, the extract didn't land — fix that before continuing.


===============================================================================
STAGE 1 — offline harnesses (no network, ~30 seconds, do this FIRST)
===============================================================================

These prove the point-in-time logic with zero API cost. If they don't pass,
stop and send me the output — no point testing anything live yet.

    python scripts\check_reconstruct_offline.py
    python scripts\check_training_builder_offline.py

EXPECT exactly:
    23 passed, 0 failed          (A2)
    30 passed, 0 failed          (A3)


===============================================================================
STAGE 2 — your existing test suite (no network)
===============================================================================

This is the one layer I could NOT run for you (my sandbox has no pytest), so
it's the most important thing to check on your end. It covers the A1 outcome-
recorder changes.

    pip install pytest
    python -m pytest tests\ -q

EXPECT: all passing. If anything fails, paste me the last ~15 lines — a
failure here would most likely be the new model_version column, which I'll
fix fast.


===============================================================================
STAGE 3 — finish the A1 merge and push
===============================================================================

Your repo is mid-merge (from before the A1 session). These steps complete it.

3a. Keep your local (correct) outcome-recorder script over origin's broken one:

    git checkout HEAD -- run_record_outcomes.py

3b. Repair the merge marker if it got dropped:

    git rev-parse -q --verify MERGE_HEAD || git rev-parse origin/main > .git\MERGE_HEAD

3c. Stage EXACTLY these — do NOT use `git add -A` (your tree has thousands of
    lines of harmless CRLF line-ending noise that would pollute the commit):

    git add run_slate.py src\utils\model_version.py src\learning\outcome_recorder.py .github\workflows\daily-predictions.yml .github\workflows\record-outcomes.yml .gitignore run_record_outcomes.py data\learning\prediction_outcomes.csv data\learning\predictions\

3d. Also stage the A2 + A3 code and the audit tool (all additive):

    git add run_reconstruct_date.py run_build_training_set.py run_audit_player.py src\data\http_cache.py src\learning\training_set_builder.py scripts\check_reconstruct_offline.py scripts\check_training_builder_offline.py

3e. Sanity-check what's staged BEFORE committing. It should be a short, sane
    list — the files above, not thousands of others:

    git status --short

    If you see a flood of unrelated modified files, stop and tell me — don't
    commit. (It'd mean the CRLF noise got swept in.)

3f. Commit and push:

    git commit -m "A1-A3: all-category automation, point-in-time reconstruction + training-set builder"
    git push

3g. Confirm the history looks right:

    git log --oneline -5


===============================================================================
STAGE 4 — A1 live: the automation actually runs
===============================================================================

On GitHub (browser):

4a. Actions tab -> "Daily Predictions" -> Run workflow (leave date blank) ->
    wait for green.

4b. When it's done, open the new commit it pushed and confirm this file exists:

        data/learning/predictions/predictions_<today>.json

    Open it and confirm all FOUR categories appear (hits, home_runs, hrr,
    strikeouts) and that it has hitter_projections AND pitcher_projections.

4c. Actions tab -> "Record Outcomes" -> Run workflow. Set the date input to
    YESTERDAY (YYYY-MM-DD) so there's a slate to grade. Wait for green.

4d. Confirm rows appended to data/learning/prediction_outcomes.csv, and that
    the new rows have the model_version column populated (a 12-char hash;
    should be dab23f4fbac8 for the current config). Older rows staying blank
    is expected.

    (If step 4c has nothing to grade because no archive existed yet for
    yesterday, that's fine — it'll grade naturally tomorrow once today's
    archive is a day old. Not a failure.)


===============================================================================
STAGE 5 — A2 live: reconstruct one historical date
===============================================================================

    python run_reconstruct_date.py --date 2025-06-15 --verbose

Watch the FEATURE RESOLUTION table it prints. This answers the two questions
we couldn't verify offline:

  - opposing_pitcher: resolved vs missing  -> does historical probablePitcher
    persist on old schedules?
  - weather: resolved vs default           -> does the feed keep weather for
    completed games?

Either answer is fine — we just need to KNOW. It also writes
reports/reconstruction_2025-06-15.json. Send me the console output.

Expect hitter MAE to look mediocre — that's deliberate (Statcast is off until
A4). Don't read anything into the accuracy yet; we're testing plumbing.


===============================================================================
STAGE 6 — A3 live: build a few real dates
===============================================================================

Small smoke test first (~1-2 min, a few hundred API calls):

    python run_build_training_set.py --seasons 2024 --limit-dates 3

Then inspect:

    dir data\training\2024
    type data\training\manifest_2024.json

The manifest's "resolution" block is the aggregated evidence (probable vs
actual_starter counts, weather_resolved, as-of-empty call-ups, etc.).

Prove resume works — run the SAME command again:

    python run_build_training_set.py --seasons 2024 --limit-dates 3

It should build nothing new and report cache hits (near-zero new API calls).
Send me the manifest resolution block and the second-run console line.


===============================================================================
STAGE 7 — the real trust check (audit one player against Baseball-Reference)
===============================================================================

This is the check only real data can give. Pick a player who was in a lineup
on a date you built in Stage 6, then:

    python run_audit_player.py --player <MLB_ID> --date 2024-04-05

(or use --name "First Last" to look the id up). For a pitcher, add --pitching.

It prints the as-of season snapshot beside the game-log rows behind it, and
shows the slate-date game in an EXCLUDED list. Two things to verify:

  1. The season snapshot (PA/H/HR/AVG) matches that player's line ENTERING
     that date on Baseball-Reference's game log. If they match, your point-
     in-time reconstruction is trustworthy.
  2. The slate-date game is in the EXCLUDED list and its stats are NOT in the
     snapshot. That's the leakage boundary, confirmed on real data.

Do this for 2-3 players before trusting a full-season build. Send me one
audit printout + the Baseball-Ref line you compared it to, and I'll confirm.


===============================================================================
WHAT TO SEND ME
===============================================================================

1. Stage 1: the two "N passed, 0 failed" lines.
2. Stage 2: the pytest summary line (and any failure tail).
3. Stage 3f/3g: that the push succeeded + `git log --oneline -5`.
4. Stage 4: that the archive JSON has 4 categories, and that new pairs rows
   have model_version populated.
5. Stage 5: the A2 console resolution table.
6. Stage 6: the manifest "resolution" block + the second-run cache line.
7. Stage 7: one audit printout + the Baseball-Ref line you checked it against.

Once the real 2023-2025 build is trustworthy (Stage 7 passes), the full run is
just:  python run_build_training_set.py --seasons 2023 2024 2025
...and A4 (the Statcast signal join) is the next build.
