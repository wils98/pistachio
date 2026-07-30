"""
Standalone entry point for the native-PSD-calibrated pipeline — produces
hitters_native.html/pitchers_native.html for side-by-side comparison against
the live stopgap-based pistachio output. Does not import from, or get
imported by, main.py — running this touches nothing in the live pipeline.

Fielding is explicitly deferred (see calibration/README.md — no verified
fielding-outcome dataset yet), so this still runs the existing, unmodified
metrics_fielding.py against the old stopgap-scaled domain for *_def columns
only; hitting/pitching genuinely use the natively-fit tables on raw ratings.

Role classification (sp/rp) and pitch/fielding-eligibility counts still
depend on PITCH_MINIMUM_RATING/POSITION_THRESHOLDS/MINIMUM_STARTER_STAMINA —
bare thresholds calibrated to the old stopgap-scaled domain, also deferred —
so those are computed on a stopgap-scaled copy (matching production exactly)
and merged onto the native dataframe; see metrics_pitching_native.py's
module docstring for why this split is necessary (mixing a stopgap-scaled
threshold with a raw-scale table lookup in the same function would silently
misapply one or the other).

Usage:
    PISTACHIO_DB_PATH=/path/to/ootp.db PISTACHIO_OUTPUT_DIR=/path/to/outputs \\
        python calibration/main_native.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import MINIMUM_STARTER_PITCHES, MINIMUM_RELIEVER_PITCHES, MINIMUM_STARTER_STAMINA
from reader import (
    load_players,
    add_pitching_career_stats,
    add_hitting_career_stats,
    add_scouted_ratings,
    apply_native_scale_stopgap,
    count_pitches,
    can_field,
    is_flagged,
    add_draft_availability,
)
from metrics_fielding import calc_fielding_metrics
from metrics_war import calc_war
from metrics_hitting_native import calc_hitting_metrics_native, calc_potential_hitting_metrics_native
from metrics_pitching_native import calc_pitching_metrics_native, calc_potential_pitching_metrics_native
from exporter import export_advanced_html


def _identify_role(row, pitches_col):
    if row[pitches_col] >= MINIMUM_STARTER_PITCHES and row["stamina"] >= MINIMUM_STARTER_STAMINA:
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

    # Deferred-scope thresholds (see module docstring): computed on a
    # stopgap-scaled copy, matching production, then merged back.
    df_stopgap = apply_native_scale_stopgap(df.copy())
    df_stopgap = count_pitches(df_stopgap)
    df_stopgap = can_field(df_stopgap)
    df_stopgap["sprp"] = df_stopgap.apply(lambda row: _identify_role(row, "pitches"), axis=1)
    df_stopgap["sprpP"] = df_stopgap.apply(lambda row: _identify_role(row, "pitchesP"), axis=1)

    for col in ["pitches", "pitchesP", "field", "sprp", "sprpP"]:
        df[col] = df_stopgap[col]

    df = is_flagged(df)
    df = add_draft_availability(df)

    df = calc_pitching_metrics_native(df)
    df = calc_potential_pitching_metrics_native(df)
    df = calc_hitting_metrics_native(df)
    df = calc_potential_hitting_metrics_native(df)

    # Fielding: still the old table + stopgap domain (deferred, see docstring).
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
