#!/usr/bin/env python3
"""
A5 LINE LOGGER — read-only daily MLB player-prop line logger.

Pulls player-prop lines from The Odds API and appends them to a lines log.
Standalone by design: every returned line is logged as its own row, keyed by
    raw player name + market + game_date + book + snapshot_type + timestamp.
The logger does NOT read the prediction archive, does NOT resolve names to
player IDs, and does NOT compute edges. Joining lines to model predictions is
C3's job, done properly there and revisable without touching logged history.

ADDITIVE / COLLECTION-SAFE (per A5 KICKOFF + the discipline section):
  - touches NO model, NO archives, NO prediction->outcome pairs
  - performs GET requests only; the ONLY thing it writes is the lines log
  - reuses the existing config `odds` block (odds_api.* + market_category_map
    + file.*) rather than inventing config

TWO-TRACK CLV DISCIPLINE (per A5 KICKOFF "CLV BACKLOG"):
  CLV is forward-only and grades CLOSING lines. Every row carries an explicit
  snapshot_type and a clv_eligible flag:
    - snapshot_type=close  -> clv_eligible=1  (captured near first pitch; C4/CLV)
    - snapshot_type=midday -> clv_eligible=0  (betting + model-validation only)
    - snapshot_type=open   -> clv_eligible=0  (model-validation only)
    - snapshot_type=backfill-> clv_eligible=0  (historical, validation ONLY)
  A non-closing line must NEVER be fed into CLV math. The flag makes that a
  data property, not a downstream promise: C4 filters clv_eligible=1.

  IMPORTANT: --snapshot close is a LABEL asserting you ran this near first
  pitch. Because MLB start times are staggered, a single "close" pass cannot
  be near first pitch for every game. The honest close workflow is per-game
  (see --commence-within); a blanket close pass over the whole slate mislabels
  early games. This tool warns when you request close over games that have
  already started or don't commence soon.

Usage (PowerShell):
    $env:PYTHONPATH="."
    $env:ODDS_API_KEY="....."
    python run_log_lines.py --config config/config.json                    # midday (default)
    python run_log_lines.py --config config/config.json --snapshot close --commence-within 45
    python run_log_lines.py --config config/config.json --dry-run          # pull + report, write nothing

Exit codes:
    0  ran and logged >=1 line
    1  ran but logged 0 lines (no games / markets not posted / all filtered)
    2  could not run (missing key / config / network / auth)
"""

import argparse
import csv
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

ODDS_API_BASE = "https://api.the-odds-api.com/v4"

# Quota / rate-limit headers we surface after the run (same set as the probe).
QUOTA_HEADERS = [
    "x-requests-remaining", "x-requests-used", "x-requests-last",
    "ratelimit-remaining", "ratelimit-limit", "ratelimit-reset",
    "x-ratelimit-remaining", "x-ratelimit-limit", "retry-after",
]

# Canonical lines-log schema. Column order is stable; downstream reads by name
# (per the project note: extra columns are safe, name-based reads don't break).
LINES_COLUMNS = [
    "logged_at_utc",     # ISO8601 when THIS row was written
    "snapshot_type",     # open | midday | close | backfill
    "clv_eligible",      # 1 only for close; else 0  (C4 filters on this)
    "game_date",         # YYYY-MM-DD, derived from commence_time (UTC date)
    "commence_time_utc", # event start (from API)
    "event_id",          # Odds API event id (opaque; NOT a model key)
    "home_team",
    "away_team",
    "book",              # bookmaker key, e.g. draftkings
    "market_key",        # raw API market, e.g. batter_hits
    "category",          # mapped model PropCategory, e.g. hits (via market_category_map)
    "player_name",       # RAW name as returned by the book (no resolution here)
    "side",              # Over | Under (outcome name)
    "line_point",        # the number, e.g. 1.5
    "price_american",    # American odds, e.g. -115
    "line_last_update",  # market/last_update from the API (book's own timestamp)
    "source",            # api provider id, e.g. the_odds_api
    "regions",           # regions requested
    "poll_timestamp_utc",# when this pull hit the API (one per run)
]


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _print_header(title):
    print("\n" + "=" * 74)
    print(title)
    print("=" * 74)


def _extract_quota(headers):
    found = {}
    for h in QUOTA_HEADERS:
        v = headers.get(h)
        if v is not None:
            found[h] = v
    return found


