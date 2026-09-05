#!/usr/bin/env python
"""Weather for outdoor venues.

CLAUDE.md: wind over 15 mph is the only variable that reliably moves totals, so
that is what this surfaces first. Dome games are skipped, not fetched and
discarded — a dome game has no weather, which is different from missing weather.

Source: Open-Meteo (open JSON API, no key). Venue coordinates come from
Open-Meteo's geocoder keyed on the ESPN venue city, cached to data/cache.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import db, rawio
from lib.base import Result
from lib.http import FetchError, get_json

SOURCE = "weather"
GEO = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST = "https://api.open-meteo.com/v1/forecast"
WIND_THRESHOLD_MPH = 15.0


def geocode(city: str, state: str | None = None) -> tuple[float, float] | None:
    payload = get_json(GEO, params={"name": city, "count": 5, "country": "US"}, ttl_s=86400 * 30)
    for r in payload.get("results") or []:
        if state and r.get("admin1") and state.lower() not in r["admin1"].lower():
            continue
        return float(r["latitude"]), float(r["longitude"])
    return None


def forecast_at(lat: float, lon: float, kickoff_utc: str) -> dict | None:
    payload = get_json(FORECAST, params={
        "latitude": lat, "longitude": lon,
        "hourly": "temperature_2m,precipitation,wind_speed_10m,wind_gusts_10m",
        "temperature_unit": "fahrenheit", "wind_speed_unit": "mph",
        "timezone": "UTC", "forecast_days": 16,
    }, ttl_s=3600)
    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    if not times:
        return None
    target = kickoff_utc[:13]                      # YYYY-MM-DDTHH
    for i, t in enumerate(times):
        if t[:13] == target:
            return {
                "valid_utc": t,
                "temp_f": (hourly.get("temperature_2m") or [None])[i],
                "precip_in": (hourly.get("precipitation") or [None])[i],
                "wind_mph": (hourly.get("wind_speed_10m") or [None])[i],
                "gust_mph": (hourly.get("wind_gusts_10m") or [None])[i],
            }
    return None                                     # kickoff outside the forecast window


def run(con, sport: str, ttl_s: int = 3600) -> Result:
    res = Result(source=f"{SOURCE}:{sport}")
    games = con.execute(
        "SELECT game_id, away, home, venue, is_dome, lat, lon, kickoff_utc FROM games "
        "WHERE sport=? AND kickoff_utc >= ? ORDER BY kickoff_utc",
        (sport, datetime.now(timezone.utc).isoformat(timespec="seconds")),
    ).fetchall()
    if not games:
        res.status, res.detail = "EMPTY", "no upcoming games in games table — run fetch_schedule first"
        return res

    out, errors, skipped = [], [], 0
    for g in games:
        if g["is_dome"]:
            skipped += 1
            continue
        lat, lon = g["lat"], g["lon"]
        if lat is None or lon is None:
            city = (g["venue"] or "").split(",")[0]
            if not city:
                errors.append(f"{g['game_id']}: no venue to geocode"); continue
            try:
                coords = geocode(city)
            except FetchError as e:
                errors.append(f"{g['game_id']}: geocode {e}"); continue
            if not coords:
                errors.append(f"{g['game_id']}: geocode found nothing for {city!r}"); continue
            lat, lon = coords
            with con:
                con.execute("UPDATE games SET lat=?, lon=? WHERE game_id=?", (lat, lon, g["game_id"]))
        try:
            wx = forecast_at(lat, lon, g["kickoff_utc"])
        except FetchError as e:
            errors.append(f"{g['game_id']}: forecast {e}"); continue
        if not wx:
            errors.append(f"{g['game_id']}: kickoff outside forecast window"); continue
        wx.update(game_id=g["game_id"], away=g["away"], home=g["home"], venue=g["venue"],
                  wind_flag=bool(wx["wind_mph"] and wx["wind_mph"] >= WIND_THRESHOLD_MPH))
        out.append(wx)

    if out or errors:
        res.raw_path = str(rawio.write_raw(f"{SOURCE}-{sport}",
                                           {"fetched_at": db.utcnow(), "forecasts": out,
                                            "domes_skipped": skipped, "errors": errors}))
    if not out:
        res.detail = "; ".join(errors[:3]) or "nothing to fetch"
        res.status = "EMPTY" if not errors else "FAILED"
        return res
    res.status, res.rows = "OK", len(out)
    windy = sum(1 for w in out if w["wind_flag"])
    res.detail = f"{len(out)} outdoor games, {windy} over {WIND_THRESHOLD_MPH:.0f}mph, {skipped} domes skipped"
    if errors:
        res.detail += f"; {len(errors)} errors"
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("sport", choices=["nfl", "cfb"])
    a = ap.parse_args(); db.init()
    r = run(db.connect(), a.sport, ttl_s=0)
    print(f"{r.source}: {r.status} rows={r.rows} {r.detail}")
    sys.exit(0 if r.status in ("OK", "EMPTY") else 1)
