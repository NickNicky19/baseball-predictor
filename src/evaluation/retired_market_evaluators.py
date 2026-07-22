"""Fail closed for superseded market-evaluation entry points.

These historical scripts are intentionally retained as code archaeology, not
as runnable research tools. They grade with SmartStake's numeric ``result``
field, which was measured to disagree with canonical MLB actuals on 16.5% of
the exact formerly evaluated rows. Some also collapse player/date identities
that are unsafe for doubleheaders. Allowing them to run would create a new CSV
with an invalid target and make it look current.
"""

from __future__ import annotations


def retired_market_evaluator_exit(script_name: str) -> int:
    """Print one unambiguous replacement path and return a non-zero status."""

    print(
        f"RETIRED UNSAFE MARKET EVALUATOR: {script_name}\n"
        "This path grades with the vendor numeric result and/or legacy identity "
        "joins. It must not produce market capture, CLV, ROI, or promotion "
        "evidence. The vendor result was measured to disagree with canonical MLB "
        "actuals on 16.5% of the former evaluated rows.\n"
        "Use scripts/run_market_ab.py only with a hash-validated strict market "
        "artifact, game-keyed model arms, and canonical official outcomes. "
        "That canonical run remains RESEARCH_ONLY until its separate policy and "
        "forward-shadow gates are satisfied.",
        flush=True,
    )
    return 2
