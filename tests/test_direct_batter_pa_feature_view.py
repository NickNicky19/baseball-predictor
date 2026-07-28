from __future__ import annotations

import json
import hashlib
import tempfile
from copy import deepcopy
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import src.features.direct_batter_pa_feature_view as feature_boundary
import scripts.build_direct_batter_pa_feature_view as feature_builder
from scripts.build_direct_batter_pa_feature_view import assert_directory_entries
from scripts.validate_direct_batter_pa_feature_view import (
    validate_manifest_envelope,
    validate_package_document_envelopes,
)

from src.features.direct_batter_pa_feature_view import (
    IDENTITY,
    LINEAGE,
    SOURCE_ONLY,
    TARGETS,
    deterministic_csv_gzip,
    expected_panel_columns,
    load_contract,
    split_panel,
    validate_feature_frame,
    validate_target_frame,
)


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "direct_batter_pa_feature_view_v1.json"


def contract() -> dict:
    return load_contract(CONTRACT_PATH)


def valid_history(*, game_date: str = "2023-06-02", game_pk: int = 1) -> dict:
    row = {column: np.nan for column in [*IDENTITY, *LINEAGE, *contract()["feature_columns"]]}
    row.update({
        "season": 2023, "game_date": game_date, "game_pk": game_pk,
        "player_id": 7, "max_source_date": "2023-06-01",
        "history_pitch_count": 10, "history_pa": 2,
        "history_strikeout_count": 1, "history_strikeout_rate": 0.5,
        "days_since_strikeout": 1, "history_walk_count": 0,
        "history_walk_rate": 0.0, "history_single_count": 1,
        "history_single_rate": 0.5, "days_since_single": 1,
        "history_double_count": 0, "history_double_rate": 0.0,
        "history_triple_count": 0, "history_triple_rate": 0.0,
        "history_home_run_count": 0, "history_home_run_rate": 0.0,
        "history_bip_out_count": 0, "history_bip_out_rate": 0.0,
        "history_other_non_ab_count": 0, "history_other_non_ab_rate": 0.0,
        "history_pa_age_days_count": 2, "history_pa_age_days_missing_count": 0,
        "history_pa_age_days_mean": 1.0, "history_pa_age_days_sd": 0.0,
        "days_since_pa": 1, "history_description_denominator": 9,
        "history_description_missing_count": 1, "history_swing_count": 4,
        "history_swing_denominator": 9, "history_swing_rate": 4 / 9,
        "history_whiff_count": 1, "history_whiff_denominator": 4,
        "history_whiff_rate": 0.25, "history_chase_count": 1,
        "history_chase_denominator": 2, "history_chase_rate": 0.5,
        "history_zone_count": 6, "history_zone_denominator": 8,
        "history_zone_missing_count": 2, "history_zone_rate": 0.75,
        "history_bip": 1, "history_exit_velocity_count": 1,
        "history_exit_velocity_missing_count": 0, "history_exit_velocity_mean": 100.0,
        "history_exit_velocity_sd": 0.0, "history_launch_angle_count": 1,
        "history_launch_angle_missing_count": 0, "history_launch_angle_mean": 20.0,
        "history_launch_angle_sd": 0.0, "history_batted_ball_denominator": 1,
        "history_barrel_count": 0, "history_hard_hit_count": 1,
        "history_barrel_rate": 0.0, "history_hard_hit_rate": 1.0,
        "history_hard_hit_non_barrel_count": 1,
        "history_other_measured_bbe_count": 0, "history_barrel_share_bbe": 0.0,
        "history_hard_hit_non_barrel_share_bbe": 1.0,
        "history_other_measured_bbe_share_bbe": 0.0,
        "history_measured_bbe_per_pa": 0.5,
        "history_pitch_type_denominator": 9, "history_pitch_type_missing_count": 1,
        "history_distinct_pitch_types": 2, "history_pitch_type_entropy": 0.5,
    })
    for prefix, population in (
        ("history_release_speed", 10), ("history_pfx_x", 10),
        ("history_pfx_z", 10), ("history_plate_x", 10), ("history_plate_z", 10),
    ):
        row[f"{prefix}_count"] = population
        row[f"{prefix}_missing_count"] = 0
        row[f"{prefix}_mean"] = 1.0
        row[f"{prefix}_sd"] = 0.0
    return row


