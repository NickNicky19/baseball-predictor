# Snapshot Freshness Contract

Schema version: `omega-snapshot-freshness-contract-v1`.

`maximum_snapshot_age_seconds` is a required positive operational input with no default. The shared chronology kernel parses only canonical UTC `YYYY-MM-DDTHH:MM:SSZ`. At load time, capture in the future is rejected, age greater than the configured maximum is rejected, and equality at the maximum is accepted. The loader also requires `observed_at < decision_horizon <= captured_at < event_start` and rejects every row after its event has started.

These numbers are operational safety limits. They do not affect prediction mathematics and are not supplied by this repository. Missing configuration produces no displayed data.
