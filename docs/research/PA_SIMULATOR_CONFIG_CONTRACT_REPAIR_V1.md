# PA simulator configuration contract repair v1 (predeclared)

## Incident

`PropEngine._build_pa_config` logs and ignores unknown `pa_simulator` keys.
The `PASimulatorConfig` dataclass accepts booleans, strings, NaN/infinity,
reversed probability bounds, invalid mixture shares, decorative fitted-model
coefficients, and nonpositive denominator scales.  A misspelled or malformed
configuration can therefore produce no intended change, an invalid probability
path, or provenance that appears consumed when it is not.

## Locked repair

1. Unknown output-affecting configuration keys fail closed.  Documentation keys
   beginning with `_` remain inert by explicit contract.
2. Every numeric field must be a real finite number; boolean and string
   substitutions are rejected.
3. Scale, ramp, shrinkage, mixture, probability-cap, and min/max relationships
   are validated at the `PASimulatorConfig` construction boundary so direct,
   calibrated, loaded, and daily construction share one rule.
4. Fitted K/BB mode requires the complete coefficient set.  Coefficients are
   forbidden when the fitted mode is off, preventing decorative provenance.
5. Legacy fields remain constructible for frozen round-trip compatibility, but
   a daily config may not claim to override an inert legacy field.
6. Regression and mutation tests cover every old failure class and audit every
   repository config that declares a PA simulator block. A configured missing
   coefficient artifact remains a hard source failure; this isolated branch
   will not copy or fabricate that artifact from another worktree.

No coefficient is changed, fitted, clipped, or selected.  Valid configurations
must be numerically identical.  This repair reads no outcome, price, feature,
prospective, 2026, or May artifact and is not performance or promotion evidence.
