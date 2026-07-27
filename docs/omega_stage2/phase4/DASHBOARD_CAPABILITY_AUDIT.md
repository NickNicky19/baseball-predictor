# Dashboard Capability Audit

Schema version: `omega-dashboard-capability-audit-v1`.

The transitive graph contains exactly six allowlisted local modules. Static enforcement resolves aliases and from-imports, rejects unlisted local edges and external imports, rejects network/AWS/database/process/dynamic-import capabilities, restricts filesystem opens to statically read-only modes, and permits only narrowly enumerated `re.compile`, Windows access-probe, and `os.open` calls.

Runtime denial tests instrument DNS and external connection helpers, subprocess and shell functions, process control, database connection, dynamic import, and filesystem writes. The Windows ASGI test framework itself requires an internal loopback socketpair; therefore the runtime test denies public resolution/connection APIs while the AST gate independently prohibits dashboard socket imports.

This layered analysis does not prove arbitrary Python behavior. Its trust claim is limited to the exact allowlisted graph, mutation set, installed wheel, and executed tests.
