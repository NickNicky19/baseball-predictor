# Durable Forward-Shadow Collector (Research Only)

This deployment exists to collect evidence that cannot be reconstructed after
the fact. It appends only locked research selections to the tamper-evident
shadow ledger. It never places or authorizes a wager.

## Roles

1. **Always-on Linux host — primary.** A system timer runs the state machine
   every minute. It creates a future schedule plan, prepares a provenance-bound
   Hits model snapshot, captures real DraftKings `batter_hits` prices inside the
   locked pre-T-4h window, commits the explicit selected/no-selection funnel,
   captures an exact prestart reference, and links official MLB final outcomes
   plus modeled DraftKings reference dispositions under `/srv/baseball-shadow`.
2. **GitHub Actions — independent verifier and backup.** The hourly workflow
   uses a read-only SSH account to copy the day's evidence tree, rechecks every
   content hash and due target, fails visibly on missing/error/tampered evidence,
   and retains a 90-day GitHub artifact. It never fetches replacement odds.
3. **Local machine — optional third audit copy.** The same read-only SSH account
   can synchronize `/srv/baseball-shadow` whenever the local computer is online.
   The local copy is not required for the primary collector to operate.

GitHub cron is deliberately not the primary collector. Its execution timing is
not guaranteed and one fixed cron cannot represent T-4h for staggered games.

## Hard blockers before the first live request

- Rotate every The Odds API key that ever appeared in plaintext. A tracked
  documentation example contained a key and has been redacted, but deleting it
  from the current tree does not revoke it or remove Git history.
- Select and explicitly authorize the external Linux host and provider plan.
- Store the replacement API key only in `/etc/baseball-shadow/odds-api.env`
  (`0600`, root-owned) or the host's native secret store.
- Create a separate read-only SSH account for GitHub. Do not give the workflow
  the collector account, root access, or the odds credential.
- Run all credential-free checks and one future real shadow target before
  enabling unattended daily operation.

## No-cost local operational smoke

The first real lifecycle smoke may run on the local Windows computer before a
paid external host is selected. It is permanently excluded from economic
evidence and cannot become the durable primary collector. The prepared scope
is `data/learning/shadow/operational_smoke_v7/evidence_scope.json`. Its separate
`runtime_manifest.json` freezes the exact Python executable, interpreter,
platform, OpenSSL, and installed distribution versions before credential entry.

After rotating the previously exposed provider key, start the runner from a
normal PowerShell window:

```powershell
powershell -ExecutionPolicy Bypass -File `
  .\deploy\shadow_collector\windows\Run-LocalOperationalSmoke.ps1 `
  -ServiceRoot .\data\learning\shadow\operational_smoke_v7 `
  -PythonExe "$env:LOCALAPPDATA\Python\pythoncore-3.14-64\python.exe"