def valid_panel_row(*, game_date: str = "2023-06-02", game_pk: int = 1) -> dict:
    history = valid_history(game_date=game_date, game_pk=game_pk)
    row = dict(history)
    row.update({
        "lineup_slot": 1, "out_pa": 2, "out_ab": 2, "out_hits": 1,
        "out_doubles": 0, "out_triples": 0, "out_hr": 0, "out_bb": 0,
        "out_k": 1, "target_date": game_date,
        **{column: 0 for column in TARGETS},
    })
    row["target_strikeout"] = 1
    row["target_single"] = 1
    return row


def panel(rows: list[dict] | None = None) -> pd.DataFrame:
    rows = rows or [valid_panel_row()]
    return pd.DataFrame(rows, columns=expected_panel_columns(contract()))


def test_positive_schema_produces_physically_separate_label_free_artifacts() -> None:
    features, targets = split_panel(panel(), contract())
    assert list(features.columns) == [*IDENTITY, *LINEAGE, *contract()["feature_columns"]]
    assert list(targets.columns) == [*IDENTITY, *TARGETS]
    assert not any(column.startswith(("out_", "target_", "lineup_")) for column in features)
    assert "target_date" not in features and "lineup_slot" not in features


def test_history_out_hr_label_rename_injection_fails_closed() -> None:
    mutated = panel().rename(columns={"target_home_run": "history_out_hr"})
    with pytest.raises(ValueError, match="exact schema"):
        split_panel(mutated, contract())


def test_label_permutation_cannot_change_feature_bytes() -> None:
    source = panel([
        valid_panel_row(game_date="2023-06-02", game_pk=1),
        valid_panel_row(game_date="2023-06-03", game_pk=2),
    ])
    source.loc[1, "max_source_date"] = "2023-06-02"
    source.loc[1, "target_strikeout"] = 0
    source.loc[1, "target_home_run"] = 1
    before, _ = split_panel(source, contract())
    mutated = source.copy()
    mutated[TARGETS] = mutated[TARGETS].iloc[::-1].to_numpy()
    after, _ = split_panel(mutated, contract())
    assert deterministic_csv_gzip(before) == deterministic_csv_gzip(after)


@pytest.mark.parametrize("mutation", ["add", "delete", "rename", "reorder"])
def test_any_feature_schema_or_order_mutation_fails_closed(mutation: str) -> None:
    features, _ = split_panel(panel(), contract())
    if mutation == "add":
        features["history_out_hr"] = 0
    elif mutation == "delete":
        features = features.drop(columns=["history_swing_rate"])
    elif mutation == "rename":
        features = features.rename(columns={"history_swing_rate": "history_swing_pct"})
    else:
        columns = list(features.columns)
        left, right = columns.index("history_swing_count"), columns.index("history_swing_rate")
        columns[left], columns[right] = columns[right], columns[left]
        features = features.loc[:, columns]
    with pytest.raises(ValueError, match="positive schema"):
        validate_feature_frame(features, contract())


@pytest.mark.parametrize("column,value,match", [
    ("history_strikeout_count", 2, "sum to history PA"),
    ("history_strikeout_rate", 0.25, "count/rate"),
    ("history_swing_denominator", 8, "swing"),
    ("history_strikeout_rate", np.nan, "missing"),
    ("history_barrel_count", 2, "count ordering"),
    ("history_hard_hit_rate", 0.5, "hard-hit count/rate"),
])
def test_count_rate_denominator_missing_and_kwan_mutations_fail(
    column: str, value: object, match: str,
) -> None:
    features, _ = split_panel(panel(), contract())
    features.loc[0, column] = value
    if column == "history_strikeout_count":
        features.loc[0, "history_strikeout_rate"] = 1.0
    with pytest.raises(ValueError, match=match):
        validate_feature_frame(features, contract())


