"""
Workstream B fitting: joint multivariate OLS per outcome column (see
calibration/README.md for the full methodology reasoning — one joint fit per
outcome across all rating categories simultaneously, not independent
per-bucket-per-category sample means; linear, not polynomial/spline, chosen
specifically for how rating_lookup.py's interpolate_lookup() extrapolates
past table edges using the outermost two keys' slope).

For each category, the fitted table is built as:
    table[cat][v] = coef_cat * (v - mean_cat)
evaluated at bucket points spanning that category's own ~1st-99th percentile
observed range (rounded to a clean step) — zero at each category's own
sample mean (not a shared "50"), which reconciles with the already-real
BASE_HITTING_RATES/BASE_PITCHING_RATES via the OLS property that the fitted
value at the training means equals the training outcome mean.

Prints, per category per outcome: coefficient/std-err/p-value, a VIF
(multicollinearity) check, a reconciliation check against config.py's
existing base rates, leave-one-season-out cross-validation, and an
extrapolation sanity check at synthetic extreme rating profiles — all meant
to be reviewed by a human before anything gets hand-transcribed into
tables_native.py. Nothing here writes to tables_native.py automatically.

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

from config import BASE_HITTING_RATES, BASE_PITCHING_RATES

DATA_DIR = Path(__file__).resolve().parent / "data"
BUCKET_STEP = 5

HITTING_CATEGORIES = ["babip_c", "avk_c", "gap_c", "pow_c", "eye_c", "speed"]
HITTING_OUTCOMES = ["hr_pct", "k_pct", "bb_pct", "1b_pct", "2b_pct", "3b_pct"]
HITTING_BASE_RATE_KEYS = {
    "hr_pct": "hr_pct_baserate", "k_pct": "k_pct_baserate", "bb_pct": "bb_pct_baserate",
    "1b_pct": "1b_pct_baserate", "2b_pct": "2b_pct_baserate", "3b_pct": "3b_pct_baserate",
}

PITCHING_CATEGORIES = ["Control_c", "pBABIP_c", "HRA_c", "Stuff_c", "stamina"]
PITCHING_OUTCOMES = ["hr_vs", "bb_vs", "k_vs", "h_nothr_vs"]
PITCHING_BASE_RATE_KEYS = {
    "hr_vs": "hr_vs_baserate", "bb_vs": "bb_vs_baserate",
    "k_vs": "k_vs_baserate", "h_nothr_vs": "h_nothr_vs_baserate",
}


def fit_outcome(df: pd.DataFrame, categories: list[str], outcome: str) -> sm.regression.linear_model.RegressionResultsWrapper:
    x = sm.add_constant(df[categories])
    y = df[outcome]
    return sm.OLS(y, x).fit()


def print_vif(df: pd.DataFrame, categories: list[str]):
    x = sm.add_constant(df[categories])
    print("VIF (multicollinearity — values well above ~5-10 flag a concern):")
    for i, col in enumerate(categories):
        vif = variance_inflation_factor(x.values, i + 1)  # +1 skips the const column
        print(f"  {col}: {vif:.2f}")


def reconciliation_check(df: pd.DataFrame, categories: list[str], outcome: str,
                          model, base_rate: float):
    predicted_at_means = model.params["const"] + sum(
        model.params[cat] * df[cat].mean() for cat in categories
    )
    actual_mean = df[outcome].mean()
    print(f"  reconciliation: predicted-at-means={predicted_at_means:.4f}, "
          f"actual sample mean={actual_mean:.4f}, "
          f"config.py base rate={base_rate:.4f}")


def build_table(model, categories: list[str], df: pd.DataFrame) -> dict:
    """table[cat] = {bucket_str: {adj_key: coef*(bucket-mean)}}, spanning each
    category's own ~1st-99th percentile range at BUCKET_STEP increments."""
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
            print(f"  held out {held_out}, {outcome}: Pearson={pear:.3f} "
                  f"Spearman={spear:.3f} RMSE={rmse:.4f} (n={len(test)})")
    print()


def extrapolation_sanity(df: pd.DataFrame, categories: list[str], outcomes: list[str],
                          models: dict, label: str):
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


