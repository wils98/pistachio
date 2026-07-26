import os
import sqlite3
import sys
from datetime import datetime, timezone

from config import DB_PATH
from reader import load_players, add_pitching_career_stats, add_hitting_career_stats, add_scouted_ratings, count_pitches, can_field, is_flagged, RATINGS_SOURCE
from metrics_pitching import calc_pitching_metrics, calc_potential_pitching_metrics
from metrics_hitting import calc_hitting_metrics, calc_potential_hitting_metrics
from metrics_fielding import calc_fielding_metrics
from metrics_war import calc_war
# from exporter import export_hitters
from exporter import export_html_pages

# Player data comes from psd-ootp's own DB, which is kept fresh by its own ingest
# schedule (not ours to police) — but if that pipeline stalls, we'd otherwise keep
# silently re-serving the same projections under a fresh-looking export timestamp.
# Guard against that using the *real* wall-clock time the ratings were actually
# fetched (raw_payload.fetched_at_utc), not the in-game date (player_ratings_
# history.as_of_game_date is in OOTP's fictional calendar, not elapsed real time).
MAX_DATA_AGE_DAYS = float(os.environ.get("PISTACHIO_MAX_DATA_AGE_DAYS", "3"))


def check_data_ready():
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro&immutable=1", uri=True)
        row = conn.execute("""
            SELECT MAX(rp.fetched_at_utc)
            FROM player_ratings_history r
            JOIN raw_payload rp ON r.payload_id = rp.payload_id
            WHERE r.source = ?
        """, (RATINGS_SOURCE,)).fetchone()
        conn.close()
    except sqlite3.Error as exc:
        sys.exit(f"Could not reach psd-ootp's DB at {DB_PATH}: {exc}")

    if row is None or row[0] is None:
        sys.exit(f"No '{RATINGS_SOURCE}' ratings found in {DB_PATH} — nothing to project.")

    fetched_at = datetime.strptime(row[0], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    age_days = (datetime.now(timezone.utc) - fetched_at).total_seconds() / 86400
    if age_days > MAX_DATA_AGE_DAYS:
        sys.exit(
            f"Most recent '{RATINGS_SOURCE}' ratings in {DB_PATH} are {age_days:.1f} day(s) "
            f"old (limit {MAX_DATA_AGE_DAYS:.0f}) — refusing to export from stale data. "
            f"Check psd-ootp's own ingest timer (psd-ootp-ingest status)."
        )


def main():
    check_data_ready()
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