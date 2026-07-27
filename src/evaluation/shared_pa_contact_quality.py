"""Strict-prior, count-bearing batter contact-quality features."""

from __future__ import annotations

from datetime import date
from math import ceil, isfinite
from typing import Any

import numpy as np
import pandas as pd


KNOWN_GAME_TYPES = frozenset({"S", "R", "F", "D", "L", "W", "C", "N", "P", "A", "I", "E"})
BB_TYPES = ("ground_ball", "line_drive", "fly_ball", "popup")
REQUIRED_SOURCE_COLUMNS = frozenset(
    {
        "game_date",
        "game_pk",
        "batter",
        "at_bat_number",
        "pitch_number",
        "game_type",
        "events",
        "type",
        "launch_speed",
        "launch_angle",
        "launch_speed_angle",
        "bb_type",
    }
)
FEATURE_COLUMNS = (
    "history_contact_bip",
    "history_contact_ev_denominator",
    "history_contact_ev_missing_count",
    "history_contact_ev50_count",
    "history_contact_ev50_mean",
    "history_contact_joint_denominator",
    "history_contact_joint_missing_count",
    "history_contact_hard_hit_count_joint",
    "history_contact_hard_hit_rate_joint",
    "history_contact_sweet_spot_count_joint",
    "history_contact_sweet_spot_rate_joint",
    "history_contact_hard_hit_sweet_spot_count_joint",
    "history_contact_hard_hit_sweet_spot_rate_joint",
    "history_contact_ground_count_joint",
    "history_contact_ground_rate_joint",
    "history_contact_line_count_joint",
    "history_contact_line_rate_joint",
    "history_contact_fly_count_joint",
    "history_contact_fly_rate_joint",
    "history_contact_popup_count_joint",
    "history_contact_popup_rate_joint",
    "history_contact_speed_angle_denominator",
    "history_contact_speed_angle_missing_count",
    "history_contact_barrel_count_classified",
    "history_contact_classified_hard_hit_count",
    "history_contact_barrel_rate_classified",
    "history_contact_classified_hard_hit_rate",
    "history_contact_bb_type_denominator",
    "history_contact_bb_type_missing_count",
    "history_contact_ground_ball_count",
    "history_contact_ground_ball_rate",
    "history_contact_line_drive_count",
    "history_contact_line_drive_rate",
    "history_contact_fly_ball_count",
    "history_contact_fly_ball_rate",
    "history_contact_popup_type_count",
    "history_contact_popup_type_rate",
    "history_contact_ev_bb_type_denominator",
    "history_contact_ev_bb_type_missing_count",
    "history_contact_hard_hit_ground_ball_count",
    "history_contact_hard_hit_line_drive_count",
    "history_contact_hard_hit_fly_ball_count",
    "history_contact_hard_hit_popup_count",
    "history_contact_max_source_date",
)


def _rate(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else float(numerator / denominator)


def _count(row: dict[str, Any], name: str) -> int:
    value = row[name]
    if isinstance(value, bool):
        raise ValueError(f"contact-quality count is boolean: {name}")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"contact-quality count is nonnumeric: {name}") from exc
    if not isfinite(numeric) or numeric < 0 or not numeric.is_integer():
        raise ValueError(f"contact-quality count is not a nonnegative integer: {name}")
    return int(numeric)


def empty_prepared_source() -> pd.DataFrame:
    output = pd.DataFrame({name: pd.Series(dtype="object") for name in sorted(REQUIRED_SOURCE_COLUMNS)})
    output["_date"] = pd.Series(dtype="datetime64[ns]")
    return output


