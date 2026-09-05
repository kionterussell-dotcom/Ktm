# Handicapping desk

Market-first NFL / CFB betting desk. Rules of engagement live in `CLAUDE.md`; this
file is how to run it.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium          # only needed once a JS-rendered source is added
python scripts/init_db.py
```

## Daily use

```bash
python scripts/run_all.py nfl --fresh          # scan; --fresh bypasses cache
python scripts/run_all.py cfb --date 2026-08-29
python dashboard/app.py                        # http://127.0.0.1:5057
```

`run_all.py` prints a status table before anything is analysed: which sources
returned, which failed and why, and which are not built. If fewer than two
independent split sources return, it says so and the board stays UNVERIFIED.

## State of the build

**Verified against real data** (ran end to end here):

| Layer | Status |
|---|---|
| `data/desk.db` schema + provenance enforcement | done |
| rate-limited cached HTTP, raw-pull writer, fail-loud parse contract | done |
| signal detection, all CLAUDE.md labels + LINE ANOMALY | done, unit-tested |
| odds / no-vig / edge / CLV math | done, unit-tested |
| pick ranking, -160 floor, quarter-Kelly sizing | done, unit-tested |
| play logging + CLV grading | done, tested end to end |
| paste ingestion for gated sources | done, tested |
| handicapper tracking (`scripts/tout.py`) | done, tested |
| **efficiency layer** (`fetch_efficiency.py`, nflverse) | **done, run against real 2023–2025 play-by-play** |
| **model + walk-forward backtest** | **done — and it FAILED its own test, see below** |
| dashboard | done |

**Written but never run against a live response** (no network route in the build
environment): `fetch_schedule.py`, `fetch_context.py` (weather),
`fetch_vsin.py`, `fetch_action.py`, `fetch_consensus.py`, `fetch_lines.py`
(needs `ODDS_API_KEY`), `fetch_injuries.py`. The scrapers among these are
**uncalibrated** — run them with `--calibrate` first.

**Not built:** player props (needs a props feed — `fetch_lines.py --props` has
the market list but is unrun), CFB efficiency (no free play-by-play release with
modelled EPA; needs a collegefootballdata.com key).

## The model failed its backtest, and that is load-bearing

`scripts/backtest.py` runs walk-forward on real data — ratings for week W use
only plays before week W, and the points-per-EPA scale is fit on a prior season.
Against 2024 closing spreads:

```
Every-game ATS: 113-105 (51.8%)     break-even at -110 is 52.4%
model projection error   MAE 10.13
closing spread error     MAE  9.43   <- the market is better than the model
edge >= 4 pts: 74-70 (51.4%), -1.9% ROI
```

No shrinkage weight toward the market recovered an edge. The probabilities were
also badly overconfident: at a stated 71.6% the model hit 54.2%.

So `lib/model.py` has a **validation gate**. Because the backtest failed, the
model is not allowed to price bets — the picks panel says so instead of quoting
numbers it has not earned. The model is used for two things it is good enough
for: **LINE ANOMALY** flags (your evenly-matched-teams-at-+8 idea, made numeric)
and matchup context. A better model has to clear the same bar to get promoted.

This is also the argument for the desk's whole design: the closing line is
efficient, so the edge is in finding where public money distorts a number, not
in out-modelling the market.

## First thing to do on a networked machine

```bash
python scripts/fetch_schedule.py nfl              # should print the slate in kickoff order
python scripts/fetch_vsin.py nfl --calibrate      # dumps live HTML, reports table shapes
python scripts/fetch_action.py nfl --calibrate
python scripts/fetch_consensus.py nfl --calibrate
```

`--calibrate` writes the real page to `data/cache/calibrate/` and prints every
table header it found, plus a warning if the page has no tables at all (which
means the grid is JS-rendered and needs Playwright). Fix the parse against that,
then re-run without the flag. Until a source returns rows it reports FAILED and
its games stay UNVERIFIED — intended behaviour, not a bug.

## Logging and grading plays

```bash
python scripts/paste.py --account @DaveMasonBOL --book BetOnline \
    --game nfl-20260913-DAL-PHI --posted 2026-09-13T14:02:00Z < note.txt
python scripts/grade.py log --game nfl-20260913-DAL-PHI --side DAL --market spread \
    --number -3 --price -110 --book DraftKings --units 2 \
    --signal "BOOK NEED" --why "74/72 DK, line static"
python scripts/grade.py close  --play 1 --number -3.5 --price -125
python scripts/grade.py result --play 1 --outcome win
python scripts/grade.py report --since 2026-09-01
```

`report` gives record, units, ROI and average CLV, then names what the losing
plays had in common — and separates losses with negative CLV (bad price) from
losses with positive CLV (right side of the number, wrong result).

## Rules the code enforces, not just documents

- `lib/db.py` rejects any row missing `source`/`fetched_at`, and any split with
  neither `ticket_pct` nor `handle_pct`.
- `lib/http.py` raises on any non-200. No fetcher ever parses an error page.
- `lib/base.assert_parsed()` turns "found the page, parsed nothing" into FAILED,
  so a drifted selector cannot read as an empty slate.
- `signals.verification_state()` holds the two-independent-source minimum.
- Splits are append-only; the dashboard reads the newest row per book/market.
- `manual` (pasted insider + Pikkit data) is never joined into `splits`.

## Docs

- `docs/x-ingestion.md` — why manual paste beats the X API and a logged-in scraper
- `docs/confidence-and-picks.md` — what the five-pick panel can honestly promise