def _print_quota(quota):
    if not quota:
        print("    quota headers: (none returned)")
        return
    print("    quota headers:")
    for k, v in quota.items():
        print(f"      {k}: {v}")


def _redact(url):
    try:
        parts = urllib.parse.urlsplit(url)
        q = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        q = [(k, "***REDACTED***" if k.lower() in ("apikey", "api_key", "key") else v)
             for k, v in q]
        return urllib.parse.urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(q), parts.fragment))
    except Exception:
        return "(url redaction failed; not printing)"


def _http_get(url, timeout=25):
    req = urllib.request.Request(url, method="GET")
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "baseball_predictor-A5-linelogger/1.0 (read-only)")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.headers, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()


def _parse_iso_utc(s):
    """Parse an API ISO timestamp (…Z) into an aware UTC datetime, or None."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# config (reuse existing odds block; degrade gracefully — mirrors the probe)
# --------------------------------------------------------------------------- #
DEFAULT_ODDS = {
    "odds_api": {
        "enabled": False,
        "api_key_env": "ODDS_API_KEY",
        "sport_key": "baseball_mlb",
        "regions": "us",
        "bookmaker": "draftkings",
        "markets": ["batter_hits", "batter_home_runs", "pitcher_strikeouts"],
        "market_category_map": {
            "batter_hits": "hits",
            "batter_home_runs": "home_runs",
            "pitcher_strikeouts": "strikeouts",
        },
    },
    "file": {
        "dir": "data/lines",
        "date_pattern": "lines_%Y-%m-%d.csv",
    },
}


def load_odds_config(config_path):
    cfg = {}
    if config_path and os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8-sig") as f:  # tolerate BOM
                cfg = json.load(f)
        except Exception as e:
            print(f"[warn] could not parse {config_path}: {e} — using defaults")
    elif config_path:
        print(f"[warn] config not found at {config_path} — using defaults")

    odds = cfg.get("odds", {}) if isinstance(cfg, dict) else {}
    merged = json.loads(json.dumps(DEFAULT_ODDS))  # deep copy
    if isinstance(odds.get("odds_api"), dict):
        merged["odds_api"].update(odds["odds_api"])
    if isinstance(odds.get("file"), dict):
        merged["file"].update(odds["file"])
    return merged


# --------------------------------------------------------------------------- #
# fetch: events -> per-event odds  (same shape the probe validated)
# --------------------------------------------------------------------------- #
def fetch_events(oc, api_key):
    sport = oc.get("sport_key", "baseball_mlb")
    url = (f"{ODDS_API_BASE}/sports/{urllib.parse.quote(sport)}/events?"
           + urllib.parse.urlencode({"apiKey": api_key}))
    print(f"\n  [GET] events  {_redact(url)}")
    status, headers, body = _http_get(url)
    quota = _extract_quota(headers)
    print(f"    HTTP {status}")
    _print_quota(quota)
    if status != 200:
        snippet = body[:300].decode("utf-8", "replace")
        print(f"    body: {snippet}")
        return None, quota, f"events HTTP {status}: {snippet}"
    try:
        return json.loads(body), quota, None
    except Exception as e:
        return None, quota, f"events non-JSON: {e!r}"


def fetch_event_odds(oc, api_key, event_id):
    sport = oc.get("sport_key", "baseball_mlb")
    regions = oc.get("regions", "us")
    bookmaker = oc.get("bookmaker", "draftkings")
    markets = oc.get("markets", [])
    markets_param = ",".join(markets) if markets else "batter_hits"
    url = (f"{ODDS_API_BASE}/sports/{urllib.parse.quote(sport)}/events/"
           f"{urllib.parse.quote(str(event_id))}/odds?"
           + urllib.parse.urlencode({
               "apiKey": api_key, "regions": regions, "markets": markets_param,
               "bookmakers": bookmaker, "oddsFormat": "american",
           }))
    status, headers, body = _http_get(url)
    quota = _extract_quota(headers)
    if status != 200:
        snippet = body[:200].decode("utf-8", "replace")
        return None, quota, f"event {event_id} HTTP {status}: {snippet}"
    try:
        return json.loads(body), quota, None
    except Exception as e:
        return None, quota, f"event {event_id} non-JSON: {e!r}"


# --------------------------------------------------------------------------- #
# flatten one event's odds payload into lines-log rows
# --------------------------------------------------------------------------- #
def rows_from_event(data, oc, snapshot_type, clv_eligible, poll_ts, logged_at):
    """Yield dict rows (one per outcome) for a single event's odds payload."""
    cat_map = oc.get("market_category_map", {})
    regions = oc.get("regions", "us")
    want_book = oc.get("bookmaker", "draftkings")

    commence = data.get("commence_time", "")
    ct = _parse_iso_utc(commence)
    game_date = ct.strftime("%Y-%m-%d") if ct else ""
    event_id = data.get("id", "")
    home = data.get("home_team", "")
    away = data.get("away_team", "")

    for bm in data.get("bookmakers", []):
        book = bm.get("key", "")
        if want_book and book != want_book:
            continue
        for mk in bm.get("markets", []):
            mkey = mk.get("key", "")
            category = cat_map.get(mkey, "")  # blank if unmapped; logged raw anyway
            last_update = mk.get("last_update", "")
            for o in mk.get("outcomes", []):
                yield {
                    "logged_at_utc": logged_at,
                    "snapshot_type": snapshot_type,
                    "clv_eligible": clv_eligible,
                    "game_date": game_date,
                    "commence_time_utc": commence,
                    "event_id": event_id,
                    "home_team": home,
                    "away_team": away,
                    "book": book,
                    "market_key": mkey,
                    "category": category,
                    # RAW name exactly as the book returns it — no resolution (C3's job)
                    "player_name": o.get("description", o.get("name", "")),
                    "side": o.get("name", ""),
                    "line_point": o.get("point", ""),
                    "price_american": o.get("price", ""),
                    "line_last_update": last_update,
                    "source": "the_odds_api",
                    "regions": regions,
                    "poll_timestamp_utc": poll_ts,
                }


