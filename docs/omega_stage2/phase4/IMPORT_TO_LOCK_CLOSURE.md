# Import-to-Lock Closure

Schema version: `omega-import-to-lock-closure-v1`.

`scripts/verify_omega_scaffold_package.py` imports the installed dashboard and contract modules, rejects checkout-resolved paths, inventories direct third-party roots from installed source ASTs, maps import roots to installed distributions, and requires every distribution in the exact runtime lock. Wheel metadata must contain only exact direct requirements matching both lock and installed versions.

The checked direct runtime distributions are FastAPI 0.115.12, Jinja2 3.1.6, Pydantic 2.11.5, and Uvicorn 0.34.2. All transitive distributions are hash-bound in `scaffold-runtime.lock`. Test, build, lint, runtime, and intentionally unlocked research profiles remain separate.
