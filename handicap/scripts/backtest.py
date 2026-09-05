#!/usr/bin/env python
"""Walk-forward backtest of the model against real closing spreads.

Discipline, because a backtest without it is a sales brochure:
  - Ratings for week W use only plays from before week W.
  - The points-per-EPA scale and home-field advantage are fit on a PRIOR season
    and never on the season being tested.
  - The closing spread comes from the data, not from us.
  - Every game is graded, including the ones the model got wrong.

    python scripts/backtest.py --train 2023 --test 2024 --start-week 5
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import efficiency as eff
from lib import model as M
from lib.odds import implied_prob

BREAKEVEN = implied_prob(-110) * 100


def game_frame(pbp: pd.DataFrame) -> pd.DataFrame:
    g = (pbp.groupby("game_id")
            .agg(week=("week", "first"), home=("home_team", "first"),
                 away=("away_team", "first"), spread_line=("spread_line", "first"),
                 total_line=("total_line", "first"), result=("result", "first"),
                 home_score=("home_score", "first"), away_score=("away_score", "first"))
            .reset_index())
    return g[g["result"].notna() & g["spread_line"].notna()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", type=int, default=2023)
    ap.add_argument("--test", type=int, default=2024)
    ap.add_argument("--start-week", type=int, default=5)
    ap.add_argument("--data", default=None, help="dir holding pbp_<year>.parquet")
    a = ap.parse_args()

    data = Path(a.data) if a.data else Path(__file__).resolve().parents[1] / "data" / "nflverse"
    tr = pd.read_parquet(data / f"pbp_{a.train}.parquet")
    te = pd.read_parquet(data / f"pbp_{a.test}.parquet")

    # --- fit scale + HFA on the training season only -------------------------
    tr_ratings = eff.team_table(tr)
    tr_ratings["net"] = tr_ratings["adj_off_epa"] - tr_ratings["adj_def_epa"]
    net = dict(zip(tr_ratings["team"], tr_ratings["net"]))
    tr_games = game_frame(tr)
    fit_rows = [{"diff": net.get(g.home, 0) - net.get(g.away, 0), "margin": g.result}
                for g in tr_games.itertuples() if g.home in net and g.away in net]
    fit = M.fit_scale(fit_rows)
    print(f"\n  FIT on {a.train}: {fit.describe()}")

    prior_ratings = tr_ratings
    te_games = game_frame(te)
    picks = []

    for week in sorted(te_games["week"].unique()):
        if week < a.start_week:
            continue
        cur = eff.team_table(te, through_week=week)          # strictly before `week`
        blended = M.blend_ratings(cur, prior_ratings, games_played=max(week - 1, 0))
        r = dict(zip(blended["team"], blended["net"]))
        for g in te_games[te_games["week"] == week].itertuples():
            if g.home not in r or g.away not in r:
                continue
            proj = M.project_margin(r[g.home], r[g.away], fit)
            p_home = M.cover_prob(proj, g.spread_line, fit.resid_sd)
            side, p = ("home", p_home) if p_home >= 0.5 else ("away", 1 - p_home)
            margin_vs_spread = g.result - g.spread_line
            if margin_vs_spread == 0:
                outcome = "push"
            elif (margin_vs_spread > 0) == (side == "home"):
                outcome = "win"
            else:
                outcome = "loss"
            picks.append({"week": week, "game": g.game_id, "side": side,
                          "model_p": p, "edge": (p * 100) - BREAKEVEN,
                          "proj": proj, "spread": g.spread_line,
                          "actual": g.result, "outcome": outcome})

    df = pd.DataFrame(picks)
    graded = df[df["outcome"] != "push"]
    w = (graded["outcome"] == "win").sum(); l = (graded["outcome"] == "loss").sum()
    print(f"\n  WALK-FORWARD {a.test}, weeks {a.start_week}+   {len(df)} games, "
          f"{len(df)-len(graded)} push")
    print(f"  Every-game ATS: {w}-{l}  ({w/(w+l)*100:.1f}%)   break-even at -110 is {BREAKEVEN:.1f}%")

    print(f"\n  BY MODEL EDGE (edge = model prob - {BREAKEVEN:.1f}% break-even)")
    print(f"  {'edge band':<16}{'n':>5}{'ATS':>10}{'win%':>8}{'units@-110':>12}")
    bands = [(0, 2), (2, 4), (4, 7), (7, 12), (12, 100)]
    for lo, hi in bands:
        s = graded[(graded["edge"] >= lo) & (graded["edge"] < hi)]
        if s.empty:
            continue
        ww = (s["outcome"] == "win").sum(); ll = (s["outcome"] == "loss").sum()
        units = ww * (100/110) - ll
        print(f"  {f'{lo}-{hi} pts':<16}{len(s):>5}{f'{ww}-{ll}':>10}"
              f"{ww/(ww+ll)*100:>7.1f}%{units:>+11.2f}u")

    print(f"\n  CALIBRATION (does a stated probability mean what it says?)")
    print(f"  {'predicted':<16}{'n':>5}{'predicted%':>12}{'actual%':>10}{'gap':>8}")
    for lo, hi in [(0.50, 0.55), (0.55, 0.60), (0.60, 0.65), (0.65, 1.01)]:
        s = graded[(graded["model_p"] >= lo) & (graded["model_p"] < hi)]
        if len(s) < 5:
            continue
        act = (s["outcome"] == "win").mean() * 100
        pred = s["model_p"].mean() * 100
        print(f"  {f'{lo:.2f}-{hi:.2f}':<16}{len(s):>5}{pred:>11.1f}%{act:>9.1f}%{act-pred:>+7.1f}")

    best = graded[graded["edge"] >= 4]
    if len(best) > 10:
        bw = (best["outcome"] == "win").sum(); bl = (best["outcome"] == "loss").sum()
        units = bw * (100/110) - bl
        print(f"\n  IF YOU ONLY BET edge >= 4 pts: {bw}-{bl} ({bw/(bw+bl)*100:.1f}%), "
              f"{units:+.2f}u on {len(best)}u risked ({units/len(best)*100:+.1f}% ROI)")
    # --- persist calibration so the desk can gate on it -----------------------
    import json
    import numpy as np
    proj_err = df["actual"] - df["proj"]
    mkt_err = df["actual"] - df["spread"]
    cal = M.Calibration(
        fit=fit, tested_season=a.test,
        ats_win_rate=float(w / (w + l)), n_graded=int(w + l),
        model_mae=float(np.abs(proj_err).mean()), market_mae=float(np.abs(mkt_err).mean()),
        honest_resid_sd=float(proj_err.std(ddof=1)),
    )
    out = Path(__file__).resolve().parents[1] / "data" / "model_calibration.json"
    out.write_text(json.dumps(cal.to_dict(), indent=2))

    print(f"  VERDICT: model {cal.verdict}")
    print(f"  Honest residual SD is {cal.honest_resid_sd:.2f}, not the train-set "
          f"{fit.resid_sd:.2f} — using the smaller one is what produced the")
    print("  overconfidence in the calibration table above.")
    print(f"  Written to {out.relative_to(Path.cwd()) if str(out).startswith(str(Path.cwd())) else out}")
    print(f"  picks will {'RANK' if cal.validated else 'NOT rank'} bets from this model.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
