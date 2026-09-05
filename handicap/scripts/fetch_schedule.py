#!/usr/bin/env python
"""Canonical slate: which games, in kickoff order, with venue.

Source: ESPN's public scoreboard JSON (site.api.espn.com). This is the one feed
in the stack that is a documented-shape JSON API rather than scraped HTML, so it
is the spine everything else keys to. Every other fetcher matches into game_id.

  python scripts/fetch_schedule.py nfl --date 2026-09-13
  python scripts/fetch_schedule.py cfb --date 2026-08-29
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import db, rawio
from lib.base import ParseError, Result, assert_parsed
from lib.http import FetchError, get_json

SOURCE = "espn-schedule"

ENDPOINT = {
    "nfl": "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard",
    # groups=80 is FBS. Without it you get every division and a 400-game slate.
    "cfb": "https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard",
}
EXTRA = {"nfl": {}, "cfb": {"groups": "80", "limit": "400"}}


def parse(payload: dict, sport: str, fetched_at: str) -> list[dict]:
    events = payload.get("events") or []
    rows: list[dict] = []
    for ev in events:
        comps = ev.get("competitions") or []
        if not comps:
            continue
        c = comps[0]
        teams = {t.get("homeAway"): t for t in (c.get("competitors") or [])}
        home, away = teams.get("home"), teams.get("away")
        if not home or not away:
            continue
        ha = (home.get("team") or {}).get("abbreviation") or (home.get("team") or {}).get("displayName")
        aa = (away.get("team") or {}).get("abbreviation") or (away.get("team") or {}).get("displayName")
        kickoff = c.get("date") or ev.get("date")
        if not (ha and aa and kickoff):
            continue
        venue = c.get("venue") or {}
        addr = venue.get("address") or {}
        rows.append({
            "game_id": db.make_game_id(sport, kickoff, aa, ha),
            "sport": sport,
            "season": (payload.get("season") or {}).get("year"),
            "week": str((payload.get("week") or {}).get("number") or ""),
            "away": aa,
            "home": ha,
            "kickoff_utc": kickoff,
            "venue": venue.get("fullName"),
            # ESPN marks indoor venues; retractable roofs read as indoor here, so
            # weather still gets checked for them downstream.
            "is_dome": 1 if venue.get("indoor") else 0,
            "lat": None, "lon": None,
            "city": addr.get("city"), "state": addr.get("state"),
            "source": SOURCE,
            "fetched_at": fetched_at,
        })
    assert_parsed(rows, SOURCE, saw_container=bool(events),
                  hint="ESPN returned events[] but no competition parsed.")
    return rows


def run(con, sport: str, date: str | None = None, ttl_s: int = 900) -> Result:
    res = Result(source=f"{SOURCE}:{sport}")
    params = dict(EXTRA[sport])
    if date:
        params["dates"] = date.replace("-", "")
    try:
        payload = get_json(ENDPOINT[sport], params=params, ttl_s=ttl_s)
    except FetchError as e:
        res.detail = str(e)
        return res

    res.raw_path = str(rawio.write_raw(f"{SOURCE}-{sport}", payload))
    try:
        rows = parse(payload, sport, db.utcnow())
    except ParseError as e:
        res.detail = str(e)
        return res

    if not rows:
        res.status, res.detail = "EMPTY", "no games on this date"
        return res

    for r in rows:
        r.pop("city", None); r.pop("state", None)
    res.rows = db.insert_games(con, rows)
    res.status = "OK"
    res.detail = f"{res.rows} games"
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("sport", choices=["nfl", "cfb"])
    ap.add_argument("--date", help="YYYY-MM-DD (default: ESPN's current slate)")
    a = ap.parse_args()
    con = db.connect(); db.init()
    r = run(con, a.sport, a.date, ttl_s=0)
    print(f"{r.source}: {r.status} rows={r.rows} {r.detail}")
    if r.status == "OK":
        for g in con.execute(
            "SELECT kickoff_utc, away, home, venue, is_dome FROM games WHERE sport=? ORDER BY kickoff_utc", (a.sport,)
        ):
            print(f"  {g['kickoff_utc']}  {g['away']:>5} @ {g['home']:<5}  {g['venue'] or ''}{'  [dome]' if g['is_dome'] else ''}")
    sys.exit(0 if r.status in ("OK", "EMPTY") else 1)
