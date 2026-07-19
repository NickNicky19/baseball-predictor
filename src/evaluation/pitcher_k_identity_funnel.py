"""Outcome-blind hard identity for historical pitcher-strikeout price paths."""
from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd


SCHEMA = "pitcher-k-identity-funnel-protocol-v1"
STATUS = "LOCKED_BEFORE_PITCHER_K_IDENTITY_BUILD"
EXPECTED_PARENT_HASH = "de131cc11efb34f95f92633a656de91e8369422856dc23e4193283dd4fd44151"
EXPECTED_SOURCE_MANIFEST_HASH = "ff5d00282b452fa848f76bfc4f5a5ac23ed48eb627c861c26d3420180c288baa"
EXPECTED_RAW_HASHES = [
    "c1058237807fbd39306a3b5091460cf4c02e63948e457e3c0a2a3b1aa4e0fba3",
    "d3eb5e15fa0309ea5b1e698daf40d32d9f6376b0600eb60a07451a12668ceebd",
    "af9938e78c3bf9ee5cda96cd646da7671e547bfbf1763f3c14c8ef53bdd97aad",
    "aab964eb77b573a3f1552ae89b0e9447fd9bb8b342370d2578f51c3ebabfa2b4",
    "e96441885d057603d4b88ede94e676a3d4cbe56e720b370a3d094e59327f4b92",
    "69b645f171c5824ae8f2f5cc6a24b652a7436d7d6a805b0b0b70310080c33188",
    "616dccd4e06c3138942236eed2531ade8db7d13c93f5f6babca504be40aba7d6",
    "dc9c09351caf0976bbc46990713a8c37c62ed14a42fdea71202fb9ada827bb74",
    "1b5db24a71e7a237f2f76bab0bbb619602b0693cac801766b13e2ac90562c37c",
    "c9e1138e9b4776fbe635b4bde94fa6bf01ea09e3a35f553fa706b2e3d545ebd1",
]
EXPECTED_STATES = [
    "mapped_unique", "conflicting_duplicate", "event_unmapped",
    "event_ambiguous", "pitcher_unmapped", "pitcher_ambiguous", "malformed",
]
PATH_KEY = ["vendor_game_id", "start_time", "player", "line"]
MARKET_KEY = ["mlb_game_pk", "player_id", "category", "line"]


def sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalize_name(value: object) -> str:
    text = "".join(
        character
        for character in unicodedata.normalize("NFKD", str(value))
        if not unicodedata.combining(character)
    ).lower().strip()
    for character in ".'`-":
        text = text.replace(character, "")
    return " ".join(
        token for token in text.split()
        if token not in {"jr", "sr", "ii", "iii", "iv", "v"}
    )


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def unwrap_official_identity_feed(
    wrapper: dict[str, Any], record: dict[str, Any], *, source: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate the cached request and expose only the official identity branch."""
    expected_game_pk = int(record.get("mlb_game_pk"))
    expected_url = f"https://statsapi.mlb.com/api/v1.1/game/{expected_game_pk}/feed/live"
    if wrapper.get("url") != expected_url or wrapper.get("params") not in ({}, None):
        raise ValueError(f"official cached request identity disagrees with manifest: {source}")
    payload = wrapper.get("data")
    if not isinstance(payload, dict):
        raise ValueError(f"official cached response lacks a JSON data object: {source}")
    game_data = payload.get("gameData")
    if not isinstance(game_data, dict):
        raise ValueError(f"official feed lacks gameData: {source}")
    if int(payload.get("gamePk", -1)) != expected_game_pk:
        raise ValueError(f"official cached response game identity disagrees with manifest: {source}")
    return payload, game_data


def validate_protocol_structure(payload: dict[str, Any]) -> None:
    if payload.get("schema_version") != SCHEMA or payload.get("status") != STATUS:
        raise ValueError("unrecognized pitcher-K identity protocol")
    if payload.get("betting_authorized") is not False or payload.get("production_unchanged") is not True:
        raise ValueError("protocol changed production or authorized betting")
    if payload.get("may_2026_opened") is not False:
        raise ValueError("protocol opened May")
    parent = payload.get("certified_parent") or {}
    if parent != {
        "path": "reports/multi_market_probability_readiness_2026-07-19.json",
        "sha256": EXPECTED_PARENT_HASH,
        "required_status": "READINESS_UPDATED_AFTER_HITS_1_5_REJECTION",
    }:
        raise ValueError("certified parent binding changed")
    inputs = payload.get("inputs") or {}
    official = inputs.get("official_source_manifest") or {}
    if official != {
        "path": "data/analysis/shared_pa_foundation_v1/open_2026_benchmark_v4/source_manifest.json",
        "sha256": EXPECTED_SOURCE_MANIFEST_HASH,
        "required_status": "SOURCES_BOUND_BEFORE_BENCHMARK_SCORING_V2",
        "official_feed_count": 710,
    }:
        raise ValueError("official identity-source binding changed")
    raw = inputs.get("market_parquets") or []
    if len(raw) != 10 or [item.get("sha256") for item in raw] != EXPECTED_RAW_HASHES:
        raise ValueError("raw market bindings changed")
    paths = [str(item.get("path", "")) for item in raw]
    if len(paths) != len(set(paths)) or any("2026-05" in path or "mon=2026-05" in path for path in paths):
        raise ValueError("raw paths are duplicated or include May")
    chronology = payload.get("chronology") or {}
    if chronology != {
        "months": ["2026-03", "2026-04", "2026-06"],
        "allowed_dates_source": "official_source_manifest.dates",
        "expected_dates": 56,
        "forbidden": ["2026-05"],
        "timezone_for_vendor_slate_date": "America/New_York",
    }:
        raise ValueError("chronology changed")
    market = payload.get("market_contract") or {}
    if market != {
        "book": "draftkings",
        "vendor_market": "player strikeouts",
        "category": "strikeouts",
        "sides": ["over", "under"],
        "entry_horizon_hours": 4,
        "entry_observation": "last quote per side at_or_before start_time_minus_4_hours",
        "close_observation": "last quote per side strictly_before start_time",
        "complete_path_count": 1948,
        "freshness_filter": None,
        "vendor_result_columns_forbidden": ["result", "won"],
        "historical_executability_verified": False,
    }:
        raise ValueError("market contract changed")
    identity = payload.get("identity_contract") or {}
    if (
        identity.get("event_rule")
        != "at least one exact normalized official-name match and every matched name resolves to the same singleton official game on the vendor Eastern slate date"
        or identity.get("event_arbitration") != "none"
        or identity.get("player_rule")
        != "pitcher name resolves to exactly one official player_id inside the hard-mapped game"
        or identity.get("fuzzy_matching") is not False
        or identity.get("vote_threshold") is not None
        or identity.get("time_tolerance") is not None
        or identity.get("market_key") != MARKET_KEY
        or identity.get("duplicate_rule")
        != "exclude every row belonging to any duplicated final market_key; never choose a winner"
    ):
        raise ValueError("identity contract changed or weakened")
    if payload.get("terminal_states") != EXPECTED_STATES:
        raise ValueError("terminal states changed")
    protected = payload.get("protected_invariants") or {}
    if len(protected) != 12 or not all(value is True for value in protected.values()):
        raise ValueError("protected invariant changed or weakened")


def load_protocol(path: str | Path, *, code_root: str | Path, evidence_root: str | Path) -> dict[str, Any]:
    payload = _read_json(Path(path))
    validate_protocol_structure(payload)
    code_root = Path(code_root)
    evidence_root = Path(evidence_root)
    parent = code_root / payload["certified_parent"]["path"]
    if not parent.is_file() or sha256(parent) != EXPECTED_PARENT_HASH:
        raise ValueError("certified readiness parent is missing or hash-mismatched")
    if _read_json(parent).get("status") != payload["certified_parent"]["required_status"]:
        raise ValueError("certified readiness parent status changed")
    official_record = payload["inputs"]["official_source_manifest"]
    official_path = evidence_root / official_record["path"]
    if not official_path.is_file() or sha256(official_path) != EXPECTED_SOURCE_MANIFEST_HASH:
        raise ValueError("official source manifest is missing or hash-mismatched")
    manifest = _read_json(official_path)
    if manifest.get("status") != official_record["required_status"]:
        raise ValueError("official source manifest status changed")
    dates = list(manifest.get("dates") or [])
    if len(dates) != 56 or dates != sorted(set(dates)) or any(date.startswith("2026-05") for date in dates):
        raise ValueError("official source date universe changed or includes May")
    feeds = list(manifest.get("official_mlb_feeds") or [])
    if len(feeds) != official_record["official_feed_count"]:
        raise ValueError("official feed count changed")
    for record in payload["inputs"]["market_parquets"]:
        source = evidence_root / record["path"]
        if not source.is_file() or sha256(source) != record["sha256"]:
            raise ValueError(f"raw market source missing or hash-mismatched: {record['path']}")
    return payload


def _sql_paths(paths: list[Path]) -> str:
    return "[" + ",".join("'" + str(path).replace("'", "''") + "'" for path in paths) + "]"


def read_complete_paths(paths: list[Path], protocol: dict[str, Any]) -> pd.DataFrame:
    source = _sql_paths(paths)
    hours = int(protocol["market_contract"]["entry_horizon_hours"])
    query = f"""
        WITH raw AS (
          SELECT CAST(game_id AS VARCHAR) AS vendor_game_id, start_time,
                 CAST(player AS VARCHAR) AS player, CAST(line AS DOUBLE) AS line,
                 lower(CAST(side AS VARCHAR)) AS side, ts, CAST(odds AS DOUBLE) AS odds
          FROM read_parquet({source}, union_by_name=true)
          WHERE lower(CAST(book AS VARCHAR)) = 'draftkings'
            AND lower(CAST(market AS VARCHAR)) = 'player strikeouts'
        ),
        pre AS (
          SELECT * FROM raw
          WHERE ts < start_time AND side IN ('over', 'under')
        ),
        selected AS (
          SELECT vendor_game_id, start_time, player, line,
                 max(ts) FILTER (WHERE side='over' AND ts <= start_time - INTERVAL {hours} HOUR) AS entry_over_time,
                 max(ts) FILTER (WHERE side='under' AND ts <= start_time - INTERVAL {hours} HOUR) AS entry_under_time,
                 max(ts) FILTER (WHERE side='over') AS close_over_time,
                 max(ts) FILTER (WHERE side='under') AS close_under_time
          FROM pre GROUP BY 1,2,3,4
        )
        SELECT s.*,
               count(DISTINCT p.odds) FILTER (WHERE p.side='over' AND p.ts=s.entry_over_time) AS entry_over_price_count,
               min(p.odds) FILTER (WHERE p.side='over' AND p.ts=s.entry_over_time) AS entry_over_odds,
               count(DISTINCT p.odds) FILTER (WHERE p.side='under' AND p.ts=s.entry_under_time) AS entry_under_price_count,
               min(p.odds) FILTER (WHERE p.side='under' AND p.ts=s.entry_under_time) AS entry_under_odds,
               count(DISTINCT p.odds) FILTER (WHERE p.side='over' AND p.ts=s.close_over_time) AS close_over_price_count,
               min(p.odds) FILTER (WHERE p.side='over' AND p.ts=s.close_over_time) AS close_over_odds,
               count(DISTINCT p.odds) FILTER (WHERE p.side='under' AND p.ts=s.close_under_time) AS close_under_price_count,
               min(p.odds) FILTER (WHERE p.side='under' AND p.ts=s.close_under_time) AS close_under_odds
        FROM selected s
        JOIN pre p USING (vendor_game_id, start_time, player, line)
        WHERE s.entry_over_time IS NOT NULL AND s.entry_under_time IS NOT NULL
          AND s.close_over_time IS NOT NULL AND s.close_under_time IS NOT NULL
        GROUP BY ALL
        ORDER BY vendor_game_id, start_time, player, line
    """
    frame = duckdb.sql(query).df()
    if len(frame) != protocol["market_contract"]["complete_path_count"]:
        raise ValueError(f"complete-path denominator changed: {len(frame)}")
    if frame[PATH_KEY].isna().any().any() or frame.duplicated(PATH_KEY).any():
        raise ValueError("complete price-path identity is null or duplicated")
    return frame


def read_fragment_names(paths: list[Path]) -> pd.DataFrame:
    source = _sql_paths(paths)
    query = f"""
        SELECT CAST(game_id AS VARCHAR) AS vendor_game_id, start_time,
               list_sort(list(DISTINCT CAST(player AS VARCHAR))) AS players
        FROM read_parquet({source}, union_by_name=true)
        WHERE ts < start_time AND player IS NOT NULL
        GROUP BY 1,2 ORDER BY 1,2
    """
    return duckdb.sql(query).df()


def build_official_index(
    source_manifest_path: Path,
    evidence_root: Path,
) -> tuple[dict[str, dict[str, set[int]]], dict[int, dict[str, set[int]]], dict[int, str], int]:
    manifest = _read_json(source_manifest_path)
    date_name_games: dict[str, dict[str, set[int]]] = defaultdict(lambda: defaultdict(set))
    game_name_ids: dict[int, dict[str, set[int]]] = defaultdict(lambda: defaultdict(set))
    game_dates: dict[int, str] = {}
    feeds = list(manifest.get("official_mlb_feeds") or [])
    for record in feeds:
        path = evidence_root / str(record.get("path", ""))
        if not path.is_file() or sha256(path) != record.get("sha256"):
            raise ValueError(f"official feed missing or hash-mismatched: {record.get('path')}")
        wrapper = _read_json(path)
        payload, game_data = unwrap_official_identity_feed(wrapper, record, source=path)
        expected_game_pk = int(record.get("mlb_game_pk"))
        game_pk = int(payload.get("gamePk"))
        date = str((game_data.get("datetime") or {}).get("officialDate", ""))
        if game_pk != expected_game_pk or date != str(record.get("game_date")):
            raise ValueError(f"official feed identity disagrees with manifest: {path}")
        if date.startswith("2026-05") or date not in set(manifest["dates"]):
            raise ValueError("forbidden or unbound official date entered identity index")
        previous = game_dates.setdefault(game_pk, date)
        if previous != date:
            raise ValueError("one official game_pk has multiple official dates")
        players = game_data.get("players") or {}
        if not isinstance(players, dict):
            raise ValueError(f"official gameData.players is malformed: {path}")
        for value in players.values():
            if not isinstance(value, dict):
                continue
            person_id = value.get("id")
            full_name = normalize_name(value.get("fullName", ""))
            if not full_name or person_id is None:
                continue
            player_id = int(person_id)
            date_name_games[date][full_name].add(game_pk)
            game_name_ids[game_pk][full_name].add(player_id)
    if len(feeds) != 710 or len(game_dates) != 710:
        raise ValueError("official feed/game identity count changed")
    return date_name_games, game_name_ids, game_dates, len(feeds)


def map_fragments(
    fragments: pd.DataFrame,
    date_name_games: dict[str, dict[str, set[int]]],
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for row in fragments.itertuples(index=False):
        timestamp = pd.to_datetime(row.start_time, utc=True, errors="coerce")
        names = sorted({normalize_name(value) for value in list(row.players) if normalize_name(value)})
        if pd.isna(timestamp):
            rows.append({
                "vendor_game_id": row.vendor_game_id, "start_time": row.start_time,
                "slate_date": None, "fragment_state": "malformed", "mlb_game_pk": np.nan,
                "vendor_name_count": len(names), "matched_name_count": 0,
                "unmatched_name_count": len(names), "matched_game_pks": "[]",
            })
            continue
        date = timestamp.tz_convert("America/New_York").strftime("%Y-%m-%d")
        matched_sets = [set(date_name_games.get(date, {}).get(name, set())) for name in names]
        resolved = [games for games in matched_sets if games]
        singleton_games = {next(iter(games)) for games in resolved if len(games) == 1}
        ambiguous_name = any(len(games) != 1 for games in resolved)
        if not resolved:
            state, game_pk = "event_unmapped", np.nan
        elif ambiguous_name or len(singleton_games) != 1:
            state, game_pk = "event_ambiguous", np.nan
        else:
            state, game_pk = "mapped", int(next(iter(singleton_games)))
        all_candidates = sorted({game for games in resolved for game in games})
        rows.append({
            "vendor_game_id": row.vendor_game_id, "start_time": row.start_time,
            "slate_date": date, "fragment_state": state, "mlb_game_pk": game_pk,
            "vendor_name_count": len(names), "matched_name_count": len(resolved),
            "unmatched_name_count": len(names) - len(resolved),
            "matched_game_pks": json.dumps(all_candidates, separators=(",", ":")),
        })
    result = pd.DataFrame(rows)
    if result[["vendor_game_id", "start_time"]].isna().any().any() or result.duplicated(["vendor_game_id", "start_time"]).any():
        raise ValueError("fragment identity rows are null or duplicated")
    result["mlb_game_pk"] = pd.array(result["mlb_game_pk"], dtype="Int64")
    return result


def assign_terminal_states(
    paths: pd.DataFrame,
    fragment_identity: pd.DataFrame,
    game_name_ids: dict[int, dict[str, set[int]]],
) -> pd.DataFrame:
    result = paths.merge(
        fragment_identity,
        on=["vendor_game_id", "start_time"], how="left", validate="many_to_one", indicator=True,
    )
    if not result["_merge"].eq("both").all():
        raise ValueError("a complete path silently lost its fragment identity")
    result = result.drop(columns="_merge")
    result["category"] = "strikeouts"
    result["player_key"] = result["player"].map(normalize_name)
    result["player_id"] = pd.Series(pd.NA, index=result.index, dtype="Int64")
    result["terminal_state"] = result["fragment_state"].replace({"mapped": "pitcher_unmapped"})
    price_count_columns = [
        "entry_over_price_count", "entry_under_price_count",
        "close_over_price_count", "close_under_price_count",
    ]
    odds_columns = ["entry_over_odds", "entry_under_odds", "close_over_odds", "close_under_odds"]
    malformed = (
        result["player_key"].eq("")
        | ~pd.to_numeric(result["line"], errors="coerce").map(lambda value: math.isfinite(value) and value > 0)
        | result[price_count_columns].ne(1).any(axis=1)
        | result[odds_columns].apply(pd.to_numeric, errors="coerce").le(1).any(axis=1)
        | result[odds_columns].apply(pd.to_numeric, errors="coerce").isna().any(axis=1)
    )
    result.loc[malformed, "terminal_state"] = "malformed"
    for index in result.index[result["fragment_state"].eq("mapped") & ~malformed]:
        game_pk = int(result.at[index, "mlb_game_pk"])
        ids = set(game_name_ids.get(game_pk, {}).get(result.at[index, "player_key"], set()))
        if len(ids) == 1:
            result.at[index, "player_id"] = int(next(iter(ids)))
            result.at[index, "terminal_state"] = "mapped_unique"
        elif len(ids) > 1:
            result.at[index, "terminal_state"] = "pitcher_ambiguous"
        else:
            result.at[index, "terminal_state"] = "pitcher_unmapped"
    candidates = result["terminal_state"].eq("mapped_unique")
    duplicated = result.loc[candidates].duplicated(MARKET_KEY, keep=False)
    duplicate_indices = result.loc[candidates].index[duplicated]
    result.loc[duplicate_indices, "terminal_state"] = "conflicting_duplicate"
    validate_terminal_ledger(result, expected_count=len(paths))
    return result.sort_values(PATH_KEY).reset_index(drop=True)


def validate_terminal_ledger(result: pd.DataFrame, *, expected_count: int) -> None:
    if len(result) != expected_count or result[PATH_KEY].isna().any().any() or result.duplicated(PATH_KEY).any():
        raise ValueError("terminal ledger changed or duplicated the path denominator")
    if result["terminal_state"].isna().any() or not set(result["terminal_state"]).issubset(EXPECTED_STATES):
        raise ValueError("a path lacks exactly one predeclared terminal state")
    mapped = result[result["terminal_state"].eq("mapped_unique")]
    if mapped[MARKET_KEY].isna().any().any() or mapped.duplicated(MARKET_KEY).any():
        raise ValueError("mapped_unique MARKET_KEY is null or duplicated")
    conflicts = result[result["terminal_state"].eq("conflicting_duplicate")]
    if len(conflicts) and not conflicts.duplicated(MARKET_KEY, keep=False).all():
        raise ValueError("a conflicting duplicate key was only partially excluded")


def build_funnel(protocol: dict[str, Any], *, evidence_root: Path) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    market_paths = [evidence_root / record["path"] for record in protocol["inputs"]["market_parquets"]]
    paths = read_complete_paths(market_paths, protocol)
    fragments = read_fragment_names(market_paths)
    source_manifest_path = evidence_root / protocol["inputs"]["official_source_manifest"]["path"]
    date_name_games, game_name_ids, _, feed_count = build_official_index(source_manifest_path, evidence_root)
    fragment_identity = map_fragments(fragments, date_name_games)
    terminal = assign_terminal_states(paths, fragment_identity, game_name_ids)
    counts = {state: int(terminal["terminal_state"].eq(state).sum()) for state in EXPECTED_STATES}
    if sum(counts.values()) != protocol["market_contract"]["complete_path_count"]:
        raise ValueError("terminal-state funnel does not reconcile")
    summary = {
        "complete_price_paths": int(len(terminal)),
        "terminal_states": counts,
        "fragment_rows": int(len(fragment_identity)),
        "fragment_states": {str(k): int(v) for k, v in fragment_identity["fragment_state"].value_counts().sort_index().items()},
        "official_feed_count": int(feed_count),
        "mapped_unique_market_keys": int(terminal["terminal_state"].eq("mapped_unique").sum()),
        "all_paths_reconciled": True,
    }
    return terminal, fragment_identity, summary


def _bound_path(root: Path, relative_path: object) -> Path:
    candidate = (root / str(relative_path)).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("artifact path escapes its bound root") from exc
    return candidate


def validate_published_report(
    report_path: str | Path, *, code_root: str | Path, evidence_root: str | Path
) -> dict[str, Any]:
    code_root = Path(code_root)
    evidence_root = Path(evidence_root)
    report = _read_json(Path(report_path))
    if report.get("schema_version") != "pitcher-k-identity-funnel-report-v1":
        raise ValueError("published pitcher-K report schema changed")
    if report.get("status") != "OUTCOME_BLIND_IDENTITY_FUNNEL_COMPLETE":
        raise ValueError("published pitcher-K report is not complete")
    if (
        report.get("betting_authorized") is not False
        or report.get("production_unchanged") is not True
        or report.get("may_2026_opened") is not False
        or report.get("economic_evidence_eligible") is not False
    ):
        raise ValueError("published pitcher-K report weakened a protected scope")

    protocol_record = report.get("protocol") or {}
    protocol_path = _bound_path(code_root, protocol_record.get("path"))
    if not protocol_path.is_file() or sha256(protocol_path) != protocol_record.get("sha256"):
        raise ValueError("published pitcher-K protocol binding changed")
    protocol = load_protocol(
        protocol_path, code_root=code_root, evidence_root=evidence_root
    )
    if protocol_record.get("status") != protocol["status"]:
        raise ValueError("published pitcher-K protocol status changed")
    if report.get("source_bindings") != protocol["inputs"]:
        raise ValueError("published pitcher-K source bindings changed")
    if report.get("market_contract") != protocol["market_contract"]:
        raise ValueError("published pitcher-K market contract changed")
    if report.get("identity_contract") != protocol["identity_contract"]:
        raise ValueError("published pitcher-K identity contract changed")
    if report.get("protected_invariants") != protocol["protected_invariants"]:
        raise ValueError("published pitcher-K invariants changed")

    implementation = report.get("implementation") or {}
    for path_key, hash_key in (
        ("module_path", "module_sha256"),
        ("builder_path", "builder_sha256"),
    ):
        path = _bound_path(code_root, implementation.get(path_key))
        if not path.is_file() or sha256(path) != implementation.get(hash_key):
            raise ValueError(f"published pitcher-K implementation binding changed: {path_key}")

    artifacts = report.get("artifacts") or {}
    terminal_record = artifacts.get("terminal") or {}
    fragment_record = artifacts.get("fragments") or {}
    terminal_path = _bound_path(evidence_root, terminal_record.get("path"))
    fragment_path = _bound_path(evidence_root, fragment_record.get("path"))
    for path, record in ((terminal_path, terminal_record), (fragment_path, fragment_record)):
        if not path.is_file() or sha256(path) != record.get("sha256"):
            raise ValueError("published pitcher-K artifact binding changed")

    terminal = pd.read_csv(terminal_path)
    fragments = pd.read_csv(fragment_path)
    if len(terminal) != terminal_record.get("rows") or len(fragments) != fragment_record.get("rows"):
        raise ValueError("published pitcher-K artifact row count changed")
    forbidden = {"result", "won", "actual_value", "sim_p_over", "model_probability"}
    if forbidden.intersection(terminal.columns) or forbidden.intersection(fragments.columns):
        raise ValueError("forbidden outcome/model field entered published pitcher-K identity")
    validate_terminal_ledger(
        terminal, expected_count=protocol["market_contract"]["complete_path_count"]
    )
    if fragments[["vendor_game_id", "start_time"]].isna().any().any() or fragments.duplicated(
        ["vendor_game_id", "start_time"]
    ).any():
        raise ValueError("published pitcher-K fragment identity is null or duplicated")

    expected_summary = {
        "complete_price_paths": int(len(terminal)),
        "terminal_states": {
            state: int(terminal["terminal_state"].eq(state).sum()) for state in EXPECTED_STATES
        },
        "fragment_rows": int(len(fragments)),
        "fragment_states": {
            str(key): int(value)
            for key, value in fragments["fragment_state"].value_counts().sort_index().items()
        },
        "official_feed_count": 710,
        "mapped_unique_market_keys": int(terminal["terminal_state"].eq("mapped_unique").sum()),
        "all_paths_reconciled": True,
    }
    if report.get("summary") != expected_summary:
        raise ValueError("published pitcher-K summary disagrees with its artifacts")
    return {
        "report_sha256": sha256(report_path),
        "terminal_sha256": sha256(terminal_path),
        "fragments_sha256": sha256(fragment_path),
        **expected_summary,
    }
