"""Official postgame eligibility facts for historical market grading.

This module deliberately lives outside the model-training builder.  The model
is trained and reconstructed for starters; the market grader must also know
about substitutes in order to apply a documented settlement rule.  Folding
substitutes into the model-training table would silently change the modelling
population to solve an evaluation problem.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd


ELIGIBILITY_KEY = ["mlb_game_pk", "player_id"]
ELIGIBILITY_COLUMNS = [
    "mlb_game_pk", "player_id", "official_game_date", "is_starter",
    "official_lineup_slot", "starter_replaced_in_slot", "official_pa",
    "official_hits",
]
OFFICIAL_HITS_COLUMNS = [
    "mlb_game_pk", "player_id", "game_date", "category", "actual_value",
]


def canonical_game_dates(
    frame: pd.DataFrame, game_pk_column: str, date_column: str, source: str
) -> pd.DataFrame:
    """Derive one canonical official date per MLB game without picking a row."""
    required = [game_pk_column, date_column]
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError(f"{source}: missing {missing}")
    pairs = frame[required].copy().rename(columns={
        game_pk_column: "mlb_game_pk", date_column: "official_game_date",
    })
    pairs["mlb_game_pk"] = pd.to_numeric(pairs.mlb_game_pk, errors="coerce")
    pairs["official_game_date"] = pd.to_datetime(
        pairs.official_game_date, errors="coerce"
    ).dt.strftime("%Y-%m-%d")
    if pairs.isna().any().any():
        raise ValueError(f"{source}: a mapped game lacks MLB game_pk or official_date")
    # Multiple independent vendor fragments may map to one hard MLB game.  Group
    # the *verified pair* rather than selecting an arbitrary fragment.
    pairs = (pairs.groupby(["mlb_game_pk", "official_game_date"], as_index=False)
                  .size().drop(columns="size"))
    dates_per_game = pairs.groupby("mlb_game_pk")["official_game_date"].nunique()
    if (dates_per_game != 1).any():
        bad = dates_per_game[dates_per_game != 1].index.tolist()[:10]
        raise ValueError(f"{source}: one MLB game_pk maps to multiple official dates {bad}")
    pairs["mlb_game_pk"] = pairs.mlb_game_pk.astype(int)
    return pairs


def game_eligibility_rows(
    game_pk: int,
    official_game_date: str,
    hitters: Mapping[int, Any],
    starter_ids: Sequence[int] | set[int],
    batting_roles: Mapping[int, Mapping[str, Any]],
) -> pd.DataFrame:
    """Format one completed game's official hitter eligibility facts.

    ``starter_ids`` is MLB's final official batting order.  This is valid for
    outcome/settlement grading after a game, but it is never a prediction input
    for a historical T-minus-hours reconstruction.
    """
    starters = {int(player_id) for player_id in starter_ids}
    hitter_ids = {int(player_id) for player_id in hitters}
    missing_starter = starters - hitter_ids
    if missing_starter:
        raise ValueError(
            f"game_pk {game_pk}: official batting order includes player(s) with "
            f"no official hitter record {sorted(missing_starter)}. Refusing to "
            "invent PA or settlement role."
        )
    rows: list[dict[str, Any]] = []
    for player_id in sorted(hitter_ids):
        stat = hitters[player_id]
        pa = getattr(stat, "pa", None)
        hits = getattr(stat, "hits", None)
        if pa is None or hits is None:
            raise ValueError(
                f"game_pk {game_pk}, player {player_id}: official hitter record "
                "lacks PA or hits"
            )
        rows.append(dict(
            mlb_game_pk=int(game_pk),
            player_id=int(player_id),
            official_game_date=str(official_game_date),
            is_starter=player_id in starters,
            official_lineup_slot=(
                int(batting_roles[player_id]["lineup_slot"])
                if player_id in batting_roles
                else pd.NA
            ),
            starter_replaced_in_slot=(
                bool(batting_roles[player_id]["starter_replaced_in_slot"])
                if player_id in batting_roles
                else False
            ),
            official_pa=float(pa),
            official_hits=float(hits),
        ))
    out = pd.DataFrame(rows, columns=ELIGIBILITY_COLUMNS)
    validate_eligibility(out)
    return out


def validate_eligibility(frame: pd.DataFrame) -> None:
    missing = [column for column in ELIGIBILITY_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"official eligibility bridge missing {missing}")
    if frame[ELIGIBILITY_KEY].isna().any().any() or frame.duplicated(ELIGIBILITY_KEY).any():
        raise ValueError("official eligibility bridge has null or duplicate game/player identity")
    if frame["is_starter"].isna().any():
        raise ValueError("official eligibility bridge has unknown starter role")
    starter_slots = pd.to_numeric(
        frame.loc[frame["is_starter"].astype(bool), "official_lineup_slot"],
        errors="coerce",
    )
    if starter_slots.isna().any() or not starter_slots.between(1, 9).all():
        raise ValueError("official eligibility bridge has invalid starter lineup slot")
    if frame["starter_replaced_in_slot"].isna().any():
        raise ValueError("official eligibility bridge has unknown replacement evidence")
    bad_replacement = (
        frame["starter_replaced_in_slot"].astype(bool)
        & ~frame["is_starter"].astype(bool)
    )
    if bad_replacement.any():
        raise ValueError("a substitute cannot be marked as a replaced starter")
    if pd.to_numeric(frame["official_pa"], errors="coerce").isna().any():
        raise ValueError("official eligibility bridge has invalid PA")
    if pd.to_numeric(frame["official_hits"], errors="coerce").isna().any():
        raise ValueError("official eligibility bridge has invalid hits")
    dates_per_game = frame.groupby("mlb_game_pk")["official_game_date"].nunique()
    if (dates_per_game != 1).any():
        bad = dates_per_game[dates_per_game != 1].index.tolist()[:10]
        raise ValueError(f"official eligibility bridge has noncanonical game dates: {bad}")


def official_hits_actuals(frame: pd.DataFrame) -> pd.DataFrame:
    """Convert the bridge into the exact official-target schema the A/B reads."""
    validate_eligibility(frame)
    out = frame[["mlb_game_pk", "player_id", "official_game_date", "official_hits"]].copy()
    out = out.rename(columns={
        "official_game_date": "game_date",
        "official_hits": "actual_value",
    })
    out["category"] = "hits"
    out = out[OFFICIAL_HITS_COLUMNS]
    if out.duplicated(["mlb_game_pk", "player_id", "category"]).any():
        raise ValueError("official hits actuals are not unique on outcome identity")
    return out


def base_pregame_player_prop_eligible(is_starter: bool, official_pa: float) -> bool:
    """DK's published base pregame hitter rule, deliberately narrow.

    A pregame player-prop selection needs an official start and at least one PA.
    This does *not* encode conditional Early Exit promotions: those require
    product/event eligibility information that a final box score cannot prove.
    """
    return bool(is_starter) and float(official_pa) >= 1.0


def apply_base_hitter_rule(frame: pd.DataFrame) -> pd.Series:
    """Return the documented base-rule eligibility mask for a role bridge."""
    required = ["is_starter", "official_pa"]
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError(f"base hitter rule missing {missing}")
    if frame["is_starter"].isna().any() or frame["official_pa"].isna().any():
        raise ValueError("base hitter rule has unresolved official role or PA")
    pa = pd.to_numeric(frame["official_pa"], errors="coerce")
    if pa.isna().any():
        raise ValueError("base hitter rule has invalid official PA")
    return frame["is_starter"].astype(bool) & pa.ge(1.0)


def partition_official_role_resolution(
    frame: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Separate usable official role facts from rows that cannot be graded.

    A hard market mapping can still name a player who has no official hitter
    record in the mapped game (for example, an inactive player quoted by the
    vendor). That absence is neither evidence of a sportsbook void nor a zero
    outcome. Keep it as an explicit exclusion; only the resolved partition may
    reach the base starter/PA rule or an official-target join.
    """
    required = ["is_starter", "official_pa"]
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError(f"official role partition missing {missing}")
    unresolved = frame[frame["is_starter"].isna() | frame["official_pa"].isna()].copy()
    resolved = frame.drop(index=unresolved.index).copy()
    if len(resolved) and (resolved["is_starter"].isna().any()
                          or resolved["official_pa"].isna().any()):
        raise AssertionError("official role partition leaked an unresolved row")
    return resolved, unresolved


