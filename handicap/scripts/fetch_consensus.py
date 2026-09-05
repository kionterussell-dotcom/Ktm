#!/usr/bin/env python
"""consensus splits. UNCALIBRATED — run with --calibrate before trusting it.

Configuration and parse live in lib/scrapers.py, shared with the other
table-shaped splits sources.
"""
import argparse, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import db, scrapers

SOURCE = "consensus"

def run(con, sport, ttl_s=900):
    return scrapers.run(con, SOURCE, sport, ttl_s)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("sport", choices=["nfl", "cfb"])
    ap.add_argument("--calibrate", action="store_true")
    a = ap.parse_args()
    if a.calibrate:
        scrapers.calibrate(SOURCE, a.sport); sys.exit(0)
    db.init(); r = run(db.connect(), a.sport, ttl_s=0)
    print(f"{r.source}: {r.status} rows={r.rows} {r.detail}")
    sys.exit(0 if r.status == "OK" else 1)
