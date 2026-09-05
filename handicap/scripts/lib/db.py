"""SQLite layer for the handicapping desk.

Provenance is enforced, not requested: every write goes through a helper that
refuses a row without source and a fetch timestamp. There is no bare execute()
path exported for the data tables.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
# DESK_DB lets a UI check or a test run against a throwaway file so desk.db only
# ever holds rows that came from a real fetch.
DB_PATH = Path(os.environ.get("DESK_DB") or ROOT / "data" / "desk.db")

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- Canonical slate. Needed so the desk can order a slate by kickoff and so
-- weather has a venue to look up. game_id is our own stable key:
--   <sport>-<YYYYMMDD>-<AWAY>-<HOME>   e.g. nfl-20260913-DAL-PHI
CREATE TABLE IF NOT EXISTS games (
    game_id     TEXT PRIMARY KEY,
    sport       TEXT NOT NULL CHECK (sport IN ('nfl','cfb')),
    season      INTEGER,
    week        TEXT,
    away        TEXT NOT NULL,
    home        TEXT NOT NULL,
    kickoff_utc TEXT NOT NULL,
    venue       TEXT,
    is_dome     INTEGER,
    lat         REAL,
    lon         REAL,
    source      TEXT NOT NULL,
    fetched_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_games_slate ON games(sport, kickoff_utc);

CREATE TABLE IF NOT EXISTS splits (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    fetched_at  TEXT NOT NULL,
    source      TEXT NOT NULL,
    book        TEXT NOT NULL,
    sport       TEXT NOT NULL,
    game_id     TEXT NOT NULL,
    away        TEXT,
    home        TEXT,
    market      TEXT NOT NULL CHECK (market IN ('spread','total','moneyline')),
    side        TEXT NOT NULL,
    ticket_pct  REAL,
    handle_pct  REAL,
    raw_json    TEXT
);
CREATE INDEX IF NOT EXISTS idx_splits_game ON splits(game_id, fetched_at);
CREATE INDEX IF NOT EXISTS idx_splits_book ON splits(book, market);

CREATE TABLE IF NOT EXISTS lines (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    fetched_at  TEXT NOT NULL,
    source      TEXT NOT NULL,
    book        TEXT NOT NULL,
    game_id     TEXT NOT NULL,
    market      TEXT NOT NULL CHECK (market IN ('spread','total','moneyline')),
    side        TEXT,
    number      REAL,
    price       INTEGER,
    is_opener   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_lines_game ON lines(game_id, market, fetched_at);

-- Pasted content from gated sources. Never blended into splits: a tracked-bettor
-- number (Pikkit) and a book-handle number are different data classes.
CREATE TABLE IF NOT EXISTS manual (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    pasted_at      TEXT NOT NULL,
    source_account TEXT NOT NULL,
    posted_at      TEXT,
    book           TEXT,
    game_id        TEXT,
    note           TEXT NOT NULL,
    extracted_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_manual_game ON manual(game_id, pasted_at);

CREATE TABLE IF NOT EXISTS plays (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    logged_at    TEXT NOT NULL,
    game_id      TEXT NOT NULL,
    sport        TEXT,
    market       TEXT,
    side         TEXT NOT NULL,
    number       REAL,
    price        INTEGER,
    book         TEXT NOT NULL,
    units        REAL NOT NULL,
    signal_label TEXT,
    rationale    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_plays_game ON plays(game_id, logged_at);

CREATE TABLE IF NOT EXISTS results (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    play_id        INTEGER NOT NULL REFERENCES plays(id),
    graded_at      TEXT NOT NULL,
    result         TEXT NOT NULL CHECK (result IN ('win','loss','push','void')),
    closing_number REAL,
    closing_price  INTEGER,
    clv            REAL,
    units_delta    REAL
);
CREATE INDEX IF NOT EXISTS idx_results_play ON results(play_id);

-- Team efficiency, one row per team per (season, through_week) snapshot, so a
-- rating can always be recomputed as it stood before a given week.
CREATE TABLE IF NOT EXISTS efficiency (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    fetched_at    TEXT NOT NULL,
    source        TEXT NOT NULL,
    sport         TEXT NOT NULL,
    season        INTEGER NOT NULL,
    through_week  INTEGER,
    team          TEXT NOT NULL,
    adj_off_epa   REAL, adj_def_epa REAL, net_epa REAL,
    off_sr        REAL, def_sr REAL,
    off_explosive REAL, def_explosive REAL,
    off_pass_epa  REAL, off_rush_epa REAL,
    early_down_pass_rate REAL, sec_per_play REAL,
    off_plays     INTEGER, def_plays INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_eff_key
    ON efficiency(sport, season, through_week, team);

-- Handicappers the desk tracks. Claimed records are not stored as facts; only
-- graded picks with CLV are, so a tout is judged on what it did here.
CREATE TABLE IF NOT EXISTS handicappers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    added_at    TEXT NOT NULL,
    handle      TEXT NOT NULL UNIQUE,
    platform    TEXT,
    focus       TEXT,
    claimed_record TEXT,
    note        TEXT
);
CREATE TABLE IF NOT EXISTS handicapper_picks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    logged_at     TEXT NOT NULL,
    handicapper_id INTEGER NOT NULL REFERENCES handicappers(id),
    posted_at     TEXT,
    game_id       TEXT,
    market        TEXT,
    side          TEXT NOT NULL,
    number        REAL,
    price         INTEGER,
    book          TEXT,
    result        TEXT CHECK (result IN ('win','loss','push','void')),
    closing_number REAL,
    closing_price  INTEGER,
    clv           REAL,
    source_note   TEXT
);
CREATE INDEX IF NOT EXISTS idx_hpicks ON handicapper_picks(handicapper_id, posted_at);

-- Per-run provenance for the status table the orchestrator prints.
CREATE TABLE IF NOT EXISTS fetch_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    fetched_at  TEXT NOT NULL,
    source      TEXT NOT NULL,
    status      TEXT NOT NULL,
    rows        INTEGER NOT NULL DEFAULT 0,
    detail      TEXT,
    raw_path    TEXT
);
CREATE INDEX IF NOT EXISTS idx_fetchlog ON fetch_log(source, fetched_at);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: os.PathLike | str | None = None) -> sqlite3.Connection:
    p = Path(path) if path else DB_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p)
    con.row_factory = sqlite3.Row
    return con


def init(path: os.PathLike | str | None = None) -> Path:
    con = connect(path)
    with con:
        con.executescript(SCHEMA)
    con.close()
    return Path(path) if path else DB_PATH


class ProvenanceError(ValueError):
    """A row arrived without a source or a fetch timestamp."""


def _require(row: dict, fields: Iterable[str], table: str) -> None:
    missing = [f for f in fields if row.get(f) in (None, "")]
    if missing:
        raise ProvenanceError(f"{table}: row missing {missing} -> {row!r}")


def make_game_id(sport: str, kickoff_utc: str, away: str, home: str) -> str:
    day = kickoff_utc[:10].replace("-", "")
    norm = lambda s: "".join(ch for ch in s.upper() if ch.isalnum())[:12]
    return f"{sport}-{day}-{norm(away)}-{norm(home)}"


def insert_games(con: sqlite3.Connection, rows: list[dict]) -> int:
    for r in rows:
        _require(r, ("game_id", "sport", "away", "home", "kickoff_utc", "source", "fetched_at"), "games")
    with con:
        con.executemany(
            """INSERT INTO games (game_id,sport,season,week,away,home,kickoff_utc,venue,
                                  is_dome,lat,lon,source,fetched_at)
               VALUES (:game_id,:sport,:season,:week,:away,:home,:kickoff_utc,:venue,
                       :is_dome,:lat,:lon,:source,:fetched_at)
               ON CONFLICT(game_id) DO UPDATE SET
                   kickoff_utc=excluded.kickoff_utc, venue=excluded.venue,
                   is_dome=excluded.is_dome, lat=excluded.lat, lon=excluded.lon,
                   source=excluded.source, fetched_at=excluded.fetched_at""",
            [{**{k: None for k in ("season", "week", "venue", "is_dome", "lat", "lon")}, **r} for r in rows],
        )
    return len(rows)


def insert_splits(con: sqlite3.Connection, rows: list[dict]) -> int:
    """Splits are append-only. History is the point: a split at 11:42a and the
    same split at 4:10p are two facts, not one row to overwrite."""
    for r in rows:
        _require(r, ("fetched_at", "source", "book", "sport", "game_id", "market", "side"), "splits")
        if r.get("ticket_pct") is None and r.get("handle_pct") is None:
            raise ProvenanceError(f"splits: row has neither ticket_pct nor handle_pct -> {r!r}")
        if isinstance(r.get("raw_json"), (dict, list)):
            r["raw_json"] = json.dumps(r["raw_json"], separators=(",", ":"))
    with con:
        con.executemany(
            """INSERT INTO splits (fetched_at,source,book,sport,game_id,away,home,market,
                                   side,ticket_pct,handle_pct,raw_json)
               VALUES (:fetched_at,:source,:book,:sport,:game_id,:away,:home,:market,
                       :side,:ticket_pct,:handle_pct,:raw_json)""",
            [{**{k: None for k in ("away", "home", "ticket_pct", "handle_pct", "raw_json")}, **r} for r in rows],
        )
    return len(rows)


def insert_lines(con: sqlite3.Connection, rows: list[dict]) -> int:
    for r in rows:
        _require(r, ("fetched_at", "source", "book", "game_id", "market"), "lines")
    with con:
        con.executemany(
            """INSERT INTO lines (fetched_at,source,book,game_id,market,side,number,price,is_opener)
               VALUES (:fetched_at,:source,:book,:game_id,:market,:side,:number,:price,:is_opener)""",
            [{**{"side": None, "number": None, "price": None, "is_opener": 0}, **r} for r in rows],
        )
    return len(rows)


def insert_manual(con: sqlite3.Connection, rows: list[dict]) -> int:
    for r in rows:
        _require(r, ("pasted_at", "source_account", "note"), "manual")
        if isinstance(r.get("extracted_json"), (dict, list)):
            r["extracted_json"] = json.dumps(r["extracted_json"], separators=(",", ":"))
    with con:
        con.executemany(
            """INSERT INTO manual (pasted_at,source_account,posted_at,book,game_id,note,extracted_json)
               VALUES (:pasted_at,:source_account,:posted_at,:book,:game_id,:note,:extracted_json)""",
            [{**{k: None for k in ("posted_at", "book", "game_id", "extracted_json")}, **r} for r in rows],
        )
    return len(rows)


def log_fetch(con: sqlite3.Connection, source: str, status: str, rows: int,
              detail: str | None = None, raw_path: str | None = None) -> None:
    with con:
        con.execute(
            "INSERT INTO fetch_log (fetched_at,source,status,rows,detail,raw_path) VALUES (?,?,?,?,?,?)",
            (utcnow(), source, status, rows, detail, raw_path),
        )
