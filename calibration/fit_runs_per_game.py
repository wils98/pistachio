"""
Workstream A fitting: refits RUNS_PER_GAME_HITTING_COEFF/_CONST and the
pitching equivalent from real team-season data (extract_team_runs.py's
output), producing constants on the same PER-PLAYER scale the consuming
formula expects.

config.py's existing formula (metrics_hitting.py/metrics_pitching.py) is:
    runs = wOBA * COEFF - CONST ;  WAR = runs / RUNS_PER_WIN
applied to ONE PLAYER's wOBA — upstream's constants decode to a ~650-PA
player-season scale with the zero-runs point at wOBA ~0.3225 (essentially
their league average: CONST/COEFF). A naive OLS of team runs-per-162 on
team wOBA fits the same relationship but on a ~6,400-PA TEAM scale — its
slope is ~10x steeper, and feeding individual wOBA through it produced
absurdities (a 0.48-wOBA ceiling projection -> 153 WAR; even league-average
wOBA -> ~78 WAR). First attempt shipped exactly that bug; caught in
verification against real prospects.

The fix, keeping the fit entirely at the team level (per direct instruction
— no individual-level refit): rescale the fitted team slope to per-650-PA
units (slope * 650 / mean_team_PA), then set CONST = COEFF * league_mean_wOBA
so zero runs lands at the league-average wOBA — the same structural zero
point upstream's own constants encode. Validation that fell out of this:
the rescaled PSD slope (~546.6) lands within ~1.5% of upstream's own 554.8,
independently derived from a different league's data — strong evidence both
are measuring the same underlying wOBA-to-runs physics, and that the slope
barely needed recalibrating (the league-context CONST is what genuinely
differs).

Prints values to transcribe into tables_native.py — reviewed literal, not
auto-wired, same discipline as tools/calibrate_league_constants.py.

Usage (needs calibration/data/team_{hitting,pitching}_runs.csv already built
by extract_team_runs.py):
    python calibration/fit_runs_per_game.py
"""

from pathlib import Path

import pandas as pd
import statsmodels.api as sm

DATA_DIR = Path(__file__).resolve().parent / "data"
PLAYER_SEASON_PA = 650


def fit_side(csv_name: str, x_col: str, pa_col: str, label: str) -> tuple[float, float]:
    df = pd.read_csv(DATA_DIR / csv_name)
    x = sm.add_constant(df[x_col])
    y = df["runs_per_162"]
    model = sm.OLS(y, x).fit()

    team_slope = model.params[x_col]
    mean_team_pa = df[pa_col].mean()
    league_mean_woba = df[x_col].mean()

    coeff = team_slope * PLAYER_SEASON_PA / mean_team_pa
    const = coeff * league_mean_woba

    print(f"=== {label} ===")
    print(model.summary())
    print()
    print(f"team-scale slope: {team_slope:.4f} over mean {mean_team_pa:.0f} team PA/BF")
    print(f"league mean {x_col}: {league_mean_woba:.4f}")
    print(f"RUNS_PER_GAME_{label.upper()}_COEFF = {coeff:.7f}  (per-{PLAYER_SEASON_PA}-PA scale)")
    print(f"RUNS_PER_GAME_{label.upper()}_CONST = {const:.7f}  (zero runs at league-average {x_col})")
    print(f"R-squared: {model.rsquared:.4f}")
    print()

    return coeff, const


def main():
    fit_side("team_hitting_runs.csv", "wOBA", "pa", "hitting")
    fit_side("team_pitching_runs.csv", "pwOBA", "bf_approx", "pitching")


if __name__ == "__main__":
    main()
