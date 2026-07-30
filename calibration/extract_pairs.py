"""
Workstream B data extraction: builds the verified MLB ratings<->stats pairs
for 2101/2102/2103 (methodology settled this session — see memory
project-historical-ratings-calibration-plan / NOTES.md), split-aware.

Three concurrent (same-season) pairs:
    2101: ratings as_of 2101-12-31 (real dump)  x  2101 stats
    2102: ratings as_of 2102-12-31 (real dump)  x  2102 stats
    2103: ratings as_of 2104-04-28 (proxy - no true 2103-12-31 dump exists) x 2103 stats

Population is built from the STATS side (level_id=1, is_latest=1 for that
season), not from player_ratings_history's own level_id — verified this
session that the rating snapshot's own level_id tag is unreliable for these
historical dumps. Rows with no rating match, or whose player_id doesn't
resolve in `player`, are dropped.

SPLIT-AWARE (corrected from an earlier version of this script, which pulled
every split_id — 1=overall, 2=vsL, 3=vsR — and summed them together; that
accidentally self-cancelled for rate outcomes (hr_pct etc.), since summing
overall+vsL+vsR just doubles both numerator and denominator identically,
but it also meant real split-specific performance (split_id=2/3) was never
actually used, even though it exists). Real per-player vsL/vsR performance
(player_batting_stats_history.split_id — 2=vsL, 3=vsR) is now paired with
the matching side's rating (e.g. real vsR outcome <-> powR/ctrlR, not a
fit-time blend of both sides into one composite). A minimum PA/BF-per-split
floor (30) drops noisy small-sample splits — verified against real data
this still leaves ~1,400+ rows per split league-wide before the ratings
join narrows it further.

Also computes real bats/throws-conditional exposure weights (what fraction
of a player's PA/BF are actually against R- vs L-handed opponents, given
their OWN bats/throws) — replaces config.py's flat, never-refit
HANDEDNESS_WEIGHTS={"R":0.7,"L":0.3} applied identically to every player.
Verified directly: PSD's real platoon split is bats-dependent (a lefty
batter faces RHP ~83.7% of the time vs. a righty batter's ~72.7% — real
platooning, not noise) — a single flat ratio was hiding this.

Reuses reader.py's RATING_SPLIT_RENAMES (player_ratings_history's own
handedness-split columns -> pistachio column name) rather than reinventing
the column list.

Usage:
    PISTACHIO_DB_PATH=/path/to/ootp.db python calibration/extract_pairs.py
"""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from config import DB_PATH
from reader import RATING_SPLIT_RENAMES

MLB_LEVEL_ID = 1
RATINGS_SOURCE = "osa"
VS_L_SPLIT_ID = 2
VS_R_SPLIT_ID = 3
MIN_PA_PER_SPLIT = 30
MIN_BF_PER_SPLIT = 30

# (season_year, ratings as_of_game_date) — see module docstring.
SEASON_RATING_PAIRS = [
    (2101, "2101-12-31"),
    (2102, "2102-12-31"),
    (2103, "2104-04-28"),  # proxy, no true 2103-12-31 dump exists
]

# side -> (stats split_id, hitting rating suffix, pitching rating suffix)
SIDES = {"R": VS_R_SPLIT_ID, "L": VS_L_SPLIT_ID}


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


def _hitting_stats_for_season_split(conn: sqlite3.Connection, season: int, split_id: int) -> pd.DataFrame:
    rows = conn.execute(
        """
        SELECT player_id, pa, h, d, t, hr, bb, k
        FROM player_batting_stats_history
        WHERE level_id = ? AND season_year = ? AND split_id = ? AND is_latest = 1
        """,
        (MLB_LEVEL_ID, season, split_id),
    ).fetchall()
    df = pd.DataFrame([dict(r) for r in rows])
    if df.empty:
        return df
    df = df.groupby("player_id", as_index=False).sum()
    return df[df["pa"] >= MIN_PA_PER_SPLIT]


