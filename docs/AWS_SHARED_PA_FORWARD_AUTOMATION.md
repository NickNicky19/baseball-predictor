# AWS Shared Batter PA Forward Evidence

This is a separate future-only research collector for the locked batter-only
empirical-Bayes PA control shared by Hits, HR over 0.5, and Total Bases. It is
not a production promotion, market policy, ROI claim, or betting authorization.

The service reads the already-published current-date pitcher receipt plan but
does not alter, restart, retime, or backfill the pitcher collector. It writes
only under `/srv/baseball-shadow/shared-pa-forward` and uses the separate
release root `/opt/baseball-predictor-shared-pa-forward`.

Safety boundaries:

- only the current US Eastern official date is considered;
- May 2026 returns before opening a plan or writing an artifact;
- old plan files are never opened or backfilled;
- requests use server-side field allowlists that exclude scores, status,
  outcomes, prices, settlement, and opposing-pitcher probability inputs;
- unexpected response fields fail before raw retention;
- projected lineup order is retained but cannot affect PA volume;
- raw safe inputs, identities, counts, denominators, features, PA volume,
  probabilities, code, runtime, plan, and terminal records are hash-bound;
- a late tick writes only an explicit permanent missed state and never fetches;
- every complete game side requires exactly nine unique players;
- GitHub independently copies and verifies the AWS tree using a read-only key.

## Exact-release installation

After an authorized commit is merged, install that exact 40-character commit:

```text
sudo /path/to/repo/deploy/shared_pa_forward/install_exact_release.sh <authorized-commit>
```

The installer creates only the two `baseball-shared-pa-forward-*` timers. It
does not issue any command against `baseball-pitcher-receipt-*` units.

## View AWS activity

```text
systemctl list-timers --all 'baseball-shared-pa-forward-*'
sudo journalctl --since today \
  -u baseball-shared-pa-forward-tick.service \
  -u baseball-shared-pa-forward-health.service --no-pager
```

The GitHub workflow `AWS Shared PA Forward Verifier` provides the durable
read-only secondary audit copy. Red means evidence is missing, late, altered,
unsafe, or unavailable; it never causes a replacement fetch.