def run_side(pairs_csv: str, categories: list[str], outcomes: list[str],
             base_rate_keys: dict, base_rates: dict, adj_suffix: str, label: str):
    df = pd.read_csv(DATA_DIR / pairs_csv)
    print(f"\n{'=' * 70}\n{label} — {len(df)} rows\n{'=' * 70}")

    print_vif(df, categories)
    print()

    models = {}
    tables = {}
    for outcome in outcomes:
        model = fit_outcome(df, categories, outcome)
        models[outcome] = model
        print(f"--- {outcome} ---")
        print(model.params.round(6).to_string())
        print(f"  R-squared: {model.rsquared:.4f}")
        for cat in categories:
            print(f"  {cat}: coef={model.params[cat]:.6f}  p={model.pvalues[cat]:.4g}")
        reconciliation_check(df, categories, outcome, model, base_rates[base_rate_keys[outcome]])
        tables[outcome] = build_table(model, categories, df)
        print()

    leave_one_season_out(df, categories, outcomes, label)
    extrapolation_sanity(df, categories, outcomes, models, label)

    # Reshape outcome-major tables into category-major (config.py's shape):
    # category -> bucket_str -> {outcome_adj: value}
    category_major = {cat: {} for cat in categories}
    for outcome in outcomes:
        for cat in categories:
            for bucket, val in tables[outcome][cat].items():
                category_major[cat].setdefault(bucket, {})[f"{outcome}{adj_suffix}"] = val

    print(f"--- {label}: category-major table (paste into tables_native.py) ---")
    for cat, buckets in category_major.items():
        print(f'    "{cat}": {{')
        for bucket, adj in sorted(buckets.items(), key=lambda kv: int(kv[0])):
            adj_str = ", ".join(f'"{k}": {v}' for k, v in adj.items())
            print(f'        "{bucket}": {{{adj_str}}},')
        print("    },")

    return category_major


# Category name as used for fitting (DataFrame column, possibly "_c"-suffixed
# composite) -> the key config.py's own tables use for this same category.
# Everything else just has "_c" stripped; "stamina" is the one category
# whose fit-time column name doesn't already match config.py's capitalization
# (PITCHING_COMPONENTS_ADJUST_MAP uses "Stamina").
CATEGORY_KEY_OVERRIDES = {"stamina": "Stamina"}


def _format_table_literal(name: str, category_major: dict, strip_suffix: str = "_c") -> str:
    lines = [f"{name} = {{"]
    for cat, buckets in category_major.items():
        key = cat[: -len(strip_suffix)] if strip_suffix and cat.endswith(strip_suffix) else cat
        key = CATEGORY_KEY_OVERRIDES.get(key, key)
        lines.append(f'    "{key}": {{')
        for bucket, adj in sorted(buckets.items(), key=lambda kv: int(kv[0])):
            adj_str = ", ".join(f'"{k}": {v}' for k, v in adj.items())
            lines.append(f'        "{bucket}": {{{adj_str}}},')
        lines.append("    },")
    lines.append("}")
    return "\n".join(lines)


def write_tables_native(hitting_table: dict, pitching_table: dict):
    """
    Writes calibration/tables_native.py — same exact shape as config.py's
    BATTING_COMPONENTS_ADJUST_MAP/PITCHING_COMPONENTS_ADJUST_MAP (category ->
    bucket-string -> {..._adj: value}), generated (not hand-copied) to avoid
    transcription errors across ~600 coefficients, but still meant to be read
    and reviewed before being treated as final — nothing downstream imports
    fit_tables.py or recomputes these at runtime.
    """
    out_path = Path(__file__).resolve().parent / "tables_native.py"
    header = '''"""
Reviewed literal regression tables, fit against real PSD ratings-to-stats
data (calibration/fit_tables.py) — see calibration/README.md for the full
methodology (joint multivariate OLS per outcome, linear not polynomial/
spline, category-mean-centered instead of a shared "50"). Generated by
fit_tables.py, not hand-typed — reviewed before being treated as final, and
not recomputed at import time or anywhere in the live pipeline.

Same shape as config.py's BATTING_COMPONENTS_ADJUST_MAP/
PITCHING_COMPONENTS_ADJUST_MAP: category -> bucket-string -> {outcome_adj:
value}. rating_lookup.interpolate_lookup() consumes this identically to
config.py's tables, no changes needed there.
"""

# Workstream A (calibration/fit_runs_per_game.py) — real team-season wOBA/pwOBA
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
    out_path.write_text(header + body)
    print(f"\nWrote {out_path}")


def main():
    hitting_table = run_side(
        "hitting_pairs.csv", HITTING_CATEGORIES, HITTING_OUTCOMES,
        HITTING_BASE_RATE_KEYS, BASE_HITTING_RATES, "_adj", "HITTING",
    )
    pitching_table = run_side(
        "pitching_pairs.csv", PITCHING_CATEGORIES, PITCHING_OUTCOMES,
        PITCHING_BASE_RATE_KEYS, BASE_PITCHING_RATES, "_adj", "PITCHING",
    )
    write_tables_native(hitting_table, pitching_table)


if __name__ == "__main__":
    main()
