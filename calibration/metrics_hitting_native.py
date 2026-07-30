"""
Mirrors metrics_hitting.py's formula shape exactly (same wOBA-weight
combination, same wOBA->runs->WAR conversion) but sources the per-category
regression tables and RUNS_PER_GAME_HITTING_* constants from tables_native.py
(genuinely fit to real PSD data — see calibration/README.md) instead of
config.py's still-upstream values. BASE_HITTING_RATES, BATTING_WOBA_WEIGHTS,
HANDEDNESS_WEIGHTS, RUNS_PER_WIN, DH_PENALTY, LEAGUE_WOBA, WOBA_SCALE,
LEAGUE_RUNS_PER_PA are unchanged — already real-data-calibrated (Workstream B
from Pass 3) or league-context constants not specific to this refit.
"""

import pandas as pd
from config import (
    BASE_HITTING_RATES,
    HANDEDNESS_WEIGHTS,
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
    RUNS_PER_GAME_HITTING_COEFF_NATIVE,
    RUNS_PER_GAME_HITTING_CONST_NATIVE,
)


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
            table = BATTING_COMPONENTS_ADJUST_MAP_NATIVE[category]
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

    df["wOBA"] = (
        df["wOBAR"] * HANDEDNESS_WEIGHTS["R"] +
        df["wOBAL"] * HANDEDNESS_WEIGHTS["L"]
    )

    df["war_hitting"] = (_runs_per_162(df["wOBA"]) / RUNS_PER_WIN).round(1)
    df["DH_hitting"] = (_runs_per_162(df["wOBA"] * (1 - DH_PENALTY)) / RUNS_PER_WIN).round(1)

    df["wRC+"] = ((((df["wOBA"] - LEAGUE_WOBA) / WOBA_SCALE) + LEAGUE_RUNS_PER_PA) / LEAGUE_RUNS_PER_PA * 100).round(0)

    return df


def calc_potential_hitting_metrics_native(df: pd.DataFrame) -> pd.DataFrame:
    """Expected wOBA/WAR for hitters from potential ratings (no handedness), native tables."""
    def adjust_rates(row):
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
            table = BATTING_COMPONENTS_ADJUST_MAP_NATIVE[category]
            adjustment = interpolate_lookup(rating, table)
            for key, value in adjustment.items():
                base_key = key.replace("_adj", "")
                rates[base_key] = rates.get(base_key, 0) + value

        return pd.Series(rates)

    rates = df.apply(adjust_rates, axis=1)
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
