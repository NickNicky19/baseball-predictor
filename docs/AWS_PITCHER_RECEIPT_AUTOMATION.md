# AWS T-4 Pitcher-Receipt Automation

This service is a future-only research input collector. It records official MLB
probable-starter identity at the predeclared T-4 horizon, or an immutable
source-error/missed terminal state. It does not load model probabilities,
lineups, prices, outcomes, settlement, or betting code.

The AWS service is deliberately isolated from the full forward-shadow collector:

- release root: `/opt/baseball-predictor-pitcher-receipts`
- evidence root: `/srv/baseball-shadow/pitcher-receipts`
- unprivileged account: `baseball-shadow`
- plan publication: 00:15 America/New_York for that official date
- receipt checks: every 15 seconds, matching the locked runtime
- health reports: every 15 minutes, with nonzero service failure on alerts
- secondary verification: hourly read-only GitHub Actions copy and replay

No historical T-4 receipt may be reconstructed. Deployment after a target is
due fails rather than fetching a late substitute.

## One-time exact-release installation

The server must already have the read-only repository deploy key at
`/srv/baseball-shadow/.ssh/id_github_repo`, owned by `baseball-shadow` with mode
`0600` or `0400`. The installer pins GitHub's published Ed25519 host key from
the official GitHub SSH fingerprint documentation; it does not use
`ssh-keyscan` or trust-on-first-use.

After the authorized commits are merged, use the exact resulting 40-character
commit—not a branch name:

```text
sudo /path/to/repo/deploy/forward_pitcher_receipts/install_exact_release.sh <authorized-commit>
```

The installer clones into a commit-addressed release directory, verifies a
clean exact checkout, runs all receipt lifecycle and mutation checks, switches
only the isolated release symlink, installs only the three pitcher-receipt
timers, and starts an immediate plan/tick/health cycle. It never enables the
model/odds collector.

## Independent GitHub verification

`.github/workflows/pitcher-receipt-verifier.yml` reuses the dedicated
`shadow-verifier` read-only SSH identity. It proves that identity cannot write
the receipt root, copies only the current official date, replays the retained
raw official schedule into the exact plan, verifies the plan receipt and ledger
hash chain, and retains a 90-day GitHub artifact. Missing, late, source-error,
missed, or altered evidence fails visibly; GitHub never fetches a replacement.

The workflow expects the existing repository variables
`SHADOW_PRIMARY_HOST`, `SHADOW_PRIMARY_USER`, and `SHADOW_PRIMARY_ROOT`, plus
the read-only secrets `SHADOW_PRIMARY_SSH_KEY` and
`SHADOW_PRIMARY_KNOWN_HOSTS`.

The pinned GitHub host key should be rechecked against
https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints
before a future rotation or new deployment artifact is accepted.
