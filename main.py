import os
import sys
import time

from config import filepath
from reader import load_players, add_pitching_career_stats, add_hitting_career_stats, add_scouted_ratings, count_pitches, can_field, is_flagged
from metrics_pitching import calc_pitching_metrics, calc_potential_pitching_metrics
from metrics_hitting import calc_hitting_metrics, calc_potential_hitting_metrics
from metrics_fielding import calc_fielding_metrics
from metrics_war import calc_war
# from exporter import export_hitters
from exporter import export_html_pages

REQUIRED_CSVS = [
    "players.csv",
    "players_scouted_ratings.csv",
    "players_career_batting_stats.csv",
    "players_career_pitching_stats.csv",
]

# Running unattended on a timer against CSVs shipped from another machine means the
# transfer can silently stop happening. Refuse to export from missing or stale input
# rather than quietly re-serving old projections under a fresh-looking timestamp.
MAX_INPUT_AGE_DAYS = float(os.environ.get("PISTACHIO_MAX_INPUT_AGE_DAYS", "3"))


def check_input_ready():
    missing = [name for name in REQUIRED_CSVS if not (filepath / name).is_file()]
    if missing:
        sys.exit(f"Missing input CSV(s) in {filepath}: {', '.join(missing)}")

    max_age_seconds = MAX_INPUT_AGE_DAYS * 86400
    now = time.time()
    stale = [
        name for name in REQUIRED_CSVS
        if now - (filepath / name).stat().st_mtime > max_age_seconds
    ]
    if stale:
        sys.exit(
            f"Input CSV(s) older than {MAX_INPUT_AGE_DAYS:.0f} day(s) in {filepath}: "
            f"{', '.join(stale)} — refusing to export from stale data."
        )


def main():
    check_input_ready()
    df = load_players()
    df = add_pitching_career_stats(df)
    df = add_hitting_career_stats(df)
    df = add_scouted_ratings(df)
    df = count_pitches(df)
    df = can_field(df)
    df = is_flagged(df)
    df = calc_pitching_metrics(df)
    df = calc_potential_pitching_metrics(df)    
    df = calc_hitting_metrics(df)
    df = calc_potential_hitting_metrics(df)    
    df = calc_fielding_metrics(df)
    df = calc_war(df)
    df = df.sort_values(by='best', ascending=False)
    print(df.head(10))  # Preview in terminal
    # export_hitters(df)
    export_html_pages(df)
    
if __name__ == "__main__":
    main()