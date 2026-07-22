#!/usr/bin/env python3
"""
Shadow compare -- model P(over) vs de-vigged market, READ-ONLY.

Joins a day's prediction file (data/learning/predictions/predictions_<date>.json,
the frozen-simulator output that runs live today) to a day's logged odds
(data/lines/lines_<date>.csv from run_log_lines.py), and prints, per game and
per player prop, the model's P(over threshold) beside the de-vigged DraftKings
probability, sorted by disagreement. It writes a dated NAME-KEYED watch CSV,
not a gradeable CLV record; only the hard-keyed forward ledger may be used for
capture evidence.

WHAT THIS IS -- and, more importantly, WHAT IT IS NOT:

  * It is a WATCH tool, not a bet-finder. A gap between model and market is a
    DISAGREEMENT, not an edge. Per PROJECT_CONTEXT: "a projection alone is
    never a play." Disagreements cluster exactly where the model's KNOWN
    biases live -- the hits over-projection (C1: +0.104) shows up as the model
    looking bullish on nearly every Over 0.5 hits line; the short-outing K
    over-projection (B4's ungated target) shows up as absurd K gaps on
    opener/short-relief arms. Seeing those is the tool working, not signal.

  * The probabilities are the FROZEN SIMULATOR's, straight from the prediction
    file -- NOT the calibrated CatBoost. CatBoost is the candidate; it is not
    the live predictor until C2's gate clears. So every row here is
    "frozen sim vs market," never "our best model vs market." The header says
    so on every run; do not let that distinction blur.

THRESHOLD CONVENTION: the market posts half-point lines (Over 0.5, Over 1.5);
the model's p_ge_threshold is keyed by integer counts (1.0, 2.0). A market
line L maps to the model key ceil(L) -- "Over 0.5 hits" == model P(>=1),
"Over 1.5" == P(>=2), "Over 4.5 K" == P(>=5). Same float(ceil(line))
convention the gate uses (see B3 key decision).

HONEST JOIN: matching is by normalized player name + category (the odds feed
carries no MLB player_id). Name matching silently drops mismatches, so this
tool ALWAYS reports how many market props matched, how many were dropped for
no model row, and -- with --show-dropped -- exactly who, so a name-alignment
gap can never masquerade as "those players had no line."

READ-ONLY: reads the two inputs, writes only the comparison CSV under --out.
Never touches config.json, never predicts, never forks model_version. Do not
upgrade this file into evidence with a later name join.

Usage:
    python run_shadow_compare.py --date 2026-07-10
    python run_shadow_compare.py --date 2026-07-10 --games "Astros@Rangers,Braves@Cardinals"
    python run_shadow_compare.py --date 2026-07-10 --min-gap 0.06 --show-dropped
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Optional


# --------------------------------------------------------------------------
# Name normalization + odds math (the two silent-failure-prone bits)
# --------------------------------------------------------------------------

_SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b")

def norm_name(name: str) -> str:
    """Lowercase, strip accents, drop punctuation and generational suffixes.

    'Fernando Tatis Jr.' -> 'fernando tatis'; 'Luis Urías' -> 'luis urias'.
    Deliberately conservative: it fixes the common accent/suffix/punctuation
    mismatches but does NOT fuzzy-match, because a fuzzy false-positive
    (joining two different players) is far worse than an honest miss that the
    dropped-rows report will surface.
    """
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = s.lower().replace(".", " ").replace("'", "")
    s = _SUFFIX.sub("", s)
    s = re.sub(r"[^a-z ]", "", s)
    s = " ".join(s.split())
    # Collapse runs of single-letter initials so "j j" == "jj" (feed writes
    # "J.J." one way, "JJ" another). Only merges adjacent 1-char tokens.
    parts = s.split()
    merged, buf = [], ""
    for p in parts:
        if len(p) == 1:
            buf += p
        else:
            if buf:
                merged.append(buf); buf = ""
            merged.append(p)
    if buf:
        merged.append(buf)
    return " ".join(merged)


def american_to_prob(american: float) -> float:
    """Implied probability from American odds (still vig-inclusive)."""
    a = float(american)
    return (-a) / ((-a) + 100.0) if a < 0 else 100.0 / (a + 100.0)


def devig_two_way(price_over: float, price_under: float) -> Optional[float]:
    """No-vig probability of the Over from a two-sided market.

    Normalizes the two implied probs so they sum to 1 (proportional / 'Shin-lite'
    two-way de-vig). Returns None if either price is missing.
    """
    if price_over is None or price_under is None:
        return None
    po = american_to_prob(price_over)
    pu = american_to_prob(price_under)
    tot = po + pu
    if tot <= 0:
        return None
    return po / tot


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------

def load_predictions(path: Path) -> dict:
    """(norm_name, category) -> {thr(float): p_over, ...} plus meta."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    out: dict[tuple[str, str], dict] = {}
    projections = list(doc.get("hitter_projections", [])) + list(doc.get("pitcher_projections", []))
    for p in projections:
        pge_raw = (p.get("simulation") or {}).get("p_ge_threshold") or {}
        try:
            pge = {float(k): float(v) for k, v in pge_raw.items()}
        except (TypeError, ValueError):
            pge = {}
        key = (norm_name(p["player_name"]), p.get("category", ""))
        out[key] = {
            "pge": pge,
            "name": p["player_name"],
            "proj": p.get("projected_value"),
            "conf": p.get("confidence"),
            "team": p.get("team", ""),
            "opp": p.get("opponent", ""),
        }
    return out


