"""
Workstream B fitting: joint multivariate OLS per outcome column, fit
SEPARATELY per side (real vsR performance regressed on vsR-side ratings;
real vsL performance regressed on vsL-side ratings) — not a single table fit
against a fit-time blended composite. See calibration/README.md and
extract_pairs.py's module docstring for why: real per-split performance
data exists (player_batting_stats_history.split_id) and pairing it with the
matching side's rating captures whether a rating's relationship to outcomes
genuinely differs by handedness, which a shared table applied to both sides
cannot.

Linear, not polynomial/spline — chosen specifically for how
rating_lookup.interpolate_lookup() extrapolates past table edges (using the
outermost two keys' slope): a table built by sampling a straight line has
constant slope everywhere, so extrapolation exactly reproduces the fit.

For each category, the fitted table is:
    table[cat][side][v] = coef_cat_side * (v - mean_cat_side)
zero at that side's own sample mean (not "50" — PSD's ratings aren't
centered there), reconciling with BASE_HITTING_RATES/BASE_PITCHING_RATES via
the OLS property that the fitted value at the training mean equals the
training outcome mean.

Also computes real bats/throws-conditional exposure weights
(extract_pairs.compute_exposure_weights) and writes them into
tables_native.py as HANDEDNESS_WEIGHTS_NATIVE_HITTING/_PITCHING — replacing
config.py's flat, never-refit HANDEDNESS_WEIGHTS={"R":0.7,"L":0.3}.

Prints, per side per category per outcome: coefficient/std-err/p-value, a
VIF check, a reconciliation check, leave-one-season-out CV, and an
extrapolation sanity check — all meant to be reviewed before
tables_native.py is treated as final. fit_tables.py generates that file
(not hand-typed) but nothing downstream imports fit_tables.py itself or
recomputes at runtime.

Usage (needs calibration/data/{hitting,pitching}_pairs.csv already built by
extract_pairs.py):
    python calibration/fit_tables.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy.stats import pearsonr, spearmanr
from statsmodels.stats.outliers_influence import variance_inflation_factor

from config import BASE_HITTING_RATES, BASE_PITCHING_RATES, DB_PATH
from extract_pairs import compute_exposure_weights, connect as db_connect

DATA_DIR = Path(__file__).resolve().parent / "data"
BUCKET_STEP = 5
SIDES = ["R", "L"]

HITTING_CATEGORIES = ["babip_side", "avk_side", "gap_side", "pow_side", "eye_side", "speed"]
HITTING_OUTCOMES = ["hr_pct", "k_pct", "bb_pct", "1b_pct", "2b_pct", "3b_pct"]
HITTING_BASE_RATE_KEYS = {
    "hr_pct": "hr_pct_baserate", "k_pct": "k_pct_baserate", "bb_pct": "bb_pct_baserate",
    "1b_pct": "1b_pct_baserate", "2b_pct": "2b_pct_baserate", "3b_pct": "3b_pct_baserate",
}

PITCHING_CATEGORIES = ["Control_side", "pBABIP_side", "HRA_side", "Stuff_side", "stamina"]
PITCHING_OUTCOMES = ["hr_vs", "bb_vs", "k_vs", "h_nothr_vs"]
PITCHING_BASE_RATE_KEYS = {
    "hr_vs": "hr_vs_baserate", "bb_vs": "bb_vs_baserate",
    "k_vs": "k_vs_baserate", "h_nothr_vs": "h_nothr_vs_baserate",
}

CATEGORY_KEY_OVERRIDES = {"stamina": "Stamina"}


def fit_outcome(df: pd.DataFrame, categories: list[str], outcome: str):
    x = sm.add_constant(df[categories])
    y = df[outcome]
    return sm.OLS(y, x).fit()


def print_vif(df: pd.DataFrame, categories: list[str]):
    x = sm.add_constant(df[categories])
    print("VIF (multicollinearity — values well above ~5-10 flag a concern):")
    for i, col in enumerate(categories):
        vif = variance_inflation_factor(x.values, i + 1)
        print(f"  {col}: {vif:.2f}")


def reconciliation_check(df: pd.DataFrame, categories: list[str], outcome: str, model, base_rate: float):
    predicted_at_means = model.params["const"] + sum(model.params[cat] * df[cat].mean() for cat in categories)
    actual_mean = df[outcome].mean()
    print(f"  reconciliation: predicted-at-means={predicted_at_means:.4f}, "
          f"actual sample mean={actual_mean:.4f}, config.py base rate={base_rate:.4f}")


def build_table(model, categories: list[str], df: pd.DataFrame) -> dict:
    table = {}
    for cat in categories:
        coef = model.params[cat]
        mean = df[cat].mean()
        lo = int(np.floor(df[cat].quantile(0.01) / BUCKET_STEP) * BUCKET_STEP)
        hi = int(np.ceil(df[cat].quantile(0.99) / BUCKET_STEP) * BUCKET_STEP)
        buckets = {}
        for v in range(lo, hi + 1, BUCKET_STEP):
            buckets[str(v)] = round(coef * (v - mean), 6)
        table[cat] = buckets
    return table


def leave_one_season_out(df: pd.DataFrame, categories: list[str], outcomes: list[str], label: str):
    print(f"=== {label}: leave-one-season-out CV ===")
    for held_out in sorted(df["season_year"].unique()):
        train = df[df["season_year"] != held_out]
        test = df[df["season_year"] == held_out]
        for outcome in outcomes:
            model = fit_outcome(train, categories, outcome)
            x_test = sm.add_constant(test[categories], has_constant="add")
            pred = model.predict(x_test)
            actual = test[outcome]
            if actual.std() == 0 or pred.std() == 0:
                print(f"  held out {held_out}, {outcome}: no variance, skipping correlation")
                continue
            pear = pearsonr(pred, actual)[0]
            spear = spearmanr(pred, actual)[0]
            rmse = np.sqrt(((pred - actual) ** 2).mean())
            print(f"  held out {held_out}, {outcome}: Pearson={pear:.3f} Spearman={spear:.3f} RMSE={rmse:.4f} (n={len(test)})")
    print()


def extrapolation_sanity(df: pd.DataFrame, categories: list[str], outcomes: list[str], models: dict, label: str):
    print(f"=== {label}: extrapolation sanity (synthetic extreme profiles) ===")
    p01 = {cat: df[cat].quantile(0.01) for cat in categories}
    p99 = {cat: df[cat].quantile(0.99) for cat in categories}
    beyond_lo = {cat: df[cat].min() - 20 for cat in categories}
    beyond_hi = {cat: df[cat].max() + 20 for cat in categories}

    for profile_name, profile in [("1st pct all", p01), ("99th pct all", p99),
                                    ("20 below observed min", beyond_lo),
                                    ("20 above observed max", beyond_hi)]:
        row = {}
        for outcome in outcomes:
            model = models[outcome]
            pred = model.params["const"] + sum(model.params[cat] * profile[cat] for cat in categories)
            row[outcome] = round(pred, 4)
        print(f"  {profile_name}: {row}")
    print()


def fit_side(df_side: pd.DataFrame, categories: list[str], outcomes: list[str],
             base_rate_keys: dict, base_rates: dict, side: str, label: str):
    print(f"\n{'-' * 60}\n{label} — side={side} — {len(df_side)} rows\n{'-' * 60}")
    print_vif(df_side, categories)
    print()

    models, tables = {}, {}
    for outcome in outcomes:
        model = fit_outcome(df_side, categories, outcome)
        models[outcome] = model
        print(f"--- {outcome} ---")
        for cat in categories:
            print(f"  {cat}: coef={model.params[cat]:.6f}  p={model.pvalues[cat]:.4g}")
        print(f"  R-squared: {model.rsquared:.4f}")
        reconciliation_check(df_side, categories, outcome, model, base_rates[base_rate_keys[outcome]])
        tables[outcome] = build_table(model, categories, df_side)
        print()

    leave_one_season_out(df_side, categories, outcomes, f"{label} ({side})")
    extrapolation_sanity(df_side, categories, outcomes, models, f"{label} ({side})")

    category_major = {cat: {} for cat in categories}
    for outcome in outcomes:
        for cat in categories:
            for bucket, val in tables[outcome][cat].items():
                category_major[cat].setdefault(bucket, {})[f"{outcome}_adj"] = val
    return category_major


def run_side(pairs_csv: str, categories: list[str], outcomes: list[str],
             base_rate_keys: dict, base_rates: dict, label: str) -> dict:
    df = pd.read_csv(DATA_DIR / pairs_csv)
    print(f"\n{'=' * 70}\n{label} — {len(df)} rows total (both sides)\n{'=' * 70}")

    per_side_tables = {}
    for side in SIDES:
        per_side_tables[side] = fit_side(
            df[df["side"] == side], categories, outcomes, base_rate_keys, base_rates, side, label,
        )

    # Reshape side-major into category-major (config.py's shape, one level
    # deeper: category -> side -> bucket -> {outcome_adj}).
    all_cats = categories
    category_major = {cat: {} for cat in all_cats}
    for side in SIDES:
        for cat in all_cats:
            category_major[cat][side] = per_side_tables[side][cat]
    return category_major


def main():
    hitting_table = run_side(
        "hitting_pairs.csv", HITTING_CATEGORIES, HITTING_OUTCOMES,
        HITTING_BASE_RATE_KEYS, BASE_HITTING_RATES, "HITTING",
    )
    pitching_table = run_side(
        "pitching_pairs.csv", PITCHING_CATEGORIES, PITCHING_OUTCOMES,
        PITCHING_BASE_RATE_KEYS, BASE_PITCHING_RATES, "PITCHING",
    )

    conn = db_connect()
    weights = compute_exposure_weights(conn)
    conn.close()
    print("\nReal exposure weights (bats/throws -> fraction of PA/BF vs R/L):")
    print(weights)

    write_tables_native(hitting_table, pitching_table, weights)


def _format_table_literal(name: str, category_major: dict, strip_suffix: str = "_side") -> str:
    lines = [f"{name} = {{"]
    for cat, sides in category_major.items():
        key = cat[: -len(strip_suffix)] if strip_suffix and cat.endswith(strip_suffix) else cat
        key = CATEGORY_KEY_OVERRIDES.get(key, key)
        lines.append(f'    "{key}": {{')
        for side in SIDES:
            lines.append(f'        "{side}": {{')
            for bucket, adj in sorted(sides[side].items(), key=lambda kv: int(kv[0])):
                adj_str = ", ".join(f'"{k}": {v}' for k, v in adj.items())
                lines.append(f'            "{bucket}": {{{adj_str}}},')
            lines.append("        },")
        lines.append("    },")
    lines.append("}")
    return "\n".join(lines)


def write_tables_native(hitting_table: dict, pitching_table: dict, weights: dict):
    """
    Writes calibration/tables_native.py — category -> side -> bucket ->
    {outcome_adj: value}, one level deeper than config.py's own tables (which
    have no side dimension at all) since rating_lookup.interpolate_lookup()
    is called per-side already in metrics_*_native.py's existing per-side
    loop — indexing table[cat][side] before calling it needs no changes
    there. Generated (not hand-copied) to avoid transcription errors across
    ~1,100 coefficients, but still meant to be read and reviewed.
    """
    out_path = Path(__file__).resolve().parent / "tables_native.py"
    header = '''"""
