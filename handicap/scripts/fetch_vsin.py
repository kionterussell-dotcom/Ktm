#!/usr/bin/env python
"""VSiN betting splits — the backbone of the desk.

Pulls ticket% and handle% for spread, total and moneyline, per book. DraftKings
is the most recreational major book (best public read); Circa is sharp-first and
does not limit bettors (best sharp read). They are stored as separate books and
never averaged together.

STATUS: selectors are UNCALIBRATED. They were written structurally (find the
table whose header mentions handle/tickets) rather than against live HTML,
because this build ran in an environment with no route to data.vsin.com. Run

    python scripts/fetch_vsin.py nfl --calibrate

on a machine with network first: it saves the live HTML to data/cache/calibrate/
and prints what it found, so the parse can be corrected against the real page.
Until that passes, this fetcher reports FAILED and games stay UNVERIFIED. It
will not invent a number.

FRAGILE, in the order it will break:
  1. BOOK_VIEWS urls  — VSiN reorganizes its splits pages periodically
  2. table discovery  — if splits move to a JS-rendered grid, switch to Playwright
  3. team-name match  — VSiN's names vs ESPN abbreviations (see match_game)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import db, parse, rawio
from lib.base import ParseError, Result, assert_parsed
from lib.http import CACHE_DIR, FetchError, get

SOURCE = "vsin"

# FRAGILE #1 — confirm these against the live site during calibration.
BOOK_VIEWS = {
    "nfl": [("DraftKings", "https://data.vsin.com/betting-splits/?view=nfl&book=draftkings"),
            ("Circa",      "https://data.vsin.com/betting-splits/?view=nfl&book=circa")],
    "cfb": [("DraftKings", "https://data.vsin.com/betting-splits/?view=ncaaf&book=draftkings"),
            ("Circa",      "https://data.vsin.com/betting-splits/?view=ncaaf&book=circa")],
}
MARKET_ORDER = ("spread", "total", "moneyline")


def _cells(tr) -> list[str]:
    return [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]


def parse_book(html: str, book: str, sport: str, fetched_at: str) -> list[dict]:
    """Scan every table; keep rows from tables whose header names handle/tickets."""
    soup = BeautifulSoup(html, "lxml")
    tables = soup.find_all("table")
    rows: list[dict] = []
    saw_splits_table = False

    for tbl in tables:
        trs = tbl.find_all("tr")
        if len(trs) < 2:
            continue
        idx = parse.header_kind(_cells(trs[0]))
        if not idx:
            continue
        saw_splits_table = True
        for tr in trs[1:]:
            c = _cells(tr)
            if len(c) < 2:
                continue
            matchup = c[0]
            if "@" not in matchup and " at " not in matchup.lower():
                continue
            sep = "@" if "@" in matchup else " at "
            away, home = [p.strip() for p in matchup.split(sep, 1)[:2]]
            tickets = parse.pct(c[idx["tickets"]]) if idx.get("tickets", 99) < len(c) else None
            handle = parse.pct(c[idx["handle"]]) if idx.get("handle", 99) < len(c) else None
            if tickets is None and handle is None:
                continue
            rows.append({
                "fetched_at": fetched_at, "source": SOURCE, "book": book, "sport": sport,
                # Resolved to a real game_id in run(); kept raw here so a name
                # mismatch is visible in the raw file rather than silently dropped.
                "game_id": f"UNRESOLVED:{away}@{home}",
                "away": away, "home": home,
                "market": "spread", "side": away,
                "ticket_pct": tickets, "handle_pct": handle,
                "raw_json": {"cells": c, "book": book},
            })

    assert_parsed(rows, f"{SOURCE}:{book}", saw_container=saw_splits_table,
                  hint="A table header mentioned handle/tickets but no matchup row parsed. "
                       "Re-run with --calibrate and inspect the saved HTML.")
    if not rows and tables:
        raise ParseError(f"{SOURCE}:{book}: {len(tables)} tables on the page, none is a splits table. "
                         "Page shape changed, or the splits grid is JS-rendered (switch to Playwright).")
    return rows


def match_game(con, sport: str, away: str, home: str) -> str | None:
    """FRAGILE #3 — VSiN team naming vs the games table. Substring match both
    ways; ambiguity returns None so the row stays UNRESOLVED rather than being
    attached to the wrong game."""
    cands = con.execute("SELECT game_id, away, home FROM games WHERE sport=?", (sport,)).fetchall()
    def norm(s): return "".join(ch for ch in s.lower() if ch.isalnum())
    a, h = norm(away), norm(home)
    hits = [g["game_id"] for g in cands
            if (a in norm(g["away"]) or norm(g["away"]) in a)
            and (h in norm(g["home"]) or norm(g["home"]) in h)]
    return hits[0] if len(hits) == 1 else None


def calibrate(sport: str) -> None:
    out = CACHE_DIR / "calibrate"; out.mkdir(parents=True, exist_ok=True)
    for book, url in BOOK_VIEWS[sport]:
        print(f"\n=== {book} :: {url}")
        try:
            html = get(url, ttl_s=0)
        except FetchError as e:
            print(f"  FAILED {e}"); continue
        p = out / f"vsin-{sport}-{book.lower()}.html"
        p.write_text(html, encoding="utf-8")
        soup = BeautifulSoup(html, "lxml")
        tables = soup.find_all("table")
        print(f"  saved {p} ({len(html)} bytes), {len(tables)} tables")
        for i, t in enumerate(tables[:8]):
            hdr = _cells(t.find_all("tr")[0]) if t.find_all("tr") else []
            print(f"   table[{i}] header={hdr[:8]} kind={parse.header_kind(hdr)}")


def run(con, sport: str, ttl_s: int = 900) -> Result:
    res = Result(source=f"{SOURCE}:{sport}")
    fetched_at = db.utcnow()
    all_rows, payload, errors = [], {}, []

    for book, url in BOOK_VIEWS[sport]:
        try:
            html = get(url, ttl_s=ttl_s)
        except FetchError as e:
            errors.append(f"{book}: {e}"); continue
        payload[book] = {"url": url, "bytes": len(html)}
        try:
            all_rows.extend(parse_book(html, book, sport, fetched_at))
        except ParseError as e:
            errors.append(str(e))

    if payload:
        res.raw_path = str(rawio.write_raw(f"{SOURCE}-{sport}", {"fetched_at": fetched_at, "books": payload,
                                                                 "rows": all_rows, "errors": errors}))
    if not all_rows:
        res.detail = "; ".join(errors) or "no rows parsed"
        return res

    unresolved = 0
    for r in all_rows:
        if r["game_id"].startswith("UNRESOLVED:"):
            gid = match_game(con, sport, r["away"], r["home"])
            if gid:
                r["game_id"] = gid
            else:
                unresolved += 1
    keep = [r for r in all_rows if not r["game_id"].startswith("UNRESOLVED:")]
    if keep:
        res.rows = db.insert_splits(con, keep)
    res.status = "OK" if keep else "FAILED"
    res.detail = f"{res.rows} rows from {len(payload)} book(s)"
    if unresolved:
        res.detail += f"; {unresolved} rows unmatched to a game (left out, not guessed)"
    if errors:
        res.detail += f"; errors: {'; '.join(errors)}"
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("sport", choices=["nfl", "cfb"])
    ap.add_argument("--calibrate", action="store_true", help="save live HTML and report table shapes")
    a = ap.parse_args()
    if a.calibrate:
        calibrate(a.sport); sys.exit(0)
    db.init(); con = db.connect()
    r = run(con, a.sport, ttl_s=0)
    print(f"{r.source}: {r.status} rows={r.rows} {r.detail}")
    sys.exit(0 if r.status == "OK" else 1)
