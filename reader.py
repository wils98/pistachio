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

# player_ratings_history's own column name -> pistachio's internal column name.
# As of psd-ootp commit 7e0151f (2026-07-30, "Flatten player_ratings_history:
# promote every /ratings field out of JSON"), handedness splits, defense
# grades, and pitch-type grades are all first-class typed columns —
# defense_json/pitches_json no longer exist, and extra_json now holds nothing
# pistachio needs (just a redundant "name" field). Straight renames only; no
# scale conversion, no polarity flip (confirmed against psd-ootp's
# src/ingest/parse.py's RATINGS_ALIASES that e.g. avoid_k_vs_r is a bare alias
# of the old ks_r).
RATING_SPLIT_RENAMES = {
    "power_vs_r": "powR", "power_vs_l": "powL",
    "eye_vs_r": "eyeR", "eye_vs_l": "eyeL",
    "gap_vs_r": "gapR", "gap_vs_l": "gapL",
    "avoid_k_vs_r": "avkR", "avoid_k_vs_l": "avkL",
    "babip_vs_r": "babipR", "babip_vs_l": "babipL",
    "control_vs_r": "ctrlR", "control_vs_l": "ctrlL",
    "stuff_vs_r": "stuffR", "stuff_vs_l": "stuffL",
    "pbabip_vs_r": "pbabipR", "pbabip_vs_l": "pbabipL",
    "hra_vs_r": "hraR", "hra_vs_l": "hraL",
}

# Defense grades: formerly defense_json's raw key -> pistachio's internal
# column name; now player_ratings_history's own def_* columns -> same names.
DEFENSE_RENAMES = {
    "def_cfrm": "Cfram", "def_cblk": "Cabil", "def_carm": "Carm",
    "def_ofr": "OFrange", "def_ofa": "OFarm", "def_ofe": "OFerror",
    "def_ifr": "IFrange", "def_ife": "IFerror", "def_ifa": "IFarm", "def_tdp": "turnDP",
}

