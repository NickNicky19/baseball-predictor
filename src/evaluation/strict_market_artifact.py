"""Naming contract for a hashed strict-market artifact and its manifest."""
from __future__ import annotations

from pathlib import Path


# A strict artifact's selection rule defines which historical quotes are
# eligible to be scored.  It is part of the experiment, not descriptive prose.
# Unknown rules must fail closed: a new rule requires an explicit consumer
# implementation and mutation coverage before it can reach the evaluator.
LEGACY_VENDOR_SETTLEMENT_RULE = "legacy_vendor_settlement_proxy_v1"
BASE_PREGAME_HITTER_RULE = "dk_base_pregame_starter_and_pa_v1"
TWO_SIDED_ENTRY_FRESH_RULE = "both_entry_sides_fresh_v1"
HR_OVER_05_COMPLETE_PATH_RULE = "dk_hr_over_05_complete_price_path_identity_v1"
ONE_SIDED_FRESHNESS_DEFERRED_RULE = "one_sided_quote_age_deferred_v1"
HR_RECONSTRUCTION_ARTIFACT_KIND = "strict_unique_reconstruction_only"
KNOWN_SELECTION_RULES = frozenset({
    LEGACY_VENDOR_SETTLEMENT_RULE,
    BASE_PREGAME_HITTER_RULE,
    HR_OVER_05_COMPLETE_PATH_RULE,
})


def artifact_path_for_manifest(manifest_path: str | Path) -> Path:
    """Return the only artifact paired with a strict-market manifest.

    A hard-coded ``strict_unique_hits.csv`` makes a second, rule-distinct
    research artifact accidentally consume the first one's universe.  The
    manifest basename is therefore part of the contract:
    ``name_manifest.json`` pairs only with ``name.csv``.
    """
    manifest = Path(manifest_path)
    suffix = "_manifest.json"
    if not manifest.name.endswith(suffix):
        raise ValueError(
            f"strict manifest must end in {suffix!r}; got {manifest.name!r}"
        )
    stem = manifest.name[:-len(suffix)]
    if not stem:
        raise ValueError("strict manifest has an empty artifact stem")
    return manifest.with_name(stem + ".csv")


def selection_rule_for_manifest(payload: dict) -> str:
    """Validate the declared historical-selection rule.

    A hash proves which bytes were consumed.  It cannot prove that the runner
    understands what those bytes mean, so selection-rule validation is a
    separate fail-closed contract.
    """
    rule = payload.get("selection_rule")
    if rule not in KNOWN_SELECTION_RULES:
        raise ValueError(
            "strict market manifest has an unknown or missing selection_rule "
            f"{rule!r}; expected one of {sorted(KNOWN_SELECTION_RULES)}. "
            "Do not score a universe whose eligibility semantics the runner "
            "does not explicitly understand."
        )
    return str(rule)


def price_freshness_rule_for_manifest(payload: dict) -> str:
    """Require the exact freshness semantics for the declared market product.

    A de-vigged probability is an observation from *both* quoted sides.  A
    manifest that recorded freshness from only the over side cannot be safely
    consumed: stale under odds can materially change the price while the old
    one-sided guard reports the row as fresh.  Missing is not grandfathered;
    it means the historical artifact has an unverified price boundary.
    """
    selection_rule = selection_rule_for_manifest(payload)
    if selection_rule == HR_OVER_05_COMPLETE_PATH_RULE:
        rule = payload.get("price_freshness_rule")
        if rule != ONE_SIDED_FRESHNESS_DEFERRED_RULE:
            raise ValueError(
                "HR-over reconstruction manifest lacks the required uncensored "
                f"one-sided freshness rule {ONE_SIDED_FRESHNESS_DEFERRED_RULE!r}; "
                f"got {rule!r}. Do not inherit the Hits freshness boundary."
            )
        if payload.get("max_quote_age") is not None:
            raise ValueError(
                "HR-over reconstruction source must not carry a selected "
                "max_quote_age before that policy is justified"
            )
        return str(rule)

    rule = payload.get("price_freshness_rule")
    if rule != TWO_SIDED_ENTRY_FRESH_RULE:
        raise ValueError(
            "strict market manifest lacks the required two-sided price freshness "
            f"rule {TWO_SIDED_ENTRY_FRESH_RULE!r}; got {rule!r}. Rebuild a new "
            "artifact rather than scoring a one-sided-fresh universe."
        )
    return str(rule)


def market_category_for_manifest(payload: dict) -> str:
    """Return the only model category allowed by the selection rule."""
    rule = selection_rule_for_manifest(payload)
    if rule == HR_OVER_05_COMPLETE_PATH_RULE:
        return "home_runs"
    return "hits"


def validate_manifest_purpose(payload: dict) -> str:
    """Keep reconstruction-only HR evidence outside every scoring contract."""
    rule = selection_rule_for_manifest(payload)
    if rule != HR_OVER_05_COMPLETE_PATH_RULE:
        return "scoring"
    if payload.get("artifact_kind") != HR_RECONSTRUCTION_ARTIFACT_KIND:
        raise ValueError(
            "HR-over identity universe must be explicitly marked "
            f"{HR_RECONSTRUCTION_ARTIFACT_KIND!r}; it is not a scoring universe"
        )
    if payload.get("betting_authorized") is not False:
        raise ValueError("HR-over reconstruction-only manifest attempted authorization")
    if payload.get("settlement_presence_filter_applied") is not False:
        raise ValueError(
            "HR-over reconstruction-only source cannot claim settlement eligibility"
        )
    return HR_RECONSTRUCTION_ARTIFACT_KIND
