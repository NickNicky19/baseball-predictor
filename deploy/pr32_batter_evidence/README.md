# PR32 batter evidence deployment templates

These files are code-only research templates. They are not installed or
enabled by this change and do not authorize deployment, probability use,
promotion, or betting.

The inventory unit fails closed unless `current` is an immutable reviewed release and
`current-venv/bin/python` is an attested release-scoped Python environment
created without system-site packages. A later deployment review must bind the
release pointer, interpreter hash, environment inventory, exact lock, unit,
and every manifest hash before enabling the timer. It has no EnvironmentFile
or environment expansion. The explicit unset list is defense in depth, not a
substitute for the release-scoped interpreter and systemd sandbox.

The existing prospective history producer is deliberately not included as a
deployable unit here. Its transitive path does not yet satisfy the linked-path,
duplicate-key, import-closure, and release-binding gates. The inventory unit
reads already-existing plan, roster, and history roots read-only and
publishes one exact-byte T-4 inventory. Surfaces not produced by the existing
collector - including the schedule denominator, date-bounded stats transport,
external clean-runtime receipt, projected lineup, side bundle, and candidate
records - are recorded as terminal missing. They are never silently inferred.

A later separately reviewed integration is required before surfaces 3, 5-7,
10-17 can be captured as present. Process 20872 and all existing
collector release trees are outside this template's writable boundary.
