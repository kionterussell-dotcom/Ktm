#!/usr/bin/env python
"""Track handicappers on evidence instead of claims.

You asked me to source handicappers with good track records, especially for
props. The honest position, which is why this tool exists in this shape:

  - Publicly verified long-run prop records essentially do not exist. Records are
    self-reported, and self-reported records are selected: losing months quietly
    stop being posted, plays get regraded, "units" get redefined mid-season.
  - Third-party verification services grade only what a capper chooses to submit,
    which is the same selection problem with a logo on it.
  - So the desk does not store a claimed record as a fact. It stores what a capper
    posted BEFORE the game, then grades it here.

The measure that matters is CLV, not win rate. A capper beating the closing
number consistently is finding real value; one at 55% with negative CLV got lucky
and will regress. CLV is visible in weeks; a win rate needs hundreds of plays.

    python scripts/tout.py add --handle @somecapper --focus props --platform X \
        --claimed "62% on props 2024" --note "found via ..."
    python scripts/tout.py pick --handle @somecapper --game nfl-20260913-DAL-PHI \
        --market prop --side "Lamar over 249.5 pass yds" --number 249.5 --price -115 \
        --book DraftKings --posted 2026-09-13T12:00:00Z
    python scripts/tout.py grade --pick 1 --outcome win --closing-price -130
    python scripts/tout.py report
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import db
from lib.odds import clv_points, clv_pts


def _id(con, handle: str) -> int:
    r = con.execute("SELECT id FROM handicappers WHERE handle=?", (handle,)).fetchone()
    if not r:
        sys.exit(f"unknown handicapper {handle} — add it first")
    return r["id"]


def cmd_add(con, a) -> None:
    with con:
        cur = con.execute(
            """INSERT INTO handicappers (added_at,handle,platform,focus,claimed_record,note)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(handle) DO UPDATE SET platform=excluded.platform,
                   focus=excluded.focus, claimed_record=excluded.claimed_record,
                   note=excluded.note""",
            (db.utcnow(), a.handle, a.platform, a.focus, a.claimed, a.note))
    print(f"tracking {a.handle}" + (f" (claims: {a.claimed})" if a.claimed else ""))
    if a.claimed:
        print("  claimed record stored as a CLAIM, not a fact. It is never used in the report.")


def cmd_pick(con, a) -> None:
    hid = _id(con, a.handle)
    with con:
        cur = con.execute(
            """INSERT INTO handicapper_picks (logged_at,handicapper_id,posted_at,game_id,
                   market,side,number,price,book,source_note)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (db.utcnow(), hid, a.posted, a.game, a.market, a.side, a.number,
             a.price, a.book, a.note))
    print(f"logged pick #{cur.lastrowid} for {a.handle}: {a.side} {a.price or ''}")
    if not a.posted:
        print("  WARNING no --posted timestamp. An ungraded-for-time pick cannot prove it")
        print("  predated the line move. Record when they posted it, not when you saw it.")


def cmd_grade(con, a) -> None:
    p = con.execute("SELECT * FROM handicapper_picks WHERE id=?", (a.pick,)).fetchone()
    if not p:
        sys.exit(f"no pick #{a.pick}")
    clv = None
    if a.closing_price is not None and p["price"] is not None:
        clv = round(clv_pts(p["price"], a.closing_price), 2)
    elif a.closing_number is not None and p["number"] is not None:
        clv = round(clv_points(p["number"], a.closing_number, (p["number"] or 0) < 0), 2)
    with con:
        con.execute("""UPDATE handicapper_picks SET result=?, closing_number=?,
                       closing_price=?, clv=? WHERE id=?""",
                    (a.outcome, a.closing_number, a.closing_price, clv, a.pick))
    print(f"pick #{a.pick}: {a.outcome}, CLV {clv if clv is not None else 'n/a'}")


def cmd_report(con, a) -> None:
    caps = con.execute("SELECT * FROM handicappers ORDER BY handle").fetchall()
    if not caps:
        print("no handicappers tracked yet."); return
    print(f"\n  {'HANDLE':<20}{'FOCUS':<10}{'GRADED':>8}{'W-L':>10}{'WIN%':>7}"
          f"{'AVG CLV':>9}{'BEAT CLOSE':>12}")
    print("  " + "-" * 76)
    for c in caps:
        rows = con.execute(
            "SELECT result, clv FROM handicapper_picks WHERE handicapper_id=? AND result IS NOT NULL",
            (c["id"],)).fetchall()
        w = sum(1 for r in rows if r["result"] == "win")
        l = sum(1 for r in rows if r["result"] == "loss")
        clvs = [r["clv"] for r in rows if r["clv"] is not None]
        winp = f"{w/(w+l)*100:.1f}%" if (w + l) else "—"
        avg = f"{sum(clvs)/len(clvs):+.2f}" if clvs else "—"
        beat = f"{sum(1 for x in clvs if x > 0)}/{len(clvs)}" if clvs else "—"
        print(f"  {c['handle']:<20}{(c['focus'] or ''):<10}{len(rows):>8}{f'{w}-{l}':>10}"
              f"{winp:>7}{avg:>9}{beat:>12}")
        if c["claimed_record"]:
            print(f"    claims: {c['claimed_record']}  (unverified — not counted above)")

    total = con.execute("SELECT COUNT(*) n FROM handicapper_picks WHERE result IS NOT NULL").fetchone()["n"]
    print(f"\n  {total} graded pick(s) tracked.")
    if total < 50:
        print("  Too few to judge anyone. A win rate needs hundreds of plays to separate")
        print("  skill from variance; CLV separates it in dozens. Watch the CLV column first.")
    print()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd", required=True)
    A = sub.add_parser("add"); A.add_argument("--handle", required=True)
    A.add_argument("--platform"); A.add_argument("--focus"); A.add_argument("--claimed")
    A.add_argument("--note")
    P = sub.add_parser("pick"); P.add_argument("--handle", required=True)
    P.add_argument("--game"); P.add_argument("--market", default="prop")
    P.add_argument("--side", required=True); P.add_argument("--number", type=float)
    P.add_argument("--price", type=int); P.add_argument("--book"); P.add_argument("--posted")
    P.add_argument("--note")
    G = sub.add_parser("grade"); G.add_argument("--pick", type=int, required=True)
    G.add_argument("--outcome", choices=["win", "loss", "push", "void"], required=True)
    G.add_argument("--closing-number", type=float); G.add_argument("--closing-price", type=int)
    R = sub.add_parser("report")
    a = ap.parse_args(); db.init(); con = db.connect()
    {"add": cmd_add, "pick": cmd_pick, "grade": cmd_grade, "report": cmd_report}[a.cmd](con, a)
