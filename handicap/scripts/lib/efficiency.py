"""Team efficiency from play-by-play.

Source: nflverse play-by-play (github.com/nflverse/nflverse-data), the same data
the public analytics community uses. EPA is already computed per play upstream by
nflfastR's model; this module aggregates it into the team metrics CLAUDE.md asks
for, with an opponent adjustment so a good defence is not credited for playing
bad offences.

Everything here is walk-forward safe: pass `through_week` and no play from that
week or later is used. That discipline is the difference between a backtest and
a lie.
"""
from __future__ import annotations

import pandas as pd

# Plays that reflect team quality. Special teams and kneels are noise here.
SCRIMMAGE = ("pass", "run")
EXPLOSIVE_PASS_YDS = 16
EXPLOSIVE_RUSH_YDS = 12


def _scrimmage(pbp: pd.DataFrame) -> pd.DataFrame:
    df = pbp[pbp["play_type"].isin(SCRIMMAGE)].copy()
    for col in ("qb_kneel", "qb_spike"):
        if col in df.columns:
            df = df[df[col] != 1]
    return df[df["epa"].notna() & df["posteam"].notna() & df["defteam"].notna()]


def raw_team_metrics(pbp: pd.DataFrame, through_week: int | None = None) -> pd.DataFrame:
    """Unadjusted per-team offensive and defensive rates."""
    df = _scrimmage(pbp)
    if through_week is not None:
        df = df[df["week"] < through_week]
    if df.empty:
        return pd.DataFrame()

    df["explosive"] = (
        ((df["pass"] == 1) & (df["yards_gained"] >= EXPLOSIVE_PASS_YDS)) |
        ((df["rush"] == 1) & (df["yards_gained"] >= EXPLOSIVE_RUSH_YDS))
    ).astype(int)
    df["early_down"] = df["down"].isin([1, 2])
    df["early_pass"] = (df["early_down"] & (df["pass"] == 1)).astype(int)

    off = df.groupby("posteam").agg(
        off_plays=("epa", "size"), off_epa=("epa", "mean"), off_sr=("success", "mean"),
        off_explosive=("explosive", "mean"),
        off_pass_epa=("epa", lambda s: s[df.loc[s.index, "pass"] == 1].mean()),
        off_rush_epa=("epa", lambda s: s[df.loc[s.index, "rush"] == 1].mean()),
        early_down_plays=("early_down", "sum"), early_down_pass=("early_pass", "sum"),
    )
    off["early_down_pass_rate"] = off["early_down_pass"] / off["early_down_plays"].replace(0, pd.NA)

    dfn = df.groupby("defteam").agg(
        def_plays=("epa", "size"), def_epa=("epa", "mean"), def_sr=("success", "mean"),
        def_explosive=("explosive", "mean"),
    )

    # Pace: seconds per scrimmage play in neutral game state, which is what
    # actually drives totals. A team trailing by 20 runs fast for reasons that
    # say nothing about how it plays a close game.
    pace = pd.DataFrame()
    if "game_seconds_remaining" in df.columns and "wp" in df.columns:
        neutral = df[(df["wp"] > 0.2) & (df["wp"] < 0.8)].copy()
        if not neutral.empty:
            neutral = neutral.sort_values(["game_id", "posteam", "game_seconds_remaining"],
                                          ascending=[True, True, False])
            neutral["delta"] = -neutral.groupby(["game_id", "posteam"])["game_seconds_remaining"].diff()
            good = neutral[(neutral["delta"] > 0) & (neutral["delta"] < 60)]
            pace = good.groupby("posteam").agg(sec_per_play=("delta", "mean"))

    out = off.join(dfn, how="outer")
    if not pace.empty:
        out = out.join(pace, how="left")
    out.index.name = "team"
    return out.reset_index()


def opponent_adjust(pbp: pd.DataFrame, through_week: int | None = None,
                    iterations: int = 6) -> pd.DataFrame:
    """Iteratively adjust EPA for schedule faced.

    A team's adjusted offence is its raw EPA/play minus the average adjusted
    defence it faced (weighted by plays). Repeating a few times converges, the
    same idea SRS uses for points. Without this, playing the league's worst
    defences reads as being good.
    """
    df = _scrimmage(pbp)
    if through_week is not None:
        df = df[df["week"] < through_week]
    if df.empty:
        return pd.DataFrame()

    league = df["epa"].mean()
    off = df.groupby("posteam")["epa"].agg(["mean", "size"]).rename(columns={"mean": "off", "size": "n_off"})
    dfn = df.groupby("defteam")["epa"].agg(["mean", "size"]).rename(columns={"mean": "def", "size": "n_def"})
    adj_off = (off["off"] - league).to_dict()
    adj_def = (dfn["def"] - league).to_dict()

    pair = df.groupby(["posteam", "defteam"])["epa"].agg(["mean", "size"]).reset_index()
    for _ in range(iterations):
        new_off, new_def = {}, {}
        for team in adj_off:
            g = pair[pair["posteam"] == team]
            if g.empty:
                new_off[team] = adj_off[team]; continue
            w = g["size"]
            faced = g["defteam"].map(lambda t: adj_def.get(t, 0.0))
            new_off[team] = float(((g["mean"] - league - faced) * w).sum() / w.sum())
        for team in adj_def:
            g = pair[pair["defteam"] == team]
            if g.empty:
                new_def[team] = adj_def[team]; continue
            w = g["size"]
            faced = g["posteam"].map(lambda t: adj_off.get(t, 0.0))
            new_def[team] = float(((g["mean"] - league - faced) * w).sum() / w.sum())
        adj_off, adj_def = new_off, new_def

    out = pd.DataFrame({
        "team": list(adj_off.keys()),
        "adj_off_epa": [adj_off[t] for t in adj_off],
        "adj_def_epa": [adj_def.get(t, 0.0) for t in adj_off],
    })
    out["n_off"] = out["team"].map(off["n_off"]).fillna(0)
    out["n_def"] = out["team"].map(dfn["n_def"]).fillna(0)
    return out


def team_table(pbp: pd.DataFrame, through_week: int | None = None) -> pd.DataFrame:
    raw = raw_team_metrics(pbp, through_week)
    adj = opponent_adjust(pbp, through_week)
    if raw.empty or adj.empty:
        return pd.DataFrame()
    return raw.merge(adj, on="team", how="outer")
