"""
Standalone entry point for the native-PSD-calibrated pipeline — produces
hitters_native.html/pitchers_native.html for side-by-side comparison against
the live stopgap-based pistachio output. Does not import from, or get
imported by, main.py — running this touches nothing in the live pipeline.

Fielding VALUE is still explicitly deferred (see calibration/README.md — no
verified fielding-outcome dataset yet, Phase B not started), so this still
runs the existing, unmodified metrics_fielding.py against a stopgap-scaled
copy for *_def columns only.

Role classification (sp/rp) and position ELIGIBILITY ("field") are Phase A,
now done: computed directly on raw (non-stopgap) ratings using
thresholds_native.py's refit cutoffs (count_pitches_native/can_field_native
below), replacing the previous stopgap-scaled-copy workaround entirely —
these two no longer touch apply_native_scale_stopgap() at all.

Usage:
    PISTACHIO_DB_PATH=/path/to/ootp.db PISTACHIO_OUTPUT_DIR=/path/to/outputs \\
        python calibration/main_native.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import (
    MINIMUM_STARTER_PITCHES,
    MINIMUM_RELIEVER_PITCHES,
    PITCH_RATING_COLUMNS,
    POTENTIAL_PITCH_RATING_COLUMNS,
)
from thresholds_native import (
    MINIMUM_STARTER_STAMINA_NATIVE,
    PITCH_MINIMUM_RATING_NATIVE,
    POSITION_THRESHOLDS_NATIVE,
)
from reader import (
    load_players,
    add_pitching_career_stats,
    add_hitting_career_stats,
    add_scouted_ratings,
    apply_native_scale_stopgap,
    is_flagged,
    add_draft_availability,
)
from metrics_fielding import calc_fielding_metrics
from metrics_war import calc_war
from metrics_hitting_native import calc_hitting_metrics_native, calc_potential_hitting_metrics_native
from metrics_pitching_native import calc_pitching_metrics_native, calc_potential_pitching_metrics_native
from exporter import export_advanced_html


def count_pitches_native(df):
    """Native equivalent of reader.py's count_pitches() — same shape, but
    against raw ratings with the refit PITCH_MINIMUM_RATING_NATIVE cutoff."""
    pitch_flags = df[PITCH_RATING_COLUMNS] >= PITCH_MINIMUM_RATING_NATIVE
    df["pitches"] = pitch_flags.astype(int).sum(axis=1)
    potential_pitch_flags = df[POTENTIAL_PITCH_RATING_COLUMNS] >= PITCH_MINIMUM_RATING_NATIVE
    df["pitchesP"] = potential_pitch_flags.astype(int).sum(axis=1)
    return df


def can_field_native(df):
    """Native equivalent of reader.py's can_field() — same shape, but
    against raw ratings with POSITION_THRESHOLDS_NATIVE's refit cutoffs."""
    def evaluate_row(row):
        positions = []
        for pos, checks in POSITION_THRESHOLDS_NATIVE.items():
            if all(row.get(col, 0) >= threshold for col, threshold in checks):
                positions.append(pos)
        return ", ".join(positions)
    df["field"] = df.apply(evaluate_row, axis=1)
    return df


def _identify_role(row, pitches_col):
    if row[pitches_col] >= MINIMUM_STARTER_PITCHES and row["stamina"] >= MINIMUM_STARTER_STAMINA_NATIVE:
        return "sp"
    elif row[pitches_col] >= MINIMUM_RELIEVER_PITCHES:
        return "rp"
    return ""


NATIVE_EXPORT_PAGES = [
    {
        "filename": "hitters_native.html",
        "title": "Hitters (native calibration)",
        "columns": [
            "name", "org", "minor", "age", "pa", "best", "bestP", "pos", "field",
            "wRC+", "wOBA", "wOBAR", "wOBAL", "wOBAP",
            "DH", "1B", "2B", "3B", "SS", "LF", "CF", "RF", "C", "flag",
        ],
        "filter": lambda df: df["wOBAP"] > 0.200,
        "page_len": 100,
    },
    {
        "filename": "pitchers_native.html",
        "title": "Pitchers (native calibration)",
        "columns": [
            "name", "org", "minor", "age", "ip", "sp_war", "rp_war",
            "pwOBA", "pwOBAR", "pwOBAL", "sp_warP", "rp_warP", "pwOBAP", "flag",
        ],
        "filter": lambda df: df["pwOBAP"] < 1.000,
        "page_len": 100,
    },
]


def main():
    df = load_players()
    df = add_pitching_career_stats(df)
    df = add_hitting_career_stats(df)
    df = add_scouted_ratings(df)  # raw 1-100 ratings — fed directly to the native tables

    # Phase A (done): role/eligibility computed directly on raw ratings with
    # thresholds_native.py's refit cutoffs, no stopgap involved.
    df = count_pitches_native(df)
    df = can_field_native(df)
    df["sprp"] = df.apply(lambda row: _identify_role(row, "pitches"), axis=1)
    df["sprpP"] = df.apply(lambda row: _identify_role(row, "pitchesP"), axis=1)

    df = is_flagged(df)
    df = add_draft_availability(df)

    df = calc_pitching_metrics_native(df)
    df = calc_potential_pitching_metrics_native(df)
    df = calc_hitting_metrics_native(df)
    df = calc_potential_hitting_metrics_native(df)

    # Fielding VALUE: still the old table + stopgap domain (Phase B, deferred, see docstring).
    df_stopgap = apply_native_scale_stopgap(df.copy())
    df_stopgap = calc_fielding_metrics(df_stopgap)
    def_cols = [c for c in df_stopgap.columns if c.endswith("_def")]
    df = df.join(df_stopgap[def_cols])

    df = calc_war(df)
    df = df.sort_values(by="best", ascending=False)
    print(df.head(10))  # Preview in terminal

    for page in NATIVE_EXPORT_PAGES:
        export_advanced_html(
            df=df,
            filename=page["filename"],
            columns=page["columns"],
            title=page["title"],
            row_filter=page["filter"](df),
            page_len=page["page_len"],
        )


if __name__ == "__main__":
    main()
