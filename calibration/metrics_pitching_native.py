"""
Mirrors metrics_pitching.py's formula shape but sources the per-category
regression tables from tables_native.py (genuinely fit to real PSD data —
see calibration/README.md), fit SEPARATELY per side against real
split-specific performance rather than a fit-time blended composite, and
blends pwOBAR/pwOBAL using each pitcher's own real throws-conditional
exposure weight (HANDEDNESS_WEIGHTS_NATIVE_PITCHING — which batter-handedness
mix a pitcher actually faces) instead of config.py's flat, never-refit
HANDEDNESS_WEIGHTS={"R":0.7,"L":0.3} applied identically to every pitcher —
verified this was hiding a real, large effect (a lefty pitcher faces RHB
~79.6% of the time vs. a righty pitcher's ~57.1%, since opposing managers
stack right-handed batters against LHP specifically). BASE_PITCHING_RATES,
PITCHING_WOBA_WEIGHTS, RUNS_PER_WIN, RELIEVER_VS_STARTER_AVERAGE_IP unchanged.

One deliberate deviation from metrics_pitching.py's shape: role
classification (sp/rp, "sprp"/"sprpP") is NOT computed here. main_native.py
computes it directly on raw ratings using thresholds_native.py's refit
MINIMUM_STARTER_STAMINA_NATIVE/PITCH_MINIMUM_RATING_NATIVE (Phase A of
"close the remaining gaps", see calibration/README.md) and passes sprp/sprpP
in as already-existing columns; this file only does the genuinely native
rate/WAR math.
"""

import pandas as pd
from config import (
    BASE_PITCHING_RATES,
    PITCHING_WOBA_WEIGHTS,
    RUNS_PER_WIN,
    RELIEVER_VS_STARTER_AVERAGE_IP,
)
from rating_lookup import interpolate_lookup
from tables_native import (
    PITCHING_COMPONENTS_ADJUST_MAP_NATIVE,
    HANDEDNESS_WEIGHTS_NATIVE_PITCHING,
    RUNS_PER_GAME_PITCHING_COEFF_NATIVE,
    RUNS_PER_GAME_PITCHING_CONST_NATIVE,
)

# Fallback for a pitcher with missing/unrecognized throws data (rare) — the
# league-wide "R" throws profile (the majority cohort).
_DEFAULT_HANDEDNESS_WEIGHTS = HANDEDNESS_WEIGHTS_NATIVE_PITCHING["R"]


def _runs_per_162(pwoba: pd.Series) -> pd.Series:
    return (pwoba * RUNS_PER_GAME_PITCHING_COEFF_NATIVE) - RUNS_PER_GAME_PITCHING_CONST_NATIVE


def calc_pitching_metrics_native(df: pd.DataFrame) -> pd.DataFrame:
    """Expects df["sprp"] to already exist (see module docstring)."""
    def adjust_rates(row, side):
        rates = {
            "hr_vs": BASE_PITCHING_RATES["hr_vs_baserate"],
            "bb_vs": BASE_PITCHING_RATES["bb_vs_baserate"],
            "k_vs": BASE_PITCHING_RATES["k_vs_baserate"],
            "h_nothr_vs": BASE_PITCHING_RATES["h_nothr_vs_baserate"],
        }
        ratings = {
            "Control": row[f"ctrl{side}"],
            "pBABIP": row[f"pbabip{side}"],
            "HRA": row[f"hra{side}"],
            "Stuff": row[f"stuff{side}"],
            "Stamina": row["stamina"],
        }

        for category, value in ratings.items():
            table = PITCHING_COMPONENTS_ADJUST_MAP_NATIVE[category][side]
            adj = interpolate_lookup(value, table)
            rates["hr_vs"] += adj["hr_vs_adj"]
            rates["bb_vs"] += adj["bb_vs_adj"]
            rates["k_vs"] += adj["k_vs_adj"]
            rates["h_nothr_vs"] += adj["h_nothr_vs_adj"]

        return pd.Series({
            f"hr_vs{side}": rates["hr_vs"],
            f"bb_vs{side}": rates["bb_vs"],
            f"k_vs{side}": rates["k_vs"],
            f"h_nothr_vs{side}": rates["h_nothr_vs"],
        })

    rates_r = df.apply(lambda row: adjust_rates(row, "R"), axis=1)
    rates_l = df.apply(lambda row: adjust_rates(row, "L"), axis=1)
    df = pd.concat([df, rates_r, rates_l], axis=1)

    valid_pitcher = df["sprp"].isin(["sp", "rp"])

    df["pwOBAR"] = (
        PITCHING_WOBA_WEIGHTS["hr_vs_wOBA_weight"] * df["hr_vsR"] +
        PITCHING_WOBA_WEIGHTS["bb_vs_wOBA_weight"] * df["bb_vsR"] +
        PITCHING_WOBA_WEIGHTS["h_nothr_vs_wOBA_weight"] * df["h_nothr_vsR"]
    ).where(valid_pitcher)

    df["pwOBAL"] = (
        PITCHING_WOBA_WEIGHTS["hr_vs_wOBA_weight"] * df["hr_vsL"] +
        PITCHING_WOBA_WEIGHTS["bb_vs_wOBA_weight"] * df["bb_vsL"] +
        PITCHING_WOBA_WEIGHTS["h_nothr_vs_wOBA_weight"] * df["h_nothr_vsL"]
    ).where(valid_pitcher)

    weights = df["throws"].apply(lambda t: HANDEDNESS_WEIGHTS_NATIVE_PITCHING.get(t, _DEFAULT_HANDEDNESS_WEIGHTS))
    df["pwOBA"] = (
        df["pwOBAR"] * weights.apply(lambda w: w["R"]) +
        df["pwOBAL"] * weights.apply(lambda w: w["L"])
    ).where(valid_pitcher)

    df["war_pitching"] = -_runs_per_162(df["pwOBA"]) / RUNS_PER_WIN
    df["war_pitching"] = df["war_pitching"].round(1)
    df["is_sp"] = (df["sprp"] == "sp").astype(int)
    df["is_rp"] = (df["sprp"] == "rp").astype(int)
    df["war_pitching"] = df["war_pitching"] * (df["is_sp"] + (df["is_rp"] * RELIEVER_VS_STARTER_AVERAGE_IP))
    df.loc[~df["sprp"].isin(["sp", "rp"]), "war_pitching"] = pd.NA
    df["sp_war"] = df["war_pitching"] * df["is_sp"]
    df["rp_war"] = df["war_pitching"] * df["is_rp"]
    df.loc[df["war_pitching"].isna(), ["sp_war", "rp_war"]] = pd.NA

    return df


