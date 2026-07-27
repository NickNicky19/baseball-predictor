# Phase 4 Blockers

Schema version: `omega-phase4-blockers-v1`

## Remediable local blockers

1. Clean Ubuntu CPython 3.12 verification cannot run on this host because WSL is not installed.
2. The inspected-wheel Linux container build cannot run because Docker is not installed and no equivalent local container runtime is available.

These are mandatory verification-sequence gates. They are neither failures of the implemented Windows behavior nor passes. Under the governing rule they prevent a commit, push, PR update, and `MERGE_REVIEW_READY_AWS_BLOCKED` decision.

## Intentionally open limitations

QA-005 through QA-011 remain open except QA-004. In particular there is no qualified publisher, deployed filesystem proof, deployed runtime role proof, official source-bound history resolver, qualified research lock, predictive qualification, economic proof, or betting authorization.

No AWS work is needed to resolve the immediate local gate. The next authorized phase must provide an independently controlled clean Ubuntu 24.04 environment with the selected CPython 3.12 patch and a Linux container runtime, rerun all exact locks/tests/build checks, and preserve raw results before any commit or push.
