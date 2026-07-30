"""
Workstream B data extraction: builds the verified MLB ratings<->stats pairs
for 2101/2102/2103 (methodology settled this session — see memory
project-historical-ratings-calibration-plan / NOTES.md).

Three concurrent (same-season) pairs:
    2101: ratings as_of 2101-12-31 (real dump)  x  2101 stats
    2102: ratings as_of 2102-12-31 (real dump)  x  2102 stats
    2103: ratings as_of 2104-04-28 (proxy - no true 2103-12-31 dump exists) x 2103 stats

Population is built from the STATS side (level_id=1, is_latest=1 for that
season), not from player_ratings_history's own level_id — verified this
session that the rating snapshot's own level_id tag is unreliable for these
historical dumps (e.g. for 2102, only 836 of 1,151 real MLB stat performers
have their own rating row tagged level_id=1; the other 315 carry a different
level_id despite genuinely being MLB that season). Rows with no rating match,
or whose player_id doesn't resolve in `player`, are dropped.

Reuses reader.py's RATING_SPLIT_RENAMES (player_ratings_history's own
handedness-split columns -> pistachio column name — first-class columns as
of psd-ootp commit 7e0151f, "Flatten player_ratings_history: promote every
/ratings field out of JSON", 2026-07-30; confirmed these historical dumps
were backfilled under the same flattened shape, not just live rows) rather
than reinventing the column list — same source vocabulary reader.py uses,
just queried at an exact as_of_game_date instead of reader.py's built-in
MAX(as_of_game_date). Composite (handedness-blended) regressors are built
using config.py's own HANDEDNESS_WEIGHTS (R:0.7, L:0.3) since the outcome
data is season-aggregate, not split by opposing-pitcher hand — the resulting
fitted table gets applied to R/L (and potential) columns separately at
inference, unchanged from how metrics_hitting.py/metrics_pitching.py already
work; see calibration/README.md.

Usage:
    PISTACHIO_DB_PATH=/path/to/ootp.db python calibration/extract_pairs.py
"""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from config import DB_PATH, HANDEDNESS_WEIGHTS
from reader import RATING_SPLIT_RENAMES

MLB_LEVEL_ID = 1
RATINGS_SOURCE = "osa"

# (season_year, ratings as_of_game_date) — see module docstring.
SEASON_RATING_PAIRS = [
    (2101, "2101-12-31"),
    (2102, "2102-12-31"),
    (2103, "2104-04-28"),  # proxy, no true 2103-12-31 dump exists
]


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro&immutable=1", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _valid_player_ids(conn: sqlite3.Connection) -> set:
    return {row["player_id"] for row in conn.execute("SELECT player_id FROM player")}


def _ratings_at(conn: sqlite3.Connection, as_of_game_date: str) -> pd.DataFrame:
    column_list = ", ".join(f"r.{c}" for c in RATING_SPLIT_RENAMES)
    rows = conn.execute(
        f"""
        SELECT r.player_id, r.stamina, r.speed, {column_list}
        FROM player_ratings_history r
        WHERE r.source = ? AND r.as_of_game_date = ?
        """,
        (RATINGS_SOURCE, as_of_game_date),
    ).fetchall()

    records = []
    for row in rows:
        record = {"player_id": row["player_id"], "stamina": row["stamina"], "speed": row["speed"]}
        for raw_key, new_key in RATING_SPLIT_RENAMES.items():
            record[new_key] = row[raw_key]
        records.append(record)
    return pd.DataFrame(records)


def _hitting_stats_for_season(conn: sqlite3.Connection, season: int) -> pd.DataFrame:
    rows = conn.execute(
        """
        SELECT player_id, pa, h, d, t, hr, bb, k
        FROM player_batting_stats_history
        WHERE level_id = ? AND season_year = ? AND is_latest = 1
        """,
        (MLB_LEVEL_ID, season),
    ).fetchall()
    df = pd.DataFrame([dict(r) for r in rows])
    if df.empty:
        return df
    return df.groupby("player_id", as_index=False).sum()


def _pitching_stats_for_season(conn: sqlite3.Connection, season: int) -> pd.DataFrame:
    rows = conn.execute(
        """
        SELECT player_id, bf, ha, hra, bb, k
        FROM player_pitching_stats_history
        WHERE level_id = ? AND season_year = ? AND is_latest = 1
        """,
        (MLB_LEVEL_ID, season),
    ).fetchall()
    df = pd.DataFrame([dict(r) for r in rows])
    if df.empty:
        return df
    return df.groupby("player_id", as_index=False).sum()