def calc_potential_pitching_metrics_native(df: pd.DataFrame) -> pd.DataFrame:
    """
    Expects df["sprpP"] to already exist (see module docstring). Potential
    ratings have no L/R split — the same potential value is run through
    both the R-side and L-side tables and blended by the pitcher's own
    throws-conditional weight, mirroring calc_pitching_metrics_native's R/L
    blend exactly, just with one shared input value instead of two.
    """
    def adjust_rates(row, side):
        rates = {
            "hr_vs": BASE_PITCHING_RATES["hr_vs_baserate"],
            "bb_vs": BASE_PITCHING_RATES["bb_vs_baserate"],
            "k_vs": BASE_PITCHING_RATES["k_vs_baserate"],
            "h_nothr_vs": BASE_PITCHING_RATES["h_nothr_vs_baserate"],
        }
        ratings = {
            "Control": row["ctrlP"],
            "pBABIP": row["pbabipP"],
            "HRA": row["hraP"],
            "Stuff": row["stuffP"],
            "Stamina": row["stamina"],
        }

        for category, value in ratings.items():
            table = PITCHING_COMPONENTS_ADJUST_MAP_NATIVE[category][side]
            adj = interpolate_lookup(value, table)
            rates["hr_vs"] += adj["hr_vs_adj"]
            rates["bb_vs"] += adj["bb_vs_adj"]
            rates["k_vs"] += adj["k_vs_adj"]
            rates["h_nothr_vs"] += adj["h_nothr_vs_adj"]

        return pd.Series(rates)

    rates_r = df.apply(lambda row: adjust_rates(row, "R"), axis=1)
    rates_l = df.apply(lambda row: adjust_rates(row, "L"), axis=1)
    weights = df["throws"].apply(lambda t: HANDEDNESS_WEIGHTS_NATIVE_PITCHING.get(t, _DEFAULT_HANDEDNESS_WEIGHTS))
    w_r = weights.apply(lambda w: w["R"])
    w_l = weights.apply(lambda w: w["L"])
    rates = rates_r.multiply(w_r, axis=0) + rates_l.multiply(w_l, axis=0)
    df = pd.concat([df, rates], axis=1)

    valid_pitcher = df["sprpP"].isin(["sp", "rp"])

    df["pwOBAP"] = (
        PITCHING_WOBA_WEIGHTS["hr_vs_wOBA_weight"] * df["hr_vs"] +
        PITCHING_WOBA_WEIGHTS["bb_vs_wOBA_weight"] * df["bb_vs"] +
        PITCHING_WOBA_WEIGHTS["h_nothr_vs_wOBA_weight"] * df["h_nothr_vs"]
    ).where(valid_pitcher)

    df["war_pitchingP"] = -_runs_per_162(df["pwOBAP"]) / RUNS_PER_WIN
    df["war_pitchingP"] = df["war_pitchingP"].round(1)

    df["is_spP"] = (df["sprpP"] == "sp").astype(int)
    df["is_rpP"] = (df["sprpP"] == "rp").astype(int)
    df["war_pitchingP"] = df["war_pitchingP"] * (df["is_spP"] + (df["is_rpP"] * RELIEVER_VS_STARTER_AVERAGE_IP))
    df.loc[~df["sprpP"].isin(["sp", "rp"]), "war_pitchingP"] = pd.NA
    df["sp_warP"] = df["war_pitchingP"] * df["is_spP"]
    df["rp_warP"] = df["war_pitchingP"] * df["is_rpP"]

    return df
