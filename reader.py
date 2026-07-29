import json
import sqlite3

import numpy as np
import pandas as pd
from config import (
    DB_PATH,
    PITCH_RATING_COLUMNS,
    POTENTIAL_PITCH_RATING_COLUMNS,
    PITCH_MINIMUM_RATING,
    POSITION_THRESHOLDS,
    pistachio_filepath,
)

RATINGS_SOURCE = "osa"
MLB_LEVEL_ID = 1
OVERALL_SPLIT_ID = 1

# Maps extra_json's raw (normalized) key -> pistachio's internal column name.
# Everything here is a straight rename; no scale conversion, no polarity flip
# (confirmed against psd-ootp's ingest/parse.py that avoid_k/ks is a bare alias).
EXTRA_JSON_RENAMES = {
    "pow_r": "powR", "pow_l": "powL",
    "eye_r": "eyeR", "eye_l": "eyeL",
    "gap_r": "gapR", "gap_l": "gapL",
    "ks_r": "avkR", "ks_l": "avkL",
    "babip_r": "babipR", "babip_l": "babipL",
    "ctrl_r": "ctrlR", "ctrl_l": "ctrlL",
    "stf_r": "stuffR", "stf_l": "stuffL",
    "pbabip_r": "pbabipR", "pbabip_l": "pbabipL",
    "hra_r": "hraR", "hra_l": "hraL",
    "potbabip": "babipP", "pothra": "hraP", "potpbabip": "pbabipP",
}

# defense_json's raw key -> pistachio's internal column name.
DEFENSE_JSON_RENAMES = {
    "cfrm": "Cfram", "cblk": "Cabil", "carm": "Carm",
    "ofr": "OFrange", "ofa": "OFarm", "ofe": "OFerror",
    "ifr": "IFrange", "ife": "IFerror", "ifa": "IFarm", "tdp": "turnDP",
}


def _connect() -> sqlite3.Connection:
    """Read-only connection — physically cannot write, mirrors psd-ootp's /api/sql."""
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro&immutable=1", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def load_players() -> pd.DataFrame:
    conn = _connect()
    rows = conn.execute("""
        SELECT p.player_id, p.first_name, p.last_name, p.retired,
               p.organization_id, p.team_id, p.draft_pool_year,
               COALESCE(t.nickname, 'Free') AS org
        FROM player p
        LEFT JOIN team t ON p.organization_id = t.team_id
    """).fetchall()
    conn.close()

    df = pd.DataFrame([dict(r) for r in rows])
    df = df[df.retired != 1]
    df = df.drop(columns=["retired"])
    df["name"] = df["first_name"] + " " + df["last_name"]
    df = df.drop(columns=["first_name", "last_name"])
    # Minor-leaguers: assigned to an org whose active roster (team_id) differs from
    # their current affiliate team — same "org vs team_id" comparison as before.
    df["minor"] = (df["organization_id"] != df["team_id"]).astype(int)
    df = df.drop(columns=["organization_id"])
    return df


def add_pitching_career_stats(df: pd.DataFrame) -> pd.DataFrame:
    # Each as_of_game_date row is a season-to-date CUMULATIVE snapshot, not a
    # per-period delta — summing across snapshot dates would double/triple-count
    # every player. Take only each player's most recent snapshot (per season);
    # summing across team_id/stint within that single date is still correct and
    # intentional (combines multi-team seasons).
    conn = _connect()
    rows = conn.execute("""
        SELECT player_id, season_year, outs
        FROM player_pitching_stats_history s
        WHERE level_id = ? AND split_id = ?
          AND as_of_game_date = (
              SELECT MAX(s2.as_of_game_date) FROM player_pitching_stats_history s2
              WHERE s2.player_id = s.player_id AND s2.level_id = s.level_id
                AND s2.split_id = s.split_id AND s2.season_year = s.season_year
          )
    """, (MLB_LEVEL_ID, OVERALL_SPLIT_ID)).fetchall()
    conn.close()

    stats_df = pd.DataFrame([dict(r) for r in rows])
    if stats_df.empty:
        df["ip"] = 0
        return df
    max_year = stats_df["season_year"].max()
    stats_df = stats_df[stats_df["season_year"] == max_year]
    stats_df = stats_df.groupby("player_id")[["outs"]].sum().reset_index()
    stats_df["ip"] = (stats_df["outs"] / 3).round(1)
    stats_df = stats_df.drop(columns=["outs"])
    df = pd.merge(df, stats_df, on="player_id", how="left")
    df["ip"] = df["ip"].fillna(0)
    return df


