"""Read-only display boundary for authorized prediction snapshots.

This package intentionally has no dependency on ``src`` or any prediction,
simulation, fitting, evaluation, or collector module. Import the application
factory explicitly from :mod:`dashboard.app` so schema-only checks do not load
the web runtime.
"""
