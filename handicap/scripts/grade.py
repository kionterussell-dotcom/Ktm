#!/usr/bin/env python
"""Log plays and grade them with CLV.

CLAUDE.md: show CLV on every graded play, report the running record including
losses, no spin. The summary here reports losing streaks and negative-CLV plays
as prominently as wins, because a winning record built on bad CLV is variance
that has not corrected yet.

    python scripts/grade.py log  --game nfl-20260913-DAL-PHI --side DAL --market spread \\
                                 --number -3 --price -110 --book DraftKings --units 2 \\
                                 --signal "BOOK NEED" --why "74/72 DK, line static"
    python scripts/grade.py close --play 1 --number -3.5 --price -115
    python scripts/grade.py result --play 1 --outcome win
    python scripts/grade.py report --since 2026-09-01
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import db
from lib.odds import clv_points, clv_pts

UNIT_PAYOUT = lambda price, units: units * (100 / -price) if price < 0 else units * (price / 100)


def cmd_log(con, a) -> None:
    with con:
        cur = con.execute(
            """INSERT INTO plays (logged_at,game_id,sport,market,side,number,price,book,
                                  units,signal_label,rationale)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (db.utcnow(), a.game, a.sport, a.market, a.side, a.number, a.price, a.book,
             a.units, a.signal, a.why))
    print(f"logged play #{cur.lastrowid}: {a.units}u {a.side} {a.number or ''} {a.price} @ {a.book}")


def cmd_close(con, a) -> None:
    """Record the closing number/price so CLV can be computed at grade time."""
    p = con.execute("SELECT * FROM plays WHERE id=?", (a.play,)).fetchone()
    if not p:
        sys.exit(f"no play #{a.play}")
    with con:
        con.execute(
            """INSERT INTO results (play_id,graded_at,result,closing_number,closing_price,clv,units_delta)
               VALUES (?,?,'void',?,?,NULL,NULL)
               ON CONFLICT DO NOTHING""", (a.play, db.utcnow(), a.number, a.price))
        con.execute("UPDATE results SET closing_number=?, closing_price=? WHERE play_id=?",
                    (a.number, a.price, a.play))
    print(f"play #{a.play}: close recorded {a.number} {a.price}")


def cmd_result(con, a) -> None:
    p = con.execute("SELECT * FROM plays WHERE id=?", (a.play,)).fetchone()
    if not p:
        sys.exit(f"no play #{a.play}")
    r = con.execute("SELECT * FROM results WHERE play_id=?", (a.play,)).fetchone()
    close_num = r["closing_number"] if r else None
    close_price = r["closing_price"] if r else None

    clv = None
    if close_price is not None:
        clv = round(clv_pts(p["price"], close_price), 2)
    elif close_num is not None and p["number"] is not None:
        clv = round(clv_points(p["number"], close_num, p["number"] < 0), 2)

    delta = {"win": UNIT_PAYOUT(p["price"], p["units"]),
             "loss": -p["units"], "push": 0.0, "void": 0.0}[a.outcome]

    with con:
        if r:
            con.execute("UPDATE results SET result=?, clv=?, units_delta=?, graded_at=? WHERE play_id=?",
                        (a.outcome, clv, round(delta, 3), db.utcnow(), a.play))
        else:
            con.execute("""INSERT INTO results (play_id,graded_at,result,closing_number,
                                                closing_price,clv,units_delta)
                           VALUES (?,?,?,?,?,?,?)""",
                        (a.play, db.utcnow(), a.outcome, None, None, None, round(delta, 3)))
    print(f"play #{a.play}: {a.outcome}  {delta:+.2f}u  CLV {clv if clv is not None else 'n/a (no close recorded)'}")


def cmd_report(con, a) -> None:
    q = """SELECT p.*, r.result, r.clv, r.units_delta, r.closing_number, r.closing_price
           FROM plays p LEFT JOIN results r ON r.play_id = p.id"""
    args: list = []
    if a.since:
        q += " WHERE p.logged_at >= ?"; args.append(a.since)
    q += " ORDER BY p.logged_at"
    rows = [dict(x) for x in con.execute(q, args)]
    if not rows:
        print("no plays logged for that period."); return

    graded = [r for r in rows if r["result"] in ("win", "loss", "push")]
    w = sum(1 for r in graded if r["result"] == "win")
    l = sum(1 for r in graded if r["result"] == "loss")
    pu = sum(1 for r in graded if r["result"] == "push")
    units = sum(r["units_delta"] or 0 for r in graded)
    risked = sum(r["units"] for r in graded if r["result"] != "push")
    clvs = [r["clv"] for r in graded if r["clv"] is not None]

    print(f"\n  RECORD  {w}-{l}" + (f"-{pu}" if pu else ""))
    print(f"  UNITS   {units:+.2f}u on {risked:.1f}u risked"
          f"  ({(units/risked*100 if risked else 0):+.1f}% ROI)")
    if clvs:
        beat = sum(1 for c in clvs if c > 0)
        print(f"  CLV     {sum(clvs)/len(clvs):+.2f} avg, beat the close {beat}/{len(clvs)}")
    else:
        print("  CLV     no closing numbers recorded — CLV unknown for every play")
    if len(rows) > len(graded):
        print(f"  OPEN    {len(rows)-len(graded)} play(s) not yet graded")

    print("\n  PLAY                                          RESULT   UNITS     CLV")
    for r in rows:
        tag = f"{r['side']} {r['number'] if r['number'] is not None else ''} {r['price']}"
        clv_s = f"{r['clv']:+.2f}" if r["clv"] is not None else "n/a"
        print(f"  #{r['id']:<3} {tag:<24} {(r['signal_label'] or ''):<16} "
              f"{(r['result'] or 'open'):<8} {(r['units_delta'] or 0):+6.2f}  {clv_s:>6}")

    losses = [r for r in graded if r["result"] == "loss"]
    if losses:
        neg = [r for r in losses if r["clv"] is not None and r["clv"] < 0]
        labels = {}
        for r in losses:
            labels[r["signal_label"] or "unlabelled"] = labels.get(r["signal_label"] or "unlabelled", 0) + 1
        common = ", ".join(f"{k} x{v}" for k, v in sorted(labels.items(), key=lambda kv: -kv[1]))
        print(f"\n  LOSSES  {len(losses)} play(s). Signal mix: {common}.")
        if neg:
            print(f"          {len(neg)} of them also had negative CLV — bad price, not just bad luck.")
        elif clvs:
            print("          All losses had non-negative CLV: right side of the number, wrong result.")
    print()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("log")
    for f in ("game", "side", "book", "why"):
        g.add_argument(f"--{f}", required=True)
    g.add_argument("--market", default="spread"); g.add_argument("--sport", default="nfl")
    g.add_argument("--number", type=float); g.add_argument("--price", type=int, required=True)
    g.add_argument("--units", type=float, required=True); g.add_argument("--signal", default="")
    c = sub.add_parser("close"); c.add_argument("--play", type=int, required=True)
    c.add_argument("--number", type=float); c.add_argument("--price", type=int)
    r = sub.add_parser("result"); r.add_argument("--play", type=int, required=True)
    r.add_argument("--outcome", choices=["win", "loss", "push", "void"], required=True)
    rp = sub.add_parser("report"); rp.add_argument("--since")
    a = ap.parse_args()
    db.init(); con = db.connect()
    {"log": cmd_log, "close": cmd_close, "result": cmd_result, "report": cmd_report}[a.cmd](con, a)
