from src.evaluation.pa_volume_source_truth_v2 import build_pa_volume_artifact


def test_same_player_on_both_sides_is_not_collapsed() -> None:
    rows = []
    for side, team, base in (("away", 10, 100), ("home", 20, 200)):
        for slot in range(1, 10):
            rows.append({
                "game_pk": 1, "official_date": "2023-03-30", "side": side,
                "team_id": team, "player_id": 101 if side == "home" and slot == 1 else base + slot,
                "lineup_slot": slot, "out_pa": 4,
            })
    artifact = build_pa_volume_artifact(
        rows=rows, source_release_manifest_sha256="a" * 64,
        source_release_external_verification_sha256="b" * 64,
        dependency_lock_sha256="c" * 64, builder_source_sha256="d" * 64,
    )
    assert artifact["source"]["fit_rows"] == 18