# Pitch-type grades: formerly pitches_json's raw keys (which already matched
# PITCH_RATING_COLUMNS/POTENTIAL_PITCH_RATING_COLUMNS's names directly, no
# rename needed); now player_ratings_history's own pitch_*/pitch_*_pot
# columns -> those same config.py names.
PITCH_COLUMN_RENAMES = {
    "pitch_fst": "fst", "pitch_snk": "snk", "pitch_cutt": "cutt", "pitch_crv": "crv",
    "pitch_sld": "sld", "pitch_chg": "chg", "pitch_splt": "splt", "pitch_frk": "frk",
    "pitch_circhg": "circhg", "pitch_scr": "scr", "pitch_kncrv": "kncrv", "pitch_knbl": "knbl",
    "pitch_fst_pot": "potfst", "pitch_snk_pot": "potsnk", "pitch_cutt_pot": "potcutt",
    "pitch_crv_pot": "potcrv", "pitch_sld_pot": "potsld", "pitch_chg_pot": "potchg",
    "pitch_splt_pot": "potsplt", "pitch_frk_pot": "potfrk", "pitch_circhg_pot": "potcirchg",
    "pitch_scr_pot": "potscr", "pitch_kncrv_pot": "potkncrv", "pitch_knbl_pot": "potknbl",
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


_RATING_SOURCE_COLUMNS = (
    ["power_pot", "eye_pot", "avoid_k_pot", "gap_pot", "control_pot", "stuff_pot",
     "babip_pot", "hra_pot", "pbabip_pot", "stamina", "speed"]
    + list(RATING_SPLIT_RENAMES.keys())
    + list(DEFENSE_RENAMES.keys())
    + list(PITCH_COLUMN_RENAMES.keys())
)


def add_scouted_ratings(df: pd.DataFrame) -> pd.DataFrame:
    """
    Pulls each player's most recent OSA-sourced ratings snapshot. Every
    rating pistachio uses — current + potential for the main hit/pitch
    tools, handedness splits, defense grades, per-pitch-type grades — is a
    first-class column on player_ratings_history (see RATING_SPLIT_RENAMES/
    DEFENSE_RENAMES/PITCH_COLUMN_RENAMES's docstrings for the flattening
    history); this just selects and renames them, no JSON unpacking needed.
    """
    conn = _connect()
    column_list = ", ".join(f"r.{c}" for c in _RATING_SOURCE_COLUMNS)
    rows = conn.execute(f"""
        SELECT r.player_id, r.age, r.bats, r.throws, {column_list}
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
            "bats": row["bats"], "throws": row["throws"],
            "powP": row["power_pot"], "eyeP": row["eye_pot"],
            "avkP": row["avoid_k_pot"], "gapP": row["gap_pot"],
            "ctrlP": row["control_pot"], "stuffP": row["stuff_pot"],
            "babipP": row["babip_pot"], "hraP": row["hra_pot"], "pbabipP": row["pbabip_pot"],
            "stamina": row["stamina"], "speed": row["speed"],
        }
        for raw_key, new_key in RATING_SPLIT_RENAMES.items():
            record[new_key] = row[raw_key]
        for raw_key, new_key in DEFENSE_RENAMES.items():
            record[new_key] = row[raw_key]
        for raw_key, new_key in PITCH_COLUMN_RENAMES.items():
            record[new_key] = row[raw_key]

        records.append(record)

    ratings_df = pd.DataFrame(records)
    df = pd.merge(df, ratings_df, on="player_id", how="left")
    return df


STOPGAP_SCALED_COLUMNS = (
    list(RATING_SPLIT_RENAMES.values())
    + list(DEFENSE_RENAMES.values())
    + PITCH_RATING_COLUMNS
    + POTENTIAL_PITCH_RATING_COLUMNS
    + ["powP", "eyeP", "avkP", "gapP", "ctrlP", "stuffP", "babipP", "hraP", "pbabipP", "stamina", "speed"]
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


def add_rule5_eligible(df: pd.DataFrame) -> pd.DataFrame:
    """
    Flags each player as Rule 5 draft-eligible: still in an organization's
    system below the MLB level (organization_id > 0, level_id != MLB_LEVEL_ID
    — not already on the active MLB roster), never accrued MLB time
    (mlb_service_days = 0 — excludes anyone who's already been up, even
    briefly, since real Rule 5 exposure is about protecting players who've
    never been added to the 40-man, not just "not on the roster today"),
    not retired, and pro_service_years has reached or passed
    years_protected_from_rule_5 — OOTP's own fixed grace-period allotment
    (5 years if signed at 18 or younger, 4 if 19+; confirmed against real
    age-at-signing data in the live DB, not assumed). Neither
    years_protected_from_rule_5 nor mlb_service_days's parent fields are
    promoted uniformly — mlb_service_days IS a first-class `player` column,
    but years_protected_from_rule_5 lives in extra_json alongside
    draft_eligible/draft_year/etc. (see psd-ootp's PLAYER_DIM_ALIASES) — so
    this parses that one directly.

    ">=" rather than "==" (only the season a player first crosses the
    threshold): includes every eligible non-MLB player still in an org,
    not just this year's newly-exposed cohort — real teams do leave
    fringe players perpetually exposed for years rather than spend a
    40-man spot protecting them. There's no direct "on the 40-man roster"
    flag in this data model to narrow the pool further (extra_json's
    is_on_secondary was checked and doesn't match that shape — populated
    mostly at the MLB level, not tied to minor-league roster protection).
    """
    conn = _connect()
    rows = conn.execute("""
        SELECT player_id, pro_service_years, extra_json
        FROM player
        WHERE retired = 0 AND organization_id > 0 AND level_id != ?
          AND mlb_service_days = 0
    """, (MLB_LEVEL_ID,)).fetchall()
    conn.close()

    eligible_ids = set()
    for row in rows:
        extra = json.loads(row["extra_json"])
        allotment = extra.get("years_protected_from_rule_5")
        service = row["pro_service_years"]
        if allotment is not None and service is not None and service >= allotment:
            eligible_ids.add(row["player_id"])

    df["rule5_eligible"] = df["player_id"].isin(eligible_ids)
    return df
