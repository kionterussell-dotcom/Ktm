#!/usr/bin/env python
"""The desk — local dashboard.

Runs on your machine, reads data/desk.db, and can trigger a scan. It is a local
app rather than a hosted page on purpose: it needs to run the fetchers and read
a local SQLite file, and a sandboxed web page can do neither.

    python dashboard/app.py          # http://127.0.0.1:5057
"""
from __future__ import annotations

import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, jsonify, render_template, request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from lib import db                                            # noqa: E402
from lib.registry import SOURCES                              # noqa: E402
from lib import model as M                                     # noqa: E402
from lib.picks import Candidate, rank                          # noqa: E402
from lib.signals import GameSplits, evaluate_market, verification_state  # noqa: E402

app = Flask(__name__)

# Books the desk wants to see side by side. Order is the display order: the two
# fetchable primaries first, then aggregates, then the paste-only books — so a
# glance down the column tells you whether the read is fetched or pasted.
BOOK_ORDER = ["DraftKings", "Circa", "Action Network", "Consensus",
              "BetMGM", "FanDuel", "Pikkit", "BetOnline"]
PASTE_ONLY = {"BetMGM", "FanDuel", "Pikkit"}

RESOURCES_NFL = [
    {"name": "VSiN splits (DK)",   "url": "https://data.vsin.com/betting-splits/?view=nfl&book=draftkings", "tag": "fetched"},
    {"name": "VSiN splits (Circa)","url": "https://data.vsin.com/betting-splits/?view=nfl&book=circa",      "tag": "fetched"},
    {"name": "Action Network NFL", "url": "https://www.actionnetwork.com/nfl/public-betting",               "tag": "free tier"},
    {"name": "Action Network CFB", "url": "https://www.actionnetwork.com/ncaaf/public-betting",             "tag": "free tier"},
    {"name": "ScoresAndOdds",      "url": "https://www.scoresandodds.com/nfl",                              "tag": "consensus"},
    {"name": "Covers consensus",   "url": "https://www.covers.com/sport/football/nfl/consensus",            "tag": "consensus"},
    {"name": "SportsBettingDime",  "url": "https://www.sportsbettingdime.com/nfl/public-betting-trends/",   "tag": "consensus"},
    {"name": "BetOnline (opener)", "url": "https://www.betonline.ag/sportsbook/football/nfl",               "tag": "origination"},
    {"name": "Circa Sports",       "url": "https://www.circasports.com/",                                   "tag": "sharp book"},
    {"name": "@DaveMasonBOL",      "url": "https://x.com/DaveMasonBOL",     "tag": "liability · paste"},
    {"name": "@BetMGMnews",        "url": "https://x.com/BetMGMnews",       "tag": "liability · paste"},
    {"name": "@johnewing",         "url": "https://x.com/johnewing",        "tag": "liability · paste"},
    {"name": "@PatrickE_Vegas",    "url": "https://x.com/PatrickE_Vegas",   "tag": "liability · paste"},
]


CALIBRATION_PATH = ROOT / "data" / "model_calibration.json"


def calibration():
    return M.load_calibration(CALIBRATION_PATH)


def team_efficiency(con, sport: str) -> dict:
    """Latest efficiency snapshot per team."""
    rows = con.execute(
        """SELECT e.* FROM efficiency e
           JOIN (SELECT team, MAX(fetched_at) mx FROM efficiency WHERE sport=? GROUP BY team) m
             ON e.team=m.team AND e.fetched_at=m.mx
           WHERE e.sport=?""", (sport, sport)).fetchall()
    return {r["team"]: dict(r) for r in rows}


def _con():
    db.init()
    return db.connect()


def latest_splits(con, game_id: str) -> list[dict]:
    """Most recent row per (book, market, side). Splits are append-only, so the
    newest row per key is the current picture and the rest is history."""
    rows = con.execute(
        """SELECT s.* FROM splits s
           JOIN (SELECT book, market, side, MAX(fetched_at) AS mx
                 FROM splits WHERE game_id=? GROUP BY book, market, side) m
             ON s.book=m.book AND s.market=m.market AND s.side=m.side AND s.fetched_at=m.mx
           WHERE s.game_id=?""", (game_id, game_id)).fetchall()
    return [dict(r) for r in rows]