def prepare_contact_source(frame: pd.DataFrame, *, player_id: int) -> pd.DataFrame:
    missing = sorted(REQUIRED_SOURCE_COLUMNS.difference(frame.columns))
    if missing:
        raise ValueError(f"contact-quality source missing columns: {missing}")
    work = frame.loc[:, sorted(REQUIRED_SOURCE_COLUMNS)].copy()
    work["_date"] = pd.to_datetime(work["game_date"], format="%Y-%m-%d", errors="coerce")
    if work["_date"].isna().any() or not work["_date"].dt.year.eq(2023).all():
        raise ValueError("contact-quality source date is malformed or outside 2023")
    batter = pd.to_numeric(work["batter"], errors="coerce")
    if batter.isna().any() or not batter.astype(int).eq(int(player_id)).all():
        raise ValueError("contact-quality source batter identity mismatch")
    pitch_identity = ["game_pk", "batter", "at_bat_number", "pitch_number"]
    identity = work[pitch_identity].apply(pd.to_numeric, errors="coerce")
    if identity.isna().any().any() or (identity <= 0).any().any():
        raise ValueError("contact-quality pitch identity is invalid")
    if not np.equal(identity.to_numpy(float), np.floor(identity.to_numpy(float))).all():
        raise ValueError("contact-quality pitch identity is not integral")
    if identity.duplicated().any():
        raise ValueError("contact-quality pitch identity is duplicated")
    game_type = work["game_type"].astype("string")
    if game_type.isna().any() or game_type.str.len().eq(0).any():
        raise ValueError("contact-quality game type is missing")
    unknown_game_type = sorted(set(game_type.astype(str)).difference(KNOWN_GAME_TYPES))
    if unknown_game_type:
        raise ValueError(f"contact-quality game type is unknown: {unknown_game_type}")
    batted = work.loc[game_type.eq("R") & work["type"].astype("string").eq("X")].copy()
    if batted["events"].isna().any():
        raise ValueError("type-X contact row lacks a terminal event")
    pa_identity = ["game_pk", "batter", "at_bat_number"]
    if batted.duplicated(pa_identity).any():
        raise ValueError("contact-quality batted-ball identity is duplicated")
    ev = pd.to_numeric(batted["launch_speed"], errors="coerce")
    raw_ev = batted["launch_speed"].notna()
    if (raw_ev & ev.isna()).any() or (ev.notna() & ev.le(0.0)).any():
        raise ValueError("contact-quality exit velocity is nonnumeric or nonpositive")
    la = pd.to_numeric(batted["launch_angle"], errors="coerce")
    raw_la = batted["launch_angle"].notna()
    if (raw_la & la.isna()).any() or (la.notna() & ~la.between(-90.0, 90.0)).any():
        raise ValueError("contact-quality launch angle is nonnumeric or outside [-90,90]")
    speed_angle = pd.to_numeric(batted["launch_speed_angle"], errors="coerce")
    raw_speed_angle = batted["launch_speed_angle"].notna()
    invalid_speed_angle = raw_speed_angle & (
        speed_angle.isna() | ~speed_angle.between(1, 6) | ~speed_angle.mod(1).eq(0)
    )
    if invalid_speed_angle.any():
        raise ValueError("contact-quality speed-angle classification is invalid")
    joint = ev.notna() & la.notna()
    if (raw_speed_angle & ~joint).any():
        raise ValueError("speed-angle classification exists without joint EV/LA evidence")
    barrel = speed_angle.eq(6)
    if (barrel & ev.lt(95.0)).any():
        raise ValueError("barrel classification contradicts hard-hit evidence")
    bb_type = batted["bb_type"].astype("string")
    observed_type = bb_type.notna() & bb_type.str.len().fillna(0).gt(0)
    unknown_type = sorted(set(bb_type.loc[observed_type].astype(str)).difference(BB_TYPES))
    if unknown_type:
        raise ValueError(f"contact-quality bb_type is unknown: {unknown_type}")
    return work


