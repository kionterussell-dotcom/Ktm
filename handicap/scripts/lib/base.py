"""Shared fetcher contract.

Every fetcher returns a Result. There are exactly three honest outcomes:
  OK        rows were parsed and loaded
  EMPTY     the source answered, and genuinely had nothing (a real answer)
  FAILED    the source did not answer, or answered in a shape we could not parse

A fetcher that parses zero rows from a page that clearly had games is FAILED,
not EMPTY. That distinction is the whole point — see assert_parsed().
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Result:
    source: str
    status: str = "FAILED"
    rows: int = 0
    detail: str = ""
    raw_path: str | None = None
    notes: list[str] = field(default_factory=list)


class ParseError(RuntimeError):
    """The page loaded but did not yield the structure we expect."""


def assert_parsed(rows: list, source: str, saw_container: bool, hint: str) -> None:
    """Guard against the silent-empty failure mode.

    saw_container: did we find the page furniture that means 'this page has games'?
    If we saw it and parsed nothing, the selectors have drifted — raise, so the
    status table says FAILED and the game is UNVERIFIED rather than backfilled.
    """
    if not rows and saw_container:
        raise ParseError(f"{source}: found the container but parsed 0 rows — selectors drifted. {hint}")
