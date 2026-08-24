"""
Phase A of the "close the remaining gaps" work (see the approved plan):
refits the bare classification thresholds that determine pitcher role
(sp/rp) and position eligibility — currently `config.py`'s
`MINIMUM_STARTER_STAMINA`, `PITCH_MINIMUM_RATING`, and `POSITION_THRESHOLDS`,
all still calibrated to the old 20-80 stopgap domain, not PSD's native
1-100 scale.

Unlike Workstream A/B (continuous rate regressions), these are binary
classification thresholds — the question isn't "what's the slope" but
"what cutoff on this rating best separates real starters from real
relievers" (or real position-X players from everyone else). Optimized by
scanning candidate cutoffs and picking the one maximizing Youden's J
(sensitivity + specificity - 1) against the real label — a standard,
simple, defensible way to pick a binary threshold, and transparent enough
to print the full sweep for human review before transcribing anything
(same "reviewed literal, not auto-wired" discipline as the rest of
calibration/).

Real labels, all four season/ratings pairs (reusing extract_pairs.py's
SEASON_RATING_PAIRS so this stays in sync with the rest of the project):
  - role (sp/rp): from real player_pitching_stats_history gs/g ratio,
    g >= MIN_G_FOR_ROLE to avoid tiny-sample noise.
  - primary position: from real player_fielding_stats_history, the
    position with the most real games played that season (ties broken
    arbitrarily), with its own games-at-position floor.

Usage:
    PISTACHIO_DB_PATH=/path/to/ootp.db python calibration/extract_thresholds.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from config import DB_PATH, PITCH_RATING_COLUMNS, POSITION_THRESHOLDS, MINIMUM_STARTER_STAMINA, PITCH_MINIMUM_RATING
from reader import RATING_SPLIT_RENAMES, DEFENSE_RENAMES
from extract_pairs import connect, SEASON_RATING_PAIRS, RATINGS_SOURCE

MLB_LEVEL_ID = 1
MIN_G_FOR_ROLE = 10
MIN_G_AT_POSITION = 20
STARTER_GS_SHARE = 0.5
MINIMUM_STARTER_PITCHES = 3
MINIMUM_RELIEVER_PITCHES = 2

# player_fielding_stats_history position id -> POSITION_THRESHOLDS key
# (standard OOTP numbering: 1=P,2=C,3=1B,4=2B,5=3B,6=SS,7=LF,8=CF,9=RF;
# 1B/DH have no entry in POSITION_THRESHOLDS today, matching upstream's
# "default to 1B/DH if you don't qualify anywhere else" assumption).
POSITION_ID_TO_KEY = {2: "C", 4: "2B", 5: "3B", 6: "SS", 7: "LF", 8: "CF", 9: "RF"}


def _ratings_unsplit(conn, as_of_game_date: str) -> pd.DataFrame:
    """Current (non-split) stamina, pitch-type grades, and defense grades at one snapshot."""
    pitch_db_cols = [f"pitch_{c}" for c in PITCH_RATING_COLUMNS]
    pitch_select = ", ".join(f"r.{c}" for c in pitch_db_cols)
    def_cols = ", ".join(f"r.{c}" for c in DEFENSE_RENAMES.keys())
    rows = conn.execute(
        f"""
        SELECT r.player_id, r.stamina, {pitch_select}, {def_cols}
        FROM player_ratings_history r
        WHERE r.source = ? AND r.as_of_game_date = ?
        """,
        (RATINGS_SOURCE, as_of_game_date),
    ).fetchall()
    records = []
    for row in rows:
        rec = {"player_id": row["player_id"], "stamina": row["stamina"]}
        for col, db_col in zip(PITCH_RATING_COLUMNS, pitch_db_cols):
            rec[col] = row[db_col]
        for raw_key, new_key in DEFENSE_RENAMES.items():
            rec[new_key] = row[raw_key]
        records.append(rec)
    return pd.DataFrame(records)


def build_role_dataset(conn) -> pd.DataFrame:
    frames = []
    for season, as_of in SEASON_RATING_PAIRS:
        rows = conn.execute(
            """
            SELECT player_id, g, gs FROM player_pitching_stats_history
            WHERE level_id = ? AND season_year = ? AND split_id = 1 AND is_latest = 1 AND g >= ?
            """,
            (MLB_LEVEL_ID, season, MIN_G_FOR_ROLE),
        ).fetchall()
        stats = pd.DataFrame([dict(r) for r in rows])
        if stats.empty:
            continue
        stats["is_starter"] = (stats["gs"] / stats["g"]) >= STARTER_GS_SHARE
        ratings = _ratings_unsplit(conn, as_of)
        merged = stats.merge(ratings, on="player_id", how="inner")
        merged["season_year"] = season
        frames.append(merged)
    return pd.concat(frames, ignore_index=True).dropna(subset=["stamina"])


def build_position_dataset(conn) -> pd.DataFrame:
    frames = []
    for season, as_of in SEASON_RATING_PAIRS:
        rows = conn.execute(
            """
            SELECT player_id, position, g FROM player_fielding_stats_history
            WHERE level_id = ? AND season_year = ? AND is_latest = 1
            """,
            (MLB_LEVEL_ID, season),
        ).fetchall()
        fielding = pd.DataFrame([dict(r) for r in rows])
        if fielding.empty:
            continue
        fielding = fielding[fielding["position"].isin(POSITION_ID_TO_KEY.keys())]
        # primary position = most real games played that season, among the positions
        # POSITION_THRESHOLDS actually covers
        idx = fielding.groupby("player_id")["g"].idxmax()
        primary = fielding.loc[idx].copy()
        primary = primary[primary["g"] >= MIN_G_AT_POSITION]
        primary["primary_key"] = primary["position"].map(POSITION_ID_TO_KEY)
        ratings = _ratings_unsplit(conn, as_of)
        merged = primary.merge(ratings, on="player_id", how="inner")
        merged["season_year"] = season
        frames.append(merged)
    return pd.concat(frames, ignore_index=True)


def best_threshold(values: pd.Series, labels: pd.Series) -> tuple[float, float]:
    """Scans candidate cutoffs (every observed value), returns (best_cutoff, best_youden_j)
    for the rule `values >= cutoff` predicting `labels` (bool)."""
    candidates = sorted(values.dropna().unique())
    best_cut, best_j = candidates[0], -1.0
    n_pos = labels.sum()
    n_neg = (~labels).sum()
    for cut in candidates:
        pred = values >= cut
        tp = (pred & labels).sum()
        tn = ((~pred) & (~labels)).sum()
        sens = tp / n_pos if n_pos else 0
        spec = tn / n_neg if n_neg else 0
        j = sens + spec - 1
        if j > best_j:
            best_cut, best_j = cut, j
    return best_cut, best_j


def refit_stamina(role_df: pd.DataFrame) -> float:
    cut, j = best_threshold(role_df["stamina"], role_df["is_starter"])
    print(f"MINIMUM_STARTER_STAMINA: old={MINIMUM_STARTER_STAMINA}  new={cut:.0f}  "
          f"(Youden's J={j:.3f}, n={len(role_df)})")
    return cut


def refit_pitch_minimum(role_df: pd.DataFrame, stamina_cutoff: float) -> float:
    candidates = sorted(pd.unique(role_df[PITCH_RATING_COLUMNS].values.ravel()))
    candidates = [c for c in candidates if not pd.isna(c)]
    best_cut, best_j = candidates[0], -1.0
    labels = role_df["is_starter"]
    n_pos, n_neg = labels.sum(), (~labels).sum()
    for cut in candidates:
        pitches = (role_df[PITCH_RATING_COLUMNS] >= cut).sum(axis=1)
        pred = (pitches >= MINIMUM_STARTER_PITCHES) & (role_df["stamina"] >= stamina_cutoff)
        tp = (pred & labels).sum()
        tn = ((~pred) & (~labels)).sum()
        sens = tp / n_pos if n_pos else 0
        spec = tn / n_neg if n_neg else 0
        j = sens + spec - 1
        if j > best_j:
            best_cut, best_j = cut, j
    print(f"PITCH_MINIMUM_RATING: old={PITCH_MINIMUM_RATING}  new={best_cut:.0f}  "
          f"(Youden's J={best_j:.3f}, joint w/ refit stamina cutoff, n={len(role_df)})")
    return best_cut


def refit_position_thresholds(pos_df: pd.DataFrame) -> dict:
    print("\nPOSITION_THRESHOLDS (per-component, independent 1D cutoffs):")
    refit = {}
    for key, components in POSITION_THRESHOLDS.items():
        labels = pos_df["primary_key"] == key
        print(f"  {key} (n_qualified={labels.sum()}, n_total={len(pos_df)}):")
        refit[key] = []
        for comp, old_cut in components:
            if comp not in pos_df.columns:
                print(f"    {comp}: not available, skipped")
                refit[key].append((comp, old_cut))
                continue
            cut, j = best_threshold(pos_df[comp], labels)
            print(f"    {comp}: old={old_cut}  new={cut:.0f}  (Youden's J={j:.3f})")
            refit[key].append((comp, cut))
    return refit


def write_thresholds_native(stamina_cut: float, pitch_min_cut: float, position_thresholds: dict):
    """
    Writes calibration/thresholds_native.py — refit role/eligibility
    thresholds (Phase A of "close the remaining gaps"), reviewed literals
    generated from the Youden's J sweep above, not recomputed at import
    time. See calibration/README.md for methodology.
    """
    out_path = Path(__file__).resolve().parent / "thresholds_native.py"
    header = '''"""