Reviewed literal regression tables, fit against real PSD ratings-to-stats
data (calibration/fit_tables.py) — see calibration/README.md for the full
methodology (joint multivariate OLS per outcome, fit SEPARATELY per side
against real split-specific performance — not a fit-time blended composite;
linear not polynomial/spline; category-mean-centered instead of a shared
"50"). Generated by fit_tables.py, not hand-typed — reviewed before being
treated as final, and not recomputed at import time or anywhere in the live
pipeline.

Shape: category -> side ("R"/"L") -> bucket-string -> {outcome_adj: value}
— one level deeper than config.py's tables (which have no side dimension).
rating_lookup.interpolate_lookup() is called per-side already in
metrics_*_native.py's existing R/L loop; index table[category][side] before
calling it, no changes needed in rating_lookup.py itself.

HANDEDNESS_WEIGHTS_NATIVE_HITTING/_PITCHING: real fraction of PA/BF against
R- vs L-handed opponents, conditional on the player's OWN bats/throws —
replaces config.py's flat HANDEDNESS_WEIGHTS={"R":0.7,"L":0.3} (applied
identically to every player regardless of their own handedness; verified
this was hiding real platooning — e.g. a lefty batter faces RHP ~83.7% of
the time vs. a righty batter's ~72.7%). Keyed by bats ("L"/"R"/"S") for
hitting, throws ("L"/"R") for pitching; each value is {"R": frac, "L": frac}.
"""

'''
    weights_block = (
        f"HANDEDNESS_WEIGHTS_NATIVE_HITTING = {weights['hitting']!r}\n\n"
        f"HANDEDNESS_WEIGHTS_NATIVE_PITCHING = {weights['pitching']!r}\n\n"
    )
    runs_block = '''# Workstream A (calibration/fit_runs_per_game.py) — real team-season wOBA/pwOBA
# vs runs-per-162 regression, 32 teams x 3 complete seasons (2101-2103) each
# side, R-squared 0.933 (hitting) / 0.926 (pitching), with the fitted team
# slope rescaled to the per-650-PA player-season scale the consuming formula
# expects, and CONST anchored so zero runs lands at the league-average
# wOBA/pwOBA — the same structural zero point upstream's own constants encode
# (their CONST/COEFF ratio decodes to ~their league average). See
# fit_runs_per_game.py's docstring for the scale bug this fixes (the raw
# team-scale slope is ~10x steeper; feeding one player's wOBA through it gave
# a real prospect 153 WAR). Validation: the rescaled PSD hitting slope
# (546.6) independently lands within ~1.5% of upstream's own 554.8, fit on a
# different league's data — the slope is near-universal wOBA physics; the
# league-context CONST is what genuinely needed recalibrating. Not recomputed
# by this script — transcribed once from fit_runs_per_game.py's own output.
RUNS_PER_GAME_HITTING_COEFF_NATIVE = 546.6257409
RUNS_PER_GAME_HITTING_CONST_NATIVE = 177.5116187
RUNS_PER_GAME_PITCHING_COEFF_NATIVE = 546.6000114
RUNS_PER_GAME_PITCHING_CONST_NATIVE = 178.2959140

'''
    body = (
        _format_table_literal("BATTING_COMPONENTS_ADJUST_MAP_NATIVE", hitting_table)
        + "\n\n\n"
        + _format_table_literal("PITCHING_COMPONENTS_ADJUST_MAP_NATIVE", pitching_table)
        + "\n"
    )
    out_path.write_text(header + weights_block + runs_block + body)
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
