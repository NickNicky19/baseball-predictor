# Canonical Chronology Authority

Schema version: `omega-canonical-chronology-authority-v1`.

`src/omega_contracts/chronology.py` is the only chronology authority for the candidate scaffold. It owns canonical date parsing, aware-UTC normalization, external `Z` timestamp parsing, event eligibility, load freshness, and lexical/resolved May-seal path rejection. Dashboard contracts and the snapshot loader import these decisions; they contain no local date parser or May rule.

External display inputs accept only `YYYY-MM-DDTHH:MM:SS[.ffffff]Z`. Typed internal inputs may use an aware datetime and are normalized by the same module. Dates accept only a typed `date` or canonical `YYYY-MM-DD`. Naive, offset-bearing external, ambiguous, locale, slash, compact, or invalid values fail closed.

The retained canonical-base file `src/evaluation/shadow_capture_plan.py` is not modified. Its exact Git blob OID and byte SHA-256 are part of `config/omega_production_boundary_v1.json` and are independently checked by `src/omega_contracts/production.py`.