def _pitching_stats_for_season_split(conn: sqlite3.Connection, season: int, split_id: int) -> pd.DataFrame:
    rows = conn.execute(
        """
        SELECT player_id, bf, ha, hra, bb, k
        FROM player_pitching_stats_history
        WHERE level_id = ? AND season_year = ? AND split_id = ? AND is_latest = 1
        """,
        (MLB_LEVEL_ID, season, split_id),
    ).fetchall()
    df = pd.DataFrame([dict(r) for r in rows])
    if df.empty:
        return df
    df = df.groupby("player_id", as_index=False).sum()
    return df[df["bf"] >= MIN_BF_PER_SPLIT]


def build_hitting_pairs(conn: sqlite3.Connection, valid_ids: set) -> pd.DataFrame:
    frames = []
    for season, as_of in SEASON_RATING_PAIRS:
        ratings = _ratings_at(conn, as_of)
        for side, split_id in SIDES.items():
            stats = _hitting_stats_for_season_split(conn, season, split_id)
            if stats.empty:
                continue
            merged = stats.merge(ratings, on="player_id", how="inner")
            merged = merged[merged["player_id"].isin(valid_ids)]
            merged["season_year"] = season
            merged["side"] = side

            singles = merged["h"] - merged["d"] - merged["t"] - merged["hr"]
            merged["hr_pct"] = merged["hr"] / merged["pa"]
            merged["k_pct"] = merged["k"] / merged["pa"]
            merged["bb_pct"] = merged["bb"] / merged["pa"]
            merged["1b_pct"] = singles / merged["pa"]
            merged["2b_pct"] = merged["d"] / merged["pa"]
            merged["3b_pct"] = merged["t"] / merged["pa"]

            # this side's real outcome pairs with this side's own rating —
            # no composite blend.
            merged["babip_side"] = merged[f"babip{side}"]
            merged["avk_side"] = merged[f"avk{side}"]
            merged["gap_side"] = merged[f"gap{side}"]
            merged["pow_side"] = merged[f"pow{side}"]
            merged["eye_side"] = merged[f"eye{side}"]

            keep = [
                "player_id", "season_year", "side", "pa",
                "hr_pct", "k_pct", "bb_pct", "1b_pct", "2b_pct", "3b_pct",
                "babip_side", "avk_side", "gap_side", "pow_side", "eye_side", "speed",
            ]
            frames.append(merged[keep])

    return pd.concat(frames, ignore_index=True).dropna()


def build_pitching_pairs(conn: sqlite3.Connection, valid_ids: set) -> pd.DataFrame:
    frames = []
    for season, as_of in SEASON_RATING_PAIRS:
        ratings = _ratings_at(conn, as_of)
        for side, split_id in SIDES.items():
            stats = _pitching_stats_for_season_split(conn, season, split_id)
            if stats.empty:
                continue
            merged = stats.merge(ratings, on="player_id", how="inner")
            merged = merged[merged["player_id"].isin(valid_ids)]
            merged["season_year"] = season
            merged["side"] = side

            h_nothr = merged["ha"] - merged["hra"]
            merged["hr_vs"] = merged["hra"] / merged["bf"]
            merged["bb_vs"] = merged["bb"] / merged["bf"]
            merged["k_vs"] = merged["k"] / merged["bf"]
            merged["h_nothr_vs"] = h_nothr / merged["bf"]

            merged["Control_side"] = merged[f"ctrl{side}"]
            merged["pBABIP_side"] = merged[f"pbabip{side}"]
            merged["HRA_side"] = merged[f"hra{side}"]
            merged["Stuff_side"] = merged[f"stuff{side}"]

            keep = [
                "player_id", "season_year", "side", "bf",
                "hr_vs", "bb_vs", "k_vs", "h_nothr_vs",
                "Control_side", "pBABIP_side", "HRA_side", "Stuff_side", "stamina",
            ]
            frames.append(merged[keep])

    return pd.concat(frames, ignore_index=True).dropna()


