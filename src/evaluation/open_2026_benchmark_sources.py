"""Build and validate outcome-blind sources for the open-2026 probability benchmark."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd


SCHEMA = "open-2026-probability-benchmark-source-manifest-v2"
STATUS = "SOURCES_BOUND_BEFORE_BENCHMARK_SCORING_V2"
ALLOWED_MONTHS = {"2026-03", "2026-04", "2026-06"}
MODEL_KEY = ["mlb_game_pk", "player_id", "game_date", "category", "line"]
HITTER_KEY = ["mlb_game_pk", "player_id", "game_date"]
OUTCOME_COLUMNS = [
    *HITTER_KEY,
    "pa", "ab", "hits", "doubles", "triples", "home_runs", "walks",
    "strikeouts", "singles", "bip_out", "other_non_ab", "total_bases",
]
LINEUP_COLUMNS = [*HITTER_KEY, "lineup_slot"]


def sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def cache_key(url: str) -> str:
    canonical = json.dumps({"url": url, "params": {}}, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def require_open_dates(values: Any) -> list[str]:
    dates = sorted({str(value) for value in values})
    if not dates or any(date[:7] not in ALLOWED_MONTHS for date in dates):
        raise ValueError("date universe is empty, includes May, or leaves the locked open months")
    return dates


def validate_probability_artifact(frame: pd.DataFrame) -> list[str]:
    required = set(MODEL_KEY + ["sim_p_over"])
    if not required.issubset(frame.columns):
        raise ValueError("production probability artifact schema changed")
    if frame[MODEL_KEY].isna().any().any() or frame.duplicated(MODEL_KEY).any():
        raise ValueError("production MODEL_KEY is null or duplicated")
    categories = set(frame["category"].astype(str))
    if categories != {"hits", "home_runs", "hrr", "strikeouts"}:
        raise ValueError("production category inventory changed")
    if "total_bases" in categories:
        raise ValueError("Total Bases cannot be relabeled as an established production output")
    probability = pd.to_numeric(frame["sim_p_over"], errors="raise")
    if probability.isna().any() or ((probability < 0.0) | (probability > 1.0)).any():
        raise ValueError("production probability outside [0,1]")
    return require_open_dates(frame["game_date"])


def _batting_rows(feed: dict[str, Any], game_pk: int, game_date: str) -> list[dict[str, int | str]]:
    status = feed.get("gameData", {}).get("status", {}) or {}
    if status.get("abstractGameState") != "Final" or status.get("codedGameState") not in {"F", "O"}:
        raise ValueError(f"cached MLB feed is not final: {game_pk}")
    official_date = str(feed.get("gameData", {}).get("datetime", {}).get("officialDate", ""))
    if official_date != game_date:
        raise ValueError(f"cached MLB feed date mismatch: {game_pk}")
    teams = feed.get("liveData", {}).get("boxscore", {}).get("teams", {}) or {}
    rows: list[dict[str, int | str]] = []
    for side in ("away", "home"):
        for raw_id, player in (teams.get(side, {}).get("players", {}) or {}).items():
            batting = (player.get("stats", {}) or {}).get("batting") or {}
            if not batting:
                continue
            player_id = int(str(raw_id).removeprefix("ID"))
            pa = int(batting.get("plateAppearances") or 0)
            ab = int(batting.get("atBats") or 0)
            hits = int(batting.get("hits") or 0)
            doubles = int(batting.get("doubles") or 0)
            triples = int(batting.get("triples") or 0)
            home_runs = int(batting.get("homeRuns") or 0)
            walks = int(batting.get("baseOnBalls") or 0)
            strikeouts = int(batting.get("strikeOuts") or 0)
            singles = hits - doubles - triples - home_runs
            bip_out = ab - hits - strikeouts
            other_non_ab = pa - ab - walks
            counts = (pa, ab, hits, doubles, triples, home_runs, walks, strikeouts,
                      singles, bip_out, other_non_ab)
            if any(value < 0 for value in counts):
                raise ValueError(f"invalid official batting accounting: {game_pk}/{player_id}")
            if strikeouts + walks + singles + doubles + triples + home_runs + bip_out + other_non_ab != pa:
                raise ValueError(f"official PA classes do not sum to PA: {game_pk}/{player_id}")
            rows.append({
                "mlb_game_pk": game_pk,
                "player_id": player_id,
                "game_date": game_date,
                "pa": pa,
                "ab": ab,
                "hits": hits,
                "doubles": doubles,
                "triples": triples,
                "home_runs": home_runs,
                "walks": walks,
                "strikeouts": strikeouts,
                "singles": singles,
                "bip_out": bip_out,
                "other_non_ab": other_non_ab,
                "total_bases": singles + 2 * doubles + 3 * triples + 4 * home_runs,
            })
    return rows


def load_cached_official_outcomes(
    *, evidence_root: Path, model: pd.DataFrame
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    cache_root = evidence_root / "data/cache/http"
    game_dates = (
        model.loc[:, ["mlb_game_pk", "game_date"]]
        .drop_duplicates()
        .sort_values(["game_date", "mlb_game_pk"])
    )
    if game_dates.duplicated("mlb_game_pk").any():
        raise ValueError("one MLB game key mapped to multiple official dates")
    all_rows: list[dict[str, int | str]] = []
    records: list[dict[str, Any]] = []
    for game in game_dates.itertuples(index=False):
        game_pk = int(game.mlb_game_pk)
        game_date = str(game.game_date)
        url = f"https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live"
        key = cache_key(url)
        path = cache_root / key[:2] / f"{key}.json"
        if not path.is_file():
            raise ValueError(f"official cached MLB feed missing: {game_pk}")
        wrapper = json.loads(path.read_text(encoding="utf-8"))
        if wrapper.get("url") != url or wrapper.get("params") not in ({}, None):
            raise ValueError(f"official cached MLB request identity mismatch: {game_pk}")
        feed = wrapper.get("data")
        if not isinstance(feed, dict) or int(feed.get("gamePk", -1)) != game_pk:
            raise ValueError(f"official cached MLB payload identity mismatch: {game_pk}")
        rows = _batting_rows(feed, game_pk, game_date)
        all_rows.extend(rows)
        records.append({
            "mlb_game_pk": game_pk,
            "game_date": game_date,
            "path": relative(evidence_root, path),
            "sha256": sha256(path),
            "request_url": url,
            "cache_key": key,
            "hitter_rows": len(rows),
        })
    outcomes = pd.DataFrame(all_rows, columns=OUTCOME_COLUMNS)
    if outcomes.empty or outcomes[HITTER_KEY].isna().any().any() or outcomes.duplicated(HITTER_KEY).any():
        raise ValueError("official outcome identity is empty, null, or duplicated")
    return outcomes.sort_values(HITTER_KEY).reset_index(drop=True), records


def load_lineup_snapshots(
    *, evidence_root: Path, model_manifest: dict[str, Any], dates: list[str]
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    source = model_manifest.get("feature_snapshots") or {}
    if sorted(source) != dates:
        raise ValueError("production feature-snapshot date universe changed")
    rows: list[dict[str, int | str]] = []
    records: list[dict[str, Any]] = []
    for game_date in dates:
        record = source[game_date]
        bundle_path = Path(str(record.get("bundle_path", "")))
        manifest_path = Path(str(record.get("manifest_path", "")))
        if not bundle_path.is_absolute():
            bundle_path = evidence_root / bundle_path
        if not manifest_path.is_absolute():
            manifest_path = evidence_root / manifest_path
        if sha256(bundle_path) != record.get("bundle_sha256") or sha256(manifest_path) != record.get("manifest_sha256"):
            raise ValueError(f"production feature snapshot hash mismatch: {game_date}")
        bundles = json.loads(bundle_path.read_text(encoding="utf-8"))
        if not isinstance(bundles, list) or len(bundles) != int(record.get("bundle_count", -1)):
            raise ValueError(f"production feature bundle count mismatch: {game_date}")
        for bundle in bundles:
            hitter = bundle.get("hitter") or {}
            player = hitter.get("player") or {}
            game = hitter.get("game") or {}
            slot = int(hitter.get("lineup_slot") or 0)
            row = {
                "mlb_game_pk": int(game.get("game_pk")),
                "player_id": int(player.get("mlb_id")),
                "game_date": str(game.get("game_date")),
                "lineup_slot": slot,
            }
            if row["game_date"] != game_date or slot not in range(1, 10):
                raise ValueError(f"invalid production lineup identity or slot: {game_date}")
            rows.append(row)
        records.append({
            "game_date": game_date,
            "bundle_path": relative(evidence_root, bundle_path),
            "bundle_sha256": sha256(bundle_path),
            "manifest_path": relative(evidence_root, manifest_path),
            "manifest_sha256": sha256(manifest_path),
            "bundle_count": len(bundles),
        })
    lineups = pd.DataFrame(rows, columns=LINEUP_COLUMNS)
    if lineups.empty or lineups[HITTER_KEY].isna().any().any() or lineups.duplicated(HITTER_KEY).any():
        raise ValueError("lineup snapshot identity is empty, null, or duplicated")
    return lineups.sort_values(HITTER_KEY).reset_index(drop=True), records


def crosscheck_certified_hitter_outcomes(
    official: pd.DataFrame, reconstructed: pd.DataFrame, model: pd.DataFrame
) -> dict[str, int]:
    model_hitters = model[model["category"].isin(["hits", "home_runs"])][HITTER_KEY].drop_duplicates()
    gradeable = model_hitters.merge(official[official["pa"] > 0], on=HITTER_KEY, how="inner", validate="one_to_one")
    if gradeable.empty:
        raise ValueError("no official participating production hitters")
    expected = reconstructed[reconstructed["category"].isin(["hits", "home_runs"])].copy()
    expected["actual_value"] = pd.to_numeric(expected["actual_value"], errors="raise").astype(int)
    wide = expected.pivot(index=HITTER_KEY, columns="category", values="actual_value").reset_index()
    checked = gradeable.merge(wide, on=HITTER_KEY, how="left", validate="one_to_one", suffixes=("_official", "_reconstructed"))
    if checked[["hits_reconstructed", "home_runs_reconstructed"]].isna().any().any():
        raise ValueError("certified reconstruction lacks an official participating hitter")
    if not (checked["hits_official"] == checked["hits_reconstructed"]).all():
        raise ValueError("cached official Hits disagree with certified reconstruction")
    if not (checked["home_runs_official"] == checked["home_runs_reconstructed"]).all():
        raise ValueError("cached official HR disagree with certified reconstruction")
    return {
        "production_hitter_keys": len(model_hitters),
        "official_participating_production_hitter_keys": len(gradeable),
        "hits_crosschecked": len(checked),
        "home_runs_crosschecked": len(checked),
    }


def validate_source_manifest(payload: dict[str, Any], *, evidence_root: str | Path) -> dict[str, Any]:
    if payload.get("schema_version") != SCHEMA or payload.get("status") != STATUS:
        raise ValueError("unrecognized open-2026 benchmark source manifest")
    if payload.get("betting_authorized") is not False or payload.get("may_2026_opened") is not False:
        raise ValueError("source manifest opened May or authorized betting")
    dates = require_open_dates(payload.get("dates") or [])
    if payload.get("months") != sorted(ALLOWED_MONTHS):
        raise ValueError("open-month contract changed")
    root = Path(evidence_root)
    resolved: dict[str, Path] = {}
    for name in ("production_probabilities", "production_manifest", "production_certificate",
                 "certified_reconstruction_outcomes", "cumulative_history", "cumulative_history_certificate",
                 "official_training_outcomes", "canonical_training_certificate", "canonical_selection_protocol",
                 "pa_distribution", "official_outcomes", "lineup_snapshots"):
        record = (payload.get("artifacts") or {}).get(name) or {}
        path = root / str(record.get("path", ""))
        if not path.is_file() or sha256(path) != record.get("sha256"):
            raise ValueError(f"source artifact missing or hash-mismatched: {name}")
        resolved[name] = path
    feeds = payload.get("official_mlb_feeds")
    snapshots = payload.get("feature_snapshots")
    if not isinstance(feeds, list) or not feeds or not isinstance(snapshots, list) or len(snapshots) != len(dates):
        raise ValueError("official feed or feature-snapshot inventory incomplete")
    if sorted({item.get("game_date") for item in feeds}) != dates:
        raise ValueError("official feed dates differ from locked dates")
    if sorted(item.get("game_date") for item in snapshots) != dates:
        raise ValueError("feature-snapshot dates differ from locked dates")
    for item in [*feeds, *snapshots]:
        for path_key, hash_key in (("path", "sha256"), ("bundle_path", "bundle_sha256"), ("manifest_path", "manifest_sha256")):
            if path_key not in item:
                continue
            path = root / str(item[path_key])
            if not path.is_file() or sha256(path) != item.get(hash_key):
                raise ValueError(f"bound source file missing or hash-mismatched: {path_key}")
    if len({int(item.get("mlb_game_pk", -1)) for item in feeds}) != len(feeds):
        raise ValueError("official MLB feed inventory contains duplicate game keys")
    if payload.get("official_feed_count") != len(feeds):
        raise ValueError("official MLB feed count changed")

    model = pd.read_csv(resolved["production_probabilities"])
    if validate_probability_artifact(model) != dates:
        raise ValueError("production date universe differs from source manifest")
    official = pd.read_csv(resolved["official_outcomes"])
    lineups = pd.read_csv(resolved["lineup_snapshots"])
    if list(official.columns) != OUTCOME_COLUMNS or len(official) != payload.get("official_outcome_rows"):
        raise ValueError("official outcome artifact schema or row count changed")
    if list(lineups.columns) != LINEUP_COLUMNS or len(lineups) != payload.get("lineup_snapshot_rows"):
        raise ValueError("lineup snapshot artifact schema or row count changed")
    if official[HITTER_KEY].isna().any().any() or official.duplicated(HITTER_KEY).any():
        raise ValueError("official outcome artifact key is null or duplicated")
    if lineups[HITTER_KEY].isna().any().any() or lineups.duplicated(HITTER_KEY).any():
        raise ValueError("lineup snapshot artifact key is null or duplicated")
    if require_open_dates(official["game_date"]) != dates or require_open_dates(lineups["game_date"]) != dates:
        raise ValueError("official outcome or lineup dates differ from source manifest")
    classes = official[["strikeouts", "walks", "singles", "doubles", "triples", "home_runs", "bip_out", "other_non_ab"]].sum(axis=1)
    if not (classes == official["pa"]).all():
        raise ValueError("official PA class accounting changed")
    expected_tb = official["singles"] + 2 * official["doubles"] + 3 * official["triples"] + 4 * official["home_runs"]
    if not (expected_tb == official["total_bases"]).all():
        raise ValueError("official Total Bases derivation changed")
    hitter_keys = model[model["category"].isin(["hits", "home_runs"])][HITTER_KEY].drop_duplicates()
    joined = hitter_keys.merge(lineups, on=HITTER_KEY, how="left", validate="one_to_one")
    if joined["lineup_slot"].isna().any() or len(joined) != len(hitter_keys):
        raise ValueError("lineup snapshot no longer covers production hitter universe")
    reconstructed = pd.read_csv(resolved["certified_reconstruction_outcomes"])
    if crosscheck_certified_hitter_outcomes(official, reconstructed, model) != payload.get("crosscheck"):
        raise ValueError("official cached-feed crosscheck changed")
    boundary = payload.get("historical_outcome_boundary") or {}
    if boundary != {
        "maximum_rows_read": 87462,
        "loaded_rows": 87462,
        "loaded_seasons": [2023, 2024],
        "date_min": "2023-03-30",
        "date_max": "2024-09-30",
        "forbid_loaded_year_at_or_after": 2025,
    }:
        raise ValueError("historical outcome read boundary changed")
    training = pd.read_csv(resolved["official_training_outcomes"], nrows=87462, compression="gzip")
    required_training = {"season", "game_date", "player_id", "out_pa", "out_ab", "out_hits",
                         "out_doubles", "out_triples", "out_hr", "out_bb", "out_k"}
    if len(training) != 87462 or not required_training.issubset(training.columns):
        raise ValueError("bounded official training outcome schema or row count changed")
    loaded_seasons = sorted(pd.to_numeric(training["season"], errors="raise").astype(int).unique())
    if loaded_seasons != [2023, 2024] or int(max(loaded_seasons)) >= 2025:
        raise ValueError("bounded official training outcomes admitted confirmation data")
    loaded_dates = training["game_date"].astype(str)
    if loaded_dates.min() != "2023-03-30" or loaded_dates.max() != "2024-09-30":
        raise ValueError("bounded official training outcome dates changed")

    repo_root = Path(__file__).resolve().parents[2]
    expected_runtime = {
        "module": "src/evaluation/open_2026_benchmark_sources.py",
        "builder": "scripts/build_open_2026_benchmark_sources.py",
    }
    runtime = payload.get("runtime") or {}
    if set(runtime) != set(expected_runtime):
        raise ValueError("source-builder runtime inventory changed")
    for name, rel in expected_runtime.items():
        record = runtime.get(name) or {}
        path = repo_root / rel
        if record.get("path") != rel or not path.is_file() or sha256(path) != record.get("sha256"):
            raise ValueError(f"source-builder runtime changed: {name}")
    if payload.get("total_bases_production_comparator_available") is not False:
        raise ValueError("Total Bases production comparator was invented")
    return payload
