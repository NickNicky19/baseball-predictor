"""
B1 — GBM training dataset: load A3(+A4) rows, derive per-category targets,
split by DATE (never randomly), and expose clean feature matrices.

Design contracts (kept deliberately in lock-step with the rest of the repo):

- PROVENANCE GATE (discipline #5). The loader asserts builder_schema=='a3.2'
  on both hitter and pitcher frames, and roller_schema=='a4.1' on the HITTER
  frame only (A4 is a hitter batted-ball signal; run_build_statcast_features
  never touches pitchers). A half-built or un-enriched set must fail loudly
  here, not train silently on missing signal.

- TARGETS mirror learning.outcome_recorder.compute_actual_value EXACTLY, so a
  GBM's target at train time equals the actual_value it will be graded against
  at inference time:
      hits        <- out_hits                       (hitter)
      home_runs   <- out_hr                          (hitter)
      hrr         <- out_hits + out_runs + out_rbi   (hitter)
      fantasy     <- FantasyScoring over out_* cols  (hitter; weights FROM CONFIG)
      strikeouts  <- out_k                           (pitcher)
  Fantasy weights are read from config (never hardcoded) precisely so training
  and grading cannot drift.

- SPLIT is temporal. Rows are ordered by game_date and cut at a date boundary:
  everything strictly before the boundary trains, everything on/after is held
  out. A random split would leak point-in-time structure and flatter the GBM
  against the simulator baseline — defeating the whole "simulator is the
  baseline to beat" premise. We never random-split.

- A4 BLANKS. A4 emits "" for windows below min_bip (by design). Those must load
  as NaN (CatBoost/LightGBM read NaN natively). We coerce every feature column
  numeric; a stray "" left as object dtype would silently kill the feature.

This module is OFFLINE and READ-ONLY: it reads training CSVs and returns
matrices/targets. It never touches the live model, archives, or pairs, and it
imports nothing that would pull in a GBM library (so it can be unit-tested with
no catboost/lightgbm installed).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

import numpy as np
import pandas as pd

# a3.1 derives lineup identity from final batting-order occupants and can
# misclassify both substitutes and replaced original starters.  A GBM trained
# on that population is not a valid challenger for the production simulator.
BUILDER_SCHEMA = "a3.2"
ROLLER_SCHEMA = "a4.1"

# Categories that have a simulator baseline (via A2 run_reconstruct_date) and
# therefore can clear the promotion gate. Fantasy is intentionally NOT here:
# A2's HITTER_CATEGORIES = ("hits","hrr","home_runs") produces no fantasy
# baseline, so a fantasy GBM has nothing to beat and must not enter the gate.
GATED_HITTER_CATEGORIES: tuple[str, ...] = ("hits", "hrr", "home_runs")
GATED_PITCHER_CATEGORIES: tuple[str, ...] = ("strikeouts",)

# Trainable but ungated (no simulator baseline yet). Off by default in the gate.
UNGATED_HITTER_CATEGORIES: tuple[str, ...] = ("fantasy",)

# Columns that are identifiers / outcomes / provenance — never model features.
# Everything else in a hitter/pitcher frame is a candidate feature.
_HITTER_NON_FEATURES = {
    "builder_schema", "roller_schema", "season", "game_date", "game_pk",
    "player_id", "player_name",
    # outcomes (targets / leakage if used as features)
    "out_pa", "out_ab", "out_hits", "out_doubles", "out_triples", "out_hr",
    "out_rbi", "out_runs", "out_bb", "out_k",
}
_PITCHER_NON_FEATURES = {
    "builder_schema", "roller_schema", "season", "game_date", "game_pk",
    "player_id", "player_name",
    "out_ip", "out_k", "out_bb", "out_hr",
}

# Categorical feature columns CatBoost can target-encode natively. Anything not
# listed is treated as numeric. (LightGBM path label-encodes these; see trainer.)
HITTER_CATEGORICAL = [
    "team", "opponent", "venue", "bats",
    # Provider/player IDs are labels, never ordinal quantities.  Treating an
    # opposing-pitcher ID as a number lets a tree learn arbitrary ID cut points.
    "opp_sp_id", "opp_sp_throws", "opp_sp_source",
    "umpire_id",
]
PITCHER_CATEGORICAL = [
    "team", "opponent", "venue", "throws", "umpire_id",
]


@dataclass(frozen=True)
class FantasyWeights:
    """Mirror of simulation.monte_carlo.FantasyScoring fields used by
    compute_actual_value. Read from config so train == grade."""

    single: float = 3.0
    double: float = 5.0
    triple: float = 8.0
    home_run: float = 10.0
    rbi: float = 2.0
    run: float = 2.0
    walk: float = 2.0

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "FantasyWeights":
        # Accept either a dedicated block or a nested fantasy_scoring block,
        # matching however FantasyScoring.from_config reads it in the repo.
        block = (
            config.get("fantasy_scoring")
            or config.get("fantasy")
            or {}
        )
        return cls(
            single=float(block.get("single", 3.0)),
            double=float(block.get("double", 5.0)),
            triple=float(block.get("triple", 8.0)),
            home_run=float(block.get("home_run", 10.0)),
            rbi=float(block.get("rbi", 2.0)),
            run=float(block.get("run", 2.0)),
            walk=float(block.get("walk", 2.0)),
        )


@dataclass
class CategoryData:
    """One category's train/holdout matrices, ready for a GBM."""

    category: str
    feature_names: list[str]
    categorical_features: list[str]
    X_train: pd.DataFrame
    y_train: np.ndarray
    X_holdout: pd.DataFrame
    y_holdout: np.ndarray
    # Identity carried alongside holdout rows so predictions can be emitted as
    # PropProjection objects keyed by the originating MLB game.
    holdout_ids: pd.DataFrame  # includes game_pk, player_id, player_name, game_date
    split_date: str

    def summary(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "n_features": len(self.feature_names),
            "n_train": int(len(self.y_train)),
            "n_holdout": int(len(self.y_holdout)),
            "split_date": self.split_date,
            "train_target_mean": float(np.mean(self.y_train)) if len(self.y_train) else 0.0,
            "holdout_target_mean": float(np.mean(self.y_holdout)) if len(self.y_holdout) else 0.0,
        }


