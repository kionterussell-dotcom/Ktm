"""What the desk pulls from, and what it honestly cannot pull from yet.

`built` False is not a TODO comment — run_all prints those rows as NOT_BUILT in
the status table, so a slate scan always states which books are missing from the
read rather than quietly analysing a partial board.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Source:
    key: str
    module: str | None
    kind: str            # schedule | splits | lines | context
    built: bool
    calibrated: bool     # selectors verified against live HTML?
    note: str


SOURCES: list[Source] = [
    Source("espn-schedule", "fetch_schedule", "schedule", True, True,
           "Documented JSON API. Spine of the slate; every other source keys to its game_id."),
    Source("vsin", "fetch_vsin", "splits", True, False,
           "DraftKings + Circa ticket%/handle%. Backbone. Selectors written structurally but "
           "UNCALIBRATED — run `fetch_vsin.py <sport> --calibrate` on a networked machine first."),
    Source("weather", "fetch_context", "context", True, True,
           "Open-Meteo. Wind >=15mph flagged; domes skipped."),
    Source("action", "fetch_action", "splits", True, False,
           "Action Network free-tier public betting. UNCALIBRATED. PRO fields are gated and stay "
           "gated — paste them via /paste."),
    Source("consensus", "fetch_consensus", "splits", True, False,
           "ScoresAndOdds / Covers / SportsBettingDime aggregates. UNCALIBRATED. Cross-verification "
           "only, never reported as a per-book split."),
    Source("lines", None, "lines", False, False,
           "Current numbers, openers and line history across books, with BetOnline as the "
           "origination reference for direction of travel."),
    Source("injuries", None, "context", False, False,
           "Current injury report with practice participation. CFB reporting is unreliable by nature."),
    Source("efficiency", None, "context", False, False,
           "EPA/play, success rate, pace, pressure. CFB adds SP+/FEI/returning production."),
    Source("pikkit", None, "splits", False, False,
           "App/Pro only, no public feed. Tracked-bettor action, NOT book handle — a different data "
           "class that must never be blended into a book split. Paste-in only."),
    Source("x-insiders", None, "context", False, False,
           "@BetMGMnews @johnewing @PatrickE_Vegas @DaveMasonBOL. Stated liability beats inferred "
           "liability. Paste-in only — see docs/x-ingestion.md."),
]

BY_KEY = {s.key: s for s in SOURCES}
BUILT = [s for s in SOURCES if s.built]
MISSING = [s for s in SOURCES if not s.built]
