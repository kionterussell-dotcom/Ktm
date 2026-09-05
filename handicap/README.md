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
| `fetch_schedule.py` (ESPN JSON → slate, kickoff order, venue) | written, unrun |
| `fetch_context.py` (Open-Meteo weather, wind ≥15mph flag) | written, unrun |
| `fetch_vsin.py` (DraftKings + Circa splits) | written, **selectors uncalibrated** |
| signal detection (all CLAUDE.md labels) | done, unit-tested |
| dashboard (slate, compiled splits, signals, resources) | done |
| `fetch_action.py`, `fetch_consensus.py`, `fetch_lines.py` | not built |
| injuries, efficiency/EPA, SP+/FEI | not built |
| top-5 bets ranking, props | not built — needs the efficiency layer |

"Written, unrun" means exactly that: the code exists and compiles, and it has
never seen a live response, because the environment it was written in had no
network route to any sportsbook or data provider. Nothing in this repo has been
proven against a real page.

## First thing to do on a networked machine

```bash
python scripts/fetch_schedule.py nfl            # should print the slate in kickoff order
python scripts/fetch_vsin.py nfl --calibrate    # dumps live HTML, reports table shapes
```

`--calibrate` writes the real page to `data/cache/calibrate/` and prints every
table header it found. Fix `parse_book()` against that, then re-run without the
flag. Until it returns rows, VSiN reports FAILED and every game stays UNVERIFIED
— which is the intended behaviour, not a bug.

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
