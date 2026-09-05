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

| Layer | Status |
|---|---|
| `data/desk.db` schema + provenance enforcement | done |
| rate-limited cached HTTP, raw-pull writer, fail-loud parse contract | done |
| signal detection (all CLAUDE.md labels) | done, unit-tested |
| odds / no-vig / edge / CLV math (`lib/odds.py`) | done, unit-tested |
| pick ranking, -160 floor, quarter-Kelly sizing (`lib/picks.py`) | done, unit-tested |
| play logging + CLV grading (`scripts/grade.py`) | done, tested end to end |
| paste ingestion for gated sources (`scripts/paste.py`) | done, tested |
| dashboard (slate, compiled splits, signals, picks, resources) | done |
| `fetch_schedule.py` (ESPN JSON → slate, kickoff order, venue) | written, unrun |
| `fetch_context.py` (Open-Meteo weather, wind ≥15mph flag) | written, unrun |
| `fetch_vsin.py` (DraftKings + Circa splits) | written, **uncalibrated** |
| `fetch_action.py`, `fetch_consensus.py` | written, **uncalibrated** |
| `fetch_lines.py` (openers, history, direction of travel) | not built |
| injuries, efficiency/EPA, SP+/FEI | not built |
| props feed + handicapper sourcing | not built |

"Written, unrun" means exactly that: the code exists and compiles, and it has
never seen a live response, because the environment it was written in had no
network route to any sportsbook or data provider. No scraper here has been
proven against a real page.

The two things that block ranked picks are the **efficiency layer** (no win
probability means no edge, so `lib/picks` returns flags rather than bets) and a
**props feed**. Everything downstream of them is built and tested.

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
