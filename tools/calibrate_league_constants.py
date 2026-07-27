"""
One-off, human-run calibration helper — NOT wired into the live pipeline.

Computes real PSD league-average constants (BASE_HITTING_RATES, BASE_PITCHING_RATES,
LEAGUE_WOBA, LEAGUE_RUNS_PER_PA) from psd-ootp's DB, for manual transcription into
config.py. Deliberately not automatic: config.py's constants should stay reviewed
literals like every other value in that file, not something that silently drifts
run-to-run as more of the season gets played. Re-run this and re-transcribe
periodically as more games accumulate — diff the resulting projections before/after
each time (see NOTES.md) to see whether the calibration is stabilizing.

RUNS_PER_WIN isn't derived from the DB — team_batting_stats_history.g (games) looked
unreliably populated this early in the season, and deriving a runs-per-win constant
from a suspect games column isn't worth it. Instead, pass --runs-per-team-per-game
with OOTP's own in-game league history report's R/G value for the current season
(Team Stats > League History, or equivalent) — that number is game-computed and
trustworthy regardless of what the DB's games column looks like. Uses Tango's
commonly-cited RPW = 1.5 * RPG_per_team + 3 (see
https://library.fangraphs.com/misc/war/converting-runs-to-wins/).

Usage:
    PISTACHIO_DB_PATH=/path/to/ootp.db python tools/calibrate_league_constants.py \\
        --runs-per-team-per-game 5.16
"""

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import DB_PATH, BATTING_WOBA_WEIGHTS

MLB_LEVEL_ID = 1
OVERALL_SPLIT_ID = 1

# Well-known, independently-sourced real-MLB wOBA linear weights (Tom Tango-style,
# recent-era approximation) — used ONLY as a second, independent cross-check against
# pistachio's own BATTING_WOBA_WEIGHTS. Not used anywhere in the actual model.
REFERENCE_WOBA_WEIGHTS = {
    "bb": 0.690,
    "1b": 0.888,
    "2b": 1.271,
    "3b": 1.616,
    "hr": 2.101,
}


def connect():
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro&immutable=1", uri=True)
    return conn


def compute_hitting_rates(conn) -> dict:
    max_year = conn.execute(
        "SELECT MAX(season_year) FROM player_batting_stats_history WHERE level_id=? AND split_id=?",
        (MLB_LEVEL_ID, OVERALL_SPLIT_ID),
    ).fetchone()[0]

    row = conn.execute(
        """
        SELECT SUM(pa), SUM(h), SUM(d), SUM(t), SUM(hr), SUM(bb), SUM(k), SUM(r)
        FROM player_batting_stats_history
        WHERE level_id=? AND split_id=? AND season_year=?
        """,
        (MLB_LEVEL_ID, OVERALL_SPLIT_ID, max_year),
    ).fetchone()
    pa, h, d, t, hr, bb, k, r = row
    singles = h - d - t - hr

    return {
        "season_year": max_year,
        "pa": pa,
        "hr_pct_baserate": hr / pa,
        "k_pct_baserate": k / pa,
        "bb_pct_baserate": bb / pa,
        "1b_pct_baserate": singles / pa,
        "2b_pct_baserate": d / pa,
        "3b_pct_baserate": t / pa,
        "runs_per_pa": r / pa,
    }


def compute_pitching_rates(conn) -> dict:
    max_year = conn.execute(
        "SELECT MAX(season_year) FROM player_pitching_stats_history WHERE level_id=? AND split_id=?",
        (MLB_LEVEL_ID, OVERALL_SPLIT_ID),
    ).fetchone()[0]

    row = conn.execute(
        """
        SELECT SUM(bf), SUM(ha), SUM(hra), SUM(bb), SUM(k)
        FROM player_pitching_stats_history
        WHERE level_id=? AND split_id=? AND season_year=?
        """,
        (MLB_LEVEL_ID, OVERALL_SPLIT_ID, max_year),
    ).fetchone()
    bf, ha, hra, bb, k = row
    h_nothr = ha - hra

    return {
        "season_year": max_year,
        "bf": bf,
        "hr_vs_baserate": hra / bf,
        "bb_vs_baserate": bb / bf,
        "k_vs_baserate": k / bf,
        "h_nothr_vs_baserate": h_nothr / bf,
    }