def add_hitting_career_stats(df: pd.DataFrame) -> pd.DataFrame:
    # Same cumulative-snapshot reasoning as add_pitching_career_stats() above.
    conn = _connect()
    rows = conn.execute("""
        SELECT player_id, season_year, pa
        FROM player_batting_stats_history s
        WHERE level_id = ? AND split_id = ?
          AND as_of_game_date = (
              SELECT MAX(s2.as_of_game_date) FROM player_batting_stats_history s2
              WHERE s2.player_id = s.player_id AND s2.level_id = s.level_id
                AND s2.split_id = s.split_id AND s2.season_year = s.season_year
          )
    """, (MLB_LEVEL_ID, OVERALL_SPLIT_ID)).fetchall()
    conn.close()

    stats_df = pd.DataFrame([dict(r) for r in rows])
    if stats_df.empty:
        df["pa"] = 0
        return df
    max_year = stats_df["season_year"].max()
    stats_df = stats_df[stats_df["season_year"] == max_year]
    stats_df = stats_df.groupby("player_id")[["pa"]].sum().reset_index()
    df = pd.merge(df, stats_df, on="player_id", how="left")
    df["pa"] = df["pa"].fillna(0).astype(int)
    return df


def add_scouted_ratings(df: pd.DataFrame) -> pd.DataFrame:
    """
    Pulls each player's most recent OSA-sourced ratings snapshot. Ratings are
    a mix of first-class columns (current + potential for the main hit/pitch
    tools, stamina, speed) and JSON blobs (extra_json for handedness splits +
    potential-babip/hra/pbabip, defense_json for fielding grades, pitches_json
    for per-pitch-type grades) — unpacked here in Python rather than via SQL
    json_extract(), so every source key stays visible in one place.
    """
    conn = _connect()
    rows = conn.execute("""
        SELECT r.player_id, r.age, r.power_pot, r.eye_pot, r.avoid_k_pot, r.gap_pot,
               r.control_pot, r.stuff_pot, r.stamina, r.speed,
               r.defense_json, r.pitches_json, r.extra_json
        FROM player_ratings_history r
        WHERE r.source = ?
          AND r.as_of_game_date = (
              SELECT MAX(r2.as_of_game_date) FROM player_ratings_history r2
              WHERE r2.player_id = r.player_id AND r2.source = r.source
          )
    """, (RATINGS_SOURCE,)).fetchall()
    conn.close()

    records = []
    for row in rows:
        record = {
            "player_id": row["player_id"],
            "age": row["age"],
            "powP": row["power_pot"], "eyeP": row["eye_pot"],
            "avkP": row["avoid_k_pot"], "gapP": row["gap_pot"],
            "ctrlP": row["control_pot"], "stuffP": row["stuff_pot"],
            "stamina": row["stamina"], "speed": row["speed"],
        }

        extra = json.loads(row["extra_json"] or "{}")
        for raw_key, new_key in EXTRA_JSON_RENAMES.items():
            record[new_key] = extra.get(raw_key)

        defense = json.loads(row["defense_json"] or "{}")
        for raw_key, new_key in DEFENSE_JSON_RENAMES.items():
            record[new_key] = defense.get(raw_key)

        pitches = json.loads(row["pitches_json"] or "{}")
        for col in PITCH_RATING_COLUMNS + POTENTIAL_PITCH_RATING_COLUMNS:
            record[col] = pitches.get(col)

        records.append(record)

    ratings_df = pd.DataFrame(records)
    df = pd.merge(df, ratings_df, on="player_id", how="left")
    return df


STOPGAP_SCALED_COLUMNS = (
    list(EXTRA_JSON_RENAMES.values())
    + list(DEFENSE_JSON_RENAMES.values())
    + PITCH_RATING_COLUMNS
    + POTENTIAL_PITCH_RATING_COLUMNS
    + ["powP", "eyeP", "avkP", "gapP", "ctrlP", "stuffP", "stamina", "speed"]
)


