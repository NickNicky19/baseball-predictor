"""Fail-closed contracts for the isolated Omega integration candidate.

This package contains no model fitting, prediction formula, collector, or
activation path. It is safety-boundary infrastructure only.
"""

from .errors import ContractError

__all__ = ["ContractError"]
