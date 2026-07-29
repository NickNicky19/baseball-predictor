# Shared-PA integrated research gates v1

This integration binds four independently reviewed components without changing their bytes:

- projected-opportunity receipt inventory;
- full-game receipt replay envelope;
- direct batter PA source/runtime authority;
- experiment registry and synthetic-only evaluator.

The integration is an integrity gate, not a model candidate, prediction release, receipt producer, evaluation result, promotion, or betting authorization.

The exact component commits and trees are recorded in `config/shared_pa_integrated_research_gates_v1.json`. The binding checker proves that every component is an ancestor, its tree identity is exact, its changed files do not overlap another component, every integrated component blob still matches its component authority, and the complete integration delta contains no unlisted file.

## Truthful remaining blockers

- Genuine T-4 producers are incomplete. The inventory observer records missingness but cannot create evidence.
- A candidate family and evaluation protocol are not locked.
- The evaluator remains synthetic-only and externally unbound.
- Existing candidate and evaluator documents conflict on market naming, bootstrap design, calibration bins and tail treatment, materiality policy, and the legal post-2024 selection path. None is authoritative through this gate.
- Baseline bytes, prediction rows, settlement rows, and external evaluation authority are not bound by this gate.
- Existing multiplicity identifiers do not yet prove that related repeated tries share one family-level error budget.
- The false readiness flags are enforced by this integration checker and CI only. Candidate and evaluator runtime APIs do not consume this config, so the flags are documentary outside this non-runtime gate.
- `config/shared_pa_market_evaluator_binding_interface_v1.json` is stale, unbound documentary configuration: it says a full evaluator does not exist while the component's bound implementation state is synthetic-only. It is not candidate authority and must not be used to infer readiness.
- Historical development, future predictions, promotion, economics, and betting remain prohibited by this integration.
- May 2026 remains skipped entirely; spent 2024 selection and 2025 HR confirmation evidence cannot be reused.

The highest-value next action after this gate passes is to qualify the first missing genuine source receipt at its producer boundary. Candidate locking must wait until the required input universe and its terminal-missing behavior are executable and exact.
