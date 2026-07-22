#!/usr/bin/env python3
"""Fail closed on a retired, invalid hits-dispersion diagnostic.

Archived ``predicted_value`` is generated with projected PA.  Pairing it with
post-game ``actual_pa`` to form a binomial variance conditions on information
the prediction never had and can create impossible ``predicted / actual_pa``
rates.  The old statistic therefore produced numerical output that could not
identify simulator overdispersion.

Use ``scripts/decompose_hits_bias.py`` with a time-safe reconstruction instead.
This guard deliberately refuses to emit a misleading diagnostic result.
"""

from __future__ import annotations

import sys


def main() -> int:
    print(
        "FATAL: this diagnostic is retired. Archived predicted values use "
        "projected PA while actual_pa is post-game information. Use "
        "scripts/decompose_hits_bias.py with a time-safe reconstruction; no "
        "dispersion conclusion is valid from this input.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
