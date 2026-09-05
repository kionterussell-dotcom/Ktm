"""Config-driven splits scrapers built on parse.scrape_splits_tables.

Action Network, the consensus aggregates and the line sources all share one
parse path, so they share one runner. Each entry only supplies its URLs and the
book label its numbers should be filed under.

Every URL here is UNCALIBRATED. Run the owning script with --calibrate on a
networked machine before trusting any of it.
"""
from __future__ import annotations

from . import db, parse, rawio
from .base import ParseError, Result, assert_parsed
from .http import CACHE_DIR, FetchError, get

# key -> {sport: [(book_label, url), ...]}
CONFIG: dict[str, dict[str, list[tuple[str, str]]]] = {
    "action": {
        "nfl": [("Action Network", "https://www.actionnetwork.com/nfl/public-betting")],
        "cfb": [("Action Network", "https://www.actionnetwork.com/ncaaf/public-betting")],
    },
    "consensus": {
        "nfl": [("Consensus", "https://www.scoresandodds.com/nfl"),
                ("Consensus", "https://www.covers.com/sport/football/nfl/consensus"),
                ("Consensus", "https://www.sportsbettingdime.com/nfl/public-betting-trends/")],
        "cfb": [("Consensus", "https://www.scoresandodds.com/ncaaf"),
                ("Consensus", "https://www.covers.com/sport/football/ncaaf/consensus"),
                ("Consensus", "https://www.sportsbettingdime.com/ncaaf/public-betting-trends/")],
    },
}


def match_game(con, sport: str, away: str, home: str) -> str | None:
    cands = con.execute("SELECT game_id, away, home FROM games WHERE sport=?", (sport,)).fetchall()
    norm = lambda s: "".join(ch for ch in s.lower() if ch.isalnum())
    a, h = norm(away), norm(home)
    hits = [g["game_id"] for g in cands
            if (a in norm(g["away"]) or norm(g["away"]) in a)
            and (h in norm(g["home"]) or norm(g["home"]) in h)]
    return hits[0] if len(hits) == 1 else None


def calibrate(key: str, sport: str) -> None:
    out = CACHE_DIR / "calibrate"; out.mkdir(parents=True, exist_ok=True)
    for book, url in CONFIG[key][sport]:
        host = url.split("//", 1)[-1].split("/", 1)[0]
        print(f"\n=== {key} :: {book} :: {url}")
        try:
            html = get(url, ttl_s=0)
        except FetchError as e:
            print(f"  FAILED {e}"); continue
        p = out / f"{key}-{sport}-{host}.html"
        p.write_text(html, encoding="utf-8")
        from bs4 import BeautifulSoup
        tables = BeautifulSoup(html, "lxml").find_all("table")
        print(f"  saved {p} ({len(html)} bytes), {len(tables)} tables")
        for i, t in enumerate(tables[:8]):
            trs = t.find_all("tr")
            hdr = [c.get_text(" ", strip=True) for c in trs[0].find_all(["td", "th"])] if trs else []
            print(f"   table[{i}] header={hdr[:8]} kind={parse.header_kind(hdr)}")
        if not tables:
            print("   NO TABLES — page is almost certainly JS-rendered; use Playwright.")


def run(con, key: str, sport: str, ttl_s: int = 900) -> Result:
    res = Result(source=f"{key}:{sport}")
    fetched_at = db.utcnow()
    all_rows, seen, errors = [], {}, []

    for book, url in CONFIG[key][sport]:
        host = url.split("//", 1)[-1].split("/", 1)[0]
        try:
            html = get(url, ttl_s=ttl_s)
        except FetchError as e:
            errors.append(f"{host}: {e}"); continue
        seen[host] = len(html)
        try:
            rows = parse.scrape_splits_tables(html, f"{key}:{host}", book, sport, fetched_at)
            assert_parsed(rows, f"{key}:{host}", saw_container=bool(rows) or "%" in html,
                          hint=f"Run --calibrate and inspect data/cache/calibrate/{key}-{sport}-{host}.html")
            all_rows.extend(rows)
        except ParseError as e:
            errors.append(str(e))

    if seen:
        res.raw_path = str(rawio.write_raw(f"{key}-{sport}", {"fetched_at": fetched_at,
                                                              "pages": seen, "rows": all_rows,
                                                              "errors": errors}))
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
    res.detail = f"{res.rows} rows from {len(seen)} page(s)"
    if unresolved:
        res.detail += f"; {unresolved} unmatched (left out, not guessed)"
    if errors:
        res.detail += f"; errors: {'; '.join(errors)}"
    return res
