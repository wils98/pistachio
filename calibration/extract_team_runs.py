"""
Workstream A data extraction: pulls each team's real season batting/pitching
lines across 2101-2104 (2101-2103 complete historical seasons; 2104 added
2026-08-22 once its season finished — verified complete, 205,660 total PA,
matching 2101-2103's ~202-205k range) and computes real team-season wOBA
(BATTING_WOBA_WEIGHTS/PITCHING_WOBA_WEIGHTS, straight from config.py,
unchanged) paired with real runs scored/allowed per 162 games — the raw
material fit_runs_per_game.py regresses to refit
RUNS_PER_GAME_HITTING_COEFF/_CONST and the pitching equivalent.

team_pitching_stats_history has no bf (batters faced) column. The league-wide
workaround tools/calibrate_league_constants.py already uses (borrowing
hitting PA as a stand-in, since sum(bf) == sum(pa) exactly LEAGUE-WIDE) isn't
valid per-team — one team's own batting PA has nothing to do with that SAME
team's pitching staff facing a DIFFERENT set of opposing hitters. Instead, bf
is approximated from that team's own recorded pitching events:
    bf_approx = outs + ha (hits allowed) + bb (walks allowed)
undercounting by hit-by-pitch and reached-on-error (no columns for either at
this granularity) — a small, roughly-constant proportional undercount,
acceptable here since the regression's output coefficients get printed for
human review before any transcription, not consumed automatically.

Usage:
    PISTACHIO_DB_PATH=/path/to/ootp.db python calibration/extract_team_runs.py
"""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from config import DB_PATH, BATTING_WOBA_WEIGHTS, PITCHING_WOBA_WEIGHTS

SEASONS = [2101, 2102, 2103, 2104]
OVERALL_SPLIT_ID = 1


def connect() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{DB_PATH}?mode=ro&immutable=1", uri=True)


def extract_hitting(conn: sqlite3.Connection) -> pd.DataFrame:
    frames = []
    for season in SEASONS:
        frame = pd.read_sql_query(
            """
            SELECT team_id, g, pa, h, d, t, hr, bb, k, r
            FROM team_batting_stats_history
            WHERE season_year = ? AND split_id = ? AND is_latest = 1
            """,
            conn, params=(season, OVERALL_SPLIT_ID),
        )
        frame["season_year"] = season
        frames.append(frame)
    df = pd.concat(frames, ignore_index=True)

    # g is only populated for the historical-backfilled seasons (2101-2103,
    # hardcoded to 162 during that import); live-ingested seasons (2104, even
    # once finished) never get a team-level g at all — confirmed team-level
    # w/l are NULL too, though individual pitchers' own g/w/l ARE populated.
    # Default to 162 rather than deriving from player-level MAX(g) (a team
    # with no single iron-man pitcher could read < 162 even in a real
    # 162-game season) — justified here by 2104's total PA (205,660) already
    # closely matching the other three seasons' confirmed-162-game totals
    # (202,553-205,694), strong indirect confirmation of a standard season.
    df["g"] = df["g"].fillna(162)

    singles = df["h"] - df["d"] - df["t"] - df["hr"]
    df["hr_pct"] = df["hr"] / df["pa"]
    df["bb_pct"] = df["bb"] / df["pa"]
    df["1b_pct"] = singles / df["pa"]
    df["2b_pct"] = df["d"] / df["pa"]
    df["3b_pct"] = df["t"] / df["pa"]
    df["wOBA"] = (
        BATTING_WOBA_WEIGHTS["hr_pct_wOBA_weight"] * df["hr_pct"]
        + BATTING_WOBA_WEIGHTS["bb_pct_wOBA_weight"] * df["bb_pct"]
        + BATTING_WOBA_WEIGHTS["1b_pct_wOBA_weight"] * df["1b_pct"]
        + BATTING_WOBA_WEIGHTS["2b_pct_wOBA_weight"] * df["2b_pct"]
        + BATTING_WOBA_WEIGHTS["3b_pct_wOBA_weight"] * df["3b_pct"]
    )
    df["runs_per_162"] = df["r"] * (162 / df["g"])
    return df[["team_id", "season_year", "g", "pa", "r", "wOBA", "runs_per_162"]]


def extract_pitching(conn: sqlite3.Connection) -> pd.DataFrame:
    frames = []
    for season in SEASONS:
        frame = pd.read_sql_query(
            """
            SELECT team_id, g, outs, ha, r, hra, bb, k
            FROM team_pitching_stats_history
            WHERE season_year = ? AND split_id = ? AND is_latest = 1
            """,
            conn, params=(season, OVERALL_SPLIT_ID),
        )
        frame["season_year"] = season
        frames.append(frame)
    df = pd.concat(frames, ignore_index=True)
    df["g"] = df["g"].fillna(162)  # see extract_hitting()'s comment

    df["bf_approx"] = df["outs"] + df["ha"] + df["bb"]
    h_nothr = df["ha"] - df["hra"]
    df["hr_vs"] = df["hra"] / df["bf_approx"]
    df["bb_vs"] = df["bb"] / df["bf_approx"]
    df["k_vs"] = df["k"] / df["bf_approx"]
    df["h_nothr_vs"] = h_nothr / df["bf_approx"]
    df["pwOBA"] = (
        PITCHING_WOBA_WEIGHTS["hr_vs_wOBA_weight"] * df["hr_vs"]
        + PITCHING_WOBA_WEIGHTS["bb_vs_wOBA_weight"] * df["bb_vs"]
        + PITCHING_WOBA_WEIGHTS["h_nothr_vs_wOBA_weight"] * df["h_nothr_vs"]
    )
    df["runs_per_162"] = df["r"] * (162 / df["g"])
    return df[["team_id", "season_year", "g", "bf_approx", "r", "pwOBA", "runs_per_162"]]


def main():
    conn = connect()
    hitting = extract_hitting(conn)
    pitching = extract_pitching(conn)
    conn.close()

    out_dir = Path(__file__).resolve().parent / "data"
    out_dir.mkdir(exist_ok=True)
    hitting.to_csv(out_dir / "team_hitting_runs.csv", index=False)
    pitching.to_csv(out_dir / "team_pitching_runs.csv", index=False)

    print(f"Hitting: {len(hitting)} team-seasons -> {out_dir / 'team_hitting_runs.csv'}")
    print(hitting[["season_year", "g", "wOBA", "runs_per_162"]].describe())
    print()
    print(f"Pitching: {len(pitching)} team-seasons -> {out_dir / 'team_pitching_runs.csv'}")
    print(pitching[["season_year", "g", "pwOBA", "runs_per_162"]].describe())


if __name__ == "__main__":
    main()