def compute_exposure_weights(conn: sqlite3.Connection) -> dict:
    """
    Real fraction of PA/BF against R- vs L-handed opponents, conditional on
    the player's OWN bats/throws — replaces config.py's flat HANDEDNESS_WEIGHTS.
    League-wide across all 3 seasons, independent of the ratings-pairing
    methodology (this only needs real stats + real bio data).
    """
    hitting_rows = conn.execute(
        """
        SELECT b.bats, s.split_id, SUM(s.pa) AS total_pa
        FROM player_batting_stats_history s
        JOIN (
            SELECT player_id, bats,
                   ROW_NUMBER() OVER (PARTITION BY player_id ORDER BY as_of_game_date DESC) AS rn
            FROM player_ratings_history WHERE source = ?
        ) b ON b.player_id = s.player_id AND b.rn = 1
        WHERE s.level_id = ? AND s.season_year IN (2101, 2102, 2103)
          AND s.is_latest = 1 AND s.split_id IN (?, ?)
          AND b.bats IN ('L', 'R', 'S')
        GROUP BY b.bats, s.split_id
        """,
        (RATINGS_SOURCE, MLB_LEVEL_ID, VS_L_SPLIT_ID, VS_R_SPLIT_ID),
    ).fetchall()

    pitching_rows = conn.execute(
        """
        SELECT p.throws, s.split_id, SUM(s.bf) AS total_bf
        FROM player_pitching_stats_history s
        JOIN (
            SELECT player_id, throws,
                   ROW_NUMBER() OVER (PARTITION BY player_id ORDER BY as_of_game_date DESC) AS rn
            FROM player_ratings_history WHERE source = ?
        ) p ON p.player_id = s.player_id AND p.rn = 1
        WHERE s.level_id = ? AND s.season_year IN (2101, 2102, 2103)
          AND s.is_latest = 1 AND s.split_id IN (?, ?)
          AND p.throws IN ('L', 'R')
        GROUP BY p.throws, s.split_id
        """,
        (RATINGS_SOURCE, MLB_LEVEL_ID, VS_L_SPLIT_ID, VS_R_SPLIT_ID),
    ).fetchall()

    def _to_weights(rows, key_col):
        totals = {}
        for row in rows:
            totals.setdefault(row[key_col], {})[row["split_id"]] = row["total_pa" if key_col == "bats" else "total_bf"]
        weights = {}
        for key, splits in totals.items():
            vs_l, vs_r = splits.get(VS_L_SPLIT_ID, 0), splits.get(VS_R_SPLIT_ID, 0)
            total = vs_l + vs_r
            if total > 0:
                weights[key] = {"R": vs_r / total, "L": vs_l / total}
        return weights

    return {
        "hitting": _to_weights(hitting_rows, "bats"),
        "pitching": _to_weights(pitching_rows, "throws"),
    }


def main():
    conn = connect()
    valid_ids = _valid_player_ids(conn)
    hitting = build_hitting_pairs(conn, valid_ids)
    pitching = build_pitching_pairs(conn, valid_ids)
    weights = compute_exposure_weights(conn)
    conn.close()

    out_dir = Path(__file__).resolve().parent / "data"
    out_dir.mkdir(exist_ok=True)
    hitting.to_csv(out_dir / "hitting_pairs.csv", index=False)
    pitching.to_csv(out_dir / "pitching_pairs.csv", index=False)

    print("Hitting pairs by season/side:")
    print(hitting.groupby(["season_year", "side"]).size())
    print(f"Total: {len(hitting)} -> {out_dir / 'hitting_pairs.csv'}")
    print()
    print("Pitching pairs by season/side:")
    print(pitching.groupby(["season_year", "side"]).size())
    print(f"Total: {len(pitching)} -> {out_dir / 'pitching_pairs.csv'}")
    print()
    print("Real exposure weights (bats/throws -> fraction of PA/BF vs R/L):")
    print(json_dump(weights))


def json_dump(obj):
    import json
    return json.dumps(obj, indent=2)


if __name__ == "__main__":
    main()
