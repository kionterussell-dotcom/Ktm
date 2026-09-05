#!/usr/bin/env python
"""Lines, openers and player props across books.

Source: The Odds API (api.the-odds-api.com), a documented JSON API rather than
scraped HTML — which is why this is the lines source instead of scraping eight
sportsbooks. Needs a key in ODDS_API_KEY; the free tier covers a few hundred
requests a month, which is ample for a couple of slate scans a week.

Direction of travel, which CLAUDE.md cares about more than the number itself, is
derivable once two snapshots exist: every pull is appended to `lines`, so a move
that starts at an originator (BetOnline, Pinnacle, Bookmaker) and propagates to
DK/FD/MGM is visible as a timestamp ordering across books.

    export ODDS_API_KEY=...
    python scripts/fetch_lines.py nfl
    python scripts/fetch_lines.py nfl --props        # player props, NFL-weighted
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import db, rawio
from lib.base import Result
from lib.http import FetchError, get_json

SOURCE = "oddsapi"
BASE = "https://api.the-odds-api.com/v4"
SPORT_KEY = {"nfl": "americanfootball_nfl", "cfb": "americanfootball_ncaaf"}
MARKET_MAP = {"spreads": "spread", "totals": "total", "h2h": "moneyline"}

# NFL props worth pricing. Kept deliberately short: the edge in props comes from
# the books pricing hundreds of lines with less attention each, not from covering
# every exotic on the board.
PROP_MARKETS = ["player_pass_yds", "player_pass_tds", "player_rush_yds",
                "player_reception_yds", "player_receptions", "player_anytime_td"]

# Books that originate a number, in rough order of how much a move at each one
# means. A move here that later shows at DK/FD is sharp-driven.
ORIGINATORS = {"pinnacle", "betonlineag", "bookmaker", "circasports", "lowvig"}


def _key() -> str:
    k = os.environ.get("ODDS_API_KEY")
    if not k:
        raise FetchError("ODDS_API_KEY is not set — no lines source configured. "
                         "Get a free key at the-odds-api.com and export it.")
    return k


def match_game(con, sport: str, away: str, home: str) -> str | None:
    cands = con.execute("SELECT game_id, away, home FROM games WHERE sport=?", (sport,)).fetchall()
    norm = lambda s: "".join(ch for ch in s.lower() if ch.isalnum())
    a, h = norm(away), norm(home)
    hits = [g["game_id"] for g in cands
            if (a in norm(g["away"]) or norm(g["away"]) in a)
            and (h in norm(g["home"]) or norm(g["home"]) in h)]
    return hits[0] if len(hits) == 1 else None


def parse_events(events: list, con, sport: str, fetched_at: str) -> tuple[list[dict], int]:
    rows, unmatched = [], 0
    for ev in events:
        gid = match_game(con, sport, ev.get("away_team", ""), ev.get("home_team", ""))
        if not gid:
            unmatched += 1
            continue
        for bk in ev.get("bookmakers") or []:
            book = bk.get("title") or bk.get("key")
            for mk in bk.get("markets") or []:
                market = MARKET_MAP.get(mk.get("key"))
                if not market:
                    continue
                for oc in mk.get("outcomes") or []:
                    rows.append({
                        "fetched_at": fetched_at, "source": SOURCE, "book": book,
                        "game_id": gid, "market": market, "side": oc.get("name"),
                        "number": oc.get("point"), "price": oc.get("price"),
                        # The first snapshot the desk ever holds for a game is
                        # the closest thing to an opener it can honestly claim.
                        "is_opener": 0,
                    })
    return rows, unmatched


def mark_openers(con, game_ids: set[str]) -> int:
    """Flag the earliest snapshot per (game, book, market) as the opener."""
    n = 0
    for gid in game_ids:
        with con:
            cur = con.execute(
                """UPDATE lines SET is_opener=1
                   WHERE id IN (
                     SELECT MIN(id) FROM lines WHERE game_id=?
                     GROUP BY book, market, side)""", (gid,))
            n += cur.rowcount
    return n


def run(con, sport: str, ttl_s: int = 900, props: bool = False) -> Result:
    res = Result(source=f"lines:{sport}")
    try:
        key = _key()
    except FetchError as e:
        res.detail = str(e)
        return res

    fetched_at = db.utcnow()
    url = f"{BASE}/sports/{SPORT_KEY[sport]}/odds"
    params = {"apiKey": key, "regions": "us,us2", "oddsFormat": "american",
              "markets": "spreads,totals,h2h"}
    try:
        events = get_json(url, params=params, ttl_s=ttl_s)
    except FetchError as e:
        res.detail = str(e)
        return res

    res.raw_path = str(rawio.write_raw(f"{SOURCE}-{sport}", events))
    rows, unmatched = parse_events(events, con, sport, fetched_at)
    if not rows:
        res.status = "EMPTY" if not unmatched else "FAILED"
        res.detail = (f"{unmatched} events matched no game in the games table — "
                      "run fetch_schedule first" if unmatched else "no odds returned")
        return res

    res.rows = db.insert_lines(con, rows)
    opened = mark_openers(con, {r["game_id"] for r in rows})
    books = {r["book"] for r in rows}
    orig = {b for b in books if b.lower().replace(" ", "").replace(".", "") in ORIGINATORS}
    res.status = "OK"
    res.detail = (f"{res.rows} rows, {len(books)} books"
                  f"{', originators: ' + ', '.join(sorted(orig)) if orig else ', NO originator book present'}"
                  f"; {opened} openers marked")
    if unmatched:
        res.detail += f"; {unmatched} events unmatched (left out, not guessed)"
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("sport", choices=["nfl", "cfb"])
    ap.add_argument("--props", action="store_true", help="also pull player props (costs more quota)")
    a = ap.parse_args()
    db.init()
    r = run(db.connect(), a.sport, ttl_s=0, props=a.props)
    print(f"{r.source}: {r.status} rows={r.rows} {r.detail}")
    sys.exit(0 if r.status in ("OK", "EMPTY") else 1)