def load_lines(path: Path) -> tuple[dict, dict]:
    """Group logged odds into two-sided books.

    Returns (books, meta) where
      books[(norm_name, category, line)] = {'Over': price, 'Under': price}
      meta [(norm_name, category, line)] = {'home','away','player','event'}
    """
    books: dict[tuple, dict] = defaultdict(dict)
    meta: dict[tuple, dict] = {}
    with path.open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                line = float(r["line_point"])
                price = float(r["price_american"])
            except (TypeError, ValueError, KeyError):
                continue
            key = (norm_name(r["player_name"]), r.get("category", ""), line)
            books[key][r.get("side", "")] = price
            meta[key] = {
                "home": r.get("home_team", ""),
                "away": r.get("away_team", ""),
                "player": r["player_name"],
                "event": r.get("event_id", ""),
            }
    return books, meta


# --------------------------------------------------------------------------
# Game filtering
# --------------------------------------------------------------------------

def game_label(home: str, away: str) -> str:
    return f"{away} @ {home}"


def parse_game_filter(spec: Optional[str]) -> Optional[list[str]]:
    """'Astros@Rangers,Braves@Cardinals' -> ['astros rangers', 'braves cardinals']
    (order-insensitive token sets, matched loosely against team names)."""
    if not spec:
        return None
    out = []
    for part in spec.split(","):
        toks = re.sub(r"[^a-z ]", " ", part.lower()).split()
        toks = [t for t in toks if t not in ("at", "vs", "v")]
        if toks:
            out.append(set(toks))
    return out


def game_matches(home: str, away: str, filters) -> bool:
    if filters is None:
        return True
    hay = set(re.sub(r"[^a-z ]", " ", (home + " " + away).lower()).split())
    return any(f & hay == f or f <= hay for f in filters)


# --------------------------------------------------------------------------
# Core compare
# --------------------------------------------------------------------------