def assert_official_date_agreement(
    frame: pd.DataFrame,
    left: str = "official_game_date",
    right: str = "official_game_date_bridge",
) -> None:
    """Fail if two independently sourced canonical dates disagree.

    This is called only after role resolution.  A player absent from the box
    score has no bridge date and is reported as unresolved elsewhere; turning
    that absence into a date mismatch would conflate two diagnostics.
    """
    missing = [column for column in (left, right) if column not in frame.columns]
    if missing:
        raise ValueError(f"official date agreement missing {missing}")
    if frame[[left, right]].isna().any().any():
        raise ValueError("official date agreement received an unresolved date")
    unequal = frame[left].astype(str).ne(frame[right].astype(str))
    if unequal.any():
        sample = frame.loc[unequal, [left, right]].head(10).to_string(index=False)
        raise ValueError(f"official canonical date disagreement:\n{sample}")


def base_settlement_cell(
    vendor_settled: bool, is_starter: bool | None, official_pa: float | None
) -> str:
    """Classify vendor settlement presence against the published base rule.

    ``UNRESOLVED_ROLE`` deliberately is not folded into agreement.  Calling an
    unknown official role a void would make the audit look complete by assuming
    the answer it is supposed to measure.
    """
    if is_starter is None or official_pa is None or pd.isna(official_pa):
        return "UNRESOLVED_ROLE"
    dk_void = not base_pregame_player_prop_eligible(is_starter, official_pa)
    if not vendor_settled and dk_void:
        return "AGREE_excluded"
    if vendor_settled and not dk_void:
        return "AGREE_scored"
    if vendor_settled and dk_void:
        return "FALSE_INCLUSION"
    return "FALSE_EXCLUSION"
