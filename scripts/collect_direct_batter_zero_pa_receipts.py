#!/usr/bin/env python3
"""Collect official MLB receipts for 2023 target rows with zero plate appearances.

This is historical source repair, not prospective evidence.  It writes raw
responses and a derived identity table only after every requested player/game
has passed final-game, date, game-type, player-identity, and zero-stat checks.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
OFFICIAL_COLUMNS = [
    "season", "game_date", "game_pk", "player_id", "out_pa", "out_ab",
    "out_hits", "out_doubles", "out_triples", "out_hr", "out_bb", "out_k",
]
ZERO_STAT_FIELDS = (
    "plateAppearances", "atBats", "hits", "doubles", "triples", "homeRuns",
    "baseOnBalls", "strikeOuts",
)
SOURCE_URL = "https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live"


class ZeroPAReceiptError(ValueError):
    """An official receipt failed identity, finality, or zero-PA validation."""


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _integer(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise ZeroPAReceiptError(f"{label} is not an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ZeroPAReceiptError(f"{label} is not an integer") from exc
    if str(value).strip() not in {str(parsed), f"{parsed}.0"}:
        raise ZeroPAReceiptError(f"{label} is not canonical integer data")
    return parsed


def verify_zero_pa_receipt(payload: Mapping[str, Any], target: Mapping[str, Any]) -> None:
    """Verify one player/game target against an official final MLB live feed."""
    game_pk = _integer(target.get("game_pk"), "target.game_pk")
    player_id = _integer(target.get("player_id"), "target.player_id")
    if _integer(payload.get("gamePk"), "receipt.gamePk") != game_pk:
        raise ZeroPAReceiptError("receipt game identity mismatch")
    game_data = payload.get("gameData")
    live_data = payload.get("liveData")
    if not isinstance(game_data, Mapping) or not isinstance(live_data, Mapping):
        raise ZeroPAReceiptError("receipt lacks gameData or liveData")
    official_date = ((game_data.get("datetime") or {}).get("officialDate"))
    if official_date != target.get("game_date"):
        raise ZeroPAReceiptError("receipt official date mismatch")
    if (game_data.get("game") or {}).get("type") != "R":
        raise ZeroPAReceiptError("receipt is not a regular-season game")
    status = game_data.get("status") or {}
    if status.get("abstractGameState") != "Final":
        raise ZeroPAReceiptError("receipt game is not final")
    teams = ((live_data.get("boxscore") or {}).get("teams") or {})
    matches: list[Mapping[str, Any]] = []
    for side in ("away", "home"):
        players = ((teams.get(side) or {}).get("players") or {})
        if not isinstance(players, Mapping):
            raise ZeroPAReceiptError("receipt boxscore player collection is malformed")
        for player in players.values():
            if not isinstance(player, Mapping):
                continue
            person = player.get("person") or {}
            try:
                receipt_player_id = _integer(person.get("id"), "receipt.player.id")
            except ZeroPAReceiptError:
                continue
            if receipt_player_id == player_id:
                matches.append(player)
    if len(matches) != 1:
        raise ZeroPAReceiptError("receipt player identity is missing or ambiguous")
    batting = ((matches[0].get("stats") or {}).get("batting") or {})
    if not isinstance(batting, Mapping):
        raise ZeroPAReceiptError("receipt player batting statistics are malformed")
    for field in ZERO_STAT_FIELDS:
        if field not in batting or _integer(batting.get(field), f"receipt.batting.{field}") != 0:
            raise ZeroPAReceiptError(f"receipt batting field is not verified zero: {field}")


def load_zero_targets(official: Path, *, maximum_rows: int) -> pd.DataFrame:
    frame = pd.read_csv(
        official, usecols=OFFICIAL_COLUMNS, nrows=maximum_rows, low_memory=False,
    )
    if len(frame) != maximum_rows:
        raise ZeroPAReceiptError("official 2023 prefix row count changed")
    years = pd.to_numeric(frame["season"], errors="raise").astype(int)
    if set(years.unique()) != {2023}:
        raise ZeroPAReceiptError("zero-PA collection may read only the 2023 prefix")
    dates = pd.to_datetime(frame["game_date"], format="%Y-%m-%d", errors="coerce")
    if dates.isna().any() or not dates.dt.year.eq(years).all():
        raise ZeroPAReceiptError("official 2023 identity dates are invalid")
    numeric = frame[[
        "out_pa", "out_ab", "out_hits", "out_doubles", "out_triples",
        "out_hr", "out_bb", "out_k",
    ]].apply(pd.to_numeric, errors="raise")
    zero = frame.loc[numeric["out_pa"].eq(0)].copy()
    if zero.empty:
        raise ZeroPAReceiptError("official 2023 prefix has no zero-PA targets")
    if not numeric.loc[zero.index].eq(0).all().all():
        raise ZeroPAReceiptError("zero-PA target has contradictory nonzero outcomes")
    identity = ["season", "game_date", "game_pk", "player_id"]
    if zero.duplicated(identity).any():
        raise ZeroPAReceiptError("zero-PA target identity is duplicated")
    zero["season"] = 2023
    zero["game_pk"] = pd.to_numeric(zero["game_pk"], errors="raise").astype(int)
    zero["player_id"] = pd.to_numeric(zero["player_id"], errors="raise").astype(int)
    return zero.loc[:, identity].sort_values(identity, kind="stable").reset_index(drop=True)


def fetch_receipt(game_pk: int) -> tuple[bytes, str]:
    url = SOURCE_URL.format(game_pk=game_pk)
    request = urllib.request.Request(
        url, headers={"User-Agent": "baseball-predictor-research-receipt/1.0"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        if response.status != 200:
            raise ZeroPAReceiptError(f"MLB receipt HTTP status {response.status}")
        payload = response.read()
    return payload, url


def collect(*, official: Path, output_dir: Path, maximum_rows: int) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite zero-PA receipt directory: {output_dir}")
    targets = load_zero_targets(official, maximum_rows=maximum_rows)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent))
    try:
        raw_dir = staging / "raw"
        raw_dir.mkdir()
        observed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        receipts: dict[int, tuple[str, str]] = {}
        for game_pk in sorted(targets["game_pk"].unique()):
            raw, url = fetch_receipt(int(game_pk))
            try:
                decoded = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ZeroPAReceiptError(f"MLB receipt is invalid JSON: {game_pk}") from exc
            game_targets = targets.loc[targets["game_pk"].eq(game_pk)]
            for target in game_targets.to_dict("records"):
                verify_zero_pa_receipt(decoded, target)
            receipt_path = raw_dir / f"game_{int(game_pk)}.json"
            receipt_path.write_bytes(raw)
            receipts[int(game_pk)] = (sha256_bytes(raw), url)
        evidence_path = staging / "zero_pa_evidence.csv"
        with evidence_path.open("w", encoding="utf-8", newline="") as handle:
            fields = [
                "season", "game_date", "game_pk", "player_id", "verified_zero_pa",
                "source_kind", "source_sha256", "source_url", "observed_at_utc",
                "parser_version",
            ]
            writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            for target in targets.to_dict("records"):
                receipt_hash, url = receipts[int(target["game_pk"])]
                writer.writerow({
                    **target,
                    "verified_zero_pa": True,
                    "source_kind": "official_final_boxscore_zero_pa",
                    "source_sha256": receipt_hash,
                    "source_url": url,
                    "observed_at_utc": observed_at,
                    "parser_version": "direct-batter-zero-pa-receipt-v1",
                })
        manifest = {
            "schema_version": "direct-batter-zero-pa-receipt-manifest-v1",
            "status": "HISTORICAL_2023_SOURCE_REPAIR_ONLY",
            "official_source": {
                "path": str(official), "sha256": sha256_file(official),
                "maximum_rows_read": maximum_rows,
            },
            "evidence": {
                "path": "zero_pa_evidence.csv", "sha256": sha256_file(evidence_path),
                "rows": len(targets),
            },
            "receipts": {
                f"raw/game_{game_pk}.json": receipt_hash
                for game_pk, (receipt_hash, _) in sorted(receipts.items())
            },
            "observed_at_utc": observed_at,
            "script_sha256": sha256_file(Path(__file__)),
            "may_2026_opened": False,
            "prospective_evidence_claimed": False,
            "betting_authorized": False,
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8",
        )
        os.replace(staging, output_dir)
        return manifest
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--official", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--maximum-rows", type=int, default=43740)
    args = parser.parse_args()
    result = collect(**vars(args))
    print(json.dumps({"status": result["status"], "evidence": result["evidence"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
