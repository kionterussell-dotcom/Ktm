#!/usr/bin/env python
"""Run every fetcher, load desk.db, print the status table.

This runs at the top of every slate scan. It prints what came back and what did
not, before any analysis happens. A source that failed is reported as failed;
nothing is backfilled from a different source wearing the same name.

    python scripts/run_all.py nfl
    python scripts/run_all.py cfb --date 2026-08-29 --fresh
"""
from __future__ import annotations

import argparse
import importlib
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import db
from lib.base import Result
from lib.registry import BUILT, MISSING

STATUS_RANK = {"FAILED": 0, "NOT_BUILT": 1, "EMPTY": 2, "OK": 3}


def age_of(con, source_prefix: str) -> str:
    row = con.execute(
        "SELECT fetched_at FROM fetch_log WHERE source LIKE ? AND status='OK' "
        "ORDER BY fetched_at DESC LIMIT 1", (f"{source_prefix}%",)).fetchone()
    if not row:
        return "never"
    then = datetime.fromisoformat(row["fetched_at"])
    mins = (datetime.now(timezone.utc) - then).total_seconds() / 60
    if mins < 60:
        return f"{mins:.0f}m"
    if mins < 60 * 48:
        return f"{mins/60:.1f}h"
    return f"{mins/1440:.1f}d"


def run_source(con, src, sport: str, date: str | None, ttl_s: int) -> Result:
    mod = importlib.import_module(src.module)
    kwargs = {"ttl_s": ttl_s}
    if src.module == "fetch_schedule":
        kwargs["date"] = date
    try:
        return mod.run(con, sport, **kwargs)
    except Exception as e:                       # a fetcher crash is a FAILED row, not a dead scan
        return Result(source=f"{src.key}:{sport}", status="FAILED",
                      detail=f"{type(e).__name__}: {e}",
                      notes=[traceback.format_exc(limit=3)])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("sport", choices=["nfl", "cfb"])
    ap.add_argument("--date", help="YYYY-MM-DD slate date")
    ap.add_argument("--fresh", action="store_true",
                    help="bypass cache (required inside 6h of kickoff)")
    a = ap.parse_args()
    ttl_s = 0 if a.fresh else 900

    db.init()
    con = db.connect()
    results: list[Result] = []

    for src in BUILT:
        r = run_source(con, src, a.sport, a.date, ttl_s)
        if not src.calibrated and r.status == "OK":
            r.detail += "  [UNCALIBRATED selectors — verify before trusting]"
        db.log_fetch(con, r.source, r.status, r.rows, r.detail, r.raw_path)
        results.append(r)

    for src in MISSING:
        results.append(Result(source=f"{src.key}:{a.sport}", status="NOT_BUILT", detail=src.note))

    results.sort(key=lambda r: (STATUS_RANK.get(r.status, 9), r.source))

    w = max(len(r.source) for r in results) + 2
    print(f"\n  FETCH STATUS — {a.sport.upper()}"
          f"{'  slate ' + a.date if a.date else ''}"
          f"  ({datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC)")
    print("  " + "-" * (w + 46))
    print(f"  {'SOURCE':<{w}}{'STATUS':<11}{'ROWS':>6}  {'AGE':>6}  DETAIL")
    print("  " + "-" * (w + 46))
    for r in results:
        detail = r.detail if len(r.detail) <= 96 else r.detail[:93] + "..."
        print(f"  {r.source:<{w}}{r.status:<11}{r.rows:>6}  {age_of(con, r.source.split(':')[0]):>6}  {detail}")
    print("  " + "-" * (w + 46))

    ok = [r for r in results if r.status == "OK"]
    failed = [r for r in results if r.status == "FAILED"]
    notbuilt = [r for r in results if r.status == "NOT_BUILT"]
    print(f"  {len(ok)} OK, {len(failed)} FAILED, {len(notbuilt)} NOT_BUILT\n")

    split_sources = {r.source.split(':')[0] for r in ok
                     if r.source.split(':')[0] in {"vsin", "action", "consensus"}}
    if len(split_sources) < 2:
        print("  MISSING FROM THIS READ: fewer than two independent split sources returned.")
        print("  Per CLAUDE.md every game on this slate is UNVERIFIED. Do not flag signals.\n")

    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