def contact_quality_features(
    prepared: pd.DataFrame,
    *,
    player_id: int,
    target_date: str,
) -> dict[str, Any]:
    target = pd.Timestamp(target_date)
    if target.year != 2023 or target.date().isoformat() != target_date:
        raise ValueError("contact-quality target date must be canonical 2023 ISO")
    history = prepared.loc[
        prepared["_date"].lt(target) & prepared["game_type"].astype("string").eq("R")
    ].copy()
    if not history.empty and history["_date"].max() >= target:
        raise AssertionError("same-day or future contact row entered history")
    batted = history.loc[history["type"].astype("string").eq("X")].copy()
    bip = int(len(batted))
    ev = pd.to_numeric(batted["launch_speed"], errors="coerce")
    la = pd.to_numeric(batted["launch_angle"], errors="coerce")
    speed_angle = pd.to_numeric(batted["launch_speed_angle"], errors="coerce")
    measured_ev = ev.notna()
    joint = measured_ev & la.notna()
    classified = joint & speed_angle.notna()
    hard_hit = joint & ev.ge(95.0)
    sweet_spot = joint & la.between(8.0, 32.0, inclusive="both")
    ground = joint & la.lt(10.0)
    line = joint & la.ge(10.0) & la.lt(25.0)
    fly = joint & la.ge(25.0) & la.le(50.0)
    popup = joint & la.gt(50.0)
    if not np.array_equal((ground | line | fly | popup).to_numpy(), joint.to_numpy()):
        raise AssertionError("launch-angle bands are not exhaustive and exclusive")
    ev_values = np.sort(ev.loc[measured_ev].to_numpy(float))[::-1]
    ev50_count = int(ceil(len(ev_values) / 2)) if len(ev_values) else 0
    ev50_mean = None if ev50_count == 0 else float(ev_values[:ev50_count].mean())
    bb_type = batted["bb_type"].astype("string")
    type_observed = bb_type.notna() & bb_type.str.len().fillna(0).gt(0)
    joint_type = measured_ev & type_observed
    result: dict[str, Any] = {
        "history_contact_bip": bip,
        "history_contact_ev_denominator": int(measured_ev.sum()),
        "history_contact_ev_missing_count": int(bip - measured_ev.sum()),
        "history_contact_ev50_count": ev50_count,
        "history_contact_ev50_mean": ev50_mean,
        "history_contact_joint_denominator": int(joint.sum()),
        "history_contact_joint_missing_count": int(bip - joint.sum()),
        "history_contact_speed_angle_denominator": int(classified.sum()),
        "history_contact_speed_angle_missing_count": int(bip - classified.sum()),
        "history_contact_bb_type_denominator": int(type_observed.sum()),
        "history_contact_bb_type_missing_count": int(bip - type_observed.sum()),
        "history_contact_ev_bb_type_denominator": int(joint_type.sum()),
        "history_contact_ev_bb_type_missing_count": int(bip - joint_type.sum()),
        "history_contact_max_source_date": (
            None if history.empty else history["_date"].max().date().isoformat()
        ),
    }
    for label, mask in (
        ("hard_hit", hard_hit),
        ("sweet_spot", sweet_spot),
        ("hard_hit_sweet_spot", hard_hit & sweet_spot),
        ("ground", ground),
        ("line", line),
        ("fly", fly),
        ("popup", popup),
    ):
        count = int(mask.sum())
        result[f"history_contact_{label}_count_joint"] = count
        result[f"history_contact_{label}_rate_joint"] = _rate(count, int(joint.sum()))
    barrel = classified & speed_angle.eq(6)
    classified_hard_hit = classified & ev.ge(95.0)
    if int(barrel.sum()) > int(classified_hard_hit.sum()):
        raise ValueError("classified barrel count exceeds classified hard-hit count")
    result.update(
        {
            "history_contact_barrel_count_classified": int(barrel.sum()),
            "history_contact_classified_hard_hit_count": int(classified_hard_hit.sum()),
            "history_contact_barrel_rate_classified": _rate(int(barrel.sum()), int(classified.sum())),
            "history_contact_classified_hard_hit_rate": _rate(int(classified_hard_hit.sum()), int(classified.sum())),
        }
    )
    for value in BB_TYPES:
        count = int((type_observed & bb_type.eq(value)).sum())
        prefix = "popup_type" if value == "popup" else value
        result[f"history_contact_{prefix}_count"] = count
        result[f"history_contact_{prefix}_rate"] = _rate(count, int(type_observed.sum()))
        result[f"history_contact_hard_hit_{value}_count"] = int(
            (joint_type & bb_type.eq(value) & ev.ge(95.0)).sum()
        )
    if set(result) != set(FEATURE_COLUMNS):
        raise AssertionError("contact-quality feature surface changed")
    if result["history_contact_max_source_date"] is not None and result["history_contact_max_source_date"] >= target_date:
        raise AssertionError("contact-quality chronology certificate failed")
    return {name: result[name] for name in FEATURE_COLUMNS}