def _composite(df: pd.DataFrame, right_col: str, left_col: str) -> pd.Series:
    return HANDEDNESS_WEIGHTS["R"] * df[right_col] + HANDEDNESS_WEIGHTS["L"] * df[left_col]


def build_hitting_pairs(conn: sqlite3.Connection, valid_ids: set) -> pd.DataFrame:
    frames = []
    for season, as_of in SEASON_RATING_PAIRS:
        stats = _hitting_stats_for_season(conn, season)
        if stats.empty:
            continue
        ratings = _ratings_at(conn, as_of)
        merged = stats.merge(ratings, on="player_id", how="inner")
        merged = merged[merged["player_id"].isin(valid_ids)]
        merged["season_year"] = season
        frames.append(merged)

    df = pd.concat(frames, ignore_index=True)

    singles = df["h"] - df["d"] - df["t"] - df["hr"]
    df["hr_pct"] = df["hr"] / df["pa"]
    df["k_pct"] = df["k"] / df["pa"]
    df["bb_pct"] = df["bb"] / df["pa"]
    df["1b_pct"] = singles / df["pa"]
    df["2b_pct"] = df["d"] / df["pa"]
    df["3b_pct"] = df["t"] / df["pa"]

    df["babip_c"] = _composite(df, "babipR", "babipL")
    df["avk_c"] = _composite(df, "avkR", "avkL")
    df["gap_c"] = _composite(df, "gapR", "gapL")
    df["pow_c"] = _composite(df, "powR", "powL")
    df["eye_c"] = _composite(df, "eyeR", "eyeL")
    # speed has no R/L split — used as-is, same as production (metrics_hitting.py).

    keep = [
        "player_id", "season_year", "pa",
        "hr_pct", "k_pct", "bb_pct", "1b_pct", "2b_pct", "3b_pct",
        "babip_c", "avk_c", "gap_c", "pow_c", "eye_c", "speed",
    ]
    return df[keep].dropna()


def build_pitching_pairs(conn: sqlite3.Connection, valid_ids: set) -> pd.DataFrame:
    frames = []
    for season, as_of in SEASON_RATING_PAIRS:
        stats = _pitching_stats_for_season(conn, season)
        if stats.empty:
            continue
        ratings = _ratings_at(conn, as_of)
        merged = stats.merge(ratings, on="player_id", how="inner")
        merged = merged[merged["player_id"].isin(valid_ids)]
        merged["season_year"] = season
        frames.append(merged)

    df = pd.concat(frames, ignore_index=True)

    h_nothr = df["ha"] - df["hra"]
    df["hr_vs"] = df["hra"] / df["bf"]
    df["bb_vs"] = df["bb"] / df["bf"]
    df["k_vs"] = df["k"] / df["bf"]
    df["h_nothr_vs"] = h_nothr / df["bf"]

    df["Control_c"] = _composite(df, "ctrlR", "ctrlL")
    df["pBABIP_c"] = _composite(df, "pbabipR", "pbabipL")
    df["HRA_c"] = _composite(df, "hraR", "hraL")
    df["Stuff_c"] = _composite(df, "stuffR", "stuffL")
    # stamina has no R/L split — used as-is, same as production (metrics_pitching.py).

    keep = [
        "player_id", "season_year", "bf",
        "hr_vs", "bb_vs", "k_vs", "h_nothr_vs",
        "Control_c", "pBABIP_c", "HRA_c", "Stuff_c", "stamina",
    ]
    return df[keep].dropna()


def main():
    conn = connect()
    valid_ids = _valid_player_ids(conn)
    hitting = build_hitting_pairs(conn, valid_ids)
    pitching = build_pitching_pairs(conn, valid_ids)
    conn.close()

    out_dir = Path(__file__).resolve().parent / "data"
    out_dir.mkdir(exist_ok=True)
    hitting.to_csv(out_dir / "hitting_pairs.csv", index=False)
    pitching.to_csv(out_dir / "pitching_pairs.csv", index=False)

    print("Hitting pairs by season:")
    print(hitting["season_year"].value_counts().sort_index())
    print(f"Total: {len(hitting)} -> {out_dir / 'hitting_pairs.csv'}")
    print()
    print("Pitching pairs by season:")
    print(pitching["season_year"].value_counts().sort_index())
    print(f"Total: {len(pitching)} -> {out_dir / 'pitching_pairs.csv'}")


if __name__ == "__main__":
    main()