Reviewed literal role/eligibility thresholds, refit against real PSD outcome
data (calibration/extract_thresholds.py) — Phase A of "close the remaining
gaps" (see calibration/README.md). Replaces config.py's
MINIMUM_STARTER_STAMINA/PITCH_MINIMUM_RATING/POSITION_THRESHOLDS, all still
calibrated to the old 20-80 stopgap domain, with cutoffs fit directly on
PSD's native 1-100 scale.

Methodology: each cutoff maximizes Youden's J (sensitivity + specificity -
1) for "rating >= cutoff" predicting the real label (real starter/reliever
from player_pitching_stats_history gs/g; real primary position from
player_fielding_stats_history, most-games position that season) — a binary
classification threshold, not a regression. Generated, not hand-typed, but
meant to be read and reviewed before being treated as final.

Caveat (see calibration/README.md): the Catcher thresholds hit Youden's
J=1.000 — a real, wide gap between catchers and everyone else on these
components, not a precisely-pinned decision boundary. Any cutoff between
the highest non-catcher value and the lowest real catcher value would score
the same; treat these three specifically as "clearly separated" rather than
"precisely calibrated."

MINIMUM_STARTER_PITCHES/MINIMUM_RELIEVER_PITCHES are unchanged from
config.py (pitch-COUNT thresholds, not rating thresholds — held fixed while
jointly optimizing PITCH_MINIMUM_RATING_NATIVE against them).
"""

'''
    body = (
        f"MINIMUM_STARTER_STAMINA_NATIVE = {stamina_cut:.0f}\n\n"
        f"PITCH_MINIMUM_RATING_NATIVE = {pitch_min_cut:.0f}\n\n"
        "POSITION_THRESHOLDS_NATIVE = {\n"
    )
    for key, components in position_thresholds.items():
        comps = ", ".join(f"({comp!r}, {cut:.0f})" for comp, cut in components)
        body += f'    {key!r}: [{comps}],\n'
    body += "}\n"
    out_path.write_text(header + body)
    print(f"\nWrote {out_path}")


def main():
    conn = connect()
    role_df = build_role_dataset(conn)
    pos_df = build_position_dataset(conn)
    conn.close()

    print(f"Role dataset: {len(role_df)} pitcher-seasons "
          f"({role_df['is_starter'].sum()} starters, {(~role_df['is_starter']).sum()} relievers)")
    print(f"Position dataset: {len(pos_df)} fielder-seasons with a qualifying primary position\n")

    stamina_cut = refit_stamina(role_df)
    pitch_min_cut = refit_pitch_minimum(role_df, stamina_cut)
    position_thresholds = refit_position_thresholds(pos_df)
    write_thresholds_native(stamina_cut, pitch_min_cut, position_thresholds)

    out_dir = Path(__file__).resolve().parent / "data"
    out_dir.mkdir(exist_ok=True)
    role_df.to_csv(out_dir / "role_dataset.csv", index=False)
    pos_df.to_csv(out_dir / "position_dataset.csv", index=False)


if __name__ == "__main__":
    main()