def line_bounds(con, game_id: str, market: str) -> tuple[float | None, float | None, int]:
    """(opener, current, books_seen). The opener is only honoured when more than
    one book reported it — CLAUDE.md requires RLM verified across two books."""
    op = con.execute(
        "SELECT number, COUNT(DISTINCT book) n FROM lines "
        "WHERE game_id=? AND market=? AND is_opener=1", (game_id, market)).fetchone()
    cur = con.execute(
        "SELECT number FROM lines WHERE game_id=? AND market=? AND is_opener=0 "
        "ORDER BY fetched_at DESC LIMIT 1", (game_id, market)).fetchone()
    nbooks = con.execute(
        "SELECT COUNT(DISTINCT book) n FROM lines WHERE game_id=? AND market=?",
        (game_id, market)).fetchone()["n"]
    opener = op["number"] if op and op["n"] >= 2 else None
    return opener, (cur["number"] if cur else None), nbooks


def merge_signals(signals: list[dict]) -> list[dict]:
    """Collapse the same label firing at several books into one row.

    Three books reporting PUBLIC OVERLOAD on the same side is one fact about the
    market, not three signals — but which books agree is itself information, so
    the extra books are named rather than dropped. Confirmation across books is
    also what the two-source rule is asking for, so a merged row is stronger.
    """
    groups: dict[tuple, list[dict]] = {}
    for s in signals:
        groups.setdefault((s["label"], s["market"], s["side"]), []).append(s)

    out = []
    for (label, market, side), rows in groups.items():
        rows.sort(key=lambda r: -r["strength"])
        primary = dict(rows[0])
        others = [r["book"] for r in rows[1:] if r["book"]]
        primary["books"] = [r["book"] for r in rows if r["book"]]
        if others:
            primary["detail"] += f"  Confirmed at {', '.join(others)}."
            primary["strength"] = min(5, primary["strength"] + 1)
        out.append(primary)
    out.sort(key=lambda r: (-r["strength"], r["market"]))
    return out


def build_game(con, g: dict) -> dict:
    splits = latest_splits(con, g["game_id"])
    by_book: dict[str, dict] = defaultdict(dict)
    for s in splits:
        by_book[s["book"]][s["market"]] = s

    grid = []
    for book in BOOK_ORDER:
        cells = by_book.get(book, {})
        grid.append({
            "book": book,
            "paste_only": book in PASTE_ONLY,
            "have": bool(cells),
            "markets": {m: {"side": c["side"], "ticket": c["ticket_pct"],
                            "handle": c["handle_pct"], "at": c["fetched_at"],
                            "source": c["source"]}
                        for m, c in cells.items()},
        })

    signals, markets_meta = [], {}
    for market in ("spread", "total", "moneyline"):
        gs = [GameSplits(s["book"], s["market"], s["side"], s["ticket_pct"],
                         s["handle_pct"], s["fetched_at"], s["source"])
              for s in splits if s["market"] == market]
        opener, cur, nbooks = line_bounds(con, g["game_id"], market)
        markets_meta[market] = {"opener": opener, "current": cur, "books": nbooks}
        for sig in evaluate_market(gs, opener, cur):
            signals.append({"label": sig.label, "side": sig.side, "market": market,
                            "strength": sig.strength, "detail": sig.detail,
                            "book": sig.book})
    signals = merge_signals(signals)

    state, why = verification_state(
        [GameSplits(s["book"], s["market"], s["side"], s["ticket_pct"],
                    s["handle_pct"], s["fetched_at"], s["source"]) for s in splits])

    notes = [dict(r) for r in con.execute(
        "SELECT source_account, posted_at, book, note FROM manual WHERE game_id=? "
        "ORDER BY pasted_at DESC LIMIT 5", (g["game_id"],))]

    eff = team_efficiency(con, g["sport"])
    eh, ea = eff.get(g["home"]), eff.get(g["away"])
    matchup, anomaly = None, None
    if eh and ea:
        matchup = {
            "home_net": eh.get("net_epa"), "away_net": ea.get("net_epa"),
            "home_off": eh.get("adj_off_epa"), "away_off": ea.get("adj_off_epa"),
            "home_def": eh.get("adj_def_epa"), "away_def": ea.get("adj_def_epa"),
            "home_pace": eh.get("sec_per_play"), "away_pace": ea.get("sec_per_play"),
            "home_sr": eh.get("off_sr"), "away_sr": ea.get("off_sr"),
            "season": eh.get("season"), "through_week": eh.get("through_week"),
        }
        cal = calibration()
        spread = markets_meta.get("spread", {}).get("current")
        if cal and spread is not None:
            proj = M.project_margin(eh.get("net_epa") or 0, ea.get("net_epa") or 0, cal.fit)
            is_anom, gap = M.line_anomaly(proj, spread)
            matchup["projected_margin"] = round(proj, 1)
            if is_anom:
                anomaly = {"gap": gap, "projected": round(proj, 1), "market": spread}
                signals.insert(0, {
                    "label": "LINE ANOMALY", "side": g["home"] if gap > 0 else g["away"],
                    "market": "spread", "strength": 3, "book": "", "books": [],
                    "detail": (f"Model projects {g['home']} {proj:+.1f}, market has {spread:+.1f} — "
                               f"a {abs(gap):.1f} pt gap with no visible cause. Investigate before "
                               f"betting: an unexplained line usually means the market knows "
                               f"something the box score does not."),
                })

    return {**g, "matchup": matchup, "anomaly": anomaly,
            "grid": grid, "signals": signals, "markets": markets_meta,
            "verification": state, "verification_why": why, "manual_notes": notes,
            "has_any_splits": bool(splits)}