# --------------------------------------------------------------------------- #
# append rows to the daily lines log (create with header if new)
# --------------------------------------------------------------------------- #
def append_rows(out_path, rows):
    new_file = not os.path.exists(out_path)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    # newline="" per csv docs (correct line endings on Windows — no blank rows)
    with open(out_path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LINES_COLUMNS, extrasaction="ignore")
        if new_file:
            w.writeheader()
        for r in rows:
            w.writerow(r)
    return len(rows)


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main(argv=None):
    ap = argparse.ArgumentParser(description="A5 read-only MLB prop line logger.")
    ap.add_argument("--config", default="config/config.json",
                    help="path to config.json (reuses its `odds` block)")
    ap.add_argument("--snapshot", choices=["open", "midday", "close", "backfill"],
                    default="midday",
                    help="label for this pull. Only 'close' is CLV-eligible. "
                         "Default 'midday' (betting + validation).")
    ap.add_argument("--commence-within", type=int, default=None, metavar="MIN",
                    help="only log events whose first pitch is within MIN minutes "
                         "from now (and not already started). Use with --snapshot "
                         "close to capture a true per-game closing snapshot.")
    ap.add_argument("--events-limit", type=int, default=None,
                    help="cap number of events probed (quota control / testing).")
    ap.add_argument("--dry-run", action="store_true",
                    help="pull and report, but write NOTHING to the lines log.")
    args = ap.parse_args(argv)

    _print_header("A5 LINE LOGGER  (READ-ONLY — writes only the lines log)")
    print(f"  snapshot_type : {args.snapshot}"
          f"   (clv_eligible={'1' if args.snapshot == 'close' else '0'})")
    print(f"  config        : {args.config}")
    if args.dry_run:
        print("  MODE          : DRY RUN — no file writes")

    odds_cfg = load_odds_config(args.config)
    oc = odds_cfg["odds_api"]
    fc = odds_cfg["file"]

    key_env = oc.get("api_key_env", "ODDS_API_KEY")
    api_key = os.environ.get(key_env)
    print(f"  api_key_env   : {key_env}  ({'present' if api_key else 'MISSING'})")
    print(f"  book          : {oc.get('bookmaker')}")
    print(f"  markets       : {oc.get('markets')}")
    print(f"  category map  : {oc.get('market_category_map')}")

    # honesty guard: 'close' asserts near-first-pitch; nudge toward per-game use.
    # Fires BEFORE the key abort so the label caveat is always surfaced.
    if args.snapshot == "close" and args.commence_within is None:
        print("\n  [WARN] --snapshot close WITHOUT --commence-within logs a 'close'")
        print("         label across the WHOLE slate. Staggered start times mean")
        print("         early games are already underway — those rows would be")
        print("         mislabeled as closing. For real CLV, run per-game near")
        print("         first pitch, e.g. --commence-within 45.")

    if not api_key:
        print(f"\n  [ABORT] {key_env} not set. Cannot pull. (exit 2)")
        return 2

    poll_ts = _now_iso()
    logged_at = poll_ts
    clv_eligible = 1 if args.snapshot == "close" else 0

    events, quota, err = fetch_events(oc, api_key)
    if err:
        print(f"\n  [ABORT] {err} (exit 2)")
        return 2
    print(f"    events today: {len(events)}")
    if not events:
        print("\n  0 events — no MLB games today / off-season. Nothing to log. (exit 1)")
        return 1

    # optional commence-window filter (for true per-game close snapshots)
    now = datetime.now(timezone.utc)
    selected = []
    skipped_started = 0
    skipped_far = 0
    for ev in events:
        ct = _parse_iso_utc(ev.get("commence_time", ""))
        if args.commence_within is not None:
            if ct is None:
                skipped_far += 1
                continue
            delta_min = (ct - now).total_seconds() / 60.0
            if delta_min < 0:
                skipped_started += 1     # already started — not a valid close target
                continue
            if delta_min > args.commence_within:
                skipped_far += 1         # too far out — not near first pitch yet
                continue
        selected.append(ev)

    if args.commence_within is not None:
        print(f"    commence-within {args.commence_within}m: "
              f"{len(selected)} selected, {skipped_started} already started, "
              f"{skipped_far} too far out")

    if args.events_limit is not None:
        selected = selected[: max(0, args.events_limit)]
        print(f"    events-limit: capped to {len(selected)}")

    if not selected:
        print("\n  No events passed the filters. Nothing to log. (exit 1)")
        return 1

    # ---- pull per-event odds, flatten, collect rows ---------------------- #
    all_rows = []
    per_market_counts = {}
    problems = []
    last_quota = quota
    for i, ev in enumerate(selected, 1):
        ev_id = ev.get("id")
        away = ev.get("away_team", "?")
        home = ev.get("home_team", "?")
        if not ev_id:
            continue
        data, q, err = fetch_event_odds(oc, api_key, ev_id)
        if q:
            last_quota = q
        if err:
            print(f"    [{i}/{len(selected)}] {away} @ {home}: {err}")
            problems.append(err)
            continue
        ev_rows = list(rows_from_event(
            data, oc, args.snapshot, clv_eligible, poll_ts, logged_at))
        for r in ev_rows:
            per_market_counts[r["market_key"]] = per_market_counts.get(r["market_key"], 0) + 1
        all_rows.extend(ev_rows)
        print(f"    [{i}/{len(selected)}] {away} @ {home}: {len(ev_rows)} lines")

    _print_header("PULL SUMMARY")
    print(f"  events pulled : {len(selected)}")
    print(f"  total lines   : {len(all_rows)}")
    print(f"  by market     : {per_market_counts or '(none)'}")
    if problems:
        print(f"  problems      : {len(problems)} (e.g. {problems[0]})")
    print("  quota after run:")
    _print_quota(last_quota)

    # markets that returned nothing (e.g. HR / K not posted yet at this hour)
    requested = set(oc.get("markets", []))
    returned = set(per_market_counts.keys())
    missing = requested - returned
    if missing:
        print(f"  [note] requested markets with 0 lines this pull: {sorted(missing)}")
        print("         (books often post HR/K closer to first pitch than hits)")

    if not all_rows:
        print("\n  0 lines logged. (exit 1)")
        return 1

    # ---- write ----------------------------------------------------------- #
    out_dir = fc.get("dir", "data/lines")
    date_pattern = fc.get("date_pattern", "lines_%Y-%m-%d.csv")
    # file is dated by the RUN date (UTC); rows carry their own game_date too
    out_name = datetime.now(timezone.utc).strftime(date_pattern)
    out_path = os.path.join(out_dir, out_name)

    if args.dry_run:
        print(f"\n  DRY RUN — would append {len(all_rows)} rows to {out_path}")
        print("  Nothing written. (exit 0)")
        return 0

    n = append_rows(out_path, all_rows)
    print(f"\n  appended {n} rows -> {out_path}")
    print(f"  snapshot_type={args.snapshot}  clv_eligible={clv_eligible}")
    if clv_eligible == 0:
        print("  (validation/betting track — NOT fed into CLV; C4 filters clv_eligible=1)")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