def validate_contact_feature_row(row: dict[str, Any]) -> None:
    if set(row) != set(FEATURE_COLUMNS):
        raise ValueError("contact-quality row surface changed")
    count_names = [
        name
        for name in FEATURE_COLUMNS
        if "_count" in name or name.endswith("_denominator") or name == "history_contact_bip"
    ]
    counts = {name: _count(row, name) for name in count_names}
    bip = counts["history_contact_bip"]
    denominators = (
        "history_contact_ev_denominator",
        "history_contact_joint_denominator",
        "history_contact_speed_angle_denominator",
        "history_contact_bb_type_denominator",
        "history_contact_ev_bb_type_denominator",
    )
    if any(counts[name] > bip for name in denominators):
        raise ValueError("contact-quality denominator lies outside BIP population")
    for denominator_name, missing_name in (
        ("history_contact_ev_denominator", "history_contact_ev_missing_count"),
        ("history_contact_joint_denominator", "history_contact_joint_missing_count"),
        ("history_contact_speed_angle_denominator", "history_contact_speed_angle_missing_count"),
        ("history_contact_bb_type_denominator", "history_contact_bb_type_missing_count"),
        ("history_contact_ev_bb_type_denominator", "history_contact_ev_bb_type_missing_count"),
    ):
        if counts[denominator_name] + counts[missing_name] != bip:
            raise ValueError("contact-quality denominator/missingness mismatch")
    if counts["history_contact_joint_denominator"] > counts["history_contact_ev_denominator"]:
        raise ValueError("contact-quality joint denominator exceeds measured EV denominator")
    if counts["history_contact_speed_angle_denominator"] > counts["history_contact_joint_denominator"]:
        raise ValueError("contact-quality classified denominator exceeds joint denominator")
    if counts["history_contact_ev_bb_type_denominator"] > min(
        counts["history_contact_ev_denominator"], counts["history_contact_bb_type_denominator"]
    ):
        raise ValueError("contact-quality EV/batted-ball-type denominator is not an intersection")
    if counts["history_contact_ev50_count"] != (
        ceil(counts["history_contact_ev_denominator"] / 2)
        if counts["history_contact_ev_denominator"]
        else 0
    ):
        raise ValueError("contact-quality EV50 support does not match measured EV denominator")
    ev50_mean = row["history_contact_ev50_mean"]
    if counts["history_contact_ev50_count"] == 0:
        if ev50_mean is not None and not pd.isna(ev50_mean):
            raise ValueError("contact-quality zero EV50 support carries a mean")
    else:
        try:
            numeric_ev50 = float(ev50_mean)
        except (TypeError, ValueError) as exc:
            raise ValueError("contact-quality EV50 mean is nonnumeric") from exc
        if not isfinite(numeric_ev50) or numeric_ev50 <= 0:
            raise ValueError("contact-quality EV50 mean is invalid")
    if counts["history_contact_barrel_count_classified"] > counts["history_contact_classified_hard_hit_count"]:
        raise ValueError("contact-quality barrel count exceeds hard-hit count")
    for count_name, rate_name, denominator_name in (
        ("history_contact_hard_hit_count_joint", "history_contact_hard_hit_rate_joint", "history_contact_joint_denominator"),
        ("history_contact_sweet_spot_count_joint", "history_contact_sweet_spot_rate_joint", "history_contact_joint_denominator"),
        ("history_contact_hard_hit_sweet_spot_count_joint", "history_contact_hard_hit_sweet_spot_rate_joint", "history_contact_joint_denominator"),
        ("history_contact_ground_count_joint", "history_contact_ground_rate_joint", "history_contact_joint_denominator"),
        ("history_contact_line_count_joint", "history_contact_line_rate_joint", "history_contact_joint_denominator"),
        ("history_contact_fly_count_joint", "history_contact_fly_rate_joint", "history_contact_joint_denominator"),
        ("history_contact_popup_count_joint", "history_contact_popup_rate_joint", "history_contact_joint_denominator"),
        ("history_contact_barrel_count_classified", "history_contact_barrel_rate_classified", "history_contact_speed_angle_denominator"),
        ("history_contact_classified_hard_hit_count", "history_contact_classified_hard_hit_rate", "history_contact_speed_angle_denominator"),
        ("history_contact_ground_ball_count", "history_contact_ground_ball_rate", "history_contact_bb_type_denominator"),
        ("history_contact_line_drive_count", "history_contact_line_drive_rate", "history_contact_bb_type_denominator"),
        ("history_contact_fly_ball_count", "history_contact_fly_ball_rate", "history_contact_bb_type_denominator"),
        ("history_contact_popup_type_count", "history_contact_popup_type_rate", "history_contact_bb_type_denominator"),
    ):
        count, denominator, rate = counts[count_name], counts[denominator_name], row[rate_name]
        if count < 0 or count > denominator:
            raise ValueError(f"contact-quality count is outside denominator: {count_name}")
        expected = _rate(count, denominator)
        if expected is None:
            if rate is not None and not pd.isna(rate):
                raise ValueError(f"zero denominator carries a rate: {rate_name}")
        elif rate is None or pd.isna(rate) or not np.isclose(float(rate), expected, rtol=0.0, atol=1e-12):
            raise ValueError(f"contact-quality count/rate mismatch: {rate_name}")
        elif not isfinite(float(rate)) or not 0.0 <= float(rate) <= 1.0:
            raise ValueError(f"contact-quality rate is outside [0,1]: {rate_name}")

    if sum(
        counts[name]
        for name in (
            "history_contact_ground_count_joint",
            "history_contact_line_count_joint",
            "history_contact_fly_count_joint",
            "history_contact_popup_count_joint",
        )
    ) != counts["history_contact_joint_denominator"]:
        raise ValueError("contact-quality launch-angle partition does not equal joint denominator")
    if sum(
        counts[name]
        for name in (
            "history_contact_ground_ball_count",
            "history_contact_line_drive_count",
            "history_contact_fly_ball_count",
            "history_contact_popup_type_count",
        )
    ) != counts["history_contact_bb_type_denominator"]:
        raise ValueError("contact-quality batted-ball-type partition does not equal its denominator")
    if counts["history_contact_hard_hit_sweet_spot_count_joint"] > min(
        counts["history_contact_hard_hit_count_joint"],
        counts["history_contact_sweet_spot_count_joint"],
    ):
        raise ValueError("contact-quality joint intersection exceeds a parent count")
    if counts["history_contact_classified_hard_hit_count"] > counts["history_contact_hard_hit_count_joint"]:
        raise ValueError("contact-quality classified hard-hit count exceeds joint hard-hit count")
    hard_hit_type_pairs = (
        ("history_contact_hard_hit_ground_ball_count", "history_contact_ground_ball_count"),
        ("history_contact_hard_hit_line_drive_count", "history_contact_line_drive_count"),
        ("history_contact_hard_hit_fly_ball_count", "history_contact_fly_ball_count"),
        ("history_contact_hard_hit_popup_count", "history_contact_popup_type_count"),
    )
    for hard_hit_name, type_name in hard_hit_type_pairs:
        if counts[hard_hit_name] > counts[type_name]:
            raise ValueError(f"contact-quality hard-hit type count exceeds type population: {hard_hit_name}")
    if sum(counts[name] for name, _ in hard_hit_type_pairs) > counts[
        "history_contact_ev_bb_type_denominator"
    ]:
        raise ValueError("contact-quality hard-hit type counts exceed their intersected population")
    max_source_date = row["history_contact_max_source_date"]
    if max_source_date is not None and not pd.isna(max_source_date):
        if not isinstance(max_source_date, str):
            raise ValueError("contact-quality max source date is not canonical text")
        try:
            parsed = date.fromisoformat(max_source_date)
        except ValueError as exc:
            raise ValueError("contact-quality max source date is malformed") from exc
        if parsed.isoformat() != max_source_date or parsed.year != 2023:
            raise ValueError("contact-quality max source date is not canonical 2023 ISO")