RESOURCES_CFB = [dict(r, url=r["url"].replace("/nfl", "/ncaaf").replace("view=nfl", "view=ncaaf")
                     .replace("football/nfl", "football/ncaaf"),
                     name=r["name"].replace("NFL", "CFB"))
                 for r in RESOURCES_NFL]


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/slate")
def api_slate():
    sport = request.args.get("sport", "nfl")
    date = request.args.get("date")
    con = _con()

    q = "SELECT * FROM games WHERE sport=?"
    args: list = [sport]
    if date:
        q += " AND substr(kickoff_utc,1,10)=?"
        args.append(date)
    q += " ORDER BY kickoff_utc"
    games = [dict(r) for r in con.execute(q, args)]

    return jsonify({
        "sport": sport, "date": date,
        "games": [build_game(con, g) for g in games],
        "status": api_status_payload(con, sport),
        "resources": RESOURCES_CFB if sport == "cfb" else RESOURCES_NFL,
        "book_order": BOOK_ORDER,
    })


def api_status_payload(con, sport: str) -> dict:
    out = []
    for src in SOURCES:
        row = con.execute(
            "SELECT status, rows, fetched_at, detail FROM fetch_log "
            "WHERE source LIKE ? ORDER BY fetched_at DESC LIMIT 1",
            (f"{src.key}:{sport}%",)).fetchone()
        if row is None:
            out.append({"key": src.key, "status": "NOT_BUILT" if not src.built else "never",
                        "rows": 0, "age": None, "detail": src.note,
                        "calibrated": src.calibrated, "built": src.built})
            continue
        then = datetime.fromisoformat(row["fetched_at"])
        mins = (datetime.now(timezone.utc) - then).total_seconds() / 60
        out.append({"key": src.key, "status": row["status"], "rows": row["rows"],
                    "age": round(mins), "detail": row["detail"],
                    "calibrated": src.calibrated, "built": src.built})

    live = {s["key"] for s in out if s["status"] == "OK" and s["key"] in {"vsin", "action", "consensus"}}
    return {
        "sources": out,
        "split_sources_live": len(live),
        # CLAUDE.md: two independent sources minimum before flagging any signal.
        "board_verified": len(live) >= 2,
        "stale_cutoff_min": 120,
    }