# ---------------------------------------------------------------------------
# Schema gate
# ---------------------------------------------------------------------------


def assert_schema(df: pd.DataFrame, *, kind: str) -> None:
    """Fail loudly on an un-stamped / half-built / un-enriched set.

    kind: "hitter" (requires builder + roller stamp) or "pitcher" (builder only).
    """
    if "builder_schema" not in df.columns:
        raise ValueError(
            f"{kind} frame missing 'builder_schema' column — not an A3 set."
        )
    bad = set(df["builder_schema"].dropna().unique()) - {BUILDER_SCHEMA}
    if bad:
        raise ValueError(
            f"{kind} frame has builder_schema {sorted(bad)}; expected "
            f"'{BUILDER_SCHEMA}'. Refusing to train on a mismatched set."
        )
    if kind == "hitter":
        if "roller_schema" not in df.columns:
            raise ValueError(
                "hitter frame missing 'roller_schema' — A4 enrichment has not "
                "been run. Run run_build_statcast_features.py before B1."
            )
        bad_r = set(df["roller_schema"].dropna().unique()) - {ROLLER_SCHEMA}
        if bad_r:
            raise ValueError(
                f"hitter frame has roller_schema {sorted(bad_r)}; expected "
                f"'{ROLLER_SCHEMA}'."
            )
        # An enriched frame must actually carry roller columns; guard against a
        # frame that has the stamp but not the features (e.g. partial join).
        if not any(c.startswith("roll15_") or c.startswith("roll30_") for c in df.columns):
            raise ValueError(
                "hitter frame stamped roller_schema but has no roll15_/roll30_ "
                "feature columns — enrichment looks incomplete."
            )

        # A completed-game actual starter is post-game knowledge. The shared
        # challenger may use a pregame probable pitcher or an explicit missing
        # state, but it must never silently train on realized starter identity.
        if "opp_sp_source" in df.columns:
            forbidden = df["opp_sp_source"].astype(str).eq("actual_starter")
            if forbidden.any():
                raise ValueError(
                    "hitter frame contains opp_sp_source='actual_starter' "
                    f"on {int(forbidden.sum())} rows â€” post-game pitcher "
                    "knowledge is forbidden in a point-in-time challenger."
                )


# ---------------------------------------------------------------------------
# Target derivation (mirrors compute_actual_value EXACTLY)
# ---------------------------------------------------------------------------


def derive_target(df: pd.DataFrame, category: str, fw: FantasyWeights) -> np.ndarray:
    """Vectorized equivalent of outcome_recorder.compute_actual_value."""
    if category == "hits":
        return _num(df["out_hits"])
    if category == "home_runs":
        return _num(df["out_hr"])
    if category == "total_bases":
        # 1B + 2*2B + 3*3B + 4*HR, simplified using hits = 1B+2B+3B+HR.
        return (
            _num(df["out_hits"])
            + _num(df["out_doubles"])
            + 2.0 * _num(df["out_triples"])
            + 3.0 * _num(df["out_hr"])
        )
    if category == "hrr":
        return _num(df["out_hits"]) + _num(df["out_runs"]) + _num(df["out_rbi"])
    if category == "strikeouts":
        return _num(df["out_k"])
    if category == "fantasy":
        hits = _num(df["out_hits"])
        doubles = _num(df["out_doubles"])
        triples = _num(df["out_triples"])
        hr = _num(df["out_hr"])
        singles = np.maximum(0.0, hits - doubles - triples - hr)
        return (
            singles * fw.single
            + doubles * fw.double
            + triples * fw.triple
            + hr * fw.home_run
            + _num(df["out_rbi"]) * fw.rbi
            + _num(df["out_runs"]) * fw.run
            + _num(df["out_bb"]) * fw.walk
        )
    raise ValueError(f"Unknown category: {category}")


