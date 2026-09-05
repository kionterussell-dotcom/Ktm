#!/usr/bin/env python
"""Current injury report.

Source: ESPN's per-team injuries endpoint. Status plus the designation the desk
needs (OUT / DOUBTFUL / QUESTIONABLE). Point value is NOT estimated here — that
is a judgement call the analysis layer makes with the matchup in front of it,
and a table of invented point values would be exactly the kind of fabricated
number CLAUDE.md forbids.

CFB: ESPN carries little CFB injury data, and CFB reporting is unreliable by
nature (no mandatory report). Treated as best-effort and labelled as such.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import db, rawio
from lib.base import Result
from lib.http import FetchError, get_json

SOURCE = "espn-injuries"
TEAMS = ("https://site.api.espn.com/apis/site/v2/sports/football/{league}/teams")
INJ = ("https://site.api.espn.com/apis/site/v2/sports/football/{league}/teams/{team}/injuries")
LEAGUE = {"nfl": "nfl", "cfb": "college-football"}


def run(con, sport: str, ttl_s: int = 900) -> Result:
    res = Result(source=f"injuries:{sport}")
    try:
        payload = get_json(TEAMS.format(league=LEAGUE[sport]), ttl_s=ttl_s)
    except FetchError as e:
        res.detail = str(e)
        return res

    teams = []
    for grp in (payload.get("sports") or [{}])[0].get("leagues", [{}])[0].get("teams", []):
        t = grp.get("team") or {}
        if t.get("id"):
            teams.append((t["id"], t.get("abbreviation") or t.get("displayName")))
    if not teams:
        res.detail = "ESPN returned no teams"
        return res

    out, errors = [], []
    for tid, abbr in teams:
        try:
            data = get_json(INJ.format(league=LEAGUE[sport], team=tid), ttl_s=ttl_s)
        except FetchError as e:
            errors.append(f"{abbr}: {e}")
            continue
        for grp in data.get("injuries") or []:
            for item in grp.get("injuries") or grp.get("items") or []:
                ath = item.get("athlete") or {}
                out.append({"team": abbr, "player": ath.get("displayName"),
                            "position": ((ath.get("position") or {}).get("abbreviation")),
                            "status": item.get("status"),
                            "detail": (item.get("details") or {}).get("type") or item.get("longComment"),
                            "date": item.get("date")})

    if out or errors:
        res.raw_path = str(rawio.write_raw(f"{SOURCE}-{sport}",
                                           {"fetched_at": db.utcnow(), "injuries": out,
                                            "errors": errors}))
    if not out:
        res.status = "EMPTY" if not errors else "FAILED"
        res.detail = "; ".join(errors[:2]) or "no injuries listed"
        return res

    res.status, res.rows = "OK", len(out)
    key = {"out": 0, "doubtful": 1, "questionable": 2}
    sig = sum(1 for i in out if (i["status"] or "").lower() in key)
    res.detail = f"{len(out)} entries across {len(teams)} teams, {sig} OUT/DOUBTFUL/QUESTIONABLE"
    if sport == "cfb":
        res.detail += "; CFB reporting is voluntary — treat absence of news as no information"
    if errors:
        res.detail += f"; {len(errors)} team errors"
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("sport", choices=["nfl", "cfb"])
    a = ap.parse_args(); db.init()
    r = run(db.connect(), a.sport, ttl_s=0)
    print(f"{r.source}: {r.status} rows={r.rows} {r.detail}")
    sys.exit(0 if r.status in ("OK", "EMPTY") else 1)
