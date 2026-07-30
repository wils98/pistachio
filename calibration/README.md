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
build_calculator.py                   — standalone → calculator.html (interactive, offline)
```

Run everything with `PISTACHIO_DB_PATH` pointing at (a copy of) psd-ootp's DB. The fit scripts
need `uv sync --extra calibration` (statsmodels/matplotlib); `main_native.py`/`build_calculator.py`
themselves need only the base deps — `tables_native.py` is literal constants, nothing is refit
at runtime.

## Data & methodology (settled 2026-07-30, see NOTES.md Pass 6/7)

Three concurrent (same-season), **split-aware** MLB-only pairs — population driven from the
**stats side** (`level_id=1`, `is_latest=1`), never the rating row's own unreliable `level_id`.
Split-aware means: real per-player vsR/vsL performance
(`player_batting_stats_history.split_id` — 2=vsL, 3=vsR) is paired with the *matching side's*
rating (real vsR outcome ↔ `powR`/`ctrlR`, not a blend of both sides) — a `PA`/`BF ≥ 30`
per-split floor drops noisy small samples:

| season | ratings snapshot | usable pairs, hitting (R/L) | pitching (R/L) |
|---|---|---|---|
| 2101 | `2101-12-31` (real dump) | 367 / 334 | 353 / 344 |
| 2102 | `2102-12-31` (real dump) | 512 / 455 | 505 / 478 |
| 2103 | `2104-04-28` (proxy — no true 2103 dump) | 549 / 472 | 528 / 469 |

**Workstream A** (wOBA→runs): OLS of team-season runs/162 on team wOBA (96 team-seasons/side,
R²≈0.93), slope rescaled to the per-650-PA player scale the formula consumes, CONST anchored at
league-average wOBA (the same structural zero upstream's constants encode). The rescaled PSD
hitting slope (546.6) independently landed within ~1.5% of upstream's 554.8 — the slope is
near-universal; the league-context constant is what needed recalibrating. (First attempt
shipped the raw team-scale slope — ~10x too steep, a real prospect projected at 153 WAR;
caught in verification, documented in `fit_runs_per_game.py`'s docstring.) Unaffected by the
split-aware rework below — this regression operates on already-blended team wOBA.

**Workstream B** (per-category tables) — **split-aware, rebuilt same day.** One joint
multivariate OLS per outcome (6 hitting, 4 pitching outcomes; all categories as simultaneous
predictors), fit **separately per side** against real split-specific performance — not a
per-bucket binned mean (would splinter the sample into sparse cells and bake cross-category
correlation into every table), and not the first version's fit-time composite blend either (see
"What changed" below). Linear deliberately (no polynomial/spline): a straight line keeps
constant slope at the edges, so `rating_lookup.interpolate_lookup()`'s edge-slope extrapolation
reproduces the fit exactly. Zero-point at each side's own sample mean (not "50" — PSD's 1-100
ratings aren't centered there), reconciling with the already-calibrated `BASE_*_RATES`.

**Handedness exposure weights — also real data now, not a flat 0.7/0.3.**
`HANDEDNESS_WEIGHTS_NATIVE_HITTING`/`_PITCHING` in `tables_native.py` give each player's real
bats/throws-conditional exposure (`extract_pairs.compute_exposure_weights()`, real
2101-2103 stats joined to each player's `bats`/`throws`), replacing config.py's flat
`HANDEDNESS_WEIGHTS={"R":0.7,"L":0.3}` applied identically to every player. Real platooning is
substantial: a lefty batter faces RHP ~83.7% of the time vs. a righty batter's ~72.7%; a lefty
pitcher faces RHB ~79.6% of the time vs. a righty pitcher's ~57.1% (opposing managers stack the
platoon-advantaged batter side specifically against LHP). `reader.py`'s `add_scouted_ratings()`
now also selects `bats`/`throws` (small, additive change) so `metrics_*_native.py` can look up
the right weight per player.

### What changed from the first version (same day)

The first version of Workstream B fit **one shared table per category**, built from a
fit-time composite regressor (`0.7*R + 0.3*L`) against *aggregate* (not split) outcomes, then
applied that same table to R and L ratings separately at inference. That works, but it assumes
a rating's relationship to outcomes is identical regardless of pitcher handedness — real
split-specific outcome data (`split_id=2`/`3`) says otherwise, and was sitting unused. The
rebuild fits genuinely separate `vsR`/`vsL` tables per category directly against that real
split data. Result: markedly better fit quality (e.g. hitting-vs-RHP R² 0.44–0.68 vs. the old
blended fit's 0.19–0.42) and better real-world predictive power — see Verification below.

## Deliberately out of scope

- **Fielding** (`FIELDING_RUN_VALUES_VS_REPLACEMENT`) — no verified fielding-outcome dataset;
  `main_native.py` runs the existing stopgap-based `metrics_fielding.py` for `*_def` only.
- **Role/eligibility thresholds** (`PITCH_MINIMUM_RATING`, `POSITION_THRESHOLDS`,
  `MINIMUM_STARTER_STAMINA`) — still stopgap-domain; `main_native.py` computes
  `pitches`/`field`/`sprp` on a stopgap-scaled copy exactly as production does.
- **Cutover** — the live pipeline is untouched; promoting these tables into `config.py` (and
  deleting the stopgap) is a separate, deliberate decision once the side-by-side comparison
  has been reviewed.

## Verification results (2026-07-30, local copy of live DB, split-aware version)

- Full native run: 17,862 players, 0% NaN, `best` −8.8…10.4, `war_pitching` −7.1…3.9, single
  leader at the top (no ceiling clustering — the exact Pass-5 bug class, rechecked).
- **Against real 2103 outcomes (real R+L splits recombined into a real aggregate wOBA/pwOBA,
  for an honest apples-to-apples comparison), native beats the production stopgap by a wider
  margin than the first (non-split-aware) version did**: hitting predicted-wOBA Spearman
  **0.635 vs. 0.500**; pitching **0.432 vs. 0.330**. (First version: 0.563/0.466 and
  0.364/0.271 — the split-aware rebuild is a genuine improvement, not just a different number.)
- Native vs production rankings still correlate strongly for hitting (best 0.95, bestP 0.98) —
  same relative ordering, recalibrated absolute scale.
- Floor-tie walls (thousands of pitchers/hitters at the same floor value) are position players
  whose all-1 pitching ratings correctly collapse to identical projections — not a top-end bug.
- `calculator.html`'s JS port re-verified for exact numeric parity against the real Python
  functions post-rebuild, including the bats-conditional blend (both `bats="L"` and `bats="R"`
  profiles checked against a real prospect rating set).
