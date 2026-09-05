"""Ranking the board into a shortlist.

What this does NOT do is invent a win probability. Every Candidate arrives with
a model_p supplied by the caller; if the efficiency layer has not produced one,
the candidate is returned as a flag with model_p=None and is explicitly not
ranked. See docs/confidence-and-picks.md for why there is no 'guaranteed' tier.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .odds import edge_pts, implied_prob, kelly_units, price_threshold

ODDS_FLOOR = -160          # user rule: nothing worse than -160
MIN_EDGE_PTS = 2.0         # below this the model error swamps the edge
MAX_UNITS_PER_SLATE = 15.0


@dataclass
class Candidate:
    game_id: str
    away: str
    home: str
    kickoff_utc: str
    market: str
    side: str
    number: float | None
    price: int
    book: str
    model_p: float | None = None
    signals: list[str] = field(default_factory=list)
    kills: str = ""                       # what kills this play


@dataclass
class RankedPick:
    candidate: Candidate
    edge: float
    breakeven: float
    threshold_price: int
    units: float
    tier: str
    bet_worthy: bool
    why_not: str = ""


def tier_for(units: float) -> str:
    if units >= 2.5:
        return "3 UNIT"
    if units >= 1.5:
        return "2 UNIT"
    if units > 0:
        return "1 UNIT"
    return "NO PLAY"


def rank(candidates: list[Candidate], limit: int = 5,
         odds_floor: int = ODDS_FLOOR) -> tuple[list[RankedPick], dict]:
    """Return the top `limit` by edge, plus an honest summary.

    Candidates are never dropped for failing the bar — they are returned marked
    bet_worthy=False with the reason. A shortlist that silently hides the four
    rejects looks identical to a shortlist of five good bets, and it is not.
    """
    ranked: list[RankedPick] = []
    unpriced = 0

    for c in candidates:
        if c.model_p is None:
            unpriced += 1
            continue
        breakeven = implied_prob(c.price) * 100
        edge = edge_pts(c.model_p, c.price)
        units = kelly_units(c.model_p, c.price)
        why = ""
        worthy = True
        # Worse than -160 is the user's own filter: the market charging a
        # premium for the feeling of safety, where one loss erases eight wins.
        if c.price < odds_floor:
            worthy, why = False, f"price {c.price} is worse than the {odds_floor} floor"
        elif edge < MIN_EDGE_PTS:
            worthy, why = False, f"edge {edge:+.1f} pts is inside model error"
        elif units <= 0:
            worthy, why = False, "no positive expectation at this price"
        ranked.append(RankedPick(c, round(edge, 1), round(breakeven, 1),
                                 price_threshold(c.model_p), units,
                                 tier_for(units) if worthy else "NO PLAY", worthy, why))

    ranked.sort(key=lambda r: (-r.bet_worthy, -r.edge))
    shown = ranked[:limit]
    worthy_n = sum(1 for r in ranked if r.bet_worthy)
    exposure = round(sum(r.units for r in shown if r.bet_worthy), 2)

    summary = {
        "shown": len(shown),
        "bet_worthy": worthy_n,
        "evaluated": len(ranked),
        "unpriced": unpriced,
        "exposure_units": exposure,
        "over_slate_cap": exposure > MAX_UNITS_PER_SLATE,
        "headline": (f"{len(shown)} shown, {worthy_n} clear the bet-worthy threshold"
                     if ranked else "nothing priced yet — no win probabilities available"),
    }
    return shown, summary