def test_same_day_source_date_fails_closed() -> None:
    features, _ = split_panel(panel(), contract())
    features.loc[0, "max_source_date"] = features.loc[0, "game_date"]
    with pytest.raises(ValueError, match="same-day or future"):
        validate_feature_frame(features, contract())


def test_doubleheader_identity_preserves_same_prior_date_features() -> None:
    rows = [
        valid_panel_row(game_date="2023-06-02", game_pk=1),
        valid_panel_row(game_date="2023-06-02", game_pk=2),
    ]
    features, _ = split_panel(panel(rows), contract())
    validate_feature_frame(features, contract())
    features.loc[1, "history_distinct_pitch_types"] = 3
    with pytest.raises(ValueError, match="doubleheader"):
        validate_feature_frame(features, contract())


def test_actual_lineup_slot_cannot_be_added_to_consumer_allowlist() -> None:
    mutated = deepcopy(contract())
    mutated["feature_columns"].append("lineup_slot")
    with tempfile.TemporaryDirectory(dir=ROOT) as directory:
        path = Path(directory) / "contract.json"
        path.write_text(json.dumps(mutated), encoding="utf-8")
        with pytest.raises(ValueError, match="fixed release digest"):
            load_contract(path)


def test_release_fixed_contract_and_ordered_feature_digests_are_independent() -> None:
    assert hashlib.sha256(CONTRACT_PATH.read_bytes()).hexdigest() == "54355ea512df8d92ef16c68430079cfa54c88ed6315d017017527ff30a0da20c"
    assert len(feature_boundary.FEATURE_COLUMNS) == 90
    assert hashlib.sha256(("\n".join(feature_boundary.FEATURE_COLUMNS) + "\n").encode()).hexdigest() == "f125c184e324e8b708ff75b11626ea35b4120e7b1ee90eb5ff2c7a956ef5501a"


