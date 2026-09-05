"""American odds, no-vig probability, edge and CLV.

All of this is arithmetic on numbers the desk already has, so it is exact and
testable — unlike a win probability, which is a model output and is never
invented here. Nothing in this module estimates anything.
"""
from __future__ import annotations


def implied_prob(american: int) -> float:
    """Break-even win rate for a price, vig included. -110 -> 0.5238."""
    if american == 0:
        raise ValueError("american odds cannot be 0")
    return (-american) / (-american + 100) if american < 0 else 100 / (american + 100)


def american_from_prob(p: float) -> int:
    """Fair price for a probability. 0.5238 -> -110."""
    if not 0 < p < 1:
        raise ValueError(f"probability out of range: {p}")
    return round(-100 * p / (1 - p)) if p >= 0.5 else round(100 * (1 - p) / p)


def devig_two_way(price_a: int, price_b: int) -> tuple[float, float]:
    """Strip the hold from a two-way market (multiplicative / proportional).

    The book's two implied probabilities sum to more than 1; the excess is the
    hold. This is the honest baseline to measure an edge against — measuring
    against the raw implied price flatters every bet by roughly half the vig.
    """
    a, b = implied_prob(price_a), implied_prob(price_b)
    total = a + b
    return a / total, b / total


def hold_pct(price_a: int, price_b: int) -> float:
    return (implied_prob(price_a) + implied_prob(price_b) - 1) * 100


def edge_pts(model_p: float, price: int, fair_p: float | None = None) -> float:
    """Edge in percentage points: model win prob minus what the price needs.

    Two different questions, so be clear which one is being asked:
      - against the RAW price (default): is this bet profitable? This is the one
        that decides whether to bet and how much, and it is the conservative
        number because the vig is still in it.
      - against fair_p from devig_two_way(): how far does the model disagree
        with the market's true opinion? Always the larger number. Useful for
        sanity-checking the model, never for sizing a bet.
    """
    baseline = fair_p if fair_p is not None else implied_prob(price)
    return (model_p - baseline) * 100


def price_threshold(model_p: float) -> int:
    """The worst price still break-even at this win probability.

    This is what turns a side into a bet with a number attached:
    "take at -135 or better, no value at -145".
    """
    return american_from_prob(model_p)


def kelly_units(model_p: float, price: int, bankroll_pct_per_unit: float = 1.0,
                fraction: float = 0.25, max_units: float = 3.0) -> float:
    """Quarter-Kelly, capped at the desk's 3-unit ceiling.

    Full Kelly is far too aggressive for a model whose win probabilities carry
    real error; quarter-Kelly is the standard discount for that uncertainty.
    """
    b = (100 / -price) if price < 0 else (price / 100)      # net odds per 1 staked
    q = 1 - model_p
    f = (b * model_p - q) / b
    if f <= 0:
        return 0.0
    units = (f * fraction * 100) / bankroll_pct_per_unit
    return round(min(units, max_units), 2)


def clv_pts(bet_price: int, closing_price: int) -> float:
    """Closing line value in percentage points of implied probability.

    Positive means you got a better price than the close — you bought the same
    outcome cheaper than the market's final word on it. Over a long enough
    sample this predicts profit better than win rate does.
    """
    return (implied_prob(closing_price) - implied_prob(bet_price)) * 100


def clv_points(bet_number: float, closing_number: float, side_is_favorite: bool) -> float:
    """CLV in points of line, for spreads and totals.

    Positive = your number was better than the close. A favourite laying fewer
    points than the close is value; a dog taking more points than the close is
    value, which is why the sign flips on side.
    """
    return (closing_number - bet_number) if side_is_favorite else (bet_number - closing_number)
