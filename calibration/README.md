# calibration/ — native PSD regression refit

Net-new workstream that fits pistachio's regression tables to **real PSD league data**
(ratings-snapshot → same-season performance), replacing upstream's other-league coefficients.
Nothing here is imported by the live pipeline (`main.py`/`config.py`/`metrics_*.py`) — it runs
standalone and produces its own `*_native.html` pages for side-by-side comparison.

## Workflow

```
extract_team_runs.py  ─→ data/team_*_runs.csv      ─→ fit_runs_per_game.py   (Workstream A)
extract_pairs.py      ─→ data/{hitting,pitching}_pairs.csv ─→ fit_tables.py  (Workstream B)
                                                        └─→ writes tables_native.py
metrics_{hitting,pitching}_native.py  — mirror the production formulas, import tables_native
main_native.py                        — standalone end-to-end run → hitters_native.html / pitchers_native.html
```

Run everything with `PISTACHIO_DB_PATH` pointing at (a copy of) psd-ootp's DB. The fit scripts
need `uv sync --extra calibration` (statsmodels/matplotlib); `main_native.py` itself needs only
the base deps — `tables_native.py` is literal constants, nothing is refit at runtime.

## Data & methodology (settled 2026-07-30, see NOTES.md Pass 6)

Three concurrent (same-season) MLB-only pairs — population driven from the **stats side**
(`level_id=1`, `is_latest=1`), never the rating row's own unreliable `level_id`:

| season | ratings snapshot | usable pairs (hit/pitch) |
|---|---|---|
| 2101 | `2101-12-31` (real dump) | 395 / 381 |
| 2102 | `2102-12-31` (real dump) | 578 / 556 |
| 2103 | `2104-04-28` (proxy — no true 2103 dump) | 608 / 598 |

**Workstream A** (wOBA→runs): OLS of team-season runs/162 on team wOBA (96 team-seasons/side,
R²≈0.93), slope rescaled to the per-650-PA player scale the formula consumes, CONST anchored at
league-average wOBA (the same structural zero upstream's constants encode). The rescaled PSD
hitting slope (546.6) independently landed within ~1.5% of upstream's 554.8 — the slope is
near-universal; the league-context constant is what needed recalibrating. (First attempt
shipped the raw team-scale slope — ~10x too steep, a real prospect projected at 153 WAR;
caught in verification, documented in `fit_runs_per_game.py`'s docstring.)

**Workstream B** (per-category tables): one joint multivariate OLS per outcome (6 hitting,
4 pitching outcomes; all categories as simultaneous predictors) — not per-bucket binned means,
which would splinter ~1,500 rows into sparse cells and bake cross-category correlation into
every table. Linear deliberately (no polynomial/spline): sampled onto bucket tables, a straight
line keeps constant slope at the edges, so `rating_lookup.interpolate_lookup()`'s
edge-slope extrapolation reproduces the fit exactly — no runaway values past the table edge.
Handedness: fit-time composite (`0.7*R + 0.3*L`, config's own `HANDEDNESS_WEIGHTS`) since
outcomes are season-aggregate; the fitted table applies unchanged to R/L/potential columns at
inference, exactly like production. Zero-point at each category's own sample mean (not "50" —
PSD's 1-100 ratings aren't centered there), reconciling with the already-calibrated
`BASE_*_RATES`.

## Deliberately out of scope

- **Fielding** (`FIELDING_RUN_VALUES_VS_REPLACEMENT`) — no verified fielding-outcome dataset;
  `main_native.py` runs the existing stopgap-based `metrics_fielding.py` for `*_def` only.
- **Role/eligibility thresholds** (`PITCH_MINIMUM_RATING`, `POSITION_THRESHOLDS`,
  `MINIMUM_STARTER_STAMINA`) — still stopgap-domain; `main_native.py` computes
  `pitches`/`field`/`sprp` on a stopgap-scaled copy exactly as production does.
- **Cutover** — the live pipeline is untouched; promoting these tables into `config.py` (and
  deleting the stopgap) is a separate, deliberate decision once the side-by-side comparison
  has been reviewed.

## Verification results (2026-07-30, local copy of live DB)

- Full native run: 17,862 players, 0% NaN, `best` −8.8…9.8, `war_pitching` −5.8…3.2, single
  leader at the top (no ceiling clustering — the exact Pass-5 bug class, rechecked).
- Leave-one-season-out CV (fit_tables.py prints per-fold): Spearman up to ~0.80 (k%), most
  outcomes 0.3–0.7; weakest are low-signal rates (1b%, hr-allowed) as expected.
- **Against real 2103 outcomes, native beats the production stopgap**: hitting predicted-wOBA
  Spearman 0.563 vs 0.466; pitching 0.364 vs 0.271.
- Native vs production rankings correlate strongly for hitting (best 0.95, bestP 0.98) —
  same relative ordering, recalibrated absolute scale. Pitching rankings differ more
  (Spearman 0.21) — expected, and the real-outcome check above says the native ordering is
  the better one.
- Floor-tie walls (~8k pitchers at −1.9, ~900 hitters at −8.8) are position players whose
  all-1 pitching ratings correctly collapse to identical projections — not a top-end bug.