def apply_native_scale_stopgap(df: pd.DataFrame) -> pd.DataFrame:
    """
    TEMPORARY STOPGAP — delete once config.py's regression tables are rebuilt
    natively for PSD's 1-100 rating scale (see NOTES.md).

    Every regression table in config.py (BATTING_COMPONENTS_ADJUST_MAP,
    PITCHING_COMPONENTS_ADJUST_MAP, FIELDING_RUN_VALUES_VS_REPLACEMENT) plus the
    plain thresholds outside any table (POSITION_THRESHOLDS, PITCH_MINIMUM_RATING)
    assume 20-80 scouting-grade input. PSD's ratings are natively 1-100 (by league
    rule-book design, not a bug).

    This rescales each affected column to its league-wide percentile, mapped onto
    the 20-80 domain — left as a continuous value (not rounded to a bucket) since
    the table lookups themselves now interpolate between buckets (see
    rating_lookup.py) rather than requiring an exact key match. Preserves
    relative ordering (best players still rank highest) but produces NO real
    calibration — it's upstream's coefficients applied to a rescaled view of
    PSD's ratings, nothing more. Absolute output numbers are not trustworthy
    until the tables themselves are rebuilt for the real 1-100 domain.
    """
    for col in STOPGAP_SCALED_COLUMNS:
        pct = df[col].rank(pct=True)
        df[col] = 20 + pct * 60
    return df


# count 'how many pitches' a pitcher has got based on minimum threshold ratings
def count_pitches(df: pd.DataFrame) -> pd.DataFrame:
    pitch_flags = df[PITCH_RATING_COLUMNS] >= PITCH_MINIMUM_RATING
    df["pitches"] = pitch_flags.astype(int).sum(axis=1)
    potential_pitch_flags = df[POTENTIAL_PITCH_RATING_COLUMNS] >= PITCH_MINIMUM_RATING
    df["pitchesP"] = potential_pitch_flags.astype(int).sum(axis=1)
    df = df.drop(columns=PITCH_RATING_COLUMNS)
    df = df.drop(columns=POTENTIAL_PITCH_RATING_COLUMNS)
    return df


# determine whether a player 'can field' at a given position based on minimum threshold ratings
def can_field(df: pd.DataFrame) -> pd.DataFrame:
    def evaluate_row(row):
        positions = []
        for pos, checks in POSITION_THRESHOLDS.items():
            if all(row.get(col, 0) >= threshold for col, threshold in checks):
                positions.append(pos)
        return ", ".join(positions)

    df["field"] = df.apply(evaluate_row, axis=1)
    return df


# Add a flag column for names listed in flagged.txt
def is_flagged(df: pd.DataFrame) -> pd.DataFrame:
    # Read player_ids from text file and convert to integers
    with open(pistachio_filepath / 'flagged.txt', 'r') as f:
        flagged_ids = [int(line.strip()) for line in f if line.strip().isdigit()]

    # Add 'flag' column based on player_id match
    df["flag"] = np.where(df["player_id"].isin(flagged_ids), "flag", "")
    return df


def add_draft_availability(df: pd.DataFrame) -> pd.DataFrame:
    """
    Flags each draft-pool player as still available ('avail') vs already
    selected in this year's draft. Sourced from draft_pick.picked_at_utc, not
    player.organization_id/team_id — confirmed those stay 0 for already-picked
    players too (this game's ingest doesn't move drafted players onto a roster
    immediately), so draft_pick is the only reliable "has this pick happened
    yet" signal.
    """
    conn = _connect()
    rows = conn.execute("""
        SELECT DISTINCT player_id FROM draft_pick
        WHERE picked_at_utc IS NOT NULL AND player_id IS NOT NULL
    """).fetchall()
    conn.close()

    drafted_ids = {row["player_id"] for row in rows}
    df["avail"] = np.where(
        df["draft_pool_year"].notna() & ~df["player_id"].isin(drafted_ids),
        "avail", ""
    )
    return df