@app.get("/api/model")
def api_model():
    cal = calibration()
    if cal is None:
        return jsonify({"validated": False, "verdict": "No backtest has been run. "
                        "Run scripts/backtest.py before the model is used for anything."})
    d = cal.to_dict()
    d["gate"] = ("Model probabilities are NOT used to price bets: it did not beat the closing "
                 "line out of sample. It is used for LINE ANOMALY flags and matchup context only."
                 ) if not cal.validated else (
                 "Model cleared the break-even bar out of sample and is used to price bets.")
    return jsonify(d)


@app.get("/api/status")
def api_status():
    return jsonify(api_status_payload(_con(), request.args.get("sport", "nfl")))


@app.post("/api/scan")
def api_scan():
    """Run the orchestrator. Streams nothing back but the status table — the
    dashboard shows exactly what a terminal run would show."""
    sport = request.json.get("sport", "nfl")
    date = request.json.get("date")
    cmd = [sys.executable, str(ROOT / "scripts" / "run_all.py"), sport, "--fresh"]
    if date:
        cmd += ["--date", date]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=600, cwd=ROOT)
    return jsonify({"exit": p.returncode, "stdout": p.stdout, "stderr": p.stderr[-4000:]})


@app.get("/api/picks")
def api_picks():
    """Top 5 bets and top 5 props.

    Runs the real ranking engine (lib/picks). Every candidate currently arrives
    with model_p=None because the efficiency layer that would produce a win
    probability is not built, so the engine returns them as unpriced flags and
    says so. The moment that layer lands these become ranked cards with an edge
    and a price threshold — no other change needed here.
    """
    sport = request.args.get("sport", "nfl")
    con = _con()
    cands, flags = [], []
    for g in con.execute("SELECT * FROM games WHERE sport=? ORDER BY kickoff_utc", (sport,)):
        game = build_game(con, dict(g))
        if game["verification"] != "VERIFIED" or not game["signals"]:
            continue
        best = max(game["signals"], key=lambda s: s["strength"])
        _, cur, _ = line_bounds(con, game["game_id"], best["market"])
        price = con.execute(
            "SELECT price FROM lines WHERE game_id=? AND market=? AND price IS NOT NULL "
            "ORDER BY fetched_at DESC LIMIT 1", (game["game_id"], best["market"])).fetchone()
        cands.append(Candidate(
            game_id=game["game_id"], away=game["away"], home=game["home"],
            kickoff_utc=game["kickoff_utc"], market=best["market"], side=best["side"],
            number=cur, price=price["price"] if price else -110, book=best.get("book") or "",
            model_p=None, signals=[s["label"] for s in game["signals"]]))
        flags.append({"game_id": game["game_id"], "away": game["away"], "home": game["home"],
                      "kickoff_utc": game["kickoff_utc"], "signal": best,
                      "signals": [s["label"] for s in game["signals"]]})

    picked, summary = rank(cands)
    flags.sort(key=lambda f: -f["signal"]["strength"])
    return jsonify({
        "bets": [{"game_id": p.candidate.game_id, "away": p.candidate.away,
                  "home": p.candidate.home, "market": p.candidate.market,
                  "side": p.candidate.side, "price": p.candidate.price,
                  "edge": p.edge, "breakeven": p.breakeven, "units": p.units,
                  "tier": p.tier, "threshold": p.threshold_price,
                  "bet_worthy": p.bet_worthy, "why_not": p.why_not} for p in picked],
        "flags": flags[:5],
        "summary": summary,
        "bets_blocked": ("" if picked else
                         "The ranking engine ran and priced nothing. The efficiency layer IS built "
                         "and produces a projection, but the model failed its own backtest: 51.8% "
                         "ATS against 2024 closing spreads, under the 52.4% break-even, and the "
                         "closing line predicted final margin better than it did. Quoting win "
                         "probabilities from it would be inventing precision. Flags below are the "
                         "board's strongest market signals, in signal order — not ranked bets."),
        "props": [],
        "props_blocked": "no props feed built yet; handicapper sourcing not wired in.",
        "odds_floor": -160,
    })


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5057, debug=False)