def runs_per_win(runs_per_team_per_game: float) -> float:
    """Tango's RPW = 1.5 * RPG_per_team + 3 — matches Pythagenpat within ~0.02 RPW."""
    return 1.5 * runs_per_team_per_game + 3


def woba_from_rates(rates: dict, weights: dict) -> float:
    return (
        weights["hr"] * rates["hr_pct_baserate"]
        + weights["bb"] * rates["bb_pct_baserate"]
        + weights["1b"] * rates["1b_pct_baserate"]
        + weights["2b"] * rates["2b_pct_baserate"]
        + weights["3b"] * rates["3b_pct_baserate"]
    )


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runs-per-team-per-game", type=float, default=None,
        help="R/G from OOTP's own league history report for the current season "
             "(not derived from the DB — see module docstring).",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    conn = connect()
    hitting = compute_hitting_rates(conn)
    pitching = compute_pitching_rates(conn)
    conn.close()

    pistachio_weights = {
        "hr": BATTING_WOBA_WEIGHTS["hr_pct_wOBA_weight"],
        "bb": BATTING_WOBA_WEIGHTS["bb_pct_wOBA_weight"],
        "1b": BATTING_WOBA_WEIGHTS["1b_pct_wOBA_weight"],
        "2b": BATTING_WOBA_WEIGHTS["2b_pct_wOBA_weight"],
        "3b": BATTING_WOBA_WEIGHTS["3b_pct_wOBA_weight"],
    }
    league_woba_pistachio_weights = woba_from_rates(hitting, pistachio_weights)
    league_woba_reference_weights = woba_from_rates(hitting, REFERENCE_WOBA_WEIGHTS)

    print(f"Sample: season_year={hitting['season_year']}, PA={hitting['pa']:,}, BF={pitching['bf']:,}")
    print()
    print("BASE_HITTING_RATES = {")
    for k in ["hr_pct_baserate", "k_pct_baserate", "bb_pct_baserate",
              "1b_pct_baserate", "2b_pct_baserate", "3b_pct_baserate"]:
        print(f'    "{k}": {hitting[k]:.4f},')
    print("}")
    print()
    print("BASE_PITCHING_RATES = {")
    for k in ["hr_vs_baserate", "bb_vs_baserate", "k_vs_baserate", "h_nothr_vs_baserate"]:
        print(f'    "{k}": {pitching[k]:.4f},')
    print("}")
    print()
    print(f"LEAGUE_RUNS_PER_PA = {hitting['runs_per_pa']:.4f}")
    print()
    print(f"LEAGUE_WOBA (pistachio's own BATTING_WOBA_WEIGHTS) = {league_woba_pistachio_weights:.4f}")
    print(f"LEAGUE_WOBA (independent reference wOBA weights)   = {league_woba_reference_weights:.4f}")
    print(f"  -> difference: {abs(league_woba_pistachio_weights - league_woba_reference_weights):.4f}")
    print("  -> both should land in a plausible baseball range (~0.30-0.34); a large")
    print("     discrepancy between the two methods would flag a units/aggregation bug")
    print("     rather than genuine league flavor.")
    print()
    if args.runs_per_team_per_game is not None:
        rpw = runs_per_win(args.runs_per_team_per_game)
        print(f"RUNS_PER_WIN = {rpw:.2f}  "
              f"(from R/G={args.runs_per_team_per_game}, Tango's RPW = 1.5*RPG + 3)")
    else:
        print("RUNS_PER_WIN: not computed — pass --runs-per-team-per-game with OOTP's "
              "own league history R/G (see module docstring; games-column in the DB "
              "isn't trustworthy this early in the season).")


if __name__ == "__main__":
    main()