def build_rows(preds: dict, books: dict, meta: dict, filters):
    matched, dropped = [], []
    for key, sides in books.items():
        nm, cat, line = key
        m = meta[key]
        if not game_matches(m["home"], m["away"], filters):
            continue
        over = sides.get("Over")
        under = sides.get("Under")
        devig = devig_two_way(over, under)
        if devig is None:
            continue  # one-sided market, can't de-vig honestly
        pred = preds.get((nm, cat))
        if pred is None:
            dropped.append((game_label(m["home"], m["away"]), m["player"], cat, line, "no model row"))
            continue
        thr = float(math.ceil(line))
        mp = pred["pge"].get(thr)
        if mp is None:
            dropped.append((game_label(m["home"], m["away"]), m["player"], cat, line,
                            f"model has no P(>={thr:.0f})"))
            continue
        matched.append({
            "game": game_label(m["home"], m["away"]),
            "player": m["player"],
            "category": cat,
            "line": line,
            "model_p_over": round(mp, 4),
            "market_p_over_devig": round(devig, 4),
            "gap": round(mp - devig, 4),
            "over_price": over,
            "under_price": under,
            "confidence": pred["conf"],
        })
    return matched, dropped


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Shadow compare: frozen-sim P(over) vs de-vigged market (READ-ONLY, watch not bet).")
    ap.add_argument("--date", required=True, help="YYYY-MM-DD")
    ap.add_argument("--pred", default=None, help="predictions json (default: data/learning/predictions/predictions_<date>.json)")
    ap.add_argument("--lines", default=None, help="lines csv (default: data/lines/lines_<date>.csv)")
    ap.add_argument("--out", default=None, help="output csv (default: data/analysis/shadow/shadow_compare_<date>.csv)")
    ap.add_argument("--games", default=None, help="comma list like 'Astros@Rangers,Braves@Cardinals' (default: all)")
    ap.add_argument("--min-gap", type=float, default=0.06, help="flag |gap| >= this in the printout (default 0.06)")
    ap.add_argument("--show-dropped", action="store_true", help="list every market prop that did not match a model row")
    args = ap.parse_args(argv)

    pred_path = Path(args.pred) if args.pred else Path(f"data/learning/predictions/predictions_{args.date}.json")
    lines_path = Path(args.lines) if args.lines else Path(f"data/lines/lines_{args.date}.csv")
    out_path = Path(args.out) if args.out else Path(f"data/analysis/shadow/shadow_compare_{args.date}.csv")

    if not pred_path.exists():
        print(f"[error] predictions file not found: {pred_path}", file=sys.stderr)
        return 2
    if not lines_path.exists():
        print(f"[error] lines file not found: {lines_path}", file=sys.stderr)
        return 2

    preds = load_predictions(pred_path)
    books, meta = load_lines(lines_path)
    filters = parse_game_filter(args.games)
    matched, dropped = build_rows(preds, books, meta, filters)

    print("=" * 78)
    print(f"SHADOW COMPARE  {args.date}   (READ-ONLY -- watch, do not bet)")
    print("  model = FROZEN SIMULATOR P(over threshold), straight from the prediction file.")
    print("  NOT the calibrated CatBoost (that is the candidate, ungated -- see C2).")
    print("  gap>0 = model MORE bullish on the Over than the de-vigged market.")
    print("  A gap is a DISAGREEMENT, not an edge; known model biases (hits +0.10,")
    print("  short-outing K) surface here as systematic gaps -- that is expected.")
    print("=" * 78)

    two_sided = sum(1 for s in books.values() if "Over" in s and "Under" in s)
    print(f"\nmarket props (two-sided, in scope): {two_sided}   "
          f"matched to model: {len(matched)}   dropped: {len(dropped)}")

    by_game = defaultdict(list)
    for r in matched:
        by_game[r["game"]].append(r)

    for game in sorted(by_game):
        rows = sorted(by_game[game], key=lambda r: -abs(r["gap"]))
        print(f"\n=== {game} ===")
        print(f"  {'player':22s}{'prop':11s}{'line':>5s}{'model':>7s}{'mkt':>7s}{'gap':>8s}{'conf':>6s}")
        for r in rows:
            flag = " <<<" if abs(r["gap"]) >= args.min_gap else ""
            conf = r["confidence"] if r["confidence"] is not None else float("nan")
            print(f"  {r['player'][:22]:22s}{r['category']:11s}{r['line']:>5.1f}"
                  f"{r['model_p_over']:>7.3f}{r['market_p_over_devig']:>7.3f}"
                  f"{r['gap']:>+8.3f}{conf:>6.2f}{flag}")

    print("\n" + "-" * 78)
    print(f"DROPPED: {len(dropped)} market props had no matching model row.")
    if dropped and args.show_dropped:
        for game, player, cat, line, why in sorted(dropped):
            print(f"  [{game}] {player} {cat} {line} -- {why}")
    elif dropped:
        print("  (re-run with --show-dropped to see exactly who -- a name-alignment gap")
        print("   hides here; do not assume these players simply had no line.)")

    # Explicitly label this output as name-keyed watch material. It lacks the
    # hard identity/provenance a forward CLV ledger requires.
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "record_type", "date", "game", "player", "category", "line",
            "model_p_over", "market_p_over_devig", "gap",
            "over_price", "under_price", "confidence",
        ])
        w.writeheader()
        for r in matched:
            row = {"record_type": "watch_only_name_keyed", "date": args.date, **r}
            w.writerow(row)
    print(f"\nwrote {out_path}  ({len(matched)} rows)")
    print("This is a NAME-KEYED watch snapshot, not CLV/capture evidence.")
    print("Use run_shadow_ledger.py with hard keys, selected-side policy hashes, and")
    print("tamper-evident source provenance for the forward ledger. This comparison")
    print("remains a hypothesis log, never a bet.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
