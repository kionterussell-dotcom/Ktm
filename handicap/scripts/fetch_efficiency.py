#!/usr/bin/env python
"""Team efficiency from nflverse play-by-play.

Unlike the splits scrapers this one is calibrated, because nflverse publishes
versioned parquet on GitHub releases and the schema is stable. It is the only
data source in the desk that has been run end to end against real data.

CFB is not covered: there is no equivalent free play-by-play release with EPA
already modelled. See --help for what CFB would need.

    python scripts/fetch_efficiency.py nfl --season 2024
    python scripts/fetch_efficiency.py nfl --season 2024 --through-week 10
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import db, rawio
from lib.base import Result
from lib.efficiency import team_table
from lib.http import FetchError

SOURCE = "nflverse"
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "nflverse"
URL = "https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_{season}.parquet"


def local_pbp(season: int, refresh: bool = False) -> Path:
    """Download the season's play-by-play if we do not already hold it."""
    DATA.mkdir(parents=True, exist_ok=True)
    path = DATA / f"pbp_{season}.parquet"
    if path.exists() and not refresh:
        return path
    import requests
    url = URL.format(season=season)
    try:
        r = requests.get(url, timeout=180, stream=True)
    except requests.RequestException as e:
        raise FetchError(f"nflverse {season}: {type(e).__name__}: {e}") from e
    if r.status_code != 200:
        raise FetchError(f"nflverse {season}: HTTP {r.status_code} for {url}")
    tmp = path.with_suffix(".part")
    with open(tmp, "wb") as fh:
        for chunk in r.iter_content(1 << 20):
            fh.write(chunk)
    tmp.rename(path)
    return path


def run(con, sport: str, season: int | None = None, through_week: int | None = None,
        ttl_s: int = 900) -> Result:
    res = Result(source=f"efficiency:{sport}")
    if sport != "nfl":
        res.status = "NOT_BUILT"
        res.detail = ("no free CFB play-by-play release with modelled EPA. CFB needs a "
                      "collegefootballdata.com key (free tier) or cfbfastR data.")
        return res

    # Before week 1 the current season's file does not exist yet. Fall back one
    # season rather than failing: last year's ratings are the honest starting
    # prior for week 1 anyway, and the fallback is reported, not hidden.
    wanted = season or pd.Timestamp.now("UTC").year
    tried, path, fell_back = [], None, False
    for candidate in ([wanted] if season else [wanted, wanted - 1]):
        try:
            path = local_pbp(candidate)
            fell_back = candidate != wanted
            season = candidate
            break
        except FetchError as e:
            tried.append(str(e))
    if path is None:
        res.detail = "; ".join(tried)
        return res

    pbp = pd.read_parquet(path)
    t = team_table(pbp, through_week)
    if t.empty:
        res.status, res.detail = "EMPTY", f"no scrimmage plays before week {through_week}"
        return res

    t["net_epa"] = t["adj_off_epa"] - t["adj_def_epa"]
    now = db.utcnow()
    rows = []
    for r in t.to_dict("records"):
        rows.append({
            "fetched_at": now, "source": SOURCE, "sport": sport, "season": season,
            "through_week": through_week, "team": r["team"],
            "adj_off_epa": r.get("adj_off_epa"), "adj_def_epa": r.get("adj_def_epa"),
            "net_epa": r.get("net_epa"), "off_sr": r.get("off_sr"), "def_sr": r.get("def_sr"),
            "off_explosive": r.get("off_explosive"), "def_explosive": r.get("def_explosive"),
            "off_pass_epa": r.get("off_pass_epa"), "off_rush_epa": r.get("off_rush_epa"),
            "early_down_pass_rate": r.get("early_down_pass_rate"),
            "sec_per_play": r.get("sec_per_play"),
            "off_plays": int(r.get("off_plays") or 0), "def_plays": int(r.get("def_plays") or 0),
        })

    with con:
        con.executemany(
            """INSERT INTO efficiency (fetched_at,source,sport,season,through_week,team,
                   adj_off_epa,adj_def_epa,net_epa,off_sr,def_sr,off_explosive,def_explosive,
                   off_pass_epa,off_rush_epa,early_down_pass_rate,sec_per_play,off_plays,def_plays)
               VALUES (:fetched_at,:source,:sport,:season,:through_week,:team,
                   :adj_off_epa,:adj_def_epa,:net_epa,:off_sr,:def_sr,:off_explosive,:def_explosive,
                   :off_pass_epa,:off_rush_epa,:early_down_pass_rate,:sec_per_play,:off_plays,:def_plays)
               ON CONFLICT(sport,season,through_week,team) DO UPDATE SET
                   fetched_at=excluded.fetched_at, adj_off_epa=excluded.adj_off_epa,
                   adj_def_epa=excluded.adj_def_epa, net_epa=excluded.net_epa,
                   off_sr=excluded.off_sr, def_sr=excluded.def_sr,
                   off_explosive=excluded.off_explosive, def_explosive=excluded.def_explosive,
                   off_pass_epa=excluded.off_pass_epa, off_rush_epa=excluded.off_rush_epa,
                   early_down_pass_rate=excluded.early_down_pass_rate,
                   sec_per_play=excluded.sec_per_play, off_plays=excluded.off_plays,
                   def_plays=excluded.def_plays""", rows)

    res.raw_path = str(rawio.write_raw(f"efficiency-{sport}-{season}",
                                       {"fetched_at": now, "season": season,
                                        "through_week": through_week, "teams": rows}))
    res.status, res.rows = "OK", len(rows)
    res.detail = f"{len(rows)} teams, season {season}" + (f" through week {through_week}" if through_week else "")
    if fell_back:
        res.detail += f" (season {wanted} not published yet — using {season} as the prior)"
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("sport", choices=["nfl", "cfb"])
    ap.add_argument("--season", type=int)
    ap.add_argument("--through-week", type=int)
    a = ap.parse_args()
    db.init(); con = db.connect()
    r = run(con, a.sport, a.season, a.through_week)
    print(f"{r.source}: {r.status} rows={r.rows} {r.detail}")
    if r.status == "OK":
        print(f"\n  {'TEAM':<6}{'net EPA':>9}{'off':>8}{'def':>8}{'SR':>7}{'pace':>7}")
        for row in con.execute(
            "SELECT team,net_epa,adj_off_epa,adj_def_epa,off_sr,sec_per_play FROM efficiency "
            "WHERE sport=? AND season=? AND through_week IS ? ORDER BY net_epa DESC LIMIT 10",
            (a.sport, a.season or 0, a.through_week)):
            print(f"  {row['team']:<6}{row['net_epa']:>9.3f}{row['adj_off_epa']:>8.3f}"
                  f"{row['adj_def_epa']:>8.3f}{(row['off_sr'] or 0):>7.3f}{(row['sec_per_play'] or 0):>7.1f}")
    sys.exit(0 if r.status in ("OK", "EMPTY") else 1)
