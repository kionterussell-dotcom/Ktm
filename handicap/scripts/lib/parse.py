"""Structural HTML helpers.

Deliberately structural rather than class-name based: VSiN, Covers and friends
reskin often, but a splits table is always a table whose header mentions handle
and tickets. Matching on structure survives a reskin; matching on .css-1x2y3z
does not. Where a guess is still a guess it is marked FRAGILE in the caller.
"""
from __future__ import annotations

import re

PCT_RE = re.compile(r"(-?\d{1,3}(?:\.\d+)?)\s*%")
NUM_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")


def pct(text: str | None) -> float | None:
    """'71%' -> 71.0 ; '71' -> 71.0 ; '' -> None. Never guesses a default."""
    if not text:
        return None
    m = PCT_RE.search(text)
    if m:
        return float(m.group(1))
    t = text.strip()
    if NUM_RE.fullmatch(t):
        v = float(t)
        return v if 0 <= v <= 100 else None
    return None


def american_price(text: str | None) -> int | None:
    if not text:
        return None
    m = re.search(r"[-+]\d{3,4}", text.replace("−", "-"))
    return int(m.group(0)) if m else None


def spread_number(text: str | None) -> float | None:
    if not text:
        return None
    t = text.replace("½", ".5").replace("PK", "0").replace("pk", "0").replace("−", "-")
    m = re.search(r"[-+]?\d+(?:\.\d+)?", t)
    return float(m.group(0)) if m else None


def header_kind(cells: list[str]) -> dict[str, int]:
    """Map a splits table's header row to column indexes we care about.

    Returns e.g. {'tickets': 3, 'handle': 4}. Empty dict means this table is not
    a splits table, which the caller must treat as 'keep looking', not 'no data'.
    """
    idx: dict[str, int] = {}
    for i, c in enumerate(cells):
        c = c.lower()
        if any(k in c for k in ("handle", "money", "$")):
            idx.setdefault("handle", i)
        elif any(k in c for k in ("ticket", "bets", "bet %", "count")):
            idx.setdefault("tickets", i)
    return idx


def scrape_splits_tables(html: str, source: str, book: str, sport: str,
                         fetched_at: str, market_hint: str = "spread") -> list[dict]:
    """Generic splits-table scraper shared by the consensus-style sources.

    Action Network, Covers, ScoresAndOdds and SportsBettingDime all present the
    same shape: a table whose header names bets/handle, and rows whose first cell
    is a matchup. Matching that shape rather than each site's class names means
    one parser instead of four, and a reskin at one site does not silently
    produce zero rows — the caller's assert_parsed() raises instead.

    UNCALIBRATED: written without a live response to check against.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    rows: list[dict] = []
    for tbl in soup.find_all("table"):
        trs = tbl.find_all("tr")
        if len(trs) < 2:
            continue
        head = [c.get_text(" ", strip=True) for c in trs[0].find_all(["td", "th"])]
        idx = header_kind(head)
        if not idx:
            continue
        for tr in trs[1:]:
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            if len(cells) < 2:
                continue
            m = cells[0]
            sep = "@" if "@" in m else (" at " if " at " in m.lower() else None)
            if not sep:
                continue
            away, home = [x.strip() for x in m.split(sep, 1)[:2]]
            t = pct(cells[idx["tickets"]]) if idx.get("tickets", 99) < len(cells) else None
            h = pct(cells[idx["handle"]]) if idx.get("handle", 99) < len(cells) else None
            if t is None and h is None:
                continue
            rows.append({
                "fetched_at": fetched_at, "source": source, "book": book, "sport": sport,
                "game_id": f"UNRESOLVED:{away}@{home}", "away": away, "home": home,
                "market": market_hint, "side": away,
                "ticket_pct": t, "handle_pct": h,
                "raw_json": {"cells": cells, "source": source},
            })
    return rows
