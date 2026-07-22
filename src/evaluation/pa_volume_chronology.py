"""Strict 2023-only PA-volume fitting and source projection."""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
from pathlib import Path
from typing import Iterable

import pandas as pd

from src.features.pa_volume_gate import PAVolumeDistributionArtifact


SOURCE_COLUMNS = ("game_date", "game_pk", "player_id", "lineup_slot", "out_pa")


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_seasons_without_later_outcomes(
    path: str | Path, *, seasons: Iterable[int]
) -> pd.DataFrame:
    """Read only requested leading seasons from a chronological gzip CSV.

    The source is required to be sorted by season.  Processing stops when the
    first later season is encountered, so spent 2025 outcome fields are never
    projected into memory during a 2023/2024 repair evaluation.
    """
    allowed = tuple(sorted(set(int(value) for value in seasons)))
    if not allowed or allowed != tuple(range(allowed[0], allowed[-1] + 1)):
        raise ValueError("requested seasons must be one nonempty contiguous range")
    rows: list[dict[str, str]] = []
    with gzip.open(Path(path), "rt", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration as exc:
            raise ValueError("PA-volume source is empty") from exc
        if len(header) != len(set(header)):
            raise ValueError("PA-volume source has duplicate columns")
        required = {"season", *SOURCE_COLUMNS}
        missing = sorted(required - set(header))
        if missing:
            raise ValueError(f"PA-volume source is missing columns {missing}")
        index = {name: header.index(name) for name in required}
        last_season: int | None = None
        for raw in reader:
            if len(raw) != len(header):
                raise ValueError("PA-volume source row width differs from its header")
            try:
                season = int(raw[index["season"]])
            except ValueError as exc:
                raise ValueError("PA-volume source season is not an integer") from exc
            if last_season is not None and season < last_season:
                raise ValueError("PA-volume source is not chronologically ordered by season")
            last_season = season
            if season > allowed[-1]:
                break
            if season < allowed[0]:
                continue
            rows.append({name: raw[index[name]] for name in SOURCE_COLUMNS})
    frame = pd.DataFrame(rows, columns=SOURCE_COLUMNS)
    if frame.empty:
        raise ValueError("PA-volume source has no requested-season rows")
    return frame


def _source_projection_sha256(frame: pd.DataFrame) -> str:
    ordered = frame.sort_values(["game_date", "game_pk", "player_id"], kind="stable")
    records = [
        {
            "game_date": str(row.game_date),
            "game_pk": int(row.game_pk),
            "player_id": int(row.player_id),
            "lineup_slot": int(row.lineup_slot),
            "out_pa": int(row.out_pa),
        }
        for row in ordered.itertuples(index=False)
    ]
    return hashlib.sha256(
        json.dumps(records, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def fit_2023_pa_volume(
    frame: pd.DataFrame, *, source_path: str, source_sha256: str
) -> PAVolumeDistributionArtifact:
    if tuple(frame.columns) != SOURCE_COLUMNS:
        raise ValueError("PA-volume fit must receive only the locked source projection")
    work = frame.copy()
    dates = pd.to_datetime(work["game_date"], errors="coerce")
    numeric_columns = ["game_pk", "player_id", "lineup_slot", "out_pa"]
    numeric = work[numeric_columns].apply(pd.to_numeric, errors="coerce")
    if dates.isna().any() or numeric.isna().any().any():
        raise ValueError("PA-volume fit source contains null or malformed values")
    if not dates.dt.year.eq(2023).all():
        raise ValueError("PA-volume fit must contain 2023 and no other year")
    if not (numeric.to_numpy() == numeric.to_numpy().astype(int)).all():
        raise ValueError("PA-volume identities, slots, and outcomes must be integers")
    for column in numeric_columns:
        work[column] = numeric[column].astype(int)
    work["game_date"] = dates.dt.strftime("%Y-%m-%d")
    if work.duplicated(["game_pk", "player_id"]).any():
        raise ValueError("PA-volume fit contains duplicate game/player identity")
    if not work["lineup_slot"].between(1, 9).all():
        raise ValueError("PA-volume fit contains an invalid lineup slot")
    if not work["out_pa"].between(0, 12).all():
        raise ValueError("PA-volume fit contains an invalid official PA count")
    game_sizes = work.groupby("game_pk", sort=False).size()
    if not game_sizes.eq(18).all():
        raise ValueError("PA-volume fit is not the 18-original-starter population")
    slots = work.groupby("game_pk", sort=False)["lineup_slot"].agg(
        lambda values: sorted(values.tolist())
    )
    expected = [1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9]
    if not slots.map(lambda values: values == expected).all():
        raise ValueError("PA-volume fit lineup-slot population changed")

    by_slot: dict[int, dict[int, float]] = {}
    rows_by_slot: dict[int, int] = {}
    for slot in range(1, 10):
        values = work.loc[work["lineup_slot"].eq(slot), "out_pa"]
        rows_by_slot[slot] = int(len(values))
        distribution = values.value_counts(normalize=True).sort_index()
        by_slot[slot] = {int(key): float(value) for key, value in distribution.items()}
    pooled_series = work["out_pa"].value_counts(normalize=True).sort_index()
    pooled = {int(key): float(value) for key, value in pooled_series.items()}

    return PAVolumeDistributionArtifact(
        candidate_id="pa_volume_2023_only_v1",
        fit_season=2023,
        selection_season=2024,
        population="original_sequence_zero_starters",
        source_path=str(source_path),
        source_sha256=str(source_sha256).lower(),
        source_projection_sha256=_source_projection_sha256(work),
        source_columns=SOURCE_COLUMNS,
        fit_rows=int(len(work)),
        fit_games=int(work["game_pk"].nunique()),
        date_min=str(work["game_date"].min()),
        date_max=str(work["game_date"].max()),
        rows_by_lineup_slot=rows_by_slot,
        by_lineup_slot=by_slot,
        pooled=pooled,
    )
