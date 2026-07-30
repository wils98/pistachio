"""
Mirrors metrics_hitting.py's formula shape (same wOBA-weight combination,
same wOBA->runs->WAR conversion) but sources the per-category regression
tables from tables_native.py (genuinely fit to real PSD data — see
calibration/README.md), fit SEPARATELY per side against real split-specific
performance rather than a fit-time blended composite, and blends wOBAR/wOBAL
using each player's own real bats-conditional exposure weight
(HANDEDNESS_WEIGHTS_NATIVE_HITTING) instead of config.py's flat, never-refit
HANDEDNESS_WEIGHTS={"R":0.7,"L":0.3} applied identically to every player —
verified this was hiding real platooning (a lefty batter faces RHP ~83.7%
of the time vs. a righty batter's ~72.7%). BASE_HITTING_RATES,
BATTING_WOBA_WEIGHTS, RUNS_PER_WIN, DH_PENALTY, LEAGUE_WOBA, WOBA_SCALE,
LEAGUE_RUNS_PER_PA are unchanged.
"""

import pandas as pd
from config import (
    BASE_HITTING_RATES,
    BATTING_WOBA_WEIGHTS,
    RUNS_PER_WIN,
    DH_PENALTY,
    LEAGUE_WOBA,
    WOBA_SCALE,
    LEAGUE_RUNS_PER_PA,
)
from rating_lookup import interpolate_lookup
from tables_native import (
    BATTING_COMPONENTS_ADJUST_MAP_NATIVE,
    HANDEDNESS_WEIGHTS_NATIVE_HITTING,
    RUNS_PER_GAME_HITTING_COEFF_NATIVE,
    RUNS_PER_GAME_HITTING_CONST_NATIVE,
)

# Fallback for a player with missing/unrecognized bats data (rare) — the
# league-wide "R" bats profile, the largest and closest-to-average of the
# three real cohorts (see HANDEDNESS_WEIGHTS_NATIVE_HITTING).
_DEFAULT_HANDEDNESS_WEIGHTS = HANDEDNESS_WEIGHTS_NATIVE_HITTING["R"]


def _runs_per_162(woba: pd.Series) -> pd.Series:
    return (woba * RUNS_PER_GAME_HITTING_COEFF_NATIVE) - RUNS_PER_GAME_HITTING_CONST_NATIVE


def calc_hitting_metrics_native(df: pd.DataFrame) -> pd.DataFrame:
    """Expected wOBA rates for hitters vs R/L splits and overall, native tables."""
    def adjust_rates(row, side):
        rates = {
            "hr_pct": BASE_HITTING_RATES["hr_pct_baserate"],
            "k_pct": BASE_HITTING_RATES["k_pct_baserate"],
            "bb_pct": BASE_HITTING_RATES["bb_pct_baserate"],
            "1b_pct": BASE_HITTING_RATES["1b_pct_baserate"],
            "2b_pct": BASE_HITTING_RATES["2b_pct_baserate"],
            "3b_pct": BASE_HITTING_RATES["3b_pct_baserate"],
        }

        ratings = {
            "pow": row.get(f"pow{side}", 50),
            "eye": row.get(f"eye{side}", 50),
            "avk": row.get(f"avk{side}", 50),
            "gap": row.get(f"gap{side}", 50),
            "babip": row.get(f"babip{side}", 50),
            "speed": row.get("speed", 50),
        }

        for category, rating in ratings.items():
            table = BATTING_COMPONENTS_ADJUST_MAP_NATIVE[category][side]
            adjustment = interpolate_lookup(rating, table)
            for key, value in adjustment.items():
                base_key = key.replace("_adj", "")
                rates[base_key] = rates.get(base_key, 0) + value

        return pd.Series({
            f"hr_pct{side}": rates["hr_pct"],
            f"k_pct{side}":  rates["k_pct"],
            f"bb_pct{side}": rates["bb_pct"],
            f"1b_pct{side}": rates["1b_pct"],
            f"2b_pct{side}": rates["2b_pct"],
            f"3b_pct{side}": rates["3b_pct"],
        })

    rates_r = df.apply(lambda row: adjust_rates(row, "R"), axis=1)
    rates_l = df.apply(lambda row: adjust_rates(row, "L"), axis=1)
    df = pd.concat([df, rates_r, rates_l], axis=1)

    df["wOBAR"] = (
        BATTING_WOBA_WEIGHTS["hr_pct_wOBA_weight"] * df["hr_pctR"] +
        BATTING_WOBA_WEIGHTS["bb_pct_wOBA_weight"] * df["bb_pctR"] +
        BATTING_WOBA_WEIGHTS["1b_pct_wOBA_weight"] * df["1b_pctR"] +
        BATTING_WOBA_WEIGHTS["2b_pct_wOBA_weight"] * df["2b_pctR"] +
        BATTING_WOBA_WEIGHTS["3b_pct_wOBA_weight"] * df["3b_pctR"]
    )

    df["wOBAL"] = (
        BATTING_WOBA_WEIGHTS["hr_pct_wOBA_weight"] * df["hr_pctL"] +
        BATTING_WOBA_WEIGHTS["bb_pct_wOBA_weight"] * df["bb_pctL"] +
        BATTING_WOBA_WEIGHTS["1b_pct_wOBA_weight"] * df["1b_pctL"] +
        BATTING_WOBA_WEIGHTS["2b_pct_wOBA_weight"] * df["2b_pctL"] +
        BATTING_WOBA_WEIGHTS["3b_pct_wOBA_weight"] * df["3b_pctL"]
    )

    weights = df["bats"].apply(lambda b: HANDEDNESS_WEIGHTS_NATIVE_HITTING.get(b, _DEFAULT_HANDEDNESS_WEIGHTS))
    df["wOBA"] = (
        df["wOBAR"] * weights.apply(lambda w: w["R"]) +
        df["wOBAL"] * weights.apply(lambda w: w["L"])
    )

    df["war_hitting"] = (_runs_per_162(df["wOBA"]) / RUNS_PER_WIN).round(1)
    df["DH_hitting"] = (_runs_per_162(df["wOBA"] * (1 - DH_PENALTY)) / RUNS_PER_WIN).round(1)

    df["wRC+"] = ((((df["wOBA"] - LEAGUE_WOBA) / WOBA_SCALE) + LEAGUE_RUNS_PER_PA) / LEAGUE_RUNS_PER_PA * 100).round(0)

    return df


