"""Power ratings -> projected margin -> cover probability.

The chain that turns a market signal into a bet with a number on it:

  opponent-adjusted EPA/play
    -> blended with the prior season while the sample is small
    -> scaled to points by a slope fit on past seasons (never on the test set)
    -> projected margin, plus home-field advantage
    -> cover probability from the empirical SD of (actual margin - projection)

Every constant here is FIT FROM DATA, not asserted. fit_scale() returns them and
backtest.py prints them, so a claim like "3 points of home field" can be checked
rather than believed.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

# Games of the current season before the prior season stops carrying weight.
# EPA stabilises fast but not instantly; this is the standard shrinkage shape.
PRIOR_HALF_LIFE_GAMES = 6.0


@dataclass
class Fit:
    slope: float          # points of margin per unit of net EPA/play differential
    hfa: float            # home-field advantage in points
    resid_sd: float       # SD of (actual margin - projection); drives probability
    n_games: int

    def describe(self) -> str:
        return (f"slope={self.slope:.1f} pts per EPA/play, HFA={self.hfa:+.2f} pts, "
                f"residual SD={self.resid_sd:.2f} pts, fit on {self.n_games} games")


def blend_ratings(cur: pd.DataFrame, prior: pd.DataFrame | None,
                  games_played: float) -> pd.DataFrame:
    """Weight this season against last season by how much of this season exists.

    Week 1 with zero current-season plays is entirely last year's team, which is
    wrong in the specific ways rosters change but far less wrong than a rating
    built on nothing.
    """
    if cur is None or cur.empty:
        return prior.copy() if prior is not None else pd.DataFrame()
    cur = cur.copy()
    cur["net"] = cur["adj_off_epa"] - cur["adj_def_epa"]
    if prior is None or prior.empty:
        return cur[["team", "net"]]
    prior = prior.copy()
    prior["net"] = prior["adj_off_epa"] - prior["adj_def_epa"]
    w = games_played / (games_played + PRIOR_HALF_LIFE_GAMES)
    m = cur[["team", "net"]].merge(prior[["team", "net"]], on="team", how="outer",
                                   suffixes=("_cur", "_prior")).fillna(0.0)
    m["net"] = w * m["net_cur"] + (1 - w) * m["net_prior"]
    return m[["team", "net"]]


def fit_scale(rows: list[dict]) -> Fit:
    """Least squares of actual margin on rating differential.

    rows: {"diff": home_net - away_net, "margin": home_score - away_score}
    """
    if len(rows) < 30:
        raise ValueError(f"not enough games to fit ({len(rows)})")
    df = pd.DataFrame(rows)
    x, y = df["diff"], df["margin"]
    slope = ((x - x.mean()) * (y - y.mean())).sum() / ((x - x.mean()) ** 2).sum()
    hfa = y.mean() - slope * x.mean()
    resid = y - (slope * x + hfa)
    return Fit(float(slope), float(hfa), float(resid.std(ddof=1)), len(df))


def project_margin(home_net: float, away_net: float, fit: Fit) -> float:
    """Projected home margin in points. Positive = home favoured."""
    return fit.slope * (home_net - away_net) + fit.hfa


def _norm_cdf(z: float) -> float:
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def cover_prob(projected_margin: float, spread_line: float, resid_sd: float) -> float:
    """P(home covers), where spread_line is the home spread (positive = home
    favoured by that many) and home covers when actual margin exceeds it."""
    if resid_sd <= 0:
        raise ValueError("residual SD must be positive")
    return 1 - _norm_cdf((spread_line - projected_margin) / resid_sd)


def win_prob(projected_margin: float, resid_sd: float) -> float:
    return 1 - _norm_cdf((0 - projected_margin) / resid_sd)


def total_prob_over(projected_total: float, total_line: float, total_sd: float) -> float:
    return 1 - _norm_cdf((total_line - projected_total) / total_sd)


# ---------------------------------------------------------------------------
# Validation gate
# ---------------------------------------------------------------------------
# A model may only quote win probabilities the desk will act on if a walk-forward
# backtest showed it beating the break-even rate. Ours does not: against 2024
# closing spreads it ran 51.8% ATS (break-even 52.4%), and the closing line
# predicted final margin better than the model did (MAE 9.43 vs 10.13). See
# data/model_calibration.json, written by scripts/backtest.py.
#
# So the model is NOT used to price bets. It is used for two things it is
# genuinely good enough for:
#   1. LINE ANOMALY detection — where does the number sit far from any defensible
#      projection, so that something unpriced is probably going on?
#   2. Context — pace, efficiency mismatches, which side of a total the play
#      style argues for.
#
# This gate exists so a future improved model has to earn its way past the same
# bar rather than being wired in on optimism.

MIN_VALIDATED_WIN_RATE = 0.524          # break-even at -110
ANOMALY_PTS = 6.0                       # |projection - market| worth investigating


@dataclass
class Calibration:
    fit: Fit
    tested_season: int | None = None
    ats_win_rate: float | None = None
    n_graded: int = 0
    model_mae: float | None = None
    market_mae: float | None = None
    honest_resid_sd: float | None = None

    @property
    def validated(self) -> bool:
        """True only if the model actually beat break-even out of sample."""
        return (self.ats_win_rate is not None
                and self.n_graded >= 100
                and self.ats_win_rate > MIN_VALIDATED_WIN_RATE)

    @property
    def verdict(self) -> str:
        if self.ats_win_rate is None:
            return "UNVALIDATED — no backtest has been run."
        beat = "beats" if self.validated else "does NOT beat"
        return (f"{beat} break-even: {self.ats_win_rate*100:.1f}% ATS over {self.n_graded} "
                f"graded games in {self.tested_season} (need >{MIN_VALIDATED_WIN_RATE*100:.1f}%). "
                f"Projection MAE {self.model_mae:.2f} vs closing line {self.market_mae:.2f}.")

    def to_dict(self) -> dict:
        return {"slope": self.fit.slope, "hfa": self.fit.hfa,
                "train_resid_sd": self.fit.resid_sd, "n_train_games": self.fit.n_games,
                "tested_season": self.tested_season, "ats_win_rate": self.ats_win_rate,
                "n_graded": self.n_graded, "model_mae": self.model_mae,
                "market_mae": self.market_mae, "honest_resid_sd": self.honest_resid_sd,
                "validated": self.validated, "verdict": self.verdict}

    @classmethod
    def from_dict(cls, d: dict) -> "Calibration":
        return cls(Fit(d["slope"], d["hfa"], d.get("honest_resid_sd") or d["train_resid_sd"],
                       d.get("n_train_games", 0)),
                   d.get("tested_season"), d.get("ats_win_rate"), d.get("n_graded", 0),
                   d.get("model_mae"), d.get("market_mae"), d.get("honest_resid_sd"))


def load_calibration(path) -> "Calibration | None":
    import json
    from pathlib import Path
    p = Path(path)
    if not p.exists():
        return None
    return Calibration.from_dict(json.loads(p.read_text()))


def line_anomaly(projected_margin: float, spread_line: float,
                 threshold: float = ANOMALY_PTS) -> tuple[bool, float]:
    """Your evenly-matched-teams-at-+8 flag, made numeric.

    Returns (is_anomaly, gap). The gap is signed from the home team's view. This
    opens an investigation — it is not a play. An unexplained line usually means
    the market knows something the box score does not.
    """
    gap = projected_margin - spread_line
    return abs(gap) >= threshold, round(gap, 1)
