"""
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

MINIMUM_STARTER_STAMINA_NATIVE = 37

PITCH_MINIMUM_RATING_NATIVE = 23

POSITION_THRESHOLDS_NATIVE = {
    'C': [('Cfram', 33), ('Cabil', 46), ('Carm', 21)],
    'CF': [('OFrange', 68), ('OFarm', 54)],
    'RF': [('OFrange', 55), ('OFarm', 53)],
    'LF': [('OFrange', 47), ('OFarm', 39)],
    'SS': [('IFrange', 71), ('IFerror', 43), ('IFarm', 60)],
    '2B': [('IFrange', 59), ('IFarm', 43), ('turnDP', 44)],
    '3B': [('IFrange', 46), ('IFerror', 41), ('IFarm', 62)],
}