def calc_potential_hitting_metrics_native(df: pd.DataFrame) -> pd.DataFrame:
    """
    Expected wOBA/WAR for hitters from potential ratings, native tables.
    Potential ratings have no L/R split (a single ceiling value per
    category) — but the fitted table itself is now side-dependent (see
    module docstring), so the same potential value is run through both the
    R-side and L-side tables and blended by the player's own
    bats-conditional weight, mirroring calc_hitting_metrics_native's R/L
    blend exactly, just with one shared input value instead of two.
    """
    def adjust_rates(row, side):
        rates = {
            "hr_pct": BASE_HITTING_RATES["hr_pct_baserate"],
            "k_pct": BASE_HITTING_RATES["k_pct_baserate"],
            "bb_pct": BASE_HITTING_RATES["bb_pct_baserate"],
            "1b_pct": BASE_HITTING_RATES["1b_pct_baserate"],
            "2b_pct": BASE_HITTING_RATES["2b_pct_baserate"],
            "3b_pct": BASE_HITTING_RATES["3b_pct_baserate"],
        }

        ratings = {
            "pow": row.get("powP", 50),
            "eye": row.get("eyeP", 50),
            "avk": row.get("avkP", 50),
            "gap": row.get("gapP", 50),
            "babip": row.get("babipP", 50),
            "speed": row.get("speed", 50),  # speed is not a potential rating
        }

        for category, rating in ratings.items():
            table = BATTING_COMPONENTS_ADJUST_MAP_NATIVE[category][side]
            adjustment = interpolate_lookup(rating, table)
            for key, value in adjustment.items():
                base_key = key.replace("_adj", "")
                rates[base_key] = rates.get(base_key, 0) + value

        return pd.Series(rates)

    rates_r = df.apply(lambda row: adjust_rates(row, "R"), axis=1)
    rates_l = df.apply(lambda row: adjust_rates(row, "L"), axis=1)
    weights = df["bats"].apply(lambda b: HANDEDNESS_WEIGHTS_NATIVE_HITTING.get(b, _DEFAULT_HANDEDNESS_WEIGHTS))
    w_r = weights.apply(lambda w: w["R"])
    w_l = weights.apply(lambda w: w["L"])
    rates = rates_r.multiply(w_r, axis=0) + rates_l.multiply(w_l, axis=0)
    df = pd.concat([df, rates.add_suffix("P")], axis=1)

    df["wOBAP"] = (
        BATTING_WOBA_WEIGHTS["hr_pct_wOBA_weight"] * df["hr_pctP"] +
        BATTING_WOBA_WEIGHTS["bb_pct_wOBA_weight"] * df["bb_pctP"] +
        BATTING_WOBA_WEIGHTS["1b_pct_wOBA_weight"] * df["1b_pctP"] +
        BATTING_WOBA_WEIGHTS["2b_pct_wOBA_weight"] * df["2b_pctP"] +
        BATTING_WOBA_WEIGHTS["3b_pct_wOBA_weight"] * df["3b_pctP"]
    )

    df["war_hittingP"] = (_runs_per_162(df["wOBAP"]) / RUNS_PER_WIN).round(1)
    df["DH_hittingP"] = (_runs_per_162(df["wOBAP"] * (1 - DH_PENALTY)) / RUNS_PER_WIN).round(1)

    df["wRC+P"] = ((((df["wOBAP"] - LEAGUE_WOBA) / WOBA_SCALE) + LEAGUE_RUNS_PER_PA) / LEAGUE_RUNS_PER_PA * 100).round(0)

    return df
