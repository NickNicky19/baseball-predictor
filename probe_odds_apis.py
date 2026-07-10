#!/usr/bin/env python3
"""
A5 PROBE — odds sourcing feasibility check (READ-ONLY, no logger, no writes to
model/archives/pairs). Answers ONE gating question from the A5 KICKOFF:

    "Do free-tier keys actually return MLB PLAYER-PROP lines?"
    (batter_hits / batter_home_runs / pitcher_strikeouts, on the configured book)

...and reports rate-limit / quota headers so you can compute how many
full-slate snapshots per day the free tier supports.

This script ONLY performs GET requests and prints a report. It writes nothing.
It reuses the existing config `odds` block (see A5 KICKOFF) rather than inventing
config. Run it on a NETWORKED box (this is the "explore first" step).

Usage (PowerShell):
    $env:PYTHONPATH="."
    $env:ODDS_API_KEY="....."            # required for The Odds API
    $env:SHARP_API_KEY="....."           # optional, only if probing SharpAPI
    python probe_odds_apis.py --config config/config.json
    python probe_odds_apis.py --config config/config.json --events-limit 3
    python probe_odds_apis.py --skip-sharp

Exit codes:
    0  probe ran and The Odds API returned player props on the configured book
    1  probe ran but NO player props came back (free tier likely doesn't serve them)
    2  could not run (missing key / config / network)  -- diagnostic, not a verdict
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

ODDS_API_BASE = "https://api.the-odds-api.com/v4"

# Quota / rate-limit headers we care about. The Odds API returns the first three;
# SharpAPI and many others use the RateLimit-* family. We print whatever exists.
QUOTA_HEADERS = [
    "x-requests-remaining",
    "x-requests-used",
    "x-requests-last",
    "ratelimit-remaining",
    "ratelimit-limit",
    "ratelimit-reset",
    "x-ratelimit-remaining",
    "x-ratelimit-limit",
    "retry-after",
]


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def _print_header(title):
    print("\n" + "=" * 74)
    print(title)
    print("=" * 74)


def _extract_quota(headers):
    """Return {header: value} for any quota/rate-limit headers present."""
    found = {}
    # headers is an http.client.HTTPMessage / email.message.Message (case-insensitive)
    for h in QUOTA_HEADERS:
        val = headers.get(h)
        if val is not None:
            found[h] = val
    return found


def _print_quota(quota):
    if not quota:
        print("    quota headers: (none returned)")
        return
    print("    quota headers:")
    for k, v in quota.items():
        print(f"      {k}: {v}")


def _http_get(url, timeout=25):
    """
    READ-ONLY GET. Returns (status:int, headers, body_bytes) or raises.
    Never sends a body; never uses any verb other than GET.
    """
    req = urllib.request.Request(url, method="GET")
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "baseball_predictor-A5-probe/1.0 (read-only)")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.headers, resp.read()
    except urllib.error.HTTPError as e:
        # HTTPError still carries headers + body — we WANT those (quota, error json)
        return e.code, e.headers, e.read()


def _redact(url):
    """Strip apiKey / api_key values from a URL before printing."""
    try:
        parts = urllib.parse.urlsplit(url)
        q = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        q = [(k, "***REDACTED***" if k.lower() in ("apikey", "api_key", "key") else v)
             for k, v in q]
        newq = urllib.parse.urlencode(q)
        return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, newq, parts.fragment))
    except Exception:
        return "(url redaction failed; not printing)"


# --------------------------------------------------------------------------- #
# config loading (reuse existing odds block; degrade gracefully)
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
    # SharpAPI is OPTIONAL and best-effort: no documented stable free MLB
    # player-prop endpoint, so we only probe if a base_url + key are supplied.
    "sharp_api": {
        "enabled": False,
        "api_key_env": "SHARP_API_KEY",
        "base_url": "",          # e.g. "https://api.sharpapi.example/v1/mlb/player-props"
        "sport_path": "",        # optional extra path if the endpoint needs it
    },
}


def load_odds_config(config_path):
    """
    Return the `odds` sub-config, merged over DEFAULT_ODDS so a partial or
    missing config still lets the probe run against sensible defaults.
    """
    cfg = {}
    if config_path and os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8-sig") as f:  # tolerate BOM
                cfg = json.load(f)
        except Exception as e:
            print(f"[warn] could not parse {config_path}: {e} — using defaults")
    else:
        if config_path:
            print(f"[warn] config not found at {config_path} — using defaults")

    odds = cfg.get("odds", {}) if isinstance(cfg, dict) else {}

    merged = json.loads(json.dumps(DEFAULT_ODDS))  # deep copy
    for section in ("odds_api", "sharp_api"):
        if isinstance(odds.get(section), dict):
            merged[section].update(odds[section])
    return merged


# --------------------------------------------------------------------------- #
# The Odds API probe
# --------------------------------------------------------------------------- #
def probe_odds_api(oc, events_limit):
    """
    Two-step probe against The Odds API v4:
      1) GET /sports/{sport}/events        -> list today's MLB games (cheap; costs 1)
      2) GET /events/{id}/odds?markets=... -> player props for a few events
    Player-prop markets on this API are per-EVENT (not on the bulk /odds route),
    which is exactly why free-tier quota math matters — one snapshot of a full
    slate costs ~1 (events) + N (per game). We report that arithmetic.

    Returns a result dict for the summary.
    """
    _print_header("THE ODDS API  —  MLB player-prop probe")

    key_env = oc.get("api_key_env", "ODDS_API_KEY")
    api_key = os.environ.get(key_env)
    sport = oc.get("sport_key", "baseball_mlb")
    regions = oc.get("regions", "us")
    bookmaker = oc.get("bookmaker", "draftkings")
    markets = oc.get("markets", [])
    cat_map = oc.get("market_category_map", {})

    print(f"  time            : {_now()}")
    print(f"  api_key_env     : {key_env}  ({'present' if api_key else 'MISSING'})")
    print(f"  sport_key       : {sport}")
    print(f"  regions         : {regions}")
    print(f"  bookmaker       : {bookmaker}")
    print(f"  markets         : {markets}")
    print(f"  category map    : {cat_map}")

    result = {
        "api": "the_odds_api",
        "ran": False,
        "props_returned": False,
        "events_seen": 0,
        "events_probed": 0,
        "markets_found": {},          # market -> count of prop lines seen
        "quota_after_events": {},
        "quota_after_last_event": {},
        "cost_full_slate": None,
        "notes": [],
    }

    if not api_key:
        msg = f"env var {key_env} not set — cannot probe The Odds API."
        print(f"\n  [SKIP] {msg}")
        result["notes"].append(msg)
        return result

    # ---- step 1: events -------------------------------------------------- #
    events_url = (
        f"{ODDS_API_BASE}/sports/{urllib.parse.quote(sport)}/events?"
        + urllib.parse.urlencode({"apiKey": api_key})
    )
    print(f"\n  [GET] events  {_redact(events_url)}")
    try:
        status, headers, body = _http_get(events_url)
    except Exception as e:
        msg = f"events request failed: {e!r}"
        print(f"    ERROR: {msg}")
        result["notes"].append(msg)
        return result

    quota = _extract_quota(headers)
    result["quota_after_events"] = quota
    print(f"    HTTP {status}")
    _print_quota(quota)

    if status != 200:
        snippet = body[:300].decode("utf-8", "replace")
        msg = f"events returned HTTP {status}: {snippet}"
        print(f"    body: {snippet}")
        result["notes"].append(msg)
        if status in (401, 403):
            result["notes"].append("auth rejected — check the key / plan.")
        if status == 422:
            result["notes"].append("422 — sport key or params likely invalid.")
        return result

    try:
        events = json.loads(body)
    except Exception as e:
        msg = f"events body was not JSON: {e!r}"
        print(f"    {msg}")
        result["notes"].append(msg)
        return result

    result["ran"] = True
    result["events_seen"] = len(events)
    print(f"    events today: {len(events)}")
    if not events:
        result["notes"].append(
            "0 events — no MLB games today, or off-season. "
            "Re-run on a game day before drawing conclusions."
        )
        print("    [note] 0 events — run this on an MLB game day to truly test props.")
        return result

    to_probe = events[: max(1, events_limit)]
    print(f"    probing player props on {len(to_probe)} of {len(events)} events...")

    markets_param = ",".join(markets) if markets else "batter_hits"

    last_quota = quota
    for i, ev in enumerate(to_probe, 1):
        ev_id = ev.get("id")
        home = ev.get("home_team", "?")
        away = ev.get("away_team", "?")
        if not ev_id:
            continue
        odds_url = (
            f"{ODDS_API_BASE}/sports/{urllib.parse.quote(sport)}/events/"
            f"{urllib.parse.quote(str(ev_id))}/odds?"
            + urllib.parse.urlencode({
                "apiKey": api_key,
                "regions": regions,
                "markets": markets_param,
                "bookmakers": bookmaker,
                "oddsFormat": "american",
            })
        )
        print(f"\n    [GET] event {i}/{len(to_probe)}: {away} @ {home}")
        print(f"          {_redact(odds_url)}")
        try:
            status, headers, body = _http_get(odds_url)
        except Exception as e:
            print(f"          ERROR: {e!r}")
            result["notes"].append(f"event {ev_id} odds failed: {e!r}")
            continue

        result["events_probed"] += 1
        last_quota = _extract_quota(headers)
        print(f"          HTTP {status}")
        _print_quota(last_quota)

        if status != 200:
            snippet = body[:200].decode("utf-8", "replace")
            print(f"          body: {snippet}")
            result["notes"].append(f"event {ev_id}: HTTP {status} {snippet}")
            # 422 here usually = that market not offered / not on free plan
            continue

        try:
            data = json.loads(body)
        except Exception as e:
            result["notes"].append(f"event {ev_id}: non-JSON odds body {e!r}")
            continue

        # Walk bookmakers -> markets -> outcomes, count prop lines per market.
        for bm in data.get("bookmakers", []):
            if bookmaker and bm.get("key") != bookmaker:
                continue
            for mk in bm.get("markets", []):
                mkey = mk.get("key", "?")
                n_out = len(mk.get("outcomes", []))
                result["markets_found"][mkey] = result["markets_found"].get(mkey, 0) + n_out
                # show one sample outcome so you can eyeball the shape
                sample = mk.get("outcomes", [])[:1]
                cat = cat_map.get(mkey, "(unmapped)")
                print(f"          market {mkey} -> cat {cat}: {n_out} outcomes")
                for o in sample:
                    print(f"             e.g. {o.get('description', o.get('name','?'))} "
                          f"{o.get('name','')} point={o.get('point')} price={o.get('price')}")

    result["quota_after_last_event"] = last_quota
    result["props_returned"] = any(
        m in result["markets_found"] for m in (markets or [])
    ) and sum(result["markets_found"].values()) > 0

    # ---- cost arithmetic for a full slate -------------------------------- #
    # One full-slate snapshot ~= 1 events call + (games * per-event-odds cost).
    # The Odds API charges per market*region on the odds route; we report both a
    # simple games-count estimate and note the multiplier so you can refine once
    # you see x-requests-used deltas in the printed quota above.
    games = result["events_seen"]
    n_markets = max(1, len(markets))
    n_regions = len((regions or "us").split(","))
    est_simple = 1 + games                      # if each event-odds call costs 1
    est_weighted = 1 + games * n_markets * n_regions  # upper-bound if charged per market*region
    result["cost_full_slate"] = {
        "games": games,
        "est_credits_simple": est_simple,
        "est_credits_weighted_upper": est_weighted,
        "n_markets": n_markets,
        "n_regions": n_regions,
    }

    print("\n  full-slate snapshot cost estimate:")
    print(f"    games today                     : {games}")
    print(f"    ~credits (if 1/event-odds call) : {est_simple}")
    print(f"    ~credits (upper, per mkt*region): {est_weighted}  "
          f"({n_markets} markets x {n_regions} region(s))")
    print("    -> TRUST THE MEASURED DELTA: subtract the x-requests-remaining")
    print("       shown after events vs after the last event to get real cost/call,")
    print("       then multiply by games for a true per-snapshot figure.")

    return result


# --------------------------------------------------------------------------- #
# SharpAPI probe (best-effort / optional)
# --------------------------------------------------------------------------- #
def probe_sharp_api(sc):
    _print_header("SHARPAPI  —  MLB player-prop probe (best-effort / optional)")

    key_env = sc.get("api_key_env", "SHARP_API_KEY")
    api_key = os.environ.get(key_env)
    base_url = (sc.get("base_url") or "").strip()
    sport_path = (sc.get("sport_path") or "").strip()

    result = {
        "api": "sharp_api",
        "ran": False,
        "props_returned": False,
        "quota": {},
        "notes": [],
    }

    print(f"  time         : {_now()}")
    print(f"  api_key_env  : {key_env}  ({'present' if api_key else 'MISSING'})")
    print(f"  base_url     : {base_url or '(not configured)'}")

    if not base_url:
        msg = ("no odds.sharp_api.base_url configured — skipping. SharpAPI has no "
               "documented stable free MLB player-prop endpoint; set base_url + "
               "key in config/env to probe it, otherwise The Odds API is the "
               "primary candidate.")
        print(f"\n  [SKIP] {msg}")
        result["notes"].append(msg)
        return result

    if not api_key:
        msg = f"env var {key_env} not set — skipping SharpAPI."
        print(f"\n  [SKIP] {msg}")
        result["notes"].append(msg)
        return result

    url = base_url
    if sport_path:
        url = url.rstrip("/") + "/" + sport_path.lstrip("/")
    # SharpAPI-style auth is typically a bearer/api key query param or header;
    # we try query param first (read-only), header as documented alt.
    sep = "&" if "?" in url else "?"
    url_with_key = f"{url}{sep}" + urllib.parse.urlencode({"api_key": api_key})

    print(f"\n  [GET] {_redact(url_with_key)}")
    try:
        status, headers, body = _http_get(url_with_key)
    except Exception as e:
        msg = f"request failed: {e!r}"
        print(f"    ERROR: {msg}")
        result["notes"].append(msg)
        return result

    quota = _extract_quota(headers)
    result["quota"] = quota
    result["ran"] = True
    print(f"    HTTP {status}")
    _print_quota(quota)

    snippet = body[:400].decode("utf-8", "replace")
    print(f"    body (first 400 chars): {snippet}")

    if status == 200:
        # We can't assume the schema; just flag whether the response mentions
        # our prop concepts so you know if it's worth wiring properly.
        low = snippet.lower()
        hit = any(t in low for t in ("hits", "home_run", "strikeout", "prop", "player"))
        result["props_returned"] = hit
        result["notes"].append(
            "200 OK — inspect the body shape; schema is undocumented here, "
            "so the logger would need a SharpAPI-specific adapter."
            if hit else
            "200 OK but no obvious prop fields in the sample — likely wrong endpoint."
        )
    else:
        result["notes"].append(f"HTTP {status} — endpoint/auth likely off; treat as no-go for now.")

    return result


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main(argv=None):
    ap = argparse.ArgumentParser(description="A5 read-only odds-API feasibility probe.")
    ap.add_argument("--config", default="config/config.json",
                    help="path to config.json (reuses its `odds` block)")
    ap.add_argument("--events-limit", type=int, default=3,
                    help="how many MLB events to probe for props (default 3, keeps quota low)")
    ap.add_argument("--skip-sharp", action="store_true",
                    help="skip the SharpAPI probe entirely")
    args = ap.parse_args(argv)

    _print_header("A5 ODDS-API PROBE  (READ-ONLY — writes nothing)")
    print("  Purpose: confirm free-tier MLB player props BEFORE building the logger.")
    print(f"  Config : {args.config}")
    print("  This script performs GET requests only. It does not touch the model,")
    print("  archives, pairs, or any lines log. It just reports.")

    odds_cfg = load_odds_config(args.config)

    odds_result = probe_odds_api(odds_cfg["odds_api"], args.events_limit)

    sharp_result = None
    if not args.skip_sharp:
        sharp_result = probe_sharp_api(odds_cfg["sharp_api"])

    # ---- verdict --------------------------------------------------------- #
    _print_header("VERDICT")
    oa = odds_result
    if not oa["ran"]:
        print("  The Odds API: COULD NOT RUN (see notes above).")
        for n in oa["notes"]:
            print(f"    - {n}")
    elif oa["props_returned"]:
        print("  The Odds API: ✅ PLAYER PROPS RETURNED on the configured book.")
        print(f"    markets seen: {oa['markets_found']}")
        print("    -> Logger is viable on this source. Proceed to logger DESIGN")
        print("       (open + CLOSE snapshot cadence, quota budget from the")
        print("        measured cost above, two-track CLV vs validation split).")
    else:
        print("  The Odds API: ⚠️  ran, but NO player props came back.")
        print(f"    events seen: {oa['events_seen']}, probed: {oa['events_probed']}")
        if oa["events_seen"] == 0:
            print("    Likely off-season / no games today — RE-RUN ON A GAME DAY.")
        else:
            print("    Free tier may not include player-prop markets on this book.")
            print("    Try another bookmaker, or confirm props require a paid add-on.")
        for n in oa["notes"]:
            print(f"    - {n}")

    if sharp_result is not None:
        if sharp_result["ran"] and sharp_result["props_returned"]:
            print("\n  SharpAPI: possible — 200 with prop-like fields; needs a custom adapter.")
        elif sharp_result["ran"]:
            print("\n  SharpAPI: reachable but no clear props — treat as no-go for now.")
        else:
            print("\n  SharpAPI: not probed (unconfigured or no key).")

    print("\n  REMINDER (A5 KICKOFF): CLV is forward-only. Whatever the source,")
    print("  the logger must capture a CLOSE snapshot near first pitch for C4,")
    print("  and any historical/opening backfill is model-validation ONLY —")
    print("  never fed into CLV math.")
    print("=" * 74)

    # exit code: verdict on the PRIMARY source (The Odds API)
    if not oa["ran"]:
        return 2
    return 0 if oa["props_returned"] else 1


if __name__ == "__main__":
    sys.exit(main())
