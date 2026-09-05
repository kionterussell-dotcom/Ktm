#!/usr/bin/env python
"""Ingest pasted content from gated sources (X posts, Pikkit, MGM, FanDuel, Action PRO).

Saves the text verbatim to manual/, extracts any percentages into the manual
table tagged USER-PROVIDED with the account handle and post timestamp, and
reports any conflict with a fetched number for the same book. Per CLAUDE.md the
pasted insider number wins and the conflict is flagged.

    python scripts/paste.py --account @DaveMasonBOL --book BetOnline \
        --game nfl-20260913-DAL-PHI --posted 2026-09-13T14:02:00Z < note.txt
    pbpaste | python scripts/paste.py --account @BetMGMnews --book BetMGM
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import db

ROOT = Path(__file__).resolve().parents[1]
MANUAL_DIR = ROOT / "manual"

# "74% of tickets", "72% of the handle", "71% money" — capture the number and
# whichever word tells us which of the two it is. Anything ambiguous is kept in
# the note text and NOT extracted, because reporting ticket% as handle% is one
# of the few things CLAUDE.md calls out by name.
FIG_RE = re.compile(
    r"(\d{1,3}(?:\.\d+)?)\s*%\s*(?:of\s+(?:the\s+)?)?(tickets?|bets?|handle|money|wagers?)",
    re.I)


def classify(word: str) -> str | None:
    w = word.lower()
    if w.startswith(("ticket", "bet", "wager")):
        return "ticket_pct"
    if w.startswith(("handle", "money")):
        return "handle_pct"
    return None


def extract(text: str) -> dict:
    out: dict[str, float] = {}
    for value, word in FIG_RE.findall(text):
        kind = classify(word)
        if kind and kind not in out:
            out[kind] = float(value)
    return out


def conflicts(con, book: str, game_id: str, figures: dict) -> list[str]:
    if not (book and game_id and figures):
        return []
    rows = con.execute(
        "SELECT market, side, ticket_pct, handle_pct, source, fetched_at FROM splits "
        "WHERE book=? AND game_id=? ORDER BY fetched_at DESC LIMIT 6",
        (book, game_id)).fetchall()
    out = []
    for r in rows:
        for k in ("ticket_pct", "handle_pct"):
            if k in figures and r[k] is not None and abs(r[k] - figures[k]) >= 5:
                out.append(f"{book} {r['market']} {k}: fetched {r[k]:.0f}% ({r['source']}, "
                           f"{r['fetched_at'][:16]}) vs pasted {figures[k]:.0f}% — "
                           f"pasted insider number wins")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", required=True, help="e.g. @DaveMasonBOL, Pikkit, ActionPRO")
    ap.add_argument("--book", help="book the figures describe (BetMGM, FanDuel, BetOnline...)")
    ap.add_argument("--game", help="game_id, if the note is about one game")
    ap.add_argument("--posted", help="when the source posted it (ISO)")
    ap.add_argument("--file", help="read from a file instead of stdin")
    a = ap.parse_args()

    text = Path(a.file).read_text(encoding="utf-8") if a.file else sys.stdin.read()
    if not text.strip():
        sys.exit("nothing on stdin — paste the content or pass --file")

    MANUAL_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M")
    slug = re.sub(r"[^a-z0-9]+", "-", a.account.lower()).strip("-")
    path = MANUAL_DIR / f"{stamp}-{slug}.md"
    n = 2
    while path.exists():
        path = MANUAL_DIR / f"{stamp}-{slug}.{n}.md"; n += 1
    path.write_text(
        f"source_account: {a.account}\nposted_at: {a.posted or 'unknown'}\n"
        f"book: {a.book or 'unspecified'}\ngame_id: {a.game or 'unspecified'}\n"
        f"pasted_at: {db.utcnow()}\n\n---\n\n{text}", encoding="utf-8")

    figures = extract(text)
    db.init(); con = db.connect()
    db.insert_manual(con, [{
        "pasted_at": db.utcnow(), "source_account": a.account, "posted_at": a.posted,
        "book": a.book, "game_id": a.game, "note": text.strip()[:2000],
        "extracted_json": {"provenance": "USER-PROVIDED", "figures": figures,
                           "file": str(path.relative_to(ROOT))},
    }])

    print(f"saved  {path.relative_to(ROOT)}")
    if figures:
        print("extracted " + ", ".join(f"{k.replace('_pct','')}={v:.0f}%" for k, v in figures.items()))
    else:
        print("extracted no unambiguous figures — stored as a note only.")
        print("  (a bare '74%' is not extracted: reporting ticket% as handle% is worse than not knowing)")

    if a.book and a.book.lower() in {"pikkit"}:
        print("NOTE Pikkit is tracked-bettor action, not book handle. Stored as its own line,")
        print("     never merged into a book split.")

    for c in conflicts(con, a.book, a.game, figures):
        print(f"CONFLICT {c}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