@pytest.mark.parametrize("mutation", ["history_out_hr", "lineup_slot", "actual_lineup_slot", "delete", "rename", "reorder"])
def test_joint_contract_and_runtime_allowlist_mutation_still_fails_fixed_digest(
    mutation: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    mutated = deepcopy(contract())
    columns = mutated["feature_columns"]
    if mutation in {"history_out_hr", "lineup_slot", "actual_lineup_slot"}:
        columns.append(mutation)
    elif mutation == "delete":
        columns.pop()
    elif mutation == "rename":
        columns[-1] = "history_pitch_type_entropy_renamed"
    else:
        columns[-1], columns[-2] = columns[-2], columns[-1]
    with tempfile.TemporaryDirectory(dir=ROOT) as directory:
        path = Path(directory) / "contract.json"
        path.write_text(json.dumps(mutated, sort_keys=True), encoding="utf-8")
        monkeypatch.setattr(feature_boundary, "CONTRACT_SHA256", hashlib.sha256(path.read_bytes()).hexdigest())
        monkeypatch.setattr(feature_boundary, "FEATURE_COLUMNS", tuple(columns))
        with pytest.raises(ValueError, match="ordered allowlist digest"):
            load_contract(path)


@pytest.mark.parametrize("column,value", [
    ("history_swing_rate", float("inf")),
    ("history_release_speed_mean", float("-inf")),
    ("history_pitch_count", 1.5),
    ("history_zone_missing_count", -1),
])
def test_nonfinite_negative_and_nonintegral_numeric_mutations_fail(column: str, value: float) -> None:
    features, _ = split_panel(panel(), contract())
    features[column] = features[column].astype(float)
    features.loc[0, column] = value
    with pytest.raises(ValueError, match="non-finite|negative or nonintegral"):
        validate_feature_frame(features, contract())


@pytest.mark.parametrize("value", ["", "2023/06/01", "2023-06-01T00:00:00", "2023-02-30", 20230601])
def test_max_source_date_must_be_canonical_null_or_iso(value: object) -> None:
    features, _ = split_panel(panel(), contract())
    features["max_source_date"] = features["max_source_date"].astype(object)
    features.loc[0, "max_source_date"] = value
    with pytest.raises(ValueError, match="canonical nullable ISO|invalid calendar|non-string"):
        validate_feature_frame(features, contract())


def test_distinct_pitch_types_cannot_exceed_support() -> None:
    features, _ = split_panel(panel(), contract())
    features.loc[0, "history_distinct_pitch_types"] = 10
    with pytest.raises(ValueError, match="exceed pitch-type support"):
        validate_feature_frame(features, contract())


def test_unexpected_directory_entry_fails_closed() -> None:
    with tempfile.TemporaryDirectory(dir=ROOT) as directory:
        root = Path(directory)
        (root / "expected").write_text("ok", encoding="utf-8")
        (root / "orphan").write_text("bad", encoding="utf-8")
        with pytest.raises(ValueError, match="orphan or unexpected"):
            assert_directory_entries(root, allowed=(frozenset({"expected"}),), context="test release")


def test_reparse_or_symlink_release_path_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    original = feature_builder._has_reparse_point
    contract_parent = CONTRACT_PATH.parent.resolve()
    monkeypatch.setattr(
        feature_builder,
        "_has_reparse_point",
        lambda path: path.resolve() == contract_parent or original(path),
    )
    with pytest.raises(ValueError, match="reparse point"):
        feature_builder.safe_release_path(
            CONTRACT_PATH, expected="config/direct_batter_pa_feature_view_v1.json", must_exist=True,
        )


def test_upstream_authority_is_fixed_independently_of_registry_contents() -> None:
    assert feature_builder.UPSTREAM_SOURCE_COMMIT == "c394dea5a0028b8f78e8ab78daed0d44cde5d771"
    assert feature_builder.UPSTREAM_REGISTRY_SHA256 == "f62983689106e37e57eb6ae2374c688f00c4764f75ee1a412b0f7542648e8096"
    assert feature_builder.UPSTREAM_PANEL_SHA256 == "643a4c6533dbe59fc4e6b5e5932683c0877ae0a5d982945c799976d36af1ebf6"
    assert feature_builder.UPSTREAM_MANIFEST_SHA256 == "083fe961b00299f0561130e204e5264dfa05f0b234b72ce2af214ac5f681e9a4"
    assert feature_builder.UPSTREAM_CERTIFICATE_SHA256 == "aa3fd225f946b91ab1e1249eb8b7ce415f0fccabb18a041e3960552b2aeaf8ae"


@pytest.mark.parametrize("mutation", [
    "extra_top", "missing_top", "upstream_digest", "implementation_missing",
    "dependency_extra", "output_traversal", "safety_flip",
])
def test_manifest_schema_identity_and_path_mutations_fail(mutation: str) -> None:
    path = ROOT / "data/analysis/system_integrity_v2/direct_batter_pa_feature_view_v1/manifest.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    if mutation == "extra_top":
        value["unexpected"] = True
    elif mutation == "missing_top":
        value.pop("population")
    elif mutation == "upstream_digest":
        value["upstream"]["panel_sha256"] = "0" * 64
    elif mutation == "implementation_missing":
        value["implementation_sha256"].pop(next(iter(value["implementation_sha256"])))
    elif mutation == "dependency_extra":
        value["dependency_identity"]["declaration_sha256"]["unexpected.lock"] = "0" * 64
    elif mutation == "output_traversal":
        value["outputs"]["features"]["path"] = "../features.csv.gz"
    else:
        value["betting_authorized"] = True
    with pytest.raises(ValueError):
        validate_manifest_envelope(value, contract())


@pytest.mark.parametrize("document,mutation", [
    ("certificate", "extra_top"), ("certificate", "implementation_missing"),
    ("registry", "missing_top"), ("registry", "dependency_missing"),
    ("registry", "binding_disagreement"),
])
def test_certificate_and_registry_exact_schema_mutations_fail(document: str, mutation: str) -> None:
    base = ROOT / "data/analysis/system_integrity_v2/direct_batter_pa_feature_view_v1"
    cert = json.loads((base / "certificate.json").read_text(encoding="utf-8"))
    registered = json.loads(
        (ROOT / "config/direct_batter_pa_feature_view_v1_artifact_registry.json").read_text(encoding="utf-8")
    )
    target = cert if document == "certificate" else registered
    if mutation == "extra_top":
        target["unexpected"] = True
    elif mutation == "implementation_missing":
        target["implementation_sha256"].pop(next(iter(target["implementation_sha256"])))
    elif mutation == "missing_top":
        target.pop("artifacts")
    elif mutation == "dependency_missing":
        target["dependency_identity"]["declaration_sha256"].pop("requirements.txt")
    else:
        target["release_binding"]["release_certifiable"] = True
    with pytest.raises(ValueError):
        validate_package_document_envelopes(cert, registered)


def test_partial_history_row_and_missing_identity_counterpart_fail_closed() -> None:
    features, targets = split_panel(panel(), contract())
    features.loc[0, "history_single_rate"] = np.nan
    with pytest.raises(ValueError, match="missing"):
        validate_feature_frame(features, contract())
    targets = targets.iloc[0:0]
    with pytest.raises(ValueError, match="one-to-one"):
        validate_target_frame(targets, feature_identity=features[IDENTITY])


def test_zero_history_is_explicit_and_never_filled_with_a_league_value() -> None:
    row = valid_panel_row()
    for outcome in (name.removeprefix("target_") for name in TARGETS):
        row[f"history_{outcome}_count"] = 0
        row[f"history_{outcome}_rate"] = np.nan
        row[f"days_since_{outcome}"] = np.nan
    row["history_pa"] = 0
    row["history_pitch_count"] = 0
    row["max_source_date"] = np.nan
    row["days_since_pa"] = np.nan
    row["history_pa_age_days_count"] = 0
    row["history_pa_age_days_missing_count"] = 0
    row["history_pa_age_days_mean"] = np.nan
    row["history_pa_age_days_sd"] = np.nan
    row["history_description_denominator"] = 0
    row["history_description_missing_count"] = 0
    row["history_swing_count"] = 0
    row["history_swing_denominator"] = 0
    row["history_swing_rate"] = np.nan
    row["history_whiff_count"] = 0
    row["history_whiff_denominator"] = 0
    row["history_whiff_rate"] = np.nan
    row["history_chase_count"] = 0
    row["history_chase_denominator"] = 0
    row["history_chase_rate"] = np.nan
    row["history_zone_count"] = 0
    row["history_zone_denominator"] = 0
    row["history_zone_missing_count"] = 0
    row["history_zone_rate"] = np.nan
    row["history_bip"] = 0
    for prefix in (
        "history_exit_velocity", "history_launch_angle", "history_release_speed",
        "history_pfx_x", "history_pfx_z", "history_plate_x", "history_plate_z",
    ):
        row[f"{prefix}_count"] = 0
        row[f"{prefix}_missing_count"] = 0
        row[f"{prefix}_mean"] = np.nan
        row[f"{prefix}_sd"] = np.nan
    for name in (
        "history_batted_ball_denominator", "history_barrel_count", "history_hard_hit_count",
        "history_barrel_rate", "history_hard_hit_rate", "history_hard_hit_non_barrel_count",
        "history_other_measured_bbe_count", "history_barrel_share_bbe",
        "history_hard_hit_non_barrel_share_bbe", "history_other_measured_bbe_share_bbe",
        "history_measured_bbe_per_pa",
    ):
        row[name] = np.nan
    row["history_pitch_type_denominator"] = 0
    row["history_pitch_type_missing_count"] = 0
    row["history_distinct_pitch_types"] = 0
    row["history_pitch_type_entropy"] = np.nan
    second = valid_panel_row(game_date="2023-06-03", game_pk=2)
    second["max_source_date"] = "2023-06-02"
    features, _ = split_panel(panel([row, second]), contract())
    assert pd.isna(features.loc[0, "history_home_run_rate"])
    assert pd.isna(features.loc[0, "history_barrel_rate"])