```

The key is requested through a hidden secure prompt, passed to Python only in
the child-process environment, and removed when the runner exits. Do not paste
the key into source, chat, a command-line argument, or a report. Keep the
PowerShell window open and the computer awake until the selected game's
prestart and official-settlement phases complete. A sleep, shutdown, network
failure, or late restart remains visible and is never backfilled.

The runner rejects a forward-economic scope, a scope marked betting-enabled,
and any interpreter/package/runtime drift from the frozen runtime manifest. The
runtime check completes before the hidden credential prompt or provider access.
Its purpose is only to prove the real deployed lifecycle before a clean release
and durable external primary are allowed to begin forward evidence.

## Primary host layout

Install the repository at `/opt/baseball_predictor`, its virtual environment at
`/opt/baseball_predictor/.venv`, and durable evidence at `/srv/baseball-shadow`.
Create an unprivileged `baseball-shadow` service account with write access only
to `/srv/baseball-shadow` and the repository's `data/` directory.

Copy these units to `/etc/systemd/system/`:

- `deploy/shadow_collector/baseball-shadow-collector.service`
- `deploy/shadow_collector/baseball-shadow-collector.timer`

Copy `deploy/shadow_collector/odds-api.env.example` to
`/etc/baseball-shadow/odds-api.env`, insert only the rotated key, then set owner
`root:root` and mode `0600`.

Before enabling the timer, verify the checkout, install `requirements.txt`, and
run:

```text
python scripts/check_shadow_live_provider_offline.py
python scripts/check_shadow_research_selection_policy_offline.py
python scripts/check_shadow_primary_collector_offline.py
python scripts/check_shadow_collector_tick_offline.py
python scripts/check_shadow_close_collector_offline.py
python scripts/check_shadow_official_hits_offline.py
python scripts/check_shadow_official_settlement_worker.py
python scripts/check_shadow_lifecycle_tree_offline.py
python scripts/check_execution_product_contracts_offline.py
python scripts/check_tracked_secrets.py
```

Only after those checks pass should an administrator enable
`baseball-shadow-collector.timer`. A missed target remains missed; `Persistent`
does not turn a late restart into valid T-4h evidence.

## GitHub verifier configuration

Configure these repository variables:

- `SHADOW_PRIMARY_HOST` — hostname only.
- `SHADOW_PRIMARY_USER` — dedicated read-only audit user.
- `SHADOW_PRIMARY_ROOT` — `/srv/baseball-shadow`.

Configure these repository secrets:

- `SHADOW_PRIMARY_SSH_KEY` — private key for the read-only audit user.
- `SHADOW_PRIMARY_KNOWN_HOSTS` — pinned host-key line produced out of band.

The audit user requires read-only access to:

- `/srv/baseball-shadow/plans/<YYYY-MM-DD>/`
- `/srv/baseball-shadow/live/<YYYY-MM-DD>/`
- `/srv/baseball-shadow/lifecycle/<YYYY-MM-DD>/`
- `/srv/baseball-shadow/close/<YYYY-MM-DD>/`
- `/srv/baseball-shadow/official/<YYYY-MM-DD>/`
- `/srv/baseball-shadow/ledger/forward_ledger.jsonl`

The workflow `.github/workflows/shadow-evidence-verifier.yml` runs hourly and
can also be dispatched for a specific official date. Missing SSH configuration
is a visible failure, never a silent skip.

## What is and is not captured

The local deployment lane captures real two-sided DraftKings Hits quotes and the
locked K/BB Hits candidate at T-4h. It records source quotas, timestamps, exact
MLB identities, unresolved-row funnel reasons, model provenance, and immutable
terminal receipts. Every quote receives a selected/no-selection status; selected
research entries are committed before T-4h. A separate provider call near first
pitch records an exact prestart reference. It is deliberately not called a
guaranteed last price or actual fill. After MLB Final, one retained official feed
drives starter/substitute, PA, Hits, void, and unscored dispositions.

DraftKings remains a reference market, not an execution product for the declared
Texas context. Onyx, Novig, Chalkboard, and PrizePicks remain four separate,
blocked execution contracts. No feed quote proves account access, submit-ability,
liquidity, fill, payout, or product settlement. Until the collector is deployed,
credentials are rotated, each product contract is verified, and prospective
evidence clears every locked gate, all artifacts remain `RESEARCH_ONLY` and
`betting_authorized=false`.

The current credential-free readiness certificate is
`reports/forward_shadow_readiness_v18.json`, SHA-256
`f0d39e1d4a2eda714db8cd677580b9790126ab21204e45d91e4f9d92f1b2d276`.
It records 201/201 passing local guards. This proves code readiness only; it
does not prove deployment, live executability, profitability, or authorization.

The first prospective economic look is locked by
`config/forward_shadow_evidence_boundary.json`. It requires 56 complete future
official-date blocks under one frozen evidence era. That count is derived from
the certified open March-April and June Hits date universes; May remains sealed.
The operational smoke and any incomplete date are retained in the audit funnel
but cannot count. Before the boundary, the system may collect and hash-verify
official outcomes but must not produce or inspect capture, ROI, calibration,
subgroup, or other prospective economic summaries. The boundary contract is
covered by seven mutation checks in
`scripts/check_forward_evidence_boundary_offline.py`.

The deployment root must also contain an immutable `evidence_scope.json`, built
with `scripts/prepare_forward_evidence_scope.py` against the exact readiness
report. The operational smoke uses its own root and is permanently marked
`economic_evidence_eligible=false`. After that complete lifecycle is certified
by `scripts/certify_forward_operational_smoke.py`, a different clean release root
may be created for the real forward era. The latter refuses to start without the
smoke certificate or when any source/configuration/policy/runtime/settlement/
schema/evaluation hash differs from the readiness certificate. The GitHub
verifier copies and validates this scope alongside the lifecycle evidence. Each
scope also binds an exact secret-free runtime manifest; changing Python,
OpenSSL, the OS/runtime, or any installed package creates an incompatible era.