# ---------------------------------------------------------------------------
# Feature selection + numeric coercion
# ---------------------------------------------------------------------------


def feature_columns(df: pd.DataFrame, *, kind: str) -> list[str]:
    non = _HITTER_NON_FEATURES if kind == "hitter" else _PITCHER_NON_FEATURES
    return [c for c in df.columns if c not in non]


def _coerce_features(
    df: pd.DataFrame, feats: list[str], categorical: list[str]
) -> pd.DataFrame:
    """Numeric-coerce every non-categorical feature; A4 '' -> NaN. Categorical
    columns are kept as strings (NaN-safe) for CatBoost / label-encoding."""
    out = pd.DataFrame(index=df.index)
    cat_set = set(categorical)
    for c in feats:
        if c in cat_set:
            # Keep as string; normalize blanks/NaN to a sentinel the GBM can
            # treat as its own category. CatBoost needs non-null strings.
            col = df[c].astype("object")
            col = col.where(~col.isna(), "__NA__")
            col = col.replace({"": "__NA__"})
            out[c] = col.astype(str)
        else:
            out[c] = pd.to_numeric(df[c], errors="coerce")
    return out


# ---------------------------------------------------------------------------
# Public builder
# ---------------------------------------------------------------------------


def build_category_data(
    df: pd.DataFrame,
    category: str,
    *,
    kind: str,
    split_date: str,
    fantasy_weights: FantasyWeights,
    drop_no_prior: bool = True,
) -> CategoryData:
    """Assemble one category's temporal train/holdout split.

    split_date: ISO 'YYYY-MM-DD'. Rows with game_date < split_date train;
    rows with game_date >= split_date are held out. Chosen by the caller
    (walk-forward / final holdout), never derived from a random shuffle.
    """
    if "game_date" not in df.columns:
        raise ValueError("frame missing game_date; cannot do a temporal split.")
    if "game_pk" not in df.columns:
        raise ValueError("frame missing game_pk; cannot emit game-keyed holdout predictions.")

    work = df
    # Optionally drop rows with no prior data (has_prior_data == 0): these are
    # season openers where as-of features are all empty; keeping them just adds
    # noise the simulator also can't use. Off for pitchers if column absent.
    if drop_no_prior and "has_prior_data" in work.columns:
        work = work[_num(work["has_prior_data"]) > 0]

    categorical = [
        c for c in (HITTER_CATEGORICAL if kind == "hitter" else PITCHER_CATEGORICAL)
        if c in work.columns
    ]
    feats = feature_columns(work, kind=kind)
    X_all = _coerce_features(work, feats, categorical)
    y_all = derive_target(work, category, fantasy_weights)

    gd = work["game_date"].astype(str)
    train_mask = (gd < split_date).to_numpy()
    holdout_mask = ~train_mask

    id_cols = [c for c in ("game_pk", "player_id", "player_name", "game_date", "team", "opponent")
               if c in work.columns]
    holdout_ids = work.loc[holdout_mask, id_cols].reset_index(drop=True)

    return CategoryData(
        category=category,
        feature_names=feats,
        categorical_features=categorical,
        X_train=X_all.loc[train_mask].reset_index(drop=True),
        y_train=y_all[train_mask],
        X_holdout=X_all.loc[holdout_mask].reset_index(drop=True),
        y_holdout=y_all[holdout_mask],
        holdout_ids=holdout_ids,
        split_date=split_date,
    )


def choose_split_date(df: pd.DataFrame, holdout_fraction: float = 0.2) -> str:
    """Pick a date boundary so ~holdout_fraction of rows fall on/after it.

    Uses the empirical distribution of game_date so the holdout is the most
    recent slice of the timeline (walk-forward friendly), not a random sample.
    """
    gd = pd.to_datetime(df["game_date"].astype(str)).sort_values()
    if gd.empty:
        raise ValueError("empty frame; cannot choose split date.")
    idx = int(len(gd) * (1.0 - holdout_fraction))
    idx = min(max(idx, 0), len(gd) - 1)
    return gd.iloc[idx].strftime("%Y-%m-%d")


def _num(series: pd.Series | Iterable[Any]) -> np.ndarray:
    return pd.to_numeric(pd.Series(series), errors="coerce").fillna(0.0).to_numpy(dtype=float)
